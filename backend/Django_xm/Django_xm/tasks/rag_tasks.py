"""
RAG 相关的 Celery 异步任务
包括索引创建、文档处理等耗时操作
"""
import logging
from pathlib import Path
from celery import shared_task
from celery.exceptions import Retry

from Django_xm.apps.knowledge.services.index_service import IndexManager
from Django_xm.apps.knowledge.services.document_service import load_document, load_documents_from_directory
from Django_xm.apps.knowledge.services.splitters import split_documents
from Django_xm.apps.knowledge.services.embedding_service import get_embeddings
from Django_xm.tasks.base import TrackedTask

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name='rag.create_index',
    max_retries=3,
    default_retry_delay=30,
    soft_time_limit=3600,
)
def create_index_task(
    self,
    index_name: str,
    directory_path: str = None,
    description: str = '',
    chunk_size: int = None,
    chunk_overlap: int = None,
    user_id: int = None,
    task_id: str = None,
    original_name: str = None,
    overwrite: bool = False,
):
    tracker = TrackedTask(self)
    tracker.set_task_type('rag_index')
    if user_id:
        tracker.set_created_by(user_id)
    if task_id:
        tracker.set_task_manager_id(task_id)

    try:
        logger.info(f"[Celery RAG] 开始创建索引：{index_name}")
        tracker.mark_started()

        manager = IndexManager()

        if manager.index_exists(index_name):
            if overwrite:
                logger.info(f"[Celery RAG] 覆盖已有索引：{index_name}")
                manager.delete_index(index_name)
            else:
                logger.warning(f"[Celery RAG] 索引已存在：{index_name}")
                tracker.mark_success(result={'index_name': index_name, 'exists': True})
                return {'status': 'success', 'index_name': index_name, 'chunk_count': 0, 'doc_count': 0, 'exists': True}

        documents = []
        if directory_path:
            dir_path = Path(directory_path)
            if not dir_path.exists():
                logger.error(f"[Celery RAG] 目录不存在：{directory_path}")
                tracker.mark_failure(error_message=f'目录不存在：{directory_path}')
                return {'status': 'error', 'error': f'目录不存在：{directory_path}'}

            logger.info(f"[Celery RAG] 加载文档目录：{directory_path}")
            documents = load_documents_from_directory(directory_path)
            tracker.update_progress(30, f'加载了 {len(documents)} 个文档')

            if not documents:
                logger.warning(f"[Celery RAG] 目录中没有找到支持的文档：{directory_path}")

        if documents:
            logger.info(f"[Celery RAG] 分块文档：{len(documents)} 个")
            chunks = split_documents(
                documents,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap
            )
            tracker.update_progress(50, f'分块完成：{len(chunks)} 个')

            embeddings = get_embeddings()

            logger.info(f"[Celery RAG] 创建向量索引，{len(chunks)} 个分块")
            manager.create_index(
                name=index_name,
                documents=chunks,
                embeddings=embeddings,
                description=description,
                overwrite=overwrite
            )

            tracker.update_progress(90, '索引创建完成')

            result = {
                'index_name': index_name,
                'original_name': original_name,
                'chunk_count': len(chunks),
                'doc_count': len(documents),
            }
            tracker.mark_success(result=result)
            return {'status': 'success', 'index_name': index_name, 'chunk_count': len(chunks), 'doc_count': len(documents)}
        else:
            manager.create_empty_index(
                name=index_name,
                description=description
            )

            result = {
                'index_name': index_name,
                'original_name': original_name,
                'chunk_count': 0,
                'doc_count': 0,
            }
            tracker.mark_success(result=result)
            return {'status': 'success', 'index_name': index_name, 'chunk_count': 0, 'doc_count': 0}

    except Retry:
        raise
    except Exception as exc:
        logger.error(f"[Celery RAG] 创建索引失败：{index_name}, 错误：{exc}", exc_info=True)
        try:
            tracker.mark_failure(error_message=str(exc))
        except Exception:
            pass
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        return {'status': 'error', 'error': str(exc)}


