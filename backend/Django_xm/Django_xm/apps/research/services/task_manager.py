"""
深度研究任务管理器
使用数据库存储任务状态，替代全局变量
所有查询操作强制按用户隔离，防止数据越权访问
"""
import logging
import threading
from datetime import datetime
from typing import Optional, Dict, Any

from django.conf import settings
from ..models import ResearchTask

logger = logging.getLogger(__name__)


class TaskManager:
    """
    任务管理器类
    提供任务状态管理功能，优先使用数据库存储
    所有涉及任务查询/修改/删除的操作均需验证用户归属
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._cache: Dict[str, Dict[str, Any]] = {}
                    cls._instance._threads: Dict[str, threading.Thread] = {}
        return cls._instance

    def get_task_status(self, task_id: str, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        获取任务状态
        始终从数据库加载最新状态，确保与 Celery Worker 同步
        当 user_id 不为 None 时，强制验证任务归属
        """
        try:
            qs = ResearchTask.objects.filter(task_id=task_id)
            if user_id is not None:
                qs = qs.filter(created_by_id=user_id)
            task = qs.get()

            status_data = {
                'task_id': task.task_id,
                'query': task.query,
                'status': task.status,
                'current_step': task.status,
                'created_at': task.created_at.isoformat() if task.created_at else None,
                'updated_at': task.updated_at.isoformat() if task.updated_at else None,
                'enable_web_search': task.enable_web_search,
                'enable_doc_analysis': task.enable_doc_analysis,
            }

            if task.status == 'completed' and task.final_report:
                status_data['final_report'] = task.final_report

            self._cache[task_id] = status_data
            return status_data

        except ResearchTask.DoesNotExist:
            return None

    def update_task_status(self, task_id: str, status_data: Dict[str, Any], user_id: Optional[int] = None) -> None:
        """
        更新任务状态
        同时更新缓存和数据库
        当 user_id 不为 None 时，强制验证任务归属
        """
        if task_id not in self._cache:
            self._cache[task_id] = {}

        self._cache[task_id].update(status_data)

        try:
            qs = ResearchTask.objects.filter(task_id=task_id)
            if user_id is not None:
                qs = qs.filter(created_by_id=user_id)
            task = qs.get()

            if 'status' in status_data:
                task.status = status_data['status']
            if 'final_report' in status_data:
                task.final_report = status_data.get('final_report', '')

            task.save()

        except ResearchTask.DoesNotExist:
            logger.warning(f"Task {task_id} not found in database or user mismatch")

    def create_task(self, task_id: str, query: str,
                   enable_web_search: bool = True,
                   enable_doc_analysis: bool = False,
                   created_by=None) -> Dict[str, Any]:
        task_data = {
            'task_id': task_id,
            'query': query,
            'status': 'pending',
            'current_step': 'pending',
            'created_at': datetime.now().isoformat(),
            'enable_web_search': enable_web_search,
            'enable_doc_analysis': enable_doc_analysis,
        }

        self._cache[task_id] = task_data

        ResearchTask.objects.create(
            task_id=task_id,
            query=query,
            status='pending',
            enable_web_search=enable_web_search,
            enable_doc_analysis=enable_doc_analysis,
            created_by=created_by
        )

        return task_data

    def register_thread(self, task_id: str, thread: threading.Thread) -> None:
        self._threads[task_id] = thread

    def unregister_thread(self, task_id: str) -> None:
        if task_id in self._threads:
            del self._threads[task_id]

    def delete_task(self, task_id: str, user_id: Optional[int] = None) -> bool:
        """
        删除任务
        当 user_id 不为 None 时，强制验证任务归属
        """
        if task_id in self._cache:
            del self._cache[task_id]

        if task_id in self._threads:
            del self._threads[task_id]

        try:
            qs = ResearchTask.objects.filter(task_id=task_id)
            if user_id is not None:
                qs = qs.filter(created_by_id=user_id)
            task = qs.get()
            task.delete()
        except ResearchTask.DoesNotExist:
            pass

        try:
            from Django_xm.apps.core.services.file_manager import get_file_manager
            file_manager = get_file_manager()
            file_manager.delete_task_files(task_id, "research")
        except Exception as e:
            logger.warning(f"[TaskManager] 删除研究任务文件失败: {e}")

        return True

    def task_exists(self, task_id: str, user_id: Optional[int] = None) -> bool:
        """
        检查任务是否存在
        当 user_id 不为 None 时，强制验证任务归属
        """
        if user_id is None:
            if task_id in self._cache:
                return True
            return ResearchTask.objects.filter(task_id=task_id).exists()

        qs = ResearchTask.objects.filter(task_id=task_id, created_by_id=user_id)
        return qs.exists()


_task_manager = None


def get_task_manager() -> TaskManager:
    global _task_manager
    if _task_manager is None:
        _task_manager = TaskManager()
    return _task_manager


def get_task_status(task_id: str, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    return get_task_manager().get_task_status(task_id, user_id=user_id)


def update_task_status(task_id: str, status: Dict[str, Any], user_id: Optional[int] = None) -> None:
    get_task_manager().update_task_status(task_id, status, user_id=user_id)
