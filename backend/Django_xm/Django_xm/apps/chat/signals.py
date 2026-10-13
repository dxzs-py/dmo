"""
聊天模块信号处理

负责 ChatSession 和 ChatMessage 的所有信号处理，包括:
- 会话缓存失效 (SecureSessionCacheService)
- 日志记录
"""

import logging

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from Django_xm.apps.core.signals import knowledge_base_deleted

from .models import ChatSession

logger = logging.getLogger(__name__)


@receiver(post_save, sender=ChatSession)
def on_session_save(sender, instance, created, **kwargs):
    if created:
        logger.info(f"新聊天会话创建: {instance.session_id} (user={instance.user_id})")
    else:
        logger.debug(f"聊天会话更新: {instance.session_id}")

    try:
        from Django_xm.apps.cache_manager.services.secure_session_cache import SecureSessionCacheService

        # dj-09：仅失效当前会话单条 key，不再全量清空（post_save 含每次更新，
        # 全量清空会导致缓存命中率趋零；列表索引由短 TTL 自然过期回源）
        SecureSessionCacheService.invalidate_session_entry(instance.user_id, str(instance.session_id))
    except Exception as e:
        logger.warning(f"会话安全缓存失效失败: {e}")


@receiver(post_delete, sender=ChatSession)
def on_session_delete(sender, instance, **kwargs):
    logger.info(f"聊天会话删除: {instance.session_id}")

    # 通过自定义信号通知 ai_engine 清理 checkpoint/Store 数据
    from Django_xm.apps.core.signals import ai_data_cleanup_needed

    ai_data_cleanup_needed.send(
        sender=sender,
        user_id=instance.user_id,
        session_id=str(instance.session_id),
    )

    try:
        from Django_xm.apps.cache_manager.services.secure_session_cache import SecureSessionCacheService

        # dj-09：删除场景失效单条 key 并从列表索引 remove，其它会话缓存保留
        SecureSessionCacheService.invalidate_user_session(instance.user_id, str(instance.session_id))
    except Exception:
        logger.exception("会话安全缓存失效失败")


@receiver(knowledge_base_deleted)
def on_knowledge_base_deleted(sender, **kwargs):
    """A-2 依赖反转：knowledge 删除知识库后清理会话中的选中引用。

    此前 knowledge.kb_service 函数体内直接 import chat.cross_app 调用本逻辑
    （knowledge→chat 上行依赖）；现改为 knowledge 发 core.signals 信号、chat
    订阅。延迟 import 保持（测试 patch cross_app 模块属性 + 行为等价）。
    receiver 异常沿 send 向上传播（与原直接调用一致），不吞。
    """
    from Django_xm.apps.chat.services.cross_app import clear_knowledge_base_selection

    clear_knowledge_base_selection(user_id=kwargs.get("user_id"), kb_name=kwargs.get("kb_name"))