@shared_task(
    bind=True,
    name='rag.add_documents',
    max_retries=3,
    default_retry_delay=30,
    soft_time_limit=1800,
)
def add_documents_to_index_task(
    self,
    index_name: str,
    file_paths: list,
    user_id: int = None,
    task_id: str = None,
    original_name: str = None,
):
    tracker = TrackedTask(self)
    tracker.set_task_type('rag_add_docs')
    if user_id:
        tracker.set_created_by(user_id)
    if task_id:
        tracker.set_task_manager_id(task_id)

    try:
        logger.info(f"[Celery RAG] 向索引添加文档：{index_name}")
        tracker.mark_started()

        manager = IndexManager()

        if not manager.index_exists(index_name):
            logger.error(f"[Celery RAG] 索引不存在：{index_name}")
            tracker.mark_failure(error_message='索引不存在')
            return {'status': 'error', 'error': '索引不存在'}

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

        tracker.update_progress(40, f'加载了 {len(all_documents)} 个文档')

        if not all_documents:
            tracker.mark_failure(error_message='没有成功加载任何文档')
            return {'status': 'error', 'error': '没有成功加载任何文档', 'failed_files': failed_files}

        chunks = split_documents(all_documents)
        logger.info(f"[Celery RAG] 文档分块完成：{len(chunks)} 个分块")
        tracker.update_progress(60, f'分块完成：{len(chunks)} 个')

        embeddings = get_embeddings()
        count = manager.add_documents(index_name, chunks, embeddings)

        logger.info(f"[Celery RAG] 文档添加完成：{count} 个分块")

        result = {
            'index_name': index_name,
            'original_name': original_name,
            'chunk_count': count,
            'doc_count': len(all_documents),
            'failed_files': failed_files,
        }
        tracker.mark_success(result=result)
        return {'status': 'success', 'index_name': index_name, 'chunk_count': count, 'doc_count': len(all_documents), 'failed_files': failed_files}

    except Retry:
        raise
    except Exception as exc:
        logger.error(f"[Celery RAG] 添加文档失败：{index_name}, 错误：{exc}", exc_info=True)
        try:
            tracker.mark_failure(error_message=str(exc))
        except Exception:
            pass
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        return {'status': 'error', 'error': str(exc)}


@shared_task(
    bind=True,
    name='rag.delete_index',
    max_retries=2,
    default_retry_delay=60,
    soft_time_limit=600,
)
def delete_index_task(
    self,
    index_name: str,
    user_id: int = None,
    original_name: str = None,
    task_id: str = None,
):
    tracker = TrackedTask(self)
    tracker.set_task_type('rag_delete_index')
    if user_id:
        tracker.set_created_by(user_id)
    if task_id:
        tracker.set_task_manager_id(task_id)

    try:
        logger.info(f"[Celery RAG] 删除索引：{index_name}")
        tracker.mark_started()

        manager = IndexManager()

        if not manager.index_exists(index_name):
            tracker.mark_success(result={'status': 'not_exists'})
            return {'status': 'success', 'index_name': index_name, 'existed': False}

        manager.delete_index(index_name)

        if user_id and original_name:
            from Django_xm.apps.chat.models import ChatSession
            ChatSession.objects.filter(
                user_id=user_id, selected_knowledge_base=original_name
            ).update(selected_knowledge_base='')

        logger.info(f"[Celery RAG] 索引删除完成：{index_name}")

        result = {'index_name': index_name, 'original_name': original_name}
        tracker.mark_success(result=result)
        return {'status': 'success', 'index_name': index_name}

    except Retry:
        raise
    except Exception as exc:
        logger.error(f"[Celery RAG] 删除索引失败：{index_name}, 错误：{exc}", exc_info=True)
        try:
            tracker.mark_failure(error_message=str(exc))
        except Exception:
            pass
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        return {'status': 'error', 'error': str(exc)}


@shared_task(
    bind=True,
    name='rag.update_index',
    max_retries=2,
    default_retry_delay=30,
    soft_time_limit=1200,
)
def update_index_task(self, index_name: str, user_id: int = None, task_id: str = None):
    tracker = TrackedTask(self)
    tracker.set_task_type('rag_update_index')
    if user_id:
        tracker.set_created_by(user_id)
    if task_id:
        tracker.set_task_manager_id(task_id)

    try:
        logger.info(f"[Celery RAG] 更新索引：{index_name}")
        tracker.mark_started()

        manager = IndexManager()

        if not manager.index_exists(index_name):
            tracker.mark_failure(error_message='索引不存在')
            return {'status': 'error', 'error': '索引不存在'}

        embeddings = get_embeddings()
        manager.load_index(index_name, embeddings)

        logger.info(f"[Celery RAG] 索引更新完成：{index_name}")

        result = {'index_name': index_name}
        tracker.mark_success(result=result)
        return {'status': 'success', 'index_name': index_name}

    except Retry:
        raise
    except Exception as exc:
        logger.error(f"[Celery RAG] 更新索引失败：{index_name}, 错误：{exc}", exc_info=True)
        try:
            tracker.mark_failure(error_message=str(exc))
        except Exception:
            pass
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        return {'status': 'error', 'error': str(exc)}
