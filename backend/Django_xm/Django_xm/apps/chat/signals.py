"""
聊天模块信号处理

负责 ChatSession 和 ChatMessage 的所有信号处理，包括:
- 会话缓存失效 (SecureSessionCacheService + CacheInvalidationStrategy)
- 日志记录
"""

import logging
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from .models import ChatSession, ChatMessage

logger = logging.getLogger(__name__)


@receiver(post_save, sender=ChatSession)
def on_session_save(sender, instance, created, **kwargs):
    if created:
        logger.info(f"新聊天会话创建: {instance.session_id} (user={instance.user_id})")
    else:
        logger.debug(f"聊天会话更新: {instance.session_id}")

    try:
        from Django_xm.apps.chat.services.secure_session_cache import SecureSessionCacheService
        SecureSessionCacheService.invalidate_all_user_sessions(instance.user_id)
    except Exception as e:
        logger.warning(f"会话安全缓存失效失败: {e}")

    try:
        from Django_xm.apps.cache_manager.services.cache_service import CacheInvalidationStrategy
        CacheInvalidationStrategy.on_session_updated(str(instance.session_id))
    except Exception as e:
        logger.warning(f"会话AI缓存失效失败: {e}")


@receiver(post_delete, sender=ChatSession)
def on_session_delete(sender, instance, **kwargs):
    logger.info(f"聊天会话删除: {instance.session_id}")

    try:
        from Django_xm.apps.chat.services.secure_session_cache import SecureSessionCacheService
        SecureSessionCacheService.invalidate_all_user_sessions(instance.user_id)
    except Exception as e:
        logger.warning(f"会话安全缓存失效失败: {e}")

    try:
        from Django_xm.apps.cache_manager.services.cache_service import CacheInvalidationStrategy
        CacheInvalidationStrategy.on_session_deleted(str(instance.session_id))
    except Exception as e:
        logger.warning(f"会话AI缓存失效失败: {e}")
