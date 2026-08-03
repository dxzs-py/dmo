"""审批通用工具函数。

提供跨模块复用的纯函数，避免审批/事件发布路径中的重复逻辑。
"""


def derive_cross_module_id(approval) -> str | None:
    """从 Approval 实例计算跨模块关联 ID。

    仅 DEEP_RESEARCH 关联 chat_session_id 时返回该值，
    用于工具事件/审批事件发布到 session:{chat_session_id} 双频道。

    Args:
        approval: Approval 模型实例（需有 source、chat_session_id 属性）

    Returns:
        chat_session_id 或 None
    """
    if approval.source == "deep_research" and approval.chat_session_id:
        return approval.chat_session_id
    return None


def derive_cross_module_id_from_source(source: str, chat_session_id: str | None) -> str | None:
    """从 source 字符串和 chat_session_id 计算跨模块关联 ID。

    供不持有 Approval 对象的场景使用（如 adapter.py 的工具事件发布、
    middleware.py 的安全审计）。

    Args:
        source: 模块来源标识（如 "deep_research", "chat"）
        chat_session_id: 关联的聊天会话 ID

    Returns:
        chat_session_id 或 None
    """
    if source == "deep_research" and chat_session_id:
        return chat_session_id
    return None
