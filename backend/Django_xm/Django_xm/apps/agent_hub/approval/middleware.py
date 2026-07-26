"""统一审批中间件

基于 AgentMiddleware 实现，在 after_model 阶段批量拦截
AIMessage.tool_calls 中需要审批的工具调用。

设计目标：
    1. 解决并行工具调用只捕获首个 GraphInterrupt 的问题（Send API 限制）
    2. 收敛散落在各工具内部的审批逻辑到独立策略类
    3. 统一三套审批机制（工具级 interrupt / 项目自定义 HITL / 官方 HITL）

工作流程：
    1. AIMessage 生成后、tools 节点执行前，触发 aafter_model
    2. 扫描 last_ai_msg.tool_calls，对每个 tool_call 查找对应 Policy
    3. 对需要审批的 tool_call 构建审批请求（复用 _approval 格式）
    4. 一次 interrupt() 携带所有审批请求
    5. 处理用户返回的决策列表，修订 AIMessage.tool_calls

审批决策边界：
    - 审批决策完全在 middleware 内完成，工具层不参与审批判断
    - approved 的 tool_call 保留原参数（工具层不参与审批判断）
    - rejected/timeout 的 tool_call 仍保留在 tool_calls 中（让 should_continue
      路由到 ToolNode），同时在 artificial_messages 中注入 error ToolMessage，
      ToolNode 检测到已有 ToolMessage 后跳过执行，流程回到 call_model 让 LLM
      收到拒绝/超时反馈后自行决定下一步
"""

import logging
import uuid
from typing import Any, List, Optional

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage

logger = logging.getLogger(__name__)


