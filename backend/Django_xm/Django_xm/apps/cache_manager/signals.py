"""
缓存管理模块信号处理

监听 core.index_updated 信号，在索引/文档变更时自动失效相关缓存，
解耦 knowledge 模块对 cache_manager 的硬导入。
"""

import logging

from django.dispatch import receiver

from Django_xm.apps.core.signals import index_updated

logger = logging.getLogger(__name__)


@receiver(index_updated)
def on_index_updated(sender, index_name=None, action=None, **kwargs):
    """索引更新时失效相关缓存"""
    try:
        from Django_xm.apps.cache_manager.services.cache_service import CacheInvalidationStrategy
        if action == 'created':
            CacheInvalidationStrategy.on_index_created(index_name)
        elif action == 'updated':
            CacheInvalidationStrategy.on_index_updated(index_name)
        elif action == 'deleted':
            CacheInvalidationStrategy.on_index_deleted(index_name)
        elif action == 'document_added':
            CacheInvalidationStrategy.on_document_added(index_name)
        elif action == 'document_deleted':
            CacheInvalidationStrategy.on_document_deleted(index_name)
    except Exception as e:
        logger.error(f"缓存失效失败: {e}")
