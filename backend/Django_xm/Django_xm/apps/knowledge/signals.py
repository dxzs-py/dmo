"""
知识库模块信号处理

负责 Document 和 DocumentIndex 的所有信号处理，包括:
- 索引缓存失效
- 文档计数更新
- 索引更新通知
"""

import logging
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from .models import Document, DocumentIndex

logger = logging.getLogger(__name__)


@receiver(post_save, sender=DocumentIndex)
def on_index_save(sender, instance, created, **kwargs):
    if created:
        logger.info(f"新文档索引创建: {instance.index_name} (user={instance.user_id})")
    else:
        logger.debug(f"文档索引更新: {instance.index_name}")

    try:
        from Django_xm.apps.cache_manager.services.cache_service import CacheInvalidationStrategy
        if created:
            CacheInvalidationStrategy.on_index_created(instance.index_name)
        else:
            CacheInvalidationStrategy.on_index_updated(instance.index_name)
    except Exception as e:
        logger.warning(f"索引缓存失效失败: {e}")

    from Django_xm.apps.core.signals import index_updated
    index_updated.send(
        sender=sender,
        index_name=instance.index_name,
        action='created' if created else 'updated',
    )


@receiver(post_delete, sender=DocumentIndex)
def on_index_delete(sender, instance, **kwargs):
    logger.info(f"文档索引删除: {instance.index_name}")

    try:
        from Django_xm.apps.cache_manager.services.cache_service import CacheInvalidationStrategy
        CacheInvalidationStrategy.on_index_deleted(instance.index_name)
    except Exception as e:
        logger.warning(f"索引缓存失效失败: {e}")

    from Django_xm.apps.core.signals import index_updated
    index_updated.send(
        sender=sender,
        index_name=instance.index_name,
        action='deleted',
    )


@receiver(post_save, sender=Document)
def on_document_save(sender, instance, created, **kwargs):
    if created:
        logger.info(f"新文档创建: {instance.filename} (index={instance.index_id})")

        try:
            from Django_xm.apps.cache_manager.services.cache_service import CacheInvalidationStrategy
            CacheInvalidationStrategy.on_document_added(instance.index.index_name)
        except Exception as e:
            logger.warning(f"文档缓存失效失败: {e}")

    _update_index_document_count(instance.index_id)


@receiver(post_delete, sender=Document)
def on_document_delete(sender, instance, **kwargs):
    logger.info(f"文档删除: {instance.filename}")

    try:
        from Django_xm.apps.cache_manager.services.cache_service import CacheInvalidationStrategy
        CacheInvalidationStrategy.on_document_deleted(instance.index.index_name)
    except Exception as e:
        logger.warning(f"文档缓存失效失败: {e}")

    _update_index_document_count(instance.index_id)


def _update_index_document_count(index_id):
    try:
        new_count = Document.objects.filter(index_id=index_id, is_deleted=False).count()
        DocumentIndex.objects.filter(pk=index_id).update(document_count=new_count)
    except Exception as e:
        logger.warning(f"更新索引文档数失败: {e}")
