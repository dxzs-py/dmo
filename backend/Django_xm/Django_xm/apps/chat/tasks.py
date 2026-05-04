"""
附件生命周期管理 Celery 任务
定时清理过期附件、归档旧文件、监控存储空间
"""
import logging
from celery import shared_task
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


@shared_task(bind=True, name='chat.cleanup_expired_attachments')
def cleanup_expired_attachments(self):
    """
    清理过期附件

    定时执行，扫描并删除超过保留期限的附件文件
    默认每天凌晨3点执行
    """
    from Django_xm.apps.chat.services.attachment_lifecycle import AttachmentLifecycleService

    try:
        service = AttachmentLifecycleService()
        log = service.cleanup_expired(trigger_by='celery_beat')

        result = {
            'files_processed': log.files_processed,
            'files_deleted': log.files_deleted,
            'files_archived': log.files_archived,
            'files_skipped': log.files_skipped,
            'space_freed_mb': round(log.space_freed / 1024 / 1024, 2),
            'space_archived_mb': round(log.space_archived / 1024 / 1024, 2),
            'errors': log.errors,
        }

        logger.info(
            f"附件清理完成: 处理={log.files_processed}, 删除={log.files_deleted}, "
            f"归档={log.files_archived}, 释放={result['space_freed_mb']}MB"
        )
        return result
    except Exception as e:
        logger.error(f"附件清理任务失败: {e}", exc_info=True)
        return {'error': str(e)}


@shared_task(bind=True, name='chat.archive_old_attachments')
def archive_old_attachments(self):
    """
    归档旧附件

    将超过归档天数的活跃附件转移至归档目录
    默认每天凌晨2点执行
    """
    from Django_xm.apps.chat.services.attachment_lifecycle import AttachmentLifecycleService

    try:
        service = AttachmentLifecycleService()
        log = service.archive_old_attachments(triggered_by='celery_beat')

        result = {
            'files_processed': log.files_processed,
            'files_archived': log.files_archived,
            'space_archived_mb': round(log.space_archived / 1024 / 1024, 2),
            'errors': log.errors,
        }

        logger.info(
            f"附件归档完成: 处理={log.files_processed}, 归档={log.files_archived}, "
            f"空间={result['space_archived_mb']}MB"
        )
        return result
    except Exception as e:
        logger.error(f"附件归档任务失败: {e}", exc_info=True)
        return {'error': str(e)}


@shared_task(bind=True, name='chat.check_storage_alerts')
def check_storage_alerts(self):
    """
    检查存储空间并触发告警

    监控磁盘使用率，超过阈值时创建告警记录
    默认每小时执行一次
    """
    from Django_xm.apps.chat.services.attachment_lifecycle import AttachmentLifecycleService

    try:
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
            logger.warning(f"存储告警: {alert.message}")
        else:
            logger.info(f"存储正常: 使用率={stats['disk_usage_percent']}%")

        return result
    except Exception as e:
        logger.error(f"存储检查任务失败: {e}", exc_info=True)
        return {'error': str(e)}


@shared_task(bind=True, name='chat.attachment_full_lifecycle')
def attachment_full_lifecycle(self):
    """
    完整附件生命周期管理流程

    依次执行：存储检查 → 归档 → 清理 → 再次存储检查
    默认每天凌晨3点执行
    """
    from Django_xm.apps.chat.services.attachment_lifecycle import AttachmentLifecycleService

    try:
        service = AttachmentLifecycleService()

        alert_result = check_storage_alerts()
        archive_result = archive_old_attachments()
        cleanup_result = cleanup_expired_attachments()
        alert_final = check_storage_alerts()

        result = {
            'before': alert_result,
            'archive': archive_result,
            'cleanup': cleanup_result,
            'after': alert_final,
        }

        logger.info("完整生命周期管理流程执行完成")
        return result
    except Exception as e:
        logger.error(f"完整生命周期任务失败: {e}", exc_info=True)
        return {'error': str(e)}
