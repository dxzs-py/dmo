"""
聊天消息构建服务

从 chat_service.py 拆分出的消息构建和转换逻辑：
- 用户内容构建（同步/异步，含附件处理）
- HumanMessage 创建（同步/异步）
- 聊天历史格式转换
- Token 统计更新
"""
import logging
from typing import Any

from asgiref.sync import sync_to_async
from langchain_core.messages import HumanMessage

from ..utils import convert_chat_history

logger = logging.getLogger(__name__)


class ChatMessageBuilder:

    def __init__(self, user_id: int | None = None):
        self.user_id = user_id
        from Django_xm.apps.attachments.services.cross_app import get_attachment_service
        self._attachment_service = get_attachment_service()

    async def abuild_user_content(self, data: dict[str, Any]) -> dict[str, Any]:
        user_message = data['message']
        attachment_ids = data.get('attachment_ids')
        if not attachment_ids:
            return {"type": "text", "content": user_message}
        return await sync_to_async(self._attachment_service.build_user_content)(user_message, attachment_ids)

    def build_user_content(self, data: dict[str, Any]) -> dict[str, Any]:
        user_message = data['message']
        attachment_ids = data.get('attachment_ids')
        if not attachment_ids:
            return {"type": "text", "content": user_message}
        return self._attachment_service.build_user_content(user_message, attachment_ids)

    async def acreate_human_message(self, data: dict[str, Any]) -> HumanMessage:
        preloaded_type = data.get('_preloaded_attachment_type')
        if preloaded_type == 'multimodal':
            content = data.get('_preloaded_attachment_content', data['message'])
            return HumanMessage(content=content)
        if preloaded_type == 'text':
            return HumanMessage(content=data['message'])

        user_content = await self.abuild_user_content(data)
        if user_content["type"] == "multimodal":
            return HumanMessage(content=user_content["content"])
        return HumanMessage(content=user_content["content"])

    def create_human_message(self, data: dict[str, Any]) -> HumanMessage:
        preloaded_type = data.get('_preloaded_attachment_type')
        if preloaded_type == 'multimodal':
            content = data.get('_preloaded_attachment_content', data['message'])
            return HumanMessage(content=content)
        if preloaded_type == 'text':
            return HumanMessage(content=data['message'])

        user_content = self.build_user_content(data)
        if user_content["type"] == "multimodal":
            return HumanMessage(content=user_content["content"])
        return HumanMessage(content=user_content["content"])

    @staticmethod
    def convert_chat_history(chat_history: list[dict[str, Any]]) -> list:
        return convert_chat_history(chat_history)

    @staticmethod
    async def update_last_message_tokens(
        user_id,
        session_id: str,
        token_count: int,
        token_detail: dict,
        model: str,
        response_time: float,
    ):
        try:
            from Django_xm.apps.chat.models import ChatMessage

            @sync_to_async
            def _do_update():
                msg = ChatMessage.objects.filter(
                    session__session_id=session_id,
                    session__user_id=user_id,
                    role='assistant',
                ).select_related('session').order_by('-created_at').first()
                if msg:
                    msg.token_count = token_count
                    msg.token_detail = token_detail
                    msg.model = model
                    msg.response_time = response_time
                    msg.save(update_fields=['token_count', 'token_detail', 'model', 'response_time'])

            await _do_update()
        except Exception as e:
            logger.warning(f"更新消息 Token 数据失败: {e}")
