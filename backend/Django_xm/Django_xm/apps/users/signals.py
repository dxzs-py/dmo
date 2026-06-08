"""
用户模块信号处理

监听 chat 模块的 ChatSession / ChatMessage 创建事件，
自动更新用户统计字段（total_sessions / total_messages）。
"""

import logging
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.apps import apps

logger = logging.getLogger(__name__)


@receiver(post_save, sender='chat.ChatSession')
def on_chat_session_created(sender, instance, created, **kwargs):
    """聊天会话创建时更新用户统计"""
    if not created:
        return
    User = apps.get_model('users', 'User')
    try:
        User.objects.filter(pk=instance.user_id).update(
            total_sessions=models.F('total_sessions') + 1,
        )
    except Exception as e:
        logger.error(f"更新用户会话统计失败: {e}")


@receiver(post_save, sender='chat.ChatMessage')
def on_chat_message_created(sender, instance, created, **kwargs):
    """聊天消息创建时更新用户统计"""
    if not created:
        return
    User = apps.get_model('users', 'User')
    try:
        User.objects.filter(pk=instance.created_by_id).update(
            total_messages=models.F('total_messages') + 1,
        )
    except Exception as e:
        logger.error(f"更新用户消息统计失败: {e}")
