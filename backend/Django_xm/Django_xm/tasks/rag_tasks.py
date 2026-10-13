"""
RAG 相关的 Celery 异步任务
包括索引文档上传等耗时操作（索引创建/删除走同步入口，见 views_kb）
"""

import logging

from celery import shared_task
from celery.exceptions import Retry

from Django_xm.common.exceptions import EmbeddingUnavailableError
from Django_xm.tasks.base import TrackedTask

logger = logging.getLogger(__name__)


# Redis 锁配置：防止同一知识库的文档写入任务并发执行（RP-12 KB 级互斥）
# 锁 TTL 3600s 覆盖任务最大执行时长（soft_time_limit=1500s）的两倍余
_RAG_ADD_DOCS_LOCK_TTL = 3600


class _KnowledgeBaseUploadBusy(Exception):
    """同知识库已有其他上传任务在处理（RP-12 互斥信号，触发 Celery 重试等待）"""


def _rag_add_docs_lock_key(user_id, kb_id) -> str:
    """KB 级锁键：``lock:rag_add_docs:kb:{user_id}:{kb_id}``

    (user_id, kb_id) 与 user_index_name（D-1 命名单源 build_user_index_name）
    一一对应，锁粒度即知识库；task_id 不再进锁键（原 task_id 级锁只防同任务
    重发，同 KB 两个不同上传任务并发 add_documents 无互斥，num_documents
    读改写存在竞态），task_id 改为锁值记录持有者。
    """
    return f"lock:rag_add_docs:kb:{user_id}:{kb_id}"


def _acquire_rag_add_docs_lock(user_id, kb_id, task_id: str | None) -> tuple[bool, str | None]:
    """获取 KB 级 RAG 文档写入 Redis 分布式锁

    使用 SET NX EX 原子操作；锁值为持有者的 celery task_id（幂等记录）。
    与业务层稳定 ID 共同构成幂等保障；Redis 不可用时放行由业务层兜底。

    Args:
        user_id: 用户 ID
        kb_id: 知识库名称（原始名称）
        task_id: 当前 Celery 任务 ID

    Returns:
        (acquired, holder_task_id) 二元组：acquired=False 时 holder 为当前
        持锁任务 ID（Redis 不可用/键异常等无法判定场景为 None）
    """
    lock_key = _rag_add_docs_lock_key(user_id, kb_id)
    try:
        from django_redis import get_redis_connection

        client = get_redis_connection("default")
        if client.set(lock_key, task_id or "", nx=True, ex=_RAG_ADD_DOCS_LOCK_TTL):
            return True, task_id
        holder = client.get(lock_key)
        if isinstance(holder, bytes):
            holder = holder.decode("utf-8", errors="replace")
        return False, holder
    except Exception as e:
        # Redis 不可用时不阻塞任务执行，由业务层稳定 ID 兜底幂等
        logger.warning(f"[Celery RAG] 获取 Redis 锁失败（放行由业务层兜底）: {e}")
        return True, task_id


def _release_rag_add_docs_lock(user_id, kb_id) -> None:
    """释放 KB 级 RAG 文档写入 Redis 锁（仅持有者finally调用）"""
    try:
        from django_redis import get_redis_connection

        client = get_redis_connection("default")
        client.delete(_rag_add_docs_lock_key(user_id, kb_id))
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
    # soft < 全局硬限 1800（core/config.py:81），留出 SoftTimeLimitExceeded
    # 处理窗口（S-3：全项目唯一 soft==硬限 重叠点）
    soft_time_limit=1500,
    # 3.4：EmbeddingUnavailableError（全 provider 不可用的类型化信号）纳入
    # 自动重试——embedding 服务瞬时故障（网络/限速）可经 backoff 恢复。
    # 说明：任务体末尾的 except Exception 分支对上抛异常本就走 self.retry，
    # autoretry 声明主要覆盖 try 块之外的异常面并固化可重试异常契约。
    autoretry_for=(ConnectionError, TimeoutError, OSError, EmbeddingUnavailableError),
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

    # Redis 锁：KB 级互斥（RP-12）——同知识库的上传任务串行执行，消除
    # 并发 add_documents 的 num_documents 读改写竞态；锁值记录持有者
    # task_id：同任务重复投递（既有跳过语义）与异任务占用（重试等待）可区分。
    # 与业务层稳定 ID 共同构成幂等保障
    celery_task_id = self.request.id
    lock_acquired = False
    try:
        acquired, holder = _acquire_rag_add_docs_lock(user_id, kb_id, celery_task_id)
        lock_acquired = acquired
        if not acquired and holder == celery_task_id:
            # 同任务重复投递：第一次投递仍在执行，本重复体无需再跑（既有语义）
            logger.warning(f"[Celery RAG] upload_documents 任务 {celery_task_id} 已有其他 worker 在执行，跳过本次执行")
            tracker.mark_success(result={"skipped": True, "reason": "locked_by_another_worker"})
            return {"status": "skipped", "reason": "another_worker_holding_lock"}
        if not acquired:
            # 异任务占用同 KB：Celery 重试等待（不占 worker），重试耗尽后
            # 显式失败终态（文件已落盘，用户稍后重传即可，先删后加语义保证幂等）
            raise _KnowledgeBaseUploadBusy(  # noqa: TRY301  # 忙等信号须在 try 内抛出以走重试/终态分支
                f"知识库 {kb_id} 正在处理其他上传任务（持锁任务 {holder}），请稍后重试"
            )

        def progress_callback(progress: int, message: str) -> None:
            tracker.update_progress(progress, message)

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

    except _KnowledgeBaseUploadBusy as exc:
        if self.request.retries >= self.max_retries:
            logger.exception(f"[Celery RAG] 同知识库上传互斥等待超限（重试 {self.request.retries} 次）：kb={kb_id}")
            try:
                tracker.mark_failure(error_message=str(exc))
            except Exception:
                logger.debug("tracker.mark_failure 失败（同 KB 上传互斥超限）")
            _publish_upload_terminal(task_id, user_id, success=False)
            return {"status": "error", "error": str(exc)}
        logger.warning(
            f"[Celery RAG] 同知识库上传互斥：{self.default_retry_delay}s 后重试"
            f"（第 {self.request.retries + 1}/{self.max_retries} 次）：kb={kb_id}"
        )
        raise self.retry(exc=exc) from exc
    except Retry:
        raise
    except (ValueError, FileNotFoundError) as exc:
        # E5-05：确定性错误（坏 PDF/不支持类型/文件缺失）重试必然复现失败，
        # 不再走 30s backoff × 3 的无效等待，直接秒级终态（进度仍经 TrackedTask
        # 与 task 频道推送，用户可见失败原因）。
        logger.exception(f"[Celery RAG] 上传文档确定性失败（不重试）：kb={kb_id}")
        try:
            tracker.mark_failure(error_message=str(exc))
        except Exception:
            # tracker 更新失败不影响错误处理主流程
            logger.debug("tracker.mark_failure 失败（上传文档确定性错误）")
        _publish_upload_terminal(task_id, user_id, success=False)
        return {"status": "error", "error": str(exc)}
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
        # 仅在确实持有锁时释放（同任务跳过/异任务忙等路径不持锁，
        # 严禁误删他人持有的 KB 级锁）；重试时重新获锁
        if lock_acquired:
            _release_rag_add_docs_lock(user_id, kb_id)
