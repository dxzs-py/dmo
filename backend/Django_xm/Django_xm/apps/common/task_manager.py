"""
通用异步任务管理模块
提供任务状态跟踪、查询和管理功能
"""
import json
import uuid
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List
from enum import Enum

from django.core.cache import cache
from django.conf import settings


logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    """任务状态枚举"""
    PENDING = 'pending'
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'
    CANCELLED = 'cancelled'


class TaskType(Enum):
    """任务类型枚举"""
    DEEP_RESEARCH = 'deep_research'
    RAG_INDEX = 'rag_index'
    RAG_ADD_DOCS = 'rag_add_docs'
    RAG_DELETE_INDEX = 'rag_delete_index'
    WORKFLOW = 'workflow'
    OTHER = 'other'


class TaskManager:
    """
    通用任务管理器
    使用 Redis 缓存存储任务状态
    """
    
    CACHE_PREFIX = 'task_status:'
    CACHE_TIMEOUT = 86400 * 7  # 7天
    
    def __init__(self):
        self.cache = cache
    
    def _get_cache_key(self, task_id: str) -> str:
        return f"{self.CACHE_PREFIX}{task_id}"
    
    def _get_user_tasks_key(self, user_id: int) -> str:
        return f"user_tasks:{user_id}"
    
    def create_task(
        self,
        task_type: TaskType,
        user_id: Optional[int] = None,
        task_name: Optional[str] = None,
        task_params: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        task_id: Optional[str] = None,
    ) -> str:
        if not task_id:
            task_id = str(uuid.uuid4())
        
        now = datetime.now().isoformat()
        
        metadata = metadata or {}
        if task_name:
            metadata['task_name'] = task_name
        if task_params:
            metadata['task_params'] = task_params
        
        task_data = {
            'task_id': task_id,
            'task_type': task_type.value,
            'status': TaskStatus.PENDING.value,
            'user_id': user_id,
            'created_at': now,
            'updated_at': now,
            'start_time': None,
            'end_time': None,
            'progress': 0,
            'current_step': 'waiting',
            'result': None,
            'error': None,
            'metadata': metadata,
        }
        
        cache_key = self._get_cache_key(task_id)
        self.cache.set(cache_key, task_data, self.CACHE_TIMEOUT)
        
        if user_id is not None:
            user_tasks_key = self._get_user_tasks_key(user_id)
            user_task_ids = self.cache.get(user_tasks_key, [])
            if task_id not in user_task_ids:
                user_task_ids.append(task_id)
                self.cache.set(user_tasks_key, user_task_ids, self.CACHE_TIMEOUT)
        
        logger.info(f"[TaskManager] 创建任务：{task_id}, 类型：{task_type.value}")
        
        return task_id
    
    def update_task_status(
        self,
        task_id: str,
        status_updates: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        更新任务状态
        
        Args:
            task_id: 任务ID
            status_updates: 状态更新字典
            
        Returns:
            更新后的任务状态，任务不存在时返回None
        """
        cache_key = self._get_cache_key(task_id)
        task_data = self.cache.get(cache_key)
        
        if not task_data:
            logger.warning(f"[TaskManager] 任务不存在，无法更新：{task_id}")
            return None
        
        # 更新任务数据
        task_data.update(status_updates)
        task_data['updated_at'] = datetime.now().isoformat()
        
        # 自动处理开始和结束时间
        if task_data['status'] == TaskStatus.RUNNING.value and not task_data['start_time']:
            task_data['start_time'] = datetime.now().isoformat()
        
        if task_data['status'] in [TaskStatus.COMPLETED.value, TaskStatus.FAILED.value]:
            task_data['end_time'] = datetime.now().isoformat()
        
        # 保存回缓存
        self.cache.set(cache_key, task_data, self.CACHE_TIMEOUT)
        
        logger.debug(f"[TaskManager] 更新任务状态：{task_id}, 状态：{task_data['status']}")
        
        return task_data
    
    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        获取任务状态
        
        Args:
            task_id: 任务ID
            
        Returns:
            任务状态字典，不存在时返回None
        """
        cache_key = self._get_cache_key(task_id)
        return self.cache.get(cache_key)
    
    def get_user_tasks(
        self,
        user_id: int,
        task_type: Optional[TaskType] = None,
        status: Optional[TaskStatus] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        logger.debug(f"[TaskManager] 查询用户任务：user={user_id}")

        user_tasks_key = self._get_user_tasks_key(user_id)
        user_task_ids = self.cache.get(user_tasks_key, [])

        tasks = []
        for task_id in user_task_ids:
            task_data = self.cache.get(self._get_cache_key(task_id))
            if not task_data:
                continue

            if task_type and task_data.get('task_type') != task_type.value:
                continue
            if status and task_data.get('status') != status.value:
                continue

            tasks.append(task_data)

        tasks.sort(key=lambda t: t.get('updated_at', ''), reverse=True)
        return tasks[:limit]
    
    def delete_task(self, task_id: str) -> bool:
        cache_key = self._get_cache_key(task_id)
        task_data = self.cache.get(cache_key)
        
        user_id = task_data.get('user_id') if task_data else None
        
        deleted = self.cache.delete(cache_key)
        
        if deleted and user_id is not None:
            user_tasks_key = self._get_user_tasks_key(user_id)
            user_task_ids = self.cache.get(user_tasks_key, [])
            if task_id in user_task_ids:
                user_task_ids.remove(task_id)
                self.cache.set(user_tasks_key, user_task_ids, self.CACHE_TIMEOUT)
            logger.info(f"[TaskManager] 删除任务：{task_id}")
        
        return bool(deleted)
    
    def get_all_tasks(
        self,
        task_type: Optional[TaskType] = None,
        status: Optional[TaskStatus] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        获取所有任务（仅用于管理/调试）
        
        Args:
            task_type: 任务类型过滤
            status: 状态过滤
            limit: 限制数量
            
        Returns:
            任务列表
        """
        # 简化实现
        return []


# 全局任务管理器实例
_task_manager: Optional[TaskManager] = None


def get_task_manager() -> TaskManager:
    """获取全局任务管理器实例"""
    global _task_manager
    if _task_manager is None:
        _task_manager = TaskManager()
    return _task_manager


def create_task(
    task_type: TaskType,
    user_id: Optional[int] = None,
    task_name: Optional[str] = None,
    task_params: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
) -> str:
    """便捷函数：创建任务"""
    manager = get_task_manager()
    return manager.create_task(
        task_type=task_type,
        user_id=user_id,
        task_name=task_name,
        task_params=task_params,
        metadata=metadata,
        task_id=task_id,
    )


def update_task_status(task_id: str, status_updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """便捷函数：更新任务状态"""
    manager = get_task_manager()
    return manager.update_task_status(task_id, status_updates)


def get_task_status(task_id: str) -> Optional[Dict[str, Any]]:
    """便捷函数：获取任务状态"""
    manager = get_task_manager()
    return manager.get_task_status(task_id)


def format_task_duration(task_data: Dict[str, Any]) -> Optional[str]:
    """
    格式化任务持续时间
    
    Args:
        task_data: 任务数据
        
    Returns:
        格式化的持续时间字符串
    """
    if not task_data.get('start_time'):
        return None
    
    start_time = datetime.fromisoformat(task_data['start_time'])
    end_time = task_data.get('end_time')
    
    if end_time:
        end_time = datetime.fromisoformat(end_time)
    else:
        end_time = datetime.now()
    
    duration = end_time - start_time
    
    # 格式化
    if duration.total_seconds() < 60:
        return f"{duration.total_seconds():.1f}s"
    elif duration.total_seconds() < 3600:
        minutes = int(duration.total_seconds() / 60)
        seconds = int(duration.total_seconds() % 60)
        return f"{minutes}m{seconds}s"
    else:
        hours = int(duration.total_seconds() / 3600)
        minutes = int((duration.total_seconds() % 3600) / 60)
        return f"{hours}h{minutes}m"
