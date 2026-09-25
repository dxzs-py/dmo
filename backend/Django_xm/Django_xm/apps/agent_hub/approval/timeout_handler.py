"""审批超时 ToolMessage 构造

向 agent 返回"工具运行失败：审批超时"的 ToolMessage，让 agent 能调整策略继续执行。

超时恢复链路（事件驱动化）：
    Celery 扫描超时（cleanup_expired_approvals）→ timeout_approval 落库终态 TIMEOUT
    + 发布 approval_timeout 实时事件 → Redis 信令唤醒执行服务挂起协程
    → 执行器重新 collect 批次决策后 Command(resume=TIMEOUT_DECISION) 恢复
    → ApprovalMiddleware 注入本模块 build_timeout_tool_message 构造的 ToolMessage
    → agent 调整策略继续执行。

注意：
    原进程内 ApprovalTimeoutHandler / get_timeout_handler / build_timeout_resume_value /
    is_timeout_decision 从未接线（超时统一由 Celery cleanup_expired_approvals 承担），
    已删除（死代码）。
"""

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
