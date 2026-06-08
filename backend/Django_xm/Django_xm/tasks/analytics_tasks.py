"""
分析模块 Celery 任务
"""
import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    name='analytics.track_event',
    bind=True,
    max_retries=1,
    soft_time_limit=60,
    autoretry_for=(ConnectionError, TimeoutError, OSError),
    retry_backoff=True,
    retry_backoff_max=60,
)
def track_event(self, event_data):
    """异步记录用户事件"""
    from Django_xm.apps.analytics.models import UserEvent
    try:
        UserEvent.objects.create(**event_data)
    except Exception as e:
        logger.error(f"异步记录用户事件失败: {e}")
        raise self.retry(exc=e, countdown=5)
