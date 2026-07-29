"""
RAG 相关的 Celery 异步任务
包括索引创建、文档处理等耗时操作
"""

import logging
from pathlib import Path

from celery import shared_task
from celery.exceptions import Retry

from Django_xm.apps.knowledge.services.cross_app import get_index_manager
from Django_xm.apps.knowledge.services.document_service import load_document, load_documents_from_directory
from Django_xm.apps.knowledge.services.embedding_service import get_embeddings
from Django_xm.apps.knowledge.services.splitters import split_documents
from Django_xm.tasks.base import TrackedTask

logger = logging.getLogger(__name__)


# Redis 锁配置：防止相同 task_id 的 add_documents 任务并发执行
# 锁 TTL 3600s 覆盖任务最大执行时长（soft_time_limit=1800s）的两倍
_RAG_ADD_DOCS_LOCK_TTL = 3600


def _acquire_rag_add_docs_lock(task_id: str) -> bool:
    """获取 RAG add_documents 任务的 Redis 分布式锁

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
    """释放 RAG add_documents 任务的 Redis 锁"""
    if not task_id:
        return
    try:
        from django_redis import get_redis_connection

        client = get_redis_connection("default")
        lock_key = f"lock:rag_add_docs:{task_id}"
        client.delete(lock_key)
    except Exception as e:
        logger.debug(f"[Celery RAG] 释放 Redis 锁失败（TTL 会自动过期）: {e}")


@shared_task(
    bind=True,
    name="rag.create_index",
    max_retries=3,
    default_retry_delay=30,
    soft_time_limit=3600,
    autoretry_for=(ConnectionError, TimeoutError, OSError),
    retry_backoff=True,
    retry_backoff_max=60,
)
def create_index_task(
    self,
    index_name: str,
    directory_path: str | None = None,
    description: str = "",
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    user_id: int | None = None,
    task_id: str | None = None,
    original_name: str | None = None,
    overwrite: bool = False,
):
    tracker = TrackedTask(self)
    tracker.set_task_type("rag_index")
    if user_id:
        tracker.set_created_by(user_id)
    if task_id:
        tracker.set_task_manager_id(task_id)

    try:
        logger.info(f"[Celery RAG] 开始创建索引：{index_name}")
        tracker.mark_started()

        manager = get_index_manager()

        if manager.index_exists(index_name):
            if overwrite:
                logger.info(f"[Celery RAG] 覆盖已有索引：{index_name}")
                manager.delete_index(index_name)
            else:
                logger.warning(f"[Celery RAG] 索引已存在：{index_name}")
                tracker.mark_success(result={"index_name": index_name, "exists": True})
                return {"status": "success", "index_name": index_name, "chunk_count": 0, "doc_count": 0, "exists": True}

        documents = []
        if directory_path:
            dir_path = Path(directory_path)
            if not dir_path.exists():
                logger.error(f"[Celery RAG] 目录不存在：{directory_path}")
                tracker.mark_failure(error_message=f"目录不存在：{directory_path}")
                return {"status": "error", "error": f"目录不存在：{directory_path}"}

            logger.info(f"[Celery RAG] 加载文档目录：{directory_path}")
            documents = load_documents_from_directory(directory_path)
            tracker.update_progress(30, f"加载了 {len(documents)} 个文档")

            if not documents:
                logger.warning(f"[Celery RAG] 目录中没有找到支持的文档：{directory_path}")

        if documents:
            logger.info(f"[Celery RAG] 分块文档：{len(documents)} 个")
            chunks = split_documents(documents, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
            tracker.update_progress(50, f"分块完成：{len(chunks)} 个")

            embeddings = get_embeddings()

            logger.info(f"[Celery RAG] 创建向量索引，{len(chunks)} 个分块")
            manager.create_index(
                name=index_name, documents=chunks, embeddings=embeddings, description=description, overwrite=overwrite
            )

            tracker.update_progress(90, "索引创建完成")

            result = {
                "index_name": index_name,
                "original_name": original_name,
                "chunk_count": len(chunks),
                "doc_count": len(documents),
            }
            tracker.mark_success(result=result)
            return {
                "status": "success",
                "index_name": index_name,
                "chunk_count": len(chunks),
                "doc_count": len(documents),
            }
        else:
            manager.create_empty_index(name=index_name, description=description)

            result = {
                "index_name": index_name,
                "original_name": original_name,
                "chunk_count": 0,
                "doc_count": 0,
            }
            tracker.mark_success(result=result)
            return {"status": "success", "index_name": index_name, "chunk_count": 0, "doc_count": 0}

    except Retry:
        raise
    except Exception as exc:
        logger.exception(f"[Celery RAG] 创建索引失败：{index_name}, 错误：")
        try:
            tracker.mark_failure(error_message=str(exc))
        except Exception:
            # tracker 更新失败不影响错误处理主流程
            logger.debug("tracker.mark_failure 失败（创建索引）")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc) from exc
        return {"status": "error", "error": str(exc)}


@shared_task(
    bind=True,
    name="rag.add_documents",
    max_retries=3,
    default_retry_delay=30,
    soft_time_limit=1800,
    autoretry_for=(ConnectionError, TimeoutError, OSError),
    retry_backoff=True,
    retry_backoff_max=60,
)
def add_documents_to_index_task(
    self,
    index_name: str,
    file_paths: list,
    user_id: int | None = None,
    task_id: str | None = None,
    original_name: str | None = None,
):
    tracker = TrackedTask(self)
    tracker.set_task_type("rag_add_docs")
    if user_id:
        tracker.set_created_by(user_id)
    if task_id:
        tracker.set_task_manager_id(task_id)

    # Redis 锁：基于 celery task_id 防止同任务并发执行
    # 与业务层稳定 ID（uuid5）共同构成幂等保障：
    #   - 锁防止并发 worker 同时写入
    #   - 稳定 ID 防止重试场景下重复写入
    celery_task_id = self.request.id
    if not _acquire_rag_add_docs_lock(celery_task_id):
        logger.warning(f"[Celery RAG] add_documents 任务 {celery_task_id} 已有其他 worker 在执行，跳过本次执行")
        tracker.mark_success(result={"skipped": True, "reason": "locked_by_another_worker"})
        return {
            "status": "skipped",
            "reason": "another_worker_holding_lock",
            "index_name": index_name,
        }

    try:
        logger.info(f"[Celery RAG] 向索引添加文档：{index_name}")
        tracker.mark_started()

        manager = get_index_manager()

        if not manager.index_exists(index_name):
            logger.error(f"[Celery RAG] 索引不存在：{index_name}")
            tracker.mark_failure(error_message="索引不存在")
            return {"status": "error", "error": "索引不存在"}

        all_documents = []
        failed_files = []
        for file_path in file_paths:
            try:
                docs = load_document(file_path)
                all_documents.extend(docs)
                logger.info(f"[Celery RAG] 加载文档：{file_path}, {len(docs)} 页")
            except Exception as e:
                logger.warning(f"[Celery RAG] 加载文档失败：{file_path}, 错误：{e}")
                failed_files.append(file_path)

        tracker.update_progress(40, f"加载了 {len(all_documents)} 个文档")

        if not all_documents:
            tracker.mark_failure(error_message="没有成功加载任何文档")
            return {"status": "error", "error": "没有成功加载任何文档", "failed_files": failed_files}

        chunks = split_documents(all_documents)
        logger.info(f"[Celery RAG] 文档分块完成：{len(chunks)} 个分块")
        tracker.update_progress(60, f"分块完成：{len(chunks)} 个")

        # 添加文档时使用索引原有维度约束，确保维度一致
        index_metadata = manager._load_metadata(index_name)
        required_dim = index_metadata.get("embedding_dimension") if index_metadata else None
        embeddings = get_embeddings(required_dimension=required_dim)
        count = manager.add_documents(index_name, chunks, embeddings)

        logger.info(f"[Celery RAG] 文档添加完成：{count} 个分块")

        result = {
            "index_name": index_name,
            "original_name": original_name,
            "chunk_count": count,
            "doc_count": len(all_documents),
            "failed_files": failed_files,
        }
        tracker.mark_success(result=result)
        return {
            "status": "success",
            "index_name": index_name,
            "chunk_count": count,
            "doc_count": len(all_documents),
            "failed_files": failed_files,
        }

    except Retry:
        raise
    except Exception as exc:
        logger.exception(f"[Celery RAG] 添加文档失败：{index_name}, 错误：")
        try:
            tracker.mark_failure(error_message=str(exc))
        except Exception:
            # tracker 更新失败不影响错误处理主流程
            logger.debug("tracker.mark_failure 失败（添加文档）")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc) from exc
        return {"status": "error", "error": str(exc)}
    finally:
        # 无论成功/失败/重试，都释放锁（重试时新 task_id 会重新获锁）
        _release_rag_add_docs_lock(celery_task_id)


@shared_task(
    bind=True,
    name="rag.delete_index",
    max_retries=2,
    default_retry_delay=60,
    soft_time_limit=600,
    autoretry_for=(ConnectionError, TimeoutError, OSError),
    retry_backoff=True,
    retry_backoff_max=60,
)
def delete_index_task(
    self,
    index_name: str,
    user_id: int | None = None,
    original_name: str | None = None,
    task_id: str | None = None,
):
    tracker = TrackedTask(self)
    tracker.set_task_type("rag_delete_index")
    if user_id:
        tracker.set_created_by(user_id)
    if task_id:
        tracker.set_task_manager_id(task_id)

    try:
        logger.info(f"[Celery RAG] 删除索引：{index_name}")
        tracker.mark_started()

        manager = get_index_manager()

        if not manager.index_exists(index_name):
            tracker.mark_success(result={"status": "not_exists"})
            return {"status": "success", "index_name": index_name, "existed": False}

        manager.delete_index(index_name)

        if user_id and original_name:
            from Django_xm.apps.chat.services.cross_app import clear_knowledge_base_selection

            clear_knowledge_base_selection(user_id=user_id, kb_name=original_name)

        logger.info(f"[Celery RAG] 索引删除完成：{index_name}")

        result = {"index_name": index_name, "original_name": original_name}
        tracker.mark_success(result=result)
        return {"status": "success", "index_name": index_name}

    except Retry:
        raise
    except Exception as exc:
        logger.exception(f"[Celery RAG] 删除索引失败：{index_name}, 错误：")
        try:
            tracker.mark_failure(error_message=str(exc))
        except Exception:
            # tracker 更新失败不影响错误处理主流程
            logger.debug("tracker.mark_failure 失败（删除索引）")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc) from exc
        return {"status": "error", "error": str(exc)}


@shared_task(
    bind=True,
    name="rag.update_index",
    max_retries=2,
    default_retry_delay=30,
    soft_time_limit=1200,
    autoretry_for=(ConnectionError, TimeoutError, OSError),
    retry_backoff=True,
    retry_backoff_max=60,
)
def update_index_task(self, index_name: str, user_id: int | None = None, task_id: str | None = None):
    tracker = TrackedTask(self)
    tracker.set_task_type("rag_update_index")
    if user_id:
        tracker.set_created_by(user_id)
    if task_id:
        tracker.set_task_manager_id(task_id)

    try:
        logger.info(f"[Celery RAG] 更新索引：{index_name}")
        tracker.mark_started()

        manager = get_index_manager()

        if not manager.index_exists(index_name):
            tracker.mark_failure(error_message="索引不存在")
            return {"status": "error", "error": "索引不存在"}

        embeddings = get_embeddings()
        manager.load_index(index_name, embeddings)

        logger.info(f"[Celery RAG] 索引更新完成：{index_name}")

        result = {"index_name": index_name}
        tracker.mark_success(result=result)
        return {"status": "success", "index_name": index_name}

    except Retry:
        raise
    except Exception as exc:
        logger.exception(f"[Celery RAG] 更新索引失败：{index_name}, 错误：")
        try:
            tracker.mark_failure(error_message=str(exc))
        except Exception:
            # tracker 更新失败不影响错误处理主流程
            logger.debug("tracker.mark_failure 失败（更新索引）")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc) from exc
        return {"status": "error", "error": str(exc)}
