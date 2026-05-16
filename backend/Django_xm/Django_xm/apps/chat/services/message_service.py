"""
消息持久化服务
"""
import logging
from typing import Optional, List

logger = logging.getLogger(__name__)


class MessagePersistenceService:

    def save_message_pair(
        self,
        session,
        user_content: str,
        ai_content: str,
        user_role: str = 'user',
        ai_role: str = 'assistant',
        attachment_ids: Optional[List[int]] = None
    ):
        from Django_xm.apps.chat.models import ChatMessage
        from Django_xm.apps.attachments.services.attachment_content_service import AttachmentService

        user_message = ChatMessage.objects.create(
            session=session,
            role=user_role,
            content=user_content
        )

        if attachment_ids:
            AttachmentService().link_attachments_to_message(user_message, attachment_ids)

        ai_message = ChatMessage.objects.create(
            session=session,
            role=ai_role,
            content=ai_content
        )

        return user_message, ai_message

    async def asave_message_pair(
        self,
        session,
        user_content: str,
        ai_content: str,
        user_role: str = 'user',
        ai_role: str = 'assistant',
        attachment_ids: Optional[List[int]] = None
    ):
        from asgiref.sync import sync_to_async

        return await sync_to_async(self.save_message_pair)(
            session=session,
            user_content=user_content,
            ai_content=ai_content,
            user_role=user_role,
            ai_role=ai_role,
            attachment_ids=attachment_ids,
        )
