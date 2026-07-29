"""审批超时处理器

当审批超时（默认 5 分钟）时，向 agent 返回"工具运行失败：审批超时"的 ToolMessage，
让 agent 能调整策略继续执行，而非结束对话。

工作原理：
    1. 审批请求发起时通过 register_approval 注册（记录时间戳）
    2. 超时检测由 Celery 任务 cleanup_expired_approvals 定期执行（每 60 秒）
    3. 检测到超时后，timeout_approval 设置 resume_value = TIMEOUT_DECISION
    4. LangGraph 通过 Command(resume={interrupt_id: TIMEOUT_DECISION}) 恢复执行
    5. ApprovalMiddleware.aafter_model 收到 interrupt() 返回的 TIMEOUT_DECISION，
       识别为超时，注入"审批超时"ToolMessage（而非"用户已拒绝"）
    6. agent 收到 ToolMessage 后可调整策略继续执行

与现有审批流程的集成点：
    - timeout_approval（approval_service.py）：超时时设置 resume_value = TIMEOUT_DECISION
    - ApprovalMiddleware.aafter_model（middleware.py）：识别 TIMEOUT_DECISION，注入超时 ToolMessage
    - cleanup_expired_approvals（approval_tasks.py）：Celery 定时扫描，无需修改

注意：
    超时恢复链路已完成：
    Celery 扫描超时（cleanup_expired_approvals）→ timeout_approval 设置 TIMEOUT_DECISION →
    resume_chat_after_timeout Celery 任务派发 → _stream_chat_resume_generator 恢复 LangGraph →
    middleware 注入超时 ToolMessage → agent 调整策略继续执行。

    关键日志标记（用于排查）：
    - [ApprovalService] chat 超时恢复任务已派发
    - [ResumeChatTimeout] 任务被调用
    - [ResumeChatTimeout] LangGraph 恢复执行完成，agent 已继续
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# 默认审批超时时间：5 分钟
# 与 approval_service.APPROVAL_TIMEOUT_SECONDS=300 / approval_tasks.APPROVAL_TIMEOUT_SECONDS=300 对齐
DEFAULT_APPROVAL_TIMEOUT = timedelta(minutes=5)

# 超时决策标记：定义已迁移至 common.constants，此处保留重导出
from Django_xm.common.constants import TIMEOUT_DECISION


def build_timeout_tool_message(tool_call_id: str, tool_name: str = ""):
    """构建审批超时的 ToolMessage

    超时后注入此 ToolMessage 让 agent 知道工具因审批超时未执行，
    agent 可据此调整策略（换方法、改参数或放弃）。

    Args:
        tool_call_id: 原始工具调用的 ID（必须与 AIMessage.tool_calls 中的 id 匹配）
        tool_name: 工具名称（可选，附加到 ToolMessage.name 便于日志追踪）

    Returns:
        ToolMessage: status="error"，内容为"工具运行失败：审批超时"

    Note:
        content 中的"审批超时"关键字被 stream_helpers._detect_tool_timeout 识别，
        用于发布 TOOL_CALL_TIMEOUT 事件（而非 TOOL_CALL_FAILED 或 TOOL_CALL_COMPLETED），
        确保前端工具卡片显示"已超时"状态。请勿修改此关键字。
    """
    from langchain_core.messages import ToolMessage

    return ToolMessage(
        content=(
            "工具运行失败：审批超时（超过5分钟未审批），请尝试其他方法或调整参数重试。"
            "该工具未被执行，请勿重复调用相同参数。"
        ),
        tool_call_id=tool_call_id,
        name=tool_name,
        status="error",
    )


def build_timeout_resume_value(approval_requests: list[dict]):
    """构建超时恢复值（用于 Command(resume=...)）

    批量审批场景下，所有审批请求统一标记为超时；
    单个审批场景直接返回 TIMEOUT_DECISION。

    Args:
        approval_requests: 审批请求列表（每个包含 tool_call_id）

    Returns:
        TIMEOUT_DECISION（单个）或 {tool_call_id: TIMEOUT_DECISION, ...}（批量）
    """
    if not approval_requests or len(approval_requests) == 1:
        return TIMEOUT_DECISION
    return {req["tool_call_id"]: TIMEOUT_DECISION for req in approval_requests}


def is_timeout_decision(decision: Any) -> bool:
    """判断决策值是否为超时标记

    Args:
        decision: _normalize_decisions 返回的决策值（True/False/TIMEOUT_DECISION）

    Returns:
        bool
    """
    return decision == TIMEOUT_DECISION


class ApprovalTimeoutHandler:
    """审批超时处理器

    监控审批请求是否超时，超时后向 agent 返回失败 ToolMessage。

    工作流程：
        1. 审批请求发起时 register_approval 记录时间戳
        2. 异步任务定期调用 check_timeout 检查超时
        3. 超时后 handle_timeout 调用 approval_service.timeout_approval
           持久化超时状态（设置 resume_value=TIMEOUT_DECISION，广播 timeout 事件）
        4. ApprovalMiddleware 收到 TIMEOUT_DECISION 后注入"审批超时"ToolMessage
        5. agent 收到后可调整策略继续执行

    注意：
        - 持久化的超时扫描由 Celery 任务 cleanup_expired_approvals 负责（每 60 秒）
        - 本类提供进程内超时监控能力，可作为 Celery 任务的补充（如长连接场景）
        - 实际的 ToolMessage 注入由 ApprovalMiddleware.aafter_model 完成
    """

    def __init__(self, timeout: timedelta = DEFAULT_APPROVAL_TIMEOUT):
        self.timeout = timeout
        # {interrupt_id: {"timestamp": datetime, "tool_call_id": str, "graph_state": ...}}
        self._pending_approvals: dict[str, dict[str, Any]] = {}

    async def register_approval(
        self,
        interrupt_id: str,
        tool_call_id: str,
        graph_state: dict[str, Any] | None = None,
    ) -> None:
        """注册新的审批请求

        Args:
            interrupt_id: 审批唯一标识
            tool_call_id: 关联的 LLM tool_call_id（用于构建 ToolMessage）
            graph_state: 可选的 graph 状态快照（调试用）
        """
        self._pending_approvals[interrupt_id] = {
            "timestamp": datetime.now(UTC),
            "tool_call_id": tool_call_id,
            "graph_state": graph_state,
        }
        logger.info(
            f"[ApprovalTimeoutHandler] 注册审批: interrupt_id={interrupt_id}, "
            f"tool_call_id={tool_call_id}, timeout={self.timeout}"
        )

    async def check_timeout(self) -> None:
        """检查是否有超时的审批请求，超时则调用 handle_timeout

        可由后台异步任务定期调用。注意：持久化的超时处理由 Celery 任务
        cleanup_expired_approvals 负责，本方法仅处理进程内注册的审批。
        """
        if not self._pending_approvals:
            return

        now = datetime.now(UTC)
        timed_out_ids = [
            interrupt_id
            for interrupt_id, info in self._pending_approvals.items()
            if now - info["timestamp"] > self.timeout
        ]

        for interrupt_id in timed_out_ids:
            try:
                await self.handle_timeout(interrupt_id)
            except Exception:
                logger.exception(
                    f"[ApprovalTimeoutHandler] 处理超时失败: interrupt_id={interrupt_id}",
                )

    async def handle_timeout(self, interrupt_id: str) -> None:
        """处理超时：触发持久化超时流程并注入超时 ToolMessage

        超时处理流程：
            1. 从 _pending_approvals 获取 tool_call_id
            2. 调用 approval_service.timeout_approval 持久化超时状态
               （该函数会设置 resume_value=TIMEOUT_DECISION、广播 timeout 事件、
                触发批量恢复逻辑）
            3. 注销本地的审批记录

        实际的 ToolMessage 注入由 ApprovalMiddleware.aafter_model 完成：
        当 LangGraph 通过 Command(resume=TIMEOUT_DECISION) 恢复时，
        middleware 识别 TIMEOUT_DECISION 并注入 build_timeout_tool_message 构造的消息。

        Args:
            interrupt_id: 审批唯一标识
        """
        info = self._pending_approvals.get(interrupt_id)
        if info is None:
            logger.warning(f"[ApprovalTimeoutHandler] 超时处理跳过: 未注册的 interrupt_id={interrupt_id}")
            return

        tool_call_id = info["tool_call_id"]

        # 延迟导入避免循环依赖
        from Django_xm.apps.approvals.services import approval_service

        try:
            # 调用持久化的超时处理逻辑：
            # timeout_approval 内部会：
            #   1. 设置 approval.state = processing
            #   2. 设置 extra._resume_value = TIMEOUT_DECISION
            #   3. 设置 extra._timeout = True
            #   4. 广播 timeout 事件（前端显示"已超时"）
            #   5. 触发批量恢复逻辑（深度研究场景递减 pending 计数）
            approval_service.timeout_approval(interrupt_id)
            logger.info(
                f"[ApprovalTimeoutHandler] 超时处理完成: interrupt_id={interrupt_id}, tool_call_id={tool_call_id}"
            )
        except Exception:
            logger.exception(
                f"[ApprovalTimeoutHandler] 调用 timeout_approval 失败: interrupt_id={interrupt_id}",
            )
        finally:
            await self.unregister_approval(interrupt_id)

    async def unregister_approval(self, interrupt_id: str) -> None:
        """审批完成（通过/拒绝/超时）后注销

        Args:
            interrupt_id: 审批唯一标识
        """
        info = self._pending_approvals.pop(interrupt_id, None)
        if info:
            logger.debug(
                f"[ApprovalTimeoutHandler] 注销审批: interrupt_id={interrupt_id}, "
                f"tool_call_id={info.get('tool_call_id')}"
            )

    def is_timeout_decision(self, decision: Any) -> bool:
        """判断决策值是否为超时（实例方法，便于 middleware 调用）

        Args:
            decision: _normalize_decisions 返回的决策值

        Returns:
            bool
        """
        return decision == TIMEOUT_DECISION


# 全局单例（进程内超时监控，可选使用）
_global_handler: ApprovalTimeoutHandler | None = None


def get_timeout_handler() -> ApprovalTimeoutHandler:
    """获取全局 ApprovalTimeoutHandler 单例

    用于在 middleware 或其他模块中访问进程内超时处理器。
    注意：持久化的超时扫描仍由 Celery 任务 cleanup_expired_approvals 负责。
    """
    global _global_handler
    if _global_handler is None:
        _global_handler = ApprovalTimeoutHandler()
    return _global_handler
