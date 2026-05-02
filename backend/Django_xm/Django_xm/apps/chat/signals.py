from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.core.cache import cache

from .models import ChatSession, ChatMessage
from .services.secure_session_cache import SecureSessionCacheService


def _invalidate_session_cache(instance):
    if instance.user_id:
        sessions_list_key = SecureSessionCacheService._get_user_sessions_key(instance.user_id)
        cache.delete(sessions_list_key)

    if instance.session_id:
        cache_key = SecureSessionCacheService._get_cache_key(instance.user_id, instance.session_id)
        cache.delete(cache_key)


def _invalidate_session_detail_cache(instance):
    if instance.session and instance.session.session_id:
        cache_key = SecureSessionCacheService._get_cache_key(
            instance.session.user_id, instance.session.session_id
        )
        cache.delete(cache_key)
        messages_key = f'{cache_key}:messages'
        cache.delete(messages_key)


@receiver(post_save, sender=ChatSession)
def on_session_save(sender, instance, created, **kwargs):
    _invalidate_session_cache(instance)


@receiver(post_delete, sender=ChatSession)
def on_session_delete(sender, instance, **kwargs):
    _invalidate_session_cache(instance)


@receiver(post_save, sender=ChatMessage)
def on_message_save(sender, instance, created, **kwargs):
    _invalidate_session_detail_cache(instance)


@receiver(post_delete, sender=ChatMessage)
def on_message_delete(sender, instance, **kwargs):
    _invalidate_session_detail_cache(instance)
