"""
RAG 相关的 Celery 异步任务
包括索引文档上传等耗时操作（索引创建/删除走同步入口，见 views_kb）
"""

import logging

from celery import shared_task
from celery.exceptions import Retry

from Django_xm.tasks.base import TrackedTask

logger = logging.getLogger(__name__)


# Redis 锁配置：防止相同 task_id 的文档写入任务并发执行
# 锁 TTL 3600s 覆盖任务最大执行时长（soft_time_limit=1800s）的两倍
_RAG_ADD_DOCS_LOCK_TTL = 3600


def _acquire_rag_add_docs_lock(task_id: str) -> bool:
    """获取 RAG 文档写入任务的 Redis 分布式锁

    使用 SET NX EX 原子操作，防止相同 task_id 的任务在重试/并发场景下
    同时执行导致重复写入（与业务层稳定 ID 共同构成幂等保障）。

    Args:
        task_id: Celery 任务 ID

    Returns:
        True 表示获锁成功，False 表示已有其他 worker 在执行
    """
    if not task_id:
        return True  # 无 task_id 时无法加锁，放行由业务层 ID 兜底
    try:
        from django_redis import get_redis_connection

        client = get_redis_connection("default")
        lock_key = f"lock:rag_add_docs:{task_id}"
        return bool(client.set(lock_key, "1", nx=True, ex=_RAG_ADD_DOCS_LOCK_TTL))
    except Exception as e:
        # Redis 不可用时不阻塞任务执行，由业务层稳定 ID 兜底幂等
        logger.warning(f"[Celery RAG] 获取 Redis 锁失败（放行由业务层兜底）: {e}")
        return True


def _release_rag_add_docs_lock(task_id: str) -> None:
    """释放 RAG 文档写入任务的 Redis 锁"""
    if not task_id:
        return
    try:
        from django_redis import get_redis_connection

        client = get_redis_connection("default")
        lock_key = f"lock:rag_add_docs:{task_id}"
        client.delete(lock_key)
    except Exception as e:
        logger.debug(f"[Celery RAG] 释放 Redis 锁失败（TTL 会自动过期）: {e}")


def _publish_upload_progress(task_id: str, status_updates: dict) -> None:
    """TrackedTask sync_fn：推送知识库上传进度到 task:{task_id} WebSocket 频道。

    命名边界：payload 为网络传输数据，键名统一 snake_case，
    type 为协议路由标识符（task_progress），保持原始值。
    """
    if not task_id:
        return
    try:
        from Django_xm.common.event_schema import EventSource, EventType
        from Django_xm.common.realtime_events import publish_event_sync

        publish_event_sync(
            EventType.TASK_PROGRESS,
            {
                "task_id": task_id,
                "source": EventSource.KNOWLEDGE,
                "status": status_updates.get("status"),
                "progress": status_updates.get("progress"),
                "current_step": status_updates.get("current_step"),
                "result": status_updates.get("result"),
                "error": status_updates.get("error"),
            },
            task_id=task_id,
        )
    except Exception as e:
        logger.debug(f"[Celery RAG] 发布上传进度事件失败: {e}")


def _publish_upload_terminal(task_id: str, user_id: int, success: bool) -> None:
    """任务进入终态时通知 user 频道，供知识库相关页面刷新列表。

    task:{task_id} 频道的终态事件已由 TrackedTask mark_success/mark_failure 的
    sync_fn（_publish_upload_progress）推送，此处不重复发送；仅通知 user 频道。
    """
    if not task_id:
        return
    try:
        from Django_xm.common.event_schema import EventType
        from Django_xm.common.realtime_events import publish_event_sync

        publish_event_sync(
            EventType.TASK_STATUS_CHANGED,
            {
                "task_id": task_id,
                "status": "completed" if success else "failed",
            },
            user_id=user_id,
        )
    except Exception as e:
        logger.debug(f"[Celery RAG] 发布上传终态事件失败: {e}")


@shared_task(
    bind=True,
    name="rag.upload_documents",
    max_retries=3,
    default_retry_delay=30,
    soft_time_limit=1800,
    autoretry_for=(ConnectionError, TimeoutError, OSError),
    retry_backoff=True,
    retry_backoff_max=60,
)
def upload_documents_task(
    self,
    user_id: int,
    kb_id: str,
    file_names: list,
    task_id: str | None = None,
):
    """异步上传文档到知识库：加载文档、分块、向量化、更新索引。

    文件已由视图（save_uploaded_files）落盘到 upload 目录，本任务只做处理，
    避免 40s+ 的文档处理阻塞 HTTP 请求。

    Args:
        user_id: 用户 ID
        kb_id: 知识库名称（原始名称）
        file_names: upload 目录下的文件名列表
        task_id: 业务任务 ID（WebSocket task 频道路由 ID，视图生成）
    """
    tracker = TrackedTask(self)
    tracker.set_task_type("rag_upload")
    if user_id:
        tracker.set_created_by(user_id)
    if task_id:
        tracker.set_task_manager_id(task_id, sync_fn=_publish_upload_progress)

    # Redis 锁：基于 celery task id 防止同任务重试/并发场景下重复写入
    # 与业务层稳定 ID 共同构成幂等保障
    celery_task_id = self.request.id
    if not _acquire_rag_add_docs_lock(celery_task_id):
        logger.warning(f"[Celery RAG] upload_documents 任务 {celery_task_id} 已有其他 worker 在执行，跳过本次执行")
        tracker.mark_success(result={"skipped": True, "reason": "locked_by_another_worker"})
        return {"status": "skipped", "reason": "another_worker_holding_lock"}

    def progress_callback(progress: int, message: str) -> None:
        tracker.update_progress(progress, message)

    try:
        logger.info(f"[Celery RAG] 开始上传文档：kb={kb_id}, files={file_names}")
        tracker.mark_started()

        from django.contrib.auth import get_user_model

        from Django_xm.apps.knowledge.services.kb_service import process_uploaded_documents

        user = get_user_model().objects.get(id=user_id)
        result = process_uploaded_documents(user, kb_id, file_names, progress_callback=progress_callback)

        tracker.mark_success(result=result)
        _publish_upload_terminal(task_id, user_id, success=True)

        logger.info(f"[Celery RAG] 上传文档完成：kb={kb_id}, {result['chunks_created']} 个分块")
        return {"status": "success", **result}

    except Retry:
        raise
    except Exception as exc:
        logger.exception(f"[Celery RAG] 上传文档失败：kb={kb_id}, 错误：")
        try:
            tracker.mark_failure(error_message=str(exc))
        except Exception:
            # tracker 更新失败不影响错误处理主流程
            logger.debug("tracker.mark_failure 失败（上传文档）")
        _publish_upload_terminal(task_id, user_id, success=False)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc) from exc
        return {"status": "error", "error": str(exc)}
    finally:
        # 无论成功/失败/重试，都释放锁（重试时新 task_id 会重新获锁）
        _release_rag_add_docs_lock(celery_task_id)
