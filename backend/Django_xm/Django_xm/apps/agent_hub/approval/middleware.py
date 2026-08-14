"""统一审批中间件

基于 AgentMiddleware 实现，在 after_model 阶段批量拦截
AIMessage.tool_calls 中需要审批的工具调用。

设计目标：
    1. 解决并行工具调用只捕获首个 GraphInterrupt 的问题（Send API 限制）
    2. 收敛散落在各工具内部的审批逻辑到独立策略类
    3. 统一三套审批机制（工具级 interrupt / 项目自定义 HITL / 官方 HITL）

风险分级（RiskLevel）：
    - SAFE: 自动通过，不 interrupt，仅写审计日志（auto_approved=True）
    - CONTROLLED: 进入 interrupt 批次，常规审批 UI
    - HIGH: 进入 interrupt 批次，前端红名高亮 + 强制 Docker 沙箱执行

工作流程：
    1. AIMessage 生成后、tools 节点执行前，触发 aafter_model
    2. 扫描 last_ai_msg.tool_calls，对每个 tool_call 查找对应 Policy
    3. SAFE 级 tool_call：跳过 interrupt，注册 tool_call_lifecycle 上下文 +
       转换为 TOOL_CALL_RUNNING 事件（auto_approved=True），让工具正常执行
    4. CONTROLLED/HIGH 级 tool_call：构建审批请求（携带 risk_level），进入 interrupt 批次
    5. 一次 interrupt() 携带所有审批请求
    6. 处理用户返回的决策列表，修订 AIMessage.tool_calls

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
from typing import Any

from asgiref.sync import sync_to_async
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage

# 模块级导入 interrupt：测试通过 patch("...middleware.interrupt") 拦截调用，
# 局部导入会导致 patch 失败（AttributeError: module has no attribute 'interrupt'）
from langgraph.types import interrupt

from Django_xm.common.approval_utils import derive_cross_module_id_from_source
from Django_xm.common.observability.approval_metrics import approval_metrics
from Django_xm.common.risk_levels import RiskLevel

logger = logging.getLogger(__name__)


class ApprovalMiddleware(AgentMiddleware):
    """统一审批中间件

    在 after_model 阶段扫描 AIMessage.tool_calls，
    对所有需要审批的工具调用批量发起一次 interrupt()，
    确保 __interrupt__ 事件包含所有审批请求。
    """

    def __init__(self, policies: list[Any] | None = None):
        super().__init__()
        if policies is None:
            policies = self._default_policies()
        self.policies = {p.tool_name: p for p in policies}
        logger.info(f"ApprovalMiddleware 初始化: 注册策略 {list(self.policies.keys())}")

    @staticmethod
    def _default_policies() -> list[Any]:
        from .policies import (
            AgentCleanupApprovalPolicy,
            AgentCreateApprovalPolicy,
            AgentRunApprovalPolicy,
            AttachmentRagSearchApprovalPolicy,
            EditFileApprovalPolicy,
            # deepagents 框架工具审批策略
            # deepagents FilesystemMiddleware 提供的工具名称与项目自定义工具不同，
            # 需要独立注册策略，否则 write_file/edit_file/execute 等工具会绕过审批
            ExecuteApprovalPolicy,
            FileReaderApprovalPolicy,
            FsWriteFileApprovalPolicy,
            GlobApprovalPolicy,
            GrepApprovalPolicy,
            LsApprovalPolicy,
            ReadFileApprovalPolicy,
            ShellExecApprovalPolicy,
            TodoWriteApprovalPolicy,
            WebFetchApprovalPolicy,
            WebSearchApprovalPolicy,
            WriteFileApprovalPolicy,
        )

        return [
            # 项目自定义工具策略
            ShellExecApprovalPolicy(),
            FileReaderApprovalPolicy(),
            AttachmentRagSearchApprovalPolicy(),
            FsWriteFileApprovalPolicy(),
            AgentCleanupApprovalPolicy(),
            # 其他副作用工具策略（agent_create/agent_run/write_todos）
            AgentCreateApprovalPolicy(),
            AgentRunApprovalPolicy(),
            TodoWriteApprovalPolicy(),
            # deepagents 框架工具策略
            # execute → 继承 ShellExecApprovalPolicy 黑白名单逻辑
            ExecuteApprovalPolicy(),
            # write_file/edit_file → 写副作用，固定 high，始终需要审批
            WriteFileApprovalPolicy(),
            EditFileApprovalPolicy(),
            # read_file → 继承 FileReaderApprovalPolicy 绝对路径审批逻辑
            ReadFileApprovalPolicy(),
            # deepagents 内置只读工具 → SAFE 自动通过（注册 ctx 保证参数完整）
            LsApprovalPolicy(),
            GlobApprovalPolicy(),
            GrepApprovalPolicy(),
            WebSearchApprovalPolicy(),
            WebFetchApprovalPolicy(),
        ]

    def after_model(self, state, runtime):
        """同步 after_model 钩子（不实现实际逻辑，仅异步路径生效）"""
        return None

    @staticmethod
    def _extract_subagent_context(state: dict | None = None, runtime=None) -> dict | None:
        """提取子 agent 上下文。

        deepagents 0.7.5 升级后，middleware 钩子的 runtime 参数为标准
        ``langgraph.runtime.Runtime``（无 config 属性），因此子 agent 上下文
        不再从 runtime.config.configurable 读取，改为：

        1. state 字段（SubAgentNestingMiddleware.before_model 写入）：
           - subagent_depth: int，嵌套层级（0=主 agent，1=子 agent）
           - subagent_path: list[str]，完整调用链路（如 ["main", "web-researcher"]）
           - subagent_risk_ceiling: str，子 agent 角色风险上限
        2. get_config().metadata.lc_agent_name：子 agent 名称（create_agent 官方设置）
        3. configurable.ls_agent_type == "subagent"：子 agent 判定（0.7.5 atask 注入）

        主 agent 的 state 无 subagent_* 字段，返回 None（assess_risk 不做加权）。

        Args:
            state: 当前 agent state（aafter_model 参数）
            runtime: 兼容旧调用（保留参数，实际不再依赖）

        Returns:
            dict | None: 子 agent 上下文字典；主 agent 返回 None
        """
        try:
            state = state or {}
            depth = state.get("subagent_depth", 0)
            agent_path = state.get("subagent_path")
            risk_ceiling = state.get("subagent_risk_ceiling")

            if depth == 0 and not agent_path:
                # 主 agent：无嵌套字段，返回 None
                return None

            if not isinstance(depth, int) or depth < 0:
                depth = 0
            if not isinstance(agent_path, list):
                agent_path = []

            from langgraph.config import get_config

            agent_name = ""
            try:
                config = get_config()
                metadata = config.get("metadata") if isinstance(config, dict) else None
                agent_name = (metadata or {}).get("lc_agent_name") or ""
            except Exception as e:
                logger.debug(f"[ApprovalMiddleware] 读取 lc_agent_name 失败(非致命): {e}")

            # risk_ceiling 可能是 RiskLevel 枚举或字符串，统一为 RiskLevel
            if risk_ceiling is not None and not isinstance(risk_ceiling, RiskLevel):
                try:
                    risk_ceiling = RiskLevel(risk_ceiling)
                except (ValueError, TypeError):
                    risk_ceiling = None

            return {
                "risk_ceiling": risk_ceiling,
                "depth": depth,
                "agent_name": agent_name or "",
                "agent_path": agent_path,
                # 0.7.5 官方机制不提供父 task 工具的 tool_call_id，留空（前端不展示父链路）
                "parent_tool_call_id": "",
            }
        except Exception as e:
            logger.debug(f"[ApprovalMiddleware] 提取 subagent_context 失败(非致命): {e}")
            return None

    @staticmethod
    async def _audit_auto_approved_tools(
        tool_calls: list[dict],
        chat_session_id: str,
        state,
        runtime,
        *,
        publish_running: bool = True,
    ) -> None:
        """SAFE 级自动通过的审计：注册 tool_call_lifecycle + 转换为 TOOL_CALL_RUNNING。

        SAFE 级工具调用不进入 interrupt 批次，但仍需注册 tool_call_lifecycle 上下文，
        让前端 ToolCallCard 显示"执行中"状态，并在工具完成后显示结果。

        通过统一入口 ``service.register`` + ``service.transition_async`` 发布事件，
        与审批通过后的行为完全一致（仅 auto_approved 标记不同）。

        Args:
            tool_calls: SAFE 级 tool_call 列表，每项含 tool_call_id/tool_name/args
            chat_session_id: 会话 ID（事件路由依赖）
            state: LangGraph state（用于提取 source_id）
            runtime: 运行时对象（用于提取 configurable）
            publish_running: 是否发布 TOOL_CALL_RUNNING 事件。无策略工具
                （3.3）传 False：仅注册 ctx 参数（权威参数源），不发布事件，
                避免与 extractor 的 PENDING 事件冲突。
        """
        from Django_xm.common.event_schema import EventSource, EventType
        from Django_xm.common.tool_call_lifecycle import ToolCallContext, service

        # 提取 configurable（deepagents 0.7.5 升级后 runtime 无 config 属性，
        # 优先用 get_config()，回退 runtime）
        configurable: dict[str, Any] = {}
        try:
            from langgraph.config import get_config as _get_config

            _cfg = _get_config()
            if isinstance(_cfg, dict):
                configurable = _cfg.get("configurable") or {}
        except Exception as e:
            logger.debug(f"[ApprovalMiddleware] get_config 读取 configurable 失败(非致命): {e}")
        if not configurable:
            try:
                if hasattr(runtime, "config"):
                    _config = runtime.config
                    if isinstance(_config, dict):
                        configurable = _config.get("configurable") or {}
                elif isinstance(runtime, dict):
                    configurable = runtime.get("configurable") or {}
            except Exception:
                # runtime 结构不可识别时回退到空 configurable，后续以默认值处理
                logger.debug("提取 runtime configurable 失败，使用空字典")

        # 判断来源：深度研究 agent 的 configurable 含 thread_id（=task_id）
        thread_id = configurable.get("thread_id", "")
        # assistant_message_id：chat 关联深度研究场景由 research_runner 注入 config，
        # 工具事件注册携带归属消息 ID（前端 toolCallsMap → message.toolCalls 归属依赖）
        _assistant_message_id = configurable.get("assistant_message_id") or ""

        # 嵌套层级字段（deepagents 0.7.5 机制）：
        # SubAgentNestingMiddleware 写入 state（subagent_depth/path/risk_ceiling），
        # 优先从 state 读取，回退 configurable（兼容旧调用方）
        sub_depth = state.get("subagent_depth", 0) if isinstance(state, dict) else 0
        if not isinstance(sub_depth, int) or sub_depth < 0:
            sub_depth = 0
        sub_agent_path = state.get("subagent_path") if isinstance(state, dict) else None
        if not isinstance(sub_agent_path, list):
            sub_agent_path = []
        sub_risk_ceiling_raw = state.get("subagent_risk_ceiling") if isinstance(state, dict) else None
        if sub_risk_ceiling_raw is None:
            sub_risk_ceiling_raw = configurable.get("risk_ceiling")
        # risk_ceiling 可能是 RiskLevel 枚举或字符串，统一转字符串存入 context
        if sub_risk_ceiling_raw is not None and not isinstance(sub_risk_ceiling_raw, str):
            sub_risk_ceiling = (
                sub_risk_ceiling_raw.value if hasattr(sub_risk_ceiling_raw, "value") else str(sub_risk_ceiling_raw)
            )
        else:
            sub_risk_ceiling = sub_risk_ceiling_raw or ""
        # parent_tool_call_id：0.7.5 官方机制不提供，兼容旧 configurable 值
        sub_parent_tool_call_id = configurable.get("parent_tool_call_id", "") or ""
        # agent_name：优先 metadata.lc_agent_name（create_agent 官方设置）
        sub_agent_name = ""
        try:
            _metadata = _get_config().get("metadata") if isinstance(_get_config(), dict) else None
            sub_agent_name = (_metadata or {}).get("lc_agent_name") or ""
        except Exception as e:
            logger.debug(f"[ApprovalMiddleware] 读取子 agent lc_agent_name 失败(非致命): {e}")
        if not sub_agent_name:
            sub_agent_name = configurable.get("agent_name", "") or ""

        if thread_id and sub_depth > 0:
            # 子 agent：沿用父 agent 的来源（通常为 DEEP_RESEARCH）
            module = EventSource.DEEP_RESEARCH
            module_id = thread_id
        elif thread_id:
            # 主 agent：基于 thread_id 与 chat_session_id 是否一致判断来源。
            # - thread_id != chat_session_id（含 chat_session_id 为空）→ DEEP_RESEARCH
            #   - 独立深度研究：chat_session_id="" 且 thread_id=task_id，不等
            #   - 关联深度研究：chat_session_id 与 thread_id(task_id) 不同
            # - thread_id == chat_session_id → CHAT（纯聊天场景）
            module = (
                EventSource.DEEP_RESEARCH if (thread_id != chat_session_id) else EventSource.CHAT
            )
            module_id = thread_id
        else:
            module = EventSource.CHAT
            module_id = chat_session_id

        # cross_module_id：深度研究关联 chat 场景
        cross_module_id = derive_cross_module_id_from_source(
            "deep_research" if module == EventSource.DEEP_RESEARCH else module.value, chat_session_id
        )

        for tc_info in tool_calls:
            tool_call_id = tc_info["tool_call_id"]
            tool_name = tc_info["tool_name"]
            parameters = tc_info.get("args") or {}

            try:
                # 注册上下文（幂等：已存在时不覆盖非空字段）
                # auto_approved=True 标记 SAFE 级自动通过（审计用，前端可显示"自动通过"徽章）
                # 子 agent 嵌套层级字段：注册到 context，transition_async 透传到 payload
                service.register(
                    ToolCallContext(
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        module=module,
                        module_id=module_id,
                        message_id=_assistant_message_id,
                        parameters=parameters,
                        cross_module_id=cross_module_id,
                        auto_approved=True,
                        parent_tool_call_id=sub_parent_tool_call_id,
                        depth=sub_depth,
                        agent_name=sub_agent_name,
                        agent_path=sub_agent_path,
                        risk_ceiling=sub_risk_ceiling,
                        # SAFE 级工具显式传入 risk_level='safe'，确保 tool_call_running
                        # 事件 payload 携带 risk_level，前端 toolCall.riskLevel='safe'
                        # 根因修复：原注册遗漏 risk_level，导致 SAFE 级工具事件缺风险等级
                        risk_level=str(RiskLevel.SAFE.value),
                    )
                )
                # 转换为 TOOL_CALL_RUNNING（auto_approved=True 已写入 context，
                # transition_async 会从 context 透传到事件 payload）
                # 前端 ToolCallCard 显示"执行中"状态
                # publish_running=False（无策略工具）：仅注册 ctx 参数，不发布事件
                if publish_running:
                    params_value = (
                        parameters
                        if (isinstance(parameters, dict) and parameters)
                        else ({} if isinstance(parameters, dict) else None)
                    )
                    await service.transition_async(
                        tool_call_id,
                        EventType.TOOL_CALL_RUNNING,
                        parameters=params_value,
                    )
                # 可观测性指标（F2）：SAFE 级自动通过计数
                approval_metrics.on_created(RiskLevel.SAFE, auto_approved=True)
                logger.info(
                    f"[ApprovalMiddleware] SAFE 审计已注册: tool={tool_name}, "
                    f"tc_id={tool_call_id}, auto_approved=True, module={module.value}, "
                    f"depth={sub_depth}, agent_name={sub_agent_name or '(main)'}"
                )
            except Exception as audit_err:
                logger.warning(
                    f"[ApprovalMiddleware] SAFE 审计注册失败(非致命): "
                    f"tool={tool_name}, tc_id={tool_call_id}, err={audit_err}"
                )

    @staticmethod
    async def _build_idempotent_tc_ids(last_ai_msg) -> set[str]:
        """构建审批幂等集合（3.5）。

        恢复场景下，checkpoint 旧 AIMessage.tool_calls 中的 tool_call 可能在
        上一轮已审批/已完成（DB Approval 为 approved、ctx last_event_type 为
        completed/failed），这些 tool_call 不再重复拦截。

        只对 approved 幂等：rejected / timeout 是"已决策但未执行"，恢复时
        必须走 decision_map 分支注入拒绝/超时 ToolMessage（error 反馈），
        让 ToolNode 跳过执行、LLM 收到反馈后调整策略，而不是直接执行。
        （拒绝后仍执行工具 = 拒绝失效，日志实证"幂等跳过"把 rejected
        tool_call 保留执行。）

        本方法在 async 上下文（aafter_model）中执行，DB 查询与 cache 读取
        均为同步调用，必须经 sync_to_async 包装，否则 Django 抛
        SynchronousOnlyOperation（日志实证"构建幂等集合失败(非致命): You
        cannot call this from an async context"），幂等集合恒为空。
        """
        try:
            from Django_xm.apps.approvals.models import Approval as _Approval
            from Django_xm.common.event_schema import EventType as _EventType
            from Django_xm.common.tool_call_lifecycle import service as _lifecycle_service

            _all_tc_ids = [t.get("id", "") for t in last_ai_msg.tool_calls]

            # 仅统计 approved（已批准执行的）；pending/processing/waiting 未决、
            # rejected/timeout 已决未执行——这些都不幂等，恢复时走 decision_map
            # 重新投递决策（拒绝/超时注入 error ToolMessage）。
            _resolved_ids: set[str] = await sync_to_async(
                lambda: set(
                    _Approval.objects.filter(interrupt_id__in=_all_tc_ids)
                    .filter(state=_Approval.STATE_APPROVED)
                    .values_list("interrupt_id", flat=True)
                )
            )()

            _idempotent_tc_ids: set[str] = set()
            for _t in last_ai_msg.tool_calls:
                _tid = _t.get("id", "")
                _ctx = await sync_to_async(_lifecycle_service.get_context)(_tid)
                _last_evt = _ctx.get("last_event_type") if _ctx else None
                if _tid in _resolved_ids or _last_evt in (
                    _EventType.TOOL_CALL_COMPLETED.value,
                    _EventType.TOOL_CALL_FAILED.value,
                ):
                    _idempotent_tc_ids.add(_tid)
            if _idempotent_tc_ids:
                logger.info(
                    f"[ApprovalMiddleware] 幂等集合: {len(_idempotent_tc_ids)} 个 "
                    f"tc_id={sorted(_idempotent_tc_ids)}, resolved_ids={sorted(_resolved_ids)}"
                )
            return _idempotent_tc_ids
        except Exception as _idem_err:
            logger.debug(f"[ApprovalMiddleware] 构建幂等集合失败(非致命): {_idem_err}")
            return set()

    async def aafter_model(self, state, runtime):
        """异步 after_model 钩子：批量拦截工具调用审批

        在 AIMessage 生成后、tools 节点执行前调用。
        扫描所有 tool_calls：
        - SAFE 级（白名单/只读相对路径等）：跳过 interrupt，注册 auto_approved 审计
        - CONTROLLED/HIGH 级：进入 interrupt 批次，等待用户审批
        """
        messages = state.get("messages", [])
        if not messages:
            return None

        # 提取 chat_session_id（chat 模块事件路由依赖，M1）
        # 三模块统一：chat 模块必填，deep_research 关联 chat 时填，learning 模块为 thread_id
        # P14 根因修复：深度研究 agent 的 chat_session_id 存在于 config.configurable
        # （research_runner.py 注入），经 langgraph get_config() 读取。
        # deepagents 0.7.5 升级后 middleware 钩子 runtime 为标准 Runtime（无 config），
        # 优先用 get_config()，回退 runtime.context / state。
        chat_session_id = ""
        try:
            # 优先从 get_config() 的 configurable 读取
            configurable: dict = {}
            try:
                from langgraph.config import get_config as _get_config

                _cfg = _get_config()
                if isinstance(_cfg, dict):
                    configurable = _cfg.get("configurable") or {}
            except Exception as e:
                logger.debug(f"[ApprovalMiddleware] get_config 读取 configurable 失败(非致命): {e}")
            if not configurable:
                try:
                    if hasattr(runtime, "config"):
                        config = runtime.config
                        if isinstance(config, dict):
                            configurable = config.get("configurable") or {}
                except Exception as e:
                    logger.debug(f"[ApprovalMiddleware] runtime.config 读取 configurable 失败(非致命): {e}")
            if configurable:
                # chat 模块：thread_id == session_id（agent_service.py 注入）
                # deep_research 模块：chat_session_id 显式注入
                chat_session_id = (
                    configurable.get("chat_session_id")
                    or configurable.get("session_id")
                    or configurable.get("thread_id")
                    or ""
                )
            # 回退：runtime.context
            if not chat_session_id:
                runtime_context = getattr(runtime, "context", None) or {}
                if isinstance(runtime_context, dict):
                    chat_session_id = runtime_context.get("chat_session_id") or runtime_context.get("session_id") or ""
            # 回退：state
            if not chat_session_id and isinstance(state, dict):
                chat_session_id = state.get("chat_session_id") or state.get("session_id") or ""
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
        approval_requests: list[dict] = []

        # 收集 SAFE 级自动通过的 tool_call（仅审计，不 interrupt）
        auto_approved_tool_calls: list[dict] = []

        # 生成批次 ID（graph_interrupt_id）：同批次审批共享，下游统一从 _meta 读取
        # 用于前端 grouping 和 DB 查询（Approval.objects.filter(extra__graph_interrupt_id=...)）
        # 不影响 Command(resume=...) 恢复逻辑（恢复使用 interrupt_id=tool_call_id）
        graph_interrupt_id = uuid.uuid4().hex

        # 提取 subagent_context（从 state + get_config 读取）
        # 用于子 agent 风险加权（assess_risk 的 subagent_context 参数）
        subagent_context = self._extract_subagent_context(state, runtime)

        # 3.5 审批幂等：恢复场景下，checkpoint 旧 AIMessage.tool_calls 中的
        # 已审批/已完成 tool_call 不再重复拦截（生成新批次审批，导致 L1248 冗余审批、
        # 恢复后工具未执行）。幂等跳过后 tool_call 保留在 AIMessage.tool_calls 中，
        # ToolNode 会正常执行（无 ToolMessage 则执行；已有 error ToolMessage 则跳过）。
        # 注意：aafter_model 是 async 上下文，DB 查询与 cache 读取必须经 sync_to_async
        # 包装，否则 Django 抛 SynchronousOnlyOperation（日志实证"构建幂等集合失败
        # (非致命): You cannot call this from an async context"），幂等集合恒为空。
        _idempotent_tc_ids = await self._build_idempotent_tc_ids(last_ai_msg)

        for tc in last_ai_msg.tool_calls:
            tool_name = tc.get("name", "")
            # 统一参数提取：extract_tool_params 内置 _normalize_tool_call 处理非 dict 输入
            from Django_xm.apps.tools.param_extractor import extract_tool_params

            args = extract_tool_params(tc)
            tc_id = tc.get("id", "")

            if tc_id in _idempotent_tc_ids:
                # 幂等跳过：恢复场景已审批/已完成的 tool_call 不再拦截，
                # 保留在 tool_calls 中由 ToolNode 正常执行
                logger.info(
                    f"[ApprovalMiddleware] 幂等跳过已审批/已完成 tool_call: "
                    f"tool={tool_name}, tc_id={tc_id}"
                )
                continue

            policy = self.policies.get(tool_name)
            if policy is None:
                # 3.3 无策略工具：不审批不审计，但注册 ctx（完整参数），
                # 为 extractor 参数聚合失败的场景提供权威参数源。
                # 不发布 RUNNING（避免与 extractor 的 PENDING 冲突），仅注册参数。
                if args and isinstance(args, dict):
                    try:
                        await self._audit_auto_approved_tools(
                            [
                                {
                                    "tool_call_id": tc_id,
                                    "tool_name": tool_name,
                                    "args": args,
                                }
                            ],
                            chat_session_id,
                            state,
                            runtime,
                            publish_running=False,
                        )
                        logger.info(
                            f"[ApprovalMiddleware] 无策略工具已注册 ctx（参数完整）: "
                            f"tool={tool_name}, tc_id={tc_id}"
                        )
                    except Exception as register_err:
                        logger.warning(
                            f"[ApprovalMiddleware] 无策略工具注册 ctx 失败: "
                            f"tool={tool_name}, tc_id={tc_id}, err={register_err}"
                        )
                continue  # 无策略，不审批

            # 评估风险等级（携带 subagent_context 用于子 agent 加权）
            risk_level = policy.assess_risk(args, subagent_context=subagent_context)

            if risk_level == RiskLevel.SAFE:
                # SAFE 级：自动通过，仅审计
                auto_approved_tool_calls.append(
                    {
                        "tool_call_id": tc_id,
                        "tool_name": tool_name,
                        "args": args,
                    }
                )
                logger.info(f"[ApprovalMiddleware] SAFE 自动通过: tool={tool_name}, tc_id={tc_id}, auto_approved=True")
                continue

            # CONTROLLED / HIGH 级：构建审批请求
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
                "risk_level": risk_level.value,  # 透传 RiskLevel 到前端/DB
                "args": args,
            }
            # 嵌套层级字段透传（Phase E3）：
            # 子 agent 的审批请求携带 parent_tool_call_id / depth / agent_path，
            # 供下游（research_runner / chat views_chat）写入 Approval.extra，
            # 前端 ToolCallCard 可展示完整调用链路与嵌套层级。
            if subagent_context is not None:
                request["parent_tool_call_id"] = subagent_context.get("parent_tool_call_id", "")
                request["depth"] = subagent_context.get("depth", 0)
                request["agent_name"] = subagent_context.get("agent_name", "")
                request["agent_path"] = subagent_context.get("agent_path", [])
            approval_requests.append(request)
            logger.info(
                f"[ApprovalMiddleware] 需要审批: tool={tool_name}, "
                f"operation={request['operation'][:80]}, "
                f"danger={request['danger_level']}, "
                f"risk_level={risk_level.value}, tc_id={tc_id}, "
                f"graph_interrupt_id={graph_interrupt_id}"
            )

        # SAFE 级审计：注册 tool_call_lifecycle 上下文 + 转换为 TOOL_CALL_RUNNING
        # 让前端 ToolCallCard 显示"执行中"状态（与审批通过后的行为一致）
        if auto_approved_tool_calls:
            await self._audit_auto_approved_tools(
                auto_approved_tool_calls,
                chat_session_id,
                state,
                runtime,
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
        decisions = interrupt(
            {
                "_approval": True,
                "requests": approval_requests,
                "_meta": {
                    "graph_interrupt_id": graph_interrupt_id,
                },
            }
        )

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
        revised_tool_calls: list[dict] = []
        artificial_messages: list[ToolMessage] = []
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
                    artificial_messages.append(
                        build_timeout_tool_message(
                            tool_call_id=tc_id,
                            tool_name=tool_name,
                        )
                    )
                    logger.info(f"[ApprovalMiddleware] 审批超时: {tool_name} (tc_id={tc_id}), 已注入超时 ToolMessage")
                else:
                    # 用户拒绝：保留 tool_call（让 ToolNode 跳过执行），添加 error ToolMessage 告知 Agent
                    revised_tool_calls.append(tc)
                    rejected_count += 1
                    # 构建操作描述（用于 Agent 理解被拒绝的具体操作）
                    policy = self.policies.get(tool_name)
                    operation_desc = policy.build_operation_desc(args) if policy else str(args)
                    artificial_messages.append(
                        ToolMessage(
                            content=(
                                f"用户已拒绝执行工具 {tool_name}（操作内容: {operation_desc}）。"
                                f"该工具未被执行，请勿重复调用相同参数。"
                                f"请根据用户意图尝试其他方法、调整参数重新请求，或询问用户是否需要其他帮助。"
                            ),
                            tool_call_id=tc_id,
                            name=tool_name,
                            status="error",
                        )
                    )
                    logger.info(
                        f"[ApprovalMiddleware] 用户已拒绝: {tool_name} (tc_id={tc_id}, operation={operation_desc[:80]})"
                    )
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
