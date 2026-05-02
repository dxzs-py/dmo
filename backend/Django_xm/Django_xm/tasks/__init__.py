"""
Celery 统一任务模块
按业务领域分文件管理所有异步任务
"""
from .base import debug_task
from .deep_research import run_research_task
from .rag_tasks import (
    create_index_task,
    add_documents_to_index_task,
    delete_index_task,
    update_index_task,
)

__all__ = [
    'debug_task',
    'run_research_task',
    'create_index_task',
    'add_documents_to_index_task',
    'delete_index_task',
    'update_index_task',
]

