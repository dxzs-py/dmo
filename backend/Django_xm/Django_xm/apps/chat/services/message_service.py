"""
会话查询服务

按属主与 session_id 获取会话；prefetch_attachments 用于会话详情序列化前
预取消息与附件，避免 N+1。
"""

from django.core.exceptions import ObjectDoesNotExist


def get_user_session(user, session_id, prefetch_attachments=False):
    """获取用户会话，不存在则返回 None

    Args:
        user: 用户对象
        session_id: 会话 session_id（UUID 字符串）
        prefetch_attachments: 是否预取消息及附件

    Returns:
        ChatSession 对象或 None
    """
    from Django_xm.apps.chat.models import ChatMessage, ChatSession

    try:
        queryset = ChatSession.objects
        if prefetch_attachments:
            from django.db.models import Prefetch

            queryset = queryset.prefetch_related(
                Prefetch("messages", queryset=ChatMessage.objects.prefetch_related("attachments"))
            )
        else:
            queryset = queryset.prefetch_related("messages")

        return queryset.get(session_id=session_id, user=user, is_deleted=False)
    except ObjectDoesNotExist:
        return None