class ApprovalMiddleware(AgentMiddleware):
    """统一审批中间件

    在 after_model 阶段扫描 AIMessage.tool_calls，
    对所有需要审批的工具调用批量发起一次 interrupt()，
    确保 __interrupt__ 事件包含所有审批请求。
    """

    def __init__(self, policies: Optional[List[Any]] = None):
        super().__init__()
        if policies is None:
            policies = self._default_policies()
        self.policies = {p.tool_name: p for p in policies}
        logger.info(f"ApprovalMiddleware 初始化: 注册策略 {list(self.policies.keys())}")

    @staticmethod
    def _default_policies() -> List[Any]:
        from .policies import (
            AgentCleanupApprovalPolicy,
            FileReaderApprovalPolicy,
            FsWriteFileApprovalPolicy,
            ShellExecApprovalPolicy,
            # deepagents 框架工具审批策略
            # deepagents FilesystemMiddleware 提供的工具名称与项目自定义工具不同，
            # 需要独立注册策略，否则 write_file/edit_file/execute 等工具会绕过审批
            ExecuteApprovalPolicy,
            WriteFileApprovalPolicy,
            EditFileApprovalPolicy,
            ReadFileApprovalPolicy,
        )

        return [
            # 项目自定义工具策略
            ShellExecApprovalPolicy(),
            FileReaderApprovalPolicy(),
            FsWriteFileApprovalPolicy(),
            AgentCleanupApprovalPolicy(),
            # deepagents 框架工具策略
            # execute → 继承 ShellExecApprovalPolicy 黑白名单逻辑
            ExecuteApprovalPolicy(),
            # write_file/edit_file → 写副作用，固定 high，始终需要审批
            WriteFileApprovalPolicy(),
            EditFileApprovalPolicy(),
            # read_file → 继承 FileReaderApprovalPolicy 绝对路径审批逻辑
            ReadFileApprovalPolicy(),
        ]

    def after_model(self, state, runtime):
        """同步 after_model 钩子（不实现实际逻辑，仅异步路径生效）"""
        return None

    async def aafter_model(self, state, runtime):
        """异步 after_model 钩子：批量拦截工具调用审批

        在 AIMessage 生成后、tools 节点执行前调用。
        扫描所有 tool_calls，对需要审批的批量发起 interrupt。
        """
        messages = state.get("messages", [])
        if not messages:
            return None

        # 提取 chat_session_id（chat 模块事件路由依赖，M1）
        # 三模块统一：chat 模块必填，deep_research 关联 chat 时填，learning 模块为 thread_id
        chat_session_id = ''
        try:
            runtime_context = getattr(runtime, 'context', None) or {}
            if isinstance(runtime_context, dict):
                chat_session_id = (
                    runtime_context.get('chat_session_id')
                    or runtime_context.get('session_id')
                    or ''
                )
            if not chat_session_id and isinstance(state, dict):
                chat_session_id = (
                    state.get('chat_session_id')
                    or state.get('session_id')
                    or ''
                )
        except Exception as extract_err:
            logger.warning(f"[ApprovalMiddleware] 提取 chat_session_id 失败: {extract_err}")

        # 查找最新的 AIMessage
        last_ai_msg = None
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                last_ai_msg = msg
                break

        if not last_ai_msg or not getattr(last_ai_msg, "tool_calls", None):
            return None

        # 收集需要审批的 tool_calls
        approval_requests: List[dict] = []

        # 生成批次 ID（graph_interrupt_id）：同批次审批共享，下游统一从 _meta 读取
        # 用于前端 grouping 和 DB 查询（Approval.objects.filter(extra__graph_interrupt_id=...)）
        # 不影响 Command(resume=...) 恢复逻辑（恢复使用 interrupt_id=tool_call_id）
        graph_interrupt_id = uuid.uuid4().hex

        for tc in last_ai_msg.tool_calls:
            tool_name = tc.get("name", "")
            # 统一参数提取：extract_tool_params 内置 _normalize_tool_call 处理非 dict 输入
            from Django_xm.apps.tools.param_extractor import extract_tool_params
            args = extract_tool_params(tc)
            tc_id = tc.get("id", "")

            policy = self.policies.get(tool_name)
            if policy is None:
                continue  # 无策略，不审批

            if not policy.should_approve(args):
                continue  # 不需要审批

            # 构建审批请求（复用 _approval 格式，前端兼容）
            request = {
                "_approval": True,
                "tool_name": tool_name,
                "tool_call_id": tc_id,
                "graph_interrupt_id": graph_interrupt_id,
                "session_id": chat_session_id,
                "title": policy.build_title(args),
                "description": policy.build_description(args),
                "operation": policy.build_operation_desc(args),
                "danger_level": policy.assess_danger(args),
                "args": args,
            }
            approval_requests.append(request)
            logger.info(
                f"[ApprovalMiddleware] 需要审批: tool={tool_name}, "
                f"operation={request['operation'][:80]}, "
                f"danger={request['danger_level']}, tc_id={tc_id}, "
                f"graph_interrupt_id={graph_interrupt_id}"
            )

        if not approval_requests:
            return None  # 无需审批，正常执行

        logger.info(
            f"[ApprovalMiddleware] 批量发起审批: {len(approval_requests)} 个请求, "
            f"tools={[r['tool_name'] for r in approval_requests]}, "
            f"graph_interrupt_id={graph_interrupt_id}"
        )

        # 一次 interrupt 携带所有审批请求
        # _meta.graph_interrupt_id 为批次 ID 单一来源，下游统一从 _meta 读取
        from langgraph.types import interrupt

        decisions = interrupt({
            "_approval": True,
            "requests": approval_requests,
            "_meta": {
                "graph_interrupt_id": graph_interrupt_id,
            },
        })

        # 处理可能的包装格式
        # 如果 decisions 是 {graph_interrupt_id: {tool_call_id: bool}} 格式，解包为 {tool_call_id: bool}
        if isinstance(decisions, dict):
            # 检查是否是包装格式：value 是 dict 且 key 不在 approval_requests 的 tool_call_id 列表中
            tc_ids = {r["tool_call_id"] for r in approval_requests}
            if len(decisions) == 1:
                only_key = next(iter(decisions.keys()))
                only_value = decisions[only_key]
                if isinstance(only_value, dict) and only_key not in tc_ids:
                    # 包装格式，解包
                    decisions = only_value
                    logger.info(f"[ApprovalMiddleware] 检测到包装格式，已解包: {decisions}")

        # 处理决策
        # decisions 格式灵活，统一转为 {tool_call_id: bool} 格式
        decision_map = self._normalize_decisions(decisions, approval_requests)

        # 修订 tool_calls：
        # - approved 的保留原参数，正常执行（工具层不参与审批判断）
        # - rejected 的也保留在 tool_calls 中（让 should_continue 路由到 ToolNode），
        #   但在 artificial_messages 中添加 error ToolMessage，
        #   ToolNode 会检测到已有 ToolMessage 跳过执行，
        #   流程回到 call_model 让 LLM 收到拒绝反馈后自行决定下一步
        revised_tool_calls: List[dict] = []
        artificial_messages: List[ToolMessage] = []
        rejected_count = 0

        from .timeout_handler import TIMEOUT_DECISION, build_timeout_tool_message

        timeout_count = 0

        for tc in last_ai_msg.tool_calls:
            tc_id = tc.get("id", "")
            tool_name = tc.get("name", "")
            args = tc.get("args", {})

            # 检查是否有审批决策
            if tc_id in decision_map:
                decision = decision_map[tc_id]
                if decision is True:
                    # 用户确认：保留原始 tool_call，工具层不参与审批判断
                    # 工具层不参与审批判断，所有审批决策在 middleware 内完成
                    revised_tool_calls.append(tc)
                    logger.info(f"[ApprovalMiddleware] 用户已确认: {tool_name} (tc_id={tc_id})")
                elif decision == TIMEOUT_DECISION:
                    # 审批超时：保留 tool_call（让 ToolNode 跳过执行），
                    # 注入"审批超时"ToolMessage 告知 Agent，Agent 可调整策略继续执行
                    revised_tool_calls.append(tc)
                    timeout_count += 1
                    artificial_messages.append(build_timeout_tool_message(
                        tool_call_id=tc_id,
                        tool_name=tool_name,
                    ))
                    logger.info(
                        f"[ApprovalMiddleware] 审批超时: {tool_name} (tc_id={tc_id}), "
                        f"已注入超时 ToolMessage"
                    )
                else:
                    # 用户拒绝：保留 tool_call（让 ToolNode 跳过执行），添加 error ToolMessage 告知 Agent
                    revised_tool_calls.append(tc)
                    rejected_count += 1
                    # 构建操作描述（用于 Agent 理解被拒绝的具体操作）
                    policy = self.policies.get(tool_name)
                    operation_desc = policy.build_operation_desc(args) if policy else str(args)
                    artificial_messages.append(ToolMessage(
                        content=(
                            f"用户已拒绝执行工具 {tool_name}（操作内容: {operation_desc}）。"
                            f"该工具未被执行，请勿重复调用相同参数。"
                            f"请根据用户意图尝试其他方法、调整参数重新请求，或询问用户是否需要其他帮助。"
                        ),
                        tool_call_id=tc_id,
                        name=tool_name,
                        status="error",
                    ))
                    logger.info(f"[ApprovalMiddleware] 用户已拒绝: {tool_name} (tc_id={tc_id}, operation={operation_desc[:80]})")
            else:
                # 无需审批的 tool_call，保留
                revised_tool_calls.append(tc)

        # 保留所有 tool_calls（包括被拒绝/超时的），让 should_continue 路由到 ToolNode
        # ToolNode 会跳过已有 error ToolMessage 的 tool_call
        last_ai_msg.tool_calls = revised_tool_calls

        result: dict = {}
        if artificial_messages:
            result["messages"] = [last_ai_msg, *artificial_messages]
        else:
            result["messages"] = [last_ai_msg]

        logger.info(
            f"[ApprovalMiddleware] 审批完成: 保留 {len(revised_tool_calls) - rejected_count - timeout_count} 个, "
            f"拒绝 {rejected_count} 个, 超时 {timeout_count} 个（保留 tool_call 让 ToolNode 跳过）"
        )

        return result

    @staticmethod
    def _normalize_decisions(decisions, approval_requests):
        """将各种格式的决策统一为 {tool_call_id: decision} 字典

        decision 取值：
            - True：用户确认
            - False：用户拒绝
            - TIMEOUT_DECISION（"_timeout"）：审批超时

        支持的输入格式：
            1. {tool_call_id: True/False/"_timeout"}
            2. {"approved": [tc_id, ...], "rejected": [tc_id, ...], "timeout": [tc_id, ...]}
            3. [True, False, "_timeout", ...]（按顺序对应 approval_requests）
            4. True/False/"_timeout"（全部统一批准/拒绝/超时）

        未知格式默认全部拒绝（安全第一）。
        """
        from .timeout_handler import TIMEOUT_DECISION

        def _normalize_value(v):
            """将单个决策值标准化：保留 TIMEOUT_DECISION，其余转 bool"""
            if v == TIMEOUT_DECISION:
                return TIMEOUT_DECISION
            return bool(v)

        if isinstance(decisions, dict):
            # 格式2：approved/rejected/timeout 列表
            if "approved" in decisions or "rejected" in decisions:
                result = {}
                approved_set = set(decisions.get("approved", []))
                rejected_set = set(decisions.get("rejected", []))
                timeout_set = set(decisions.get("timeout", []))
                for req in approval_requests:
                    tc_id = req["tool_call_id"]
                    if tc_id in approved_set:
                        result[tc_id] = True
                    elif tc_id in rejected_set:
                        result[tc_id] = False
                    elif tc_id in timeout_set:
                        result[tc_id] = TIMEOUT_DECISION
                    else:
                        result[tc_id] = False  # 默认拒绝
                return result
            # 格式1：{tool_call_id: bool/"_timeout"}
            return {k: _normalize_value(v) for k, v in decisions.items()}

        if isinstance(decisions, list):
            # 格式3：按顺序对应
            result = {}
            for i, req in enumerate(approval_requests):
                tc_id = req["tool_call_id"]
                result[tc_id] = _normalize_value(decisions[i]) if i < len(decisions) else False
            return result

        # 格式4：全部统一（True/False/"_timeout"）
        if decisions == TIMEOUT_DECISION:
            return {req["tool_call_id"]: TIMEOUT_DECISION for req in approval_requests}
        if isinstance(decisions, bool):
            return {req["tool_call_id"]: decisions for req in approval_requests}

        # 未知格式，默认全部拒绝（安全第一）
        logger.warning(f"[ApprovalMiddleware] 未知决策格式: {type(decisions)}, 默认全部拒绝")
        return {req["tool_call_id"]: False for req in approval_requests}
