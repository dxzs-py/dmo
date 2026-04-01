"""
深度研究任务管理器
使用数据库存储任务状态，替代全局变量
"""
import logging
import threading
from datetime import datetime
from typing import Optional, Dict, Any

from .models import ResearchTask

logger = logging.getLogger(__name__)


class TaskManager:
    """
    任务管理器类
    提供任务状态管理功能，优先使用数据库存储
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        """单例模式确保全局只有一个管理器"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._cache: Dict[str, Dict[str, Any]] = {}
                    cls._instance._threads: Dict[str, threading.Thread] = {}
        return cls._instance
    
    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        获取任务状态
        优先从缓存获取，缓存不存在时从数据库加载
        """
        if task_id in self._cache:
            return self._cache[task_id]
        
        try:
            task = ResearchTask.objects.get(task_id=task_id)
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
    
    def update_task_status(self, task_id: str, status_data: Dict[str, Any]) -> None:
        """
        更新任务状态
        同时更新缓存和数据库
        """
        if task_id not in self._cache:
            self._cache[task_id] = {}
        
        self._cache[task_id].update(status_data)
        
        try:
            task = ResearchTask.objects.get(task_id=task_id)
            
            if 'status' in status_data:
                task.status = status_data['status']
            if 'current_step' in status_data:
                task.status = status_data['current_step']
            if 'final_report' in status_data:
                task.final_report = status_data.get('final_report', '')
            
            task.save()
            
        except ResearchTask.DoesNotExist:
            logger.warning(f"Task {task_id} not found in database")
    
    def create_task(self, task_id: str, query: str, 
                   enable_web_search: bool = True, 
                   enable_doc_analysis: bool = False) -> Dict[str, Any]:
        """
        创建新任务
        """
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
            enable_doc_analysis=enable_doc_analysis
        )
        
        return task_data
    
    def register_thread(self, task_id: str, thread: threading.Thread) -> None:
        """注册后台线程"""
        self._threads[task_id] = thread
    
    def unregister_thread(self, task_id: str) -> None:
        """注销后台线程"""
        if task_id in self._threads:
            del self._threads[task_id]
    
    def delete_task(self, task_id: str) -> bool:
        """删除任务"""
        if task_id in self._cache:
            del self._cache[task_id]
        
        if task_id in self._threads:
            del self._threads[task_id]
        
        try:
            task = ResearchTask.objects.get(task_id=task_id)
            task.delete()
            return True
        except ResearchTask.DoesNotExist:
            return False
    
    def task_exists(self, task_id: str) -> bool:
        """检查任务是否存在"""
        if task_id in self._cache:
            return True
        
        return ResearchTask.objects.filter(task_id=task_id).exists()


_task_manager = None


def get_task_manager() -> TaskManager:
    """获取任务管理器单例"""
    global _task_manager
    if _task_manager is None:
        _task_manager = TaskManager()
    return _task_manager


def get_task_status(task_id: str) -> Optional[Dict[str, Any]]:
    """获取任务状态的便捷函数"""
    return get_task_manager().get_task_status(task_id)


def update_task_status(task_id: str, status: Dict[str, Any]) -> None:
    """更新任务状态的便捷函数"""
    get_task_manager().update_task_status(task_id, status)
