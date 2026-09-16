"""
深度研究任务管理器
使用数据库存储任务状态，替代全局变量
所有查询操作强制按用户隔离，防止数据越权访问
"""

import logging
import threading
from typing import Any, ClassVar

from django.core.cache import cache as redis_cache
from django.utils import timezone

from ..models import ResearchTask

logger = logging.getLogger(__name__)

_REDIS_CACHE_TTL = 5
_REDIS_CACHE_KEY_PREFIX = "research:task_status"


class TaskManager:
    """
    任务管理器类
    提供任务状态管理功能，优先使用数据库存储
    所有涉及任务查询/修改/删除的操作均需验证用户归属
    """

    _instance: ClassVar["TaskManager | None"] = None
    _lock: ClassVar[threading.Lock] = threading.Lock()
    # _cache / _threads 为实例属性，在 __new__ 中初始化（避免类级可变默认值）

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._cache: dict[str, dict[str, Any]] = {}
                    cls._instance._threads: dict[str, threading.Thread] = {}
        return cls._instance

    @staticmethod
    def _redis_cache_key(task_id: str, user_id) -> str:
        return f"{_REDIS_CACHE_KEY_PREFIX}:{task_id}:{user_id}"

    def _invalidate_redis_cache(self, task_id: str, created_by_id) -> None:
        redis_cache.delete(self._redis_cache_key(task_id, created_by_id))
        redis_cache.delete(self._redis_cache_key(task_id, None))

    def get_task_status(self, task_id: str, user_id: int | None = None) -> dict[str, Any] | None:
        """
        获取任务状态
        优先查询 Redis 缓存，未命中则查数据库并回写缓存
        当 user_id 不为 None 时，强制验证任务归属
        """
        cache_key = self._redis_cache_key(task_id, user_id)
        cached = redis_cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            qs = ResearchTask.objects.filter(task_id=task_id)
            if user_id is not None:
                qs = qs.filter(created_by_id=user_id)
            task = qs.get()

            status_data = {
                "task_id": task.task_id,
                "query": task.query,
                "status": task.status,
                "current_step": task.status,
                "created_at": task.created_at.isoformat() if task.created_at else None,
                "updated_at": task.updated_at.isoformat() if task.updated_at else None,
                "enable_web_search": task.enable_web_search,
                "enable_doc_analysis": task.enable_doc_analysis,
                "enable_sandbox": task.enable_sandbox,
                "final_report": task.final_report if task.status == "completed" else "",
                # 主代理累计正文（过程信息权威源，对齐 ChatMessage.content）：
                # 详情页打开时快照校对（useSnapshotSync task 分支）据此补全
                # researchStore.taskInfo.content，保证独立深研模式过程正文实时可见。
                "content": task.content or "",
                # 来源推导（与 ResearchTaskSerializer.get_source 同规则）：
                # 有 session_id 视为聊天触发（chat），否则独立研究（standalone）。
                # 前端详情页来源标签与 taskToolCalls 的 sessionStore 分支依赖该字段。
                "source": "chat" if task.session_id else "standalone",
                "session_id": task.session_id,
            }

            self._cache[task_id] = status_data
            redis_cache.set(cache_key, status_data, _REDIS_CACHE_TTL)
            return status_data

        except ResearchTask.DoesNotExist:
            return None

    _STATUS_MAP: ClassVar[dict[str, str]] = {
        "started": "running",
        "progress": "running",
        "success": "completed",
        "failure": "failed",
    }

    def _map_status(self, status: str) -> str:
        return self._STATUS_MAP.get(status, status)

    def update_task_status(self, task_id: str, status_data: dict[str, Any], user_id: int | None = None) -> None:
        if task_id not in self._cache:
            self._cache[task_id] = {}

        self._cache[task_id].update(status_data)

        final_report = status_data.get("final_report") or (status_data.get("result") or {}).get("final_report", "")

        try:
            qs = ResearchTask.objects.filter(task_id=task_id)
            if user_id is not None:
                qs = qs.filter(created_by_id=user_id)
            task = qs.get()

            if "status" in status_data:
                task.status = self._map_status(status_data["status"])
            if final_report:
                task.final_report = final_report
            content = status_data.get("content") or ""
            if content:
                # 主代理累计正文（过程信息权威源，对齐 ChatMessage.content）
                task.content = content
            error = status_data.get("error") or ""
            if error:
                # 模型校验限制 5000 字符，超长截断避免 ValidationError 导致状态丢失
                task.error_message = str(error)[:5000]

            task.save()
            self._invalidate_redis_cache(task_id, task.created_by_id)

        except ResearchTask.DoesNotExist:
            logger.warning(f"Task {task_id} not found in database or user mismatch")

    def create_task(
        self,
        task_id: str,
        query: str,
        enable_web_search: bool = True,
        enable_doc_analysis: bool = False,
        enable_sandbox: bool = False,
        created_by=None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        task_data = {
            "task_id": task_id,
            "query": query,
            "status": "pending",
            "current_step": "pending",
            "created_at": timezone.now().isoformat(),
            "enable_web_search": enable_web_search,
            "enable_doc_analysis": enable_doc_analysis,
            "enable_sandbox": enable_sandbox,
            "session_id": session_id,
        }

        self._cache[task_id] = task_data

        ResearchTask.objects.create(
            task_id=task_id,
            query=query,
            status="pending",
            enable_web_search=enable_web_search,
            enable_doc_analysis=enable_doc_analysis,
            enable_sandbox=enable_sandbox,
            created_by=created_by,
            session_id=session_id,
        )

        return task_data

    def register_thread(self, task_id: str, thread: threading.Thread) -> None:
        self._threads[task_id] = thread

    def unregister_thread(self, task_id: str) -> None:
        if task_id in self._threads:
            del self._threads[task_id]

    def delete_task(self, task_id: str, user_id: int | None = None) -> bool:
        """
        软删除研究任务
        - 设置 is_deleted=True, deleted_at=now
        - 深度研究已脱离 Celery（执行由 fastapi_service SessionExecutor 承载），
          无 Celery 任务可撤销；运行中协程由执行服务侧按状态自愈/终止
        - 磁盘文件/Checkpoint/Store 的清理由调用方通过 cross_app 统一守卫逻辑处理
          （需确认关联聊天也已删除才清理，避免数据不一致）
        """
        if task_id in self._cache:
            del self._cache[task_id]

        if task_id in self._threads:
            del self._threads[task_id]

        try:
            task = ResearchTask.objects.get(task_id=task_id)
            if user_id is not None and task.created_by_id != user_id:
                return False

            self._invalidate_redis_cache(task_id, task.created_by_id)

            # 软删除：设置 is_deleted=True, deleted_at=now
            task.soft_delete()
            logger.info(f"研究任务已软删除: {task_id}")

        except ResearchTask.DoesNotExist:
            return False

        return True

    def task_exists(self, task_id: str, user_id: int | None = None) -> bool:
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
    # 模块级单例惰性初始化
    global _task_manager  # noqa: PLW0603
    if _task_manager is None:
        _task_manager = TaskManager()
    return _task_manager


def get_task_status(task_id: str, user_id: int | None = None) -> dict[str, Any] | None:
    return get_task_manager().get_task_status(task_id, user_id=user_id)


def update_task_status(task_id: str, status: dict[str, Any], user_id: int | None = None) -> None:
    get_task_manager().update_task_status(task_id, status, user_id=user_id)
