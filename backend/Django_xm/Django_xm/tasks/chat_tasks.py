"""
附件生命周期管理 Celery 任务
定时清理过期附件、入库旧文件、监控存储空间
"""
import logging
from celery import shared_task
from celery.utils.log import get_task_logger

from Django_xm.tasks.base import TrackedTask

logger = get_task_logger(__name__)


@shared_task(
    bind=True,
    name='chat.cleanup_expired_attachments',
    max_retries=1,
    soft_time_limit=600,
)
def cleanup_expired_attachments(self):
    from Django_xm.apps.attachments.services.attachment_lifecycle import AttachmentLifecycleService

    tracker = TrackedTask(self)
    tracker.set_task_type('chat_cleanup')

    try:
        tracker.mark_started()
        service = AttachmentLifecycleService()
        log = service.cleanup_expired(triggered_by='celery_beat')

        result = {
            'files_processed': log.files_processed,
            'files_deleted': log.files_deleted,
            'files_indexed': log.files_archived,
            'files_skipped': log.files_skipped,
            'space_freed_mb': round(log.space_freed / 1024 / 1024, 2),
            'space_indexed_mb': round(log.space_archived / 1024 / 1024, 2),
            'errors': log.errors,
        }

        logger.info(
            f"[Celery Chat] 附件清理完成: 处理={log.files_processed}, 删除={log.files_deleted}, "
            f"入库={log.files_archived}, 释放={result['space_freed_mb']}MB"
        )
        tracker.mark_success(result=result)
        return {'status': 'success', **result}
    except Exception as e:
        logger.error(f"[Celery Chat] 附件清理任务失败: {e}", exc_info=True)
        tracker.mark_failure(error_message=str(e))
        return {'status': 'error', 'error': str(e)}


@shared_task(
    bind=True,
    name='chat.index_old_attachments',
    max_retries=1,
    soft_time_limit=600,
)
def index_old_attachments(self):
    from Django_xm.apps.attachments.services.attachment_lifecycle import AttachmentLifecycleService

    tracker = TrackedTask(self)
    tracker.set_task_type('chat_index')

    try:
        tracker.mark_started()
        service = AttachmentLifecycleService()
        log = service.index_old_attachments(triggered_by='celery_beat')

        result = {
            'files_processed': log.files_processed,
            'files_indexed': log.files_archived,
            'space_indexed_mb': round(log.space_archived / 1024 / 1024, 2),
            'errors': log.errors,
        }

        logger.info(
            f"[Celery Chat] 附件入库完成: 处理={log.files_processed}, 入库={log.files_archived}, "
            f"空间={result['space_indexed_mb']}MB"
        )
        tracker.mark_success(result=result)
        return {'status': 'success', **result}
    except Exception as e:
        logger.error(f"[Celery Chat] 附件入库任务失败: {e}", exc_info=True)
        tracker.mark_failure(error_message=str(e))
        return {'status': 'error', 'error': str(e)}


@shared_task(
    bind=True,
    name='chat.check_storage_alerts',
    max_retries=1,
    soft_time_limit=120,
)
def check_storage_alerts(self):
    from Django_xm.apps.attachments.services.attachment_lifecycle import AttachmentLifecycleService

    tracker = TrackedTask(self)
    tracker.set_task_type('chat_storage')

    try:
        tracker.mark_started()
        service = AttachmentLifecycleService()
        stats = service.get_storage_stats()
        alert = service.check_storage_alerts()

        result = {
            'disk_usage_percent': stats['disk_usage_percent'],
            'disk_free_gb': round(stats['disk_free_bytes'] / 1024 / 1024 / 1024, 2),
            'total_files': stats['total_files'],
            'total_size_mb': stats['total_size_mb'],
            'alert_triggered': alert is not None,
            'alert_level': alert.level if alert else None,
            'alert_message': alert.message if alert else None,
        }

        if alert:
            logger.warning(f"[Celery Chat] 存储告警: {alert.message}")
        else:
            logger.info(f"[Celery Chat] 存储正常: 使用率={stats['disk_usage_percent']}%")

        tracker.mark_success(result=result)
        return {'status': 'success', **result}
    except Exception as e:
        logger.error(f"[Celery Chat] 存储检查任务失败: {e}", exc_info=True)
        tracker.mark_failure(error_message=str(e))
        return {'status': 'error', 'error': str(e)}


@shared_task(
    bind=True,
    name='chat.attachment_full_lifecycle',
    max_retries=1,
    soft_time_limit=1800,
)
def attachment_full_lifecycle(self):
    tracker = TrackedTask(self)
    tracker.set_task_type('chat_cleanup')

    try:
        tracker.mark_started()

        check_storage_alerts.delay()
        index_old_attachments.delay()
        cleanup_expired_attachments.delay()

        logger.info("[Celery Chat] 完整生命周期管理流程已提交")
        tracker.mark_success(result={'status': 'dispatched'})
        return {'status': 'success', 'message': '生命周期任务已全部提交'}
    except Exception as e:
        logger.error(f"[Celery Chat] 完整生命周期任务失败: {e}", exc_info=True)
        tracker.mark_failure(error_message=str(e))
        return {'status': 'error', 'error': str(e)}
