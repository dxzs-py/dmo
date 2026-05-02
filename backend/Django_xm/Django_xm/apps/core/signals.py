"""
Django 信号处理模块

处理模型事件与跨组件通信：
- 用户创建/删除信号
- 会话创建/更新/删除信号 → 缓存失效
- 文档索引变化信号 → 缓存失效
- 研究任务状态变化信号 → 缓存失效 + 任务通知
- 工作流状态变化信号
- Celery 任务状态变化信号
"""
import logging
from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver, Signal
from django.utils import timezone

logger = logging.getLogger(__name__)


# ==================== 自定义信号 ====================

cache_invalidated = Signal()
task_status_changed = Signal()
index_updated = Signal()


# ==================== 用户信号 ====================

@receiver(post_save, sender='users.User')
def user_post_save(sender, instance, created, **kwargs):
    if created:
        logger.info(f"新用户创建: {instance.username} (id={instance.id})")
    else:
        logger.debug(f"用户更新: {instance.username} (id={instance.id})")


@receiver(post_delete, sender='users.User')
def user_post_delete(sender, instance, **kwargs):
    logger.info(f"用户删除: {instance.username} (id={instance.id})")


# ==================== 聊天会话信号 ====================

@receiver(post_save, sender='chat.ChatSession')
def chat_session_post_save(sender, instance, created, **kwargs):
    if created:
        logger.info(f"新聊天会话创建: {instance.session_id} (user={instance.user_id})")
    else:
        instance.updated_at = timezone.now()
        logger.debug(f"聊天会话更新: {instance.session_id}")

    try:
        from Django_xm.apps.ai_engine.services.cache_service import (
            CacheInvalidationStrategy,
        )
        CacheInvalidationStrategy.on_session_updated(str(instance.session_id))
    except Exception as e:
        logger.warning(f"会话缓存失效失败: {e}")


@receiver(post_delete, sender='chat.ChatSession')
def chat_session_post_delete(sender, instance, **kwargs):
    logger.info(f"聊天会话删除: {instance.session_id}")

    try:
        from Django_xm.apps.ai_engine.services.cache_service import (
            CacheInvalidationStrategy,
        )
        CacheInvalidationStrategy.on_session_deleted(str(instance.session_id))
    except Exception as e:
        logger.warning(f"会话缓存失效失败: {e}")


# ==================== 文档索引信号 ====================

@receiver(post_save, sender='knowledge.DocumentIndex')
def document_index_post_save(sender, instance, created, **kwargs):
    if created:
        logger.info(f"新文档索引创建: {instance.index_name} (user={instance.user_id})")
        try:
            from Django_xm.apps.ai_engine.services.cache_service import (
                CacheInvalidationStrategy,
            )
            CacheInvalidationStrategy.on_index_created(instance.index_name)
        except Exception as e:
            logger.warning(f"索引缓存失效失败: {e}")
    else:
        logger.debug(f"文档索引更新: {instance.index_name}")
        try:
            from Django_xm.apps.ai_engine.services.cache_service import (
                CacheInvalidationStrategy,
            )
            CacheInvalidationStrategy.on_index_updated(instance.index_name)
        except Exception as e:
            logger.warning(f"索引缓存失效失败: {e}")

    index_updated.send(
        sender=sender,
        index_name=instance.index_name,
        action='created' if created else 'updated',
    )


@receiver(post_delete, sender='knowledge.DocumentIndex')
def document_index_post_delete(sender, instance, **kwargs):
    logger.info(f"文档索引删除: {instance.index_name}")

    try:
        from Django_xm.apps.ai_engine.services.cache_service import (
            CacheInvalidationStrategy,
        )
        CacheInvalidationStrategy.on_index_deleted(instance.index_name)
    except Exception as e:
        logger.warning(f"索引缓存失效失败: {e}")

    index_updated.send(
        sender=sender,
        index_name=instance.index_name,
        action='deleted',
    )


