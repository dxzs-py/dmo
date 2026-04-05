"""
基础 Celery 任务
存放通用、调试类的任务
"""
import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True)
def debug_task(self):
    """
    调试任务：打印当前请求信息
    """
    logger.info(f"[Celery] 调试任务执行，请求：{self.request!r}")
    return {'status': 'success', 'request': repr(self.request)}
