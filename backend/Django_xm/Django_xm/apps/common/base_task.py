"""
基础任务管理器
提供通用的任务状态管理功能
"""
import logging
import threading
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, Callable

logger = logging.getLogger(__name__)


class BaseTaskManager:
    """
    基础任务管理器类
    提供通用的任务状态管理功能
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
    
    def generate_task_id(self) -> str:
        """生成唯一任务ID"""
        return str(uuid.uuid4())
    
    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        获取任务状态
        子类需要实现具体的数据库获取逻辑
        """
        raise NotImplementedError("子类必须实现 get_task_status 方法")
    
    def update_task_status(self, task_id: str, status_data: Dict[str, Any]) -> None:
        """
        更新任务状态
        子类需要实现具体的数据库更新逻辑
        """
        raise NotImplementedError("子类必须实现 update_task_status 方法")
    
    def create_task(self, task_id: str, **kwargs) -> Dict[str, Any]:
        """
        创建新任务
        子类需要实现具体的数据库创建逻辑
        """
        raise NotImplementedError("子类必须实现 create_task 方法")
    
    def register_thread(self, task_id: str, thread: threading.Thread) -> None:
        """注册后台线程"""
        self._threads[task_id] = thread
    
    def unregister_thread(self, task_id: str) -> None:
        """注销后台线程"""
        if task_id in self._threads:
            del self._threads[task_id]
    
    def delete_task(self, task_id: str) -> bool:
        """
        删除任务
        子类需要实现具体的数据库删除逻辑
        """
        raise NotImplementedError("子类必须实现 delete_task 方法")
    
    def task_exists(self, task_id: str) -> bool:
        """检查任务是否存在"""
        if task_id in self._cache:
            return True
        return False


def format_task_status(
    task_id: str,
    status: str,
    created_at: Optional[datetime] = None,
    updated_at: Optional[datetime] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    格式化任务状态响应
    
    Args:
        task_id: 任务ID
        status: 任务状态
        created_at: 创建时间
        updated_at: 更新时间
        **kwargs: 额外的状态数据
    
    Returns:
        格式化的状态字典
    """
    result = {
        'task_id': task_id,
        'status': status,
        'created_at': created_at.isoformat() if created_at else None,
        'updated_at': updated_at.isoformat() if updated_at else None,
        **kwargs
    }
    return result