@receiver(post_save, sender='knowledge.Document')
def document_post_save(sender, instance, created, **kwargs):
    if created:
        logger.info(f"新文档创建: {instance.filename} (index={instance.index_id})")

        try:
            from Django_xm.apps.ai_engine.services.cache_service import (
                CacheInvalidationStrategy,
            )
            CacheInvalidationStrategy.on_document_added(instance.index.index_name)
        except Exception as e:
            logger.warning(f"文档缓存失效失败: {e}")

        try:
            index = instance.index
            index.document_count = index.documents.filter(is_deleted=False).count()
            index.save()
            logger.debug(f"索引文档数更新: {index.index_name} -> {index.document_count}")
        except Exception as e:
            logger.warning(f"更新索引文档数失败: {e}")


@receiver(post_delete, sender='knowledge.Document')
def document_post_delete(sender, instance, **kwargs):
    logger.info(f"文档删除: {instance.filename}")

    try:
        from Django_xm.apps.ai_engine.services.cache_service import (
            CacheInvalidationStrategy,
        )
        CacheInvalidationStrategy.on_document_deleted(instance.index.index_name)
    except Exception as e:
        logger.warning(f"文档缓存失效失败: {e}")

    try:
        index = instance.index
        index.document_count = max(0, index.documents.filter(is_deleted=False).count())
        index.save()
        logger.debug(f"索引文档数更新: {index.index_name} -> {index.document_count}")
    except Exception as e:
        logger.warning(f"更新索引文档数失败: {e}")


# ==================== 研究任务信号 ====================

@receiver(post_save, sender='research.ResearchTask')
def research_task_post_save(sender, instance, created, **kwargs):
    if created:
        logger.info(f"新研究任务创建: {instance.task_id} (query={instance.query[:50]}...)")
    else:
        if instance.status == 'completed':
            logger.info(f"研究任务完成: {instance.task_id}")
            try:
                from Django_xm.apps.ai_engine.services.cache_service import (
                    CacheInvalidationStrategy,
                )
                CacheInvalidationStrategy.on_research_completed(instance.task_id)
            except Exception as e:
                logger.warning(f"研究缓存失效失败: {e}")
        elif instance.status == 'failed':
            logger.warning(f"研究任务失败: {instance.task_id}, 错误: {instance.error_message}")

    task_status_changed.send(
        sender=sender,
        task_id=instance.task_id,
        status=instance.status,
        created=created,
    )


@receiver(post_delete, sender='research.ResearchTask')
def research_task_post_delete(sender, instance, **kwargs):
    logger.info(f"研究任务删除: {instance.task_id}")


# ==================== 工作流信号 ====================

@receiver(post_save, sender='learning.WorkflowSession')
def workflow_session_post_save(sender, instance, created, **kwargs):
    if created:
        logger.info(f"新工作流会话创建: {instance.thread_id} (question={instance.user_question[:50]}...)")
    else:
        if instance.status == 'completed':
            logger.info(f"工作流会话完成: {instance.thread_id}")
        elif instance.status == 'failed':
            logger.warning(f"工作流会话失败: {instance.thread_id}, 错误: {instance.error_message}")


@receiver(post_delete, sender='learning.WorkflowSession')
def workflow_session_post_delete(sender, instance, **kwargs):
    logger.info(f"工作流会话删除: {instance.thread_id}")


@receiver(post_save, sender='learning.WorkflowExecution')
def workflow_execution_post_save(sender, instance, created, **kwargs):
    if created:
        logger.info(f"新工作流执行记录创建: {instance.thread_id} (type={instance.workflow_type})")
    else:
        logger.debug(f"工作流执行记录更新: {instance.thread_id}")


# ==================== Celery 任务记录信号 ====================

try:
    from Django_xm.apps.core.task_models import CeleryTaskRecord

    @receiver(post_save, sender=CeleryTaskRecord)
    def celery_task_record_post_save(sender, instance, created, **kwargs):
        if created:
            logger.info(f"Celery 任务记录创建: {instance.celery_task_id} ({instance.task_name})")
        else:
            if instance.status in ('success', 'failure', 'revoked'):
                logger.info(
                    f"Celery 任务完成: {instance.celery_task_id} "
                    f"status={instance.status} runtime={instance.runtime_seconds}s"
                )

        task_status_changed.send(
            sender=sender,
            task_id=instance.celery_task_id,
            status=instance.status,
            created=created,
        )
except Exception as e:
    logger.debug(f"CeleryTaskRecord 信号注册跳过: {e}")
