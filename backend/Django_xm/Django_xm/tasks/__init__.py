"""
Celery 统一任务模块
按业务领域分文件管理所有异步任务
"""
from .base import debug_task
from .deep_research import run_research_task

__all__ = ['debug_task', 'run_research_task']
