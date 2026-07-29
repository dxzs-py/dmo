"""
消息持久化服务
"""

import logging

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction

logger = logging.getLogger(__name__)


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


class MessagePersistenceService:
    @transaction.atomic
    def save_message_pair(
        self,
        session,
        user_content: str,
        ai_content: str,
        user_role: str = "user",
        ai_role: str = "assistant",
        attachment_ids: list[int] | None = None,
        token_count: int = 0,
        token_detail: dict | None = None,
        model: str | None = None,
        response_time: float = 0,
    ):
        from Django_xm.apps.attachments.services.cross_app import get_attachment_service
        from Django_xm.apps.chat.models import ChatMessage

        user_message = ChatMessage.objects.create(session=session, role=user_role, content=user_content)

        if attachment_ids:
            get_attachment_service().link_attachments_to_message(user_message, attachment_ids)

        ai_message = ChatMessage.objects.create(
            session=session,
            role=ai_role,
            content=ai_content,
            token_count=token_count,
            token_detail=token_detail or {},
            model=model,
            response_time=response_time,
        )

        return user_message, ai_message

    async def asave_message_pair(
        self,
        session,
        user_content: str,
        ai_content: str,
        user_role: str = "user",
        ai_role: str = "assistant",
        attachment_ids: list[int] | None = None,
        token_count: int = 0,
        token_detail: dict | None = None,
        model: str | None = None,
        response_time: float = 0,
    ):
        from asgiref.sync import sync_to_async

        return await sync_to_async(self.save_message_pair)(
            session=session,
            user_content=user_content,
            ai_content=ai_content,
            user_role=user_role,
            ai_role=ai_role,
            attachment_ids=attachment_ids,
            token_count=token_count,
            token_detail=token_detail,
            model=model,
            response_time=response_time,
        )
