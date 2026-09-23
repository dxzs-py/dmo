"""
通用异步任务管理模块
基于 Redis 缓存提供任务状态跟踪、查询和管理功能
"""

import logging
import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from django.core.cache import cache

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    PENDING = "pending"
    STARTED = "started"
    PROGRESS = "progress"
    SUCCESS = "success"
    FAILURE = "failure"
    REVOKED = "revoked"
    RETRY = "retry"


class TaskType(Enum):
    RAG_INDEX = "rag_index"
    RAG_ADD_DOCS = "rag_add_docs"
    RAG_UPLOAD = "rag_upload"
    RAG_DELETE_INDEX = "rag_delete_index"
    RAG_UPDATE_INDEX = "rag_update_index"
    CHAT_CLEANUP = "chat_cleanup"
    CHAT_INDEX = "chat_index"
    CHAT_STORAGE = "chat_storage"
    OTHER = "other"


_TERMINAL_STATES = {TaskStatus.SUCCESS.value, TaskStatus.FAILURE.value, TaskStatus.REVOKED.value}


class TaskManager:
    CACHE_PREFIX = "task_status:"
    CACHE_TIMEOUT = 86400 * 7

    def __init__(self):
        self.cache = cache

    def _get_cache_key(self, task_id: str) -> str:
        return f"{self.CACHE_PREFIX}{task_id}"

    def _get_user_tasks_key(self, user_id: int) -> str:
        return f"user_tasks:{user_id}"

    def create_task(
        self,
        task_type: TaskType,
        user_id: int | None = None,
        task_name: str | None = None,
        task_params: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> str:
        if not task_id:
            task_id = str(uuid.uuid4())

        now = datetime.now(UTC).isoformat()

        metadata = metadata or {}
        if task_name:
            metadata["task_name"] = task_name
        if task_params:
            metadata["task_params"] = task_params

        task_data = {
            "task_id": task_id,
            "task_type": task_type.value,
            "status": TaskStatus.PENDING.value,
            "user_id": user_id,
            "created_at": now,
            "updated_at": now,
            "start_time": None,
            "end_time": None,
            "progress": 0,
            "current_step": "waiting",
            "result": None,
            "error": None,
            "metadata": metadata,
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
        status_updates: dict[str, Any],
    ) -> dict[str, Any] | None:
        cache_key = self._get_cache_key(task_id)
        task_data = self.cache.get(cache_key)

        if not task_data:
            logger.debug(f"[TaskManager] 任务不存在，无法更新：{task_id}")
            return None

        task_data.update(status_updates)
        task_data["updated_at"] = datetime.now(UTC).isoformat()

        new_status = task_data.get("status")
        if new_status in (TaskStatus.STARTED.value, TaskStatus.PROGRESS.value) and not task_data.get("start_time"):
            task_data["start_time"] = datetime.now(UTC).isoformat()

        if new_status in _TERMINAL_STATES and not task_data.get("end_time"):
            task_data["end_time"] = datetime.now(UTC).isoformat()

        self.cache.set(cache_key, task_data, self.CACHE_TIMEOUT)

        logger.debug(f"[TaskManager] 更新任务状态：{task_id}, 状态：{task_data['status']}")

        return task_data

    def get_task_status(self, task_id: str) -> dict[str, Any] | None:
        cache_key = self._get_cache_key(task_id)
        return self.cache.get(cache_key)

    def get_user_tasks(
        self,
        user_id: int,
        task_type: TaskType | None = None,
        status: TaskStatus | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        logger.debug(f"[TaskManager] 查询用户任务：user={user_id}")

        user_tasks_key = self._get_user_tasks_key(user_id)
        user_task_ids = self.cache.get(user_tasks_key, [])

        tasks = []
        for task_id in user_task_ids:
            task_data = self.cache.get(self._get_cache_key(task_id))
            if not task_data:
                continue

            if task_type and task_data.get("task_type") != task_type.value:
                continue
            if status and task_data.get("status") != status.value:
                continue

            tasks.append(task_data)

        tasks.sort(key=lambda t: t.get("updated_at", ""), reverse=True)
        return tasks[:limit]

    def delete_task(self, task_id: str) -> bool:
        cache_key = self._get_cache_key(task_id)
        task_data = self.cache.get(cache_key)

        user_id = task_data.get("user_id") if task_data else None

        deleted = self.cache.delete(cache_key)

        if deleted and user_id is not None:
            user_tasks_key = self._get_user_tasks_key(user_id)
            user_task_ids = self.cache.get(user_tasks_key, [])
            if task_id in user_task_ids:
                user_task_ids.remove(task_id)
                self.cache.set(user_tasks_key, user_task_ids, self.CACHE_TIMEOUT)
            logger.info(f"[TaskManager] 删除任务：{task_id}")

        return bool(deleted)


_task_manager: TaskManager | None = None


def get_task_manager() -> TaskManager:
    # 模块级单例惰性初始化
    global _task_manager  # noqa: PLW0603
    if _task_manager is None:
        _task_manager = TaskManager()
    return _task_manager


def create_task(
    task_type: TaskType,
    user_id: int | None = None,
    task_name: str | None = None,
    task_params: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> str:
    manager = get_task_manager()
    return manager.create_task(
        task_type=task_type,
        user_id=user_id,
        task_name=task_name,
        task_params=task_params,
        metadata=metadata,
        task_id=task_id,
    )


def update_task_status(task_id: str, status_updates: dict[str, Any]) -> dict[str, Any] | None:
    manager = get_task_manager()
    return manager.update_task_status(task_id, status_updates)


def get_task_status(task_id: str) -> dict[str, Any] | None:
    manager = get_task_manager()
    return manager.get_task_status(task_id)


def format_task_duration(task_data: dict[str, Any]) -> str | None:
    if not task_data.get("start_time"):
        return None

    start_time = datetime.fromisoformat(task_data["start_time"])
    end_time = task_data.get("end_time")

    if end_time:
        end_time = datetime.fromisoformat(end_time)
    else:
        end_time = datetime.now(UTC)

    duration = end_time - start_time

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
