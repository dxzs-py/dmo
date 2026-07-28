"""
AI 引擎信号处理模块

监听跨应用自定义信号，触发 AI 数据清理等异步操作。
"""

import logging

from django.dispatch import receiver

from Django_xm.apps.core.signals import ai_data_cleanup_needed

logger = logging.getLogger(__name__)


@receiver(ai_data_cleanup_needed)
def on_ai_data_cleanup_needed(sender, user_id=None, session_id=None, **kwargs):
    """
    监听 AI 数据清理信号，调度 Celery 异步清理任务

    由 core.signals.user_post_delete 等场景触发，
    避免核心模块直接依赖 ai_engine 的实现细节。
    """
    from Django_xm.apps.ai_engine.services.cross_app import schedule_ai_data_cleanup
    schedule_ai_data_cleanup(user_id=user_id, session_id=session_id)
