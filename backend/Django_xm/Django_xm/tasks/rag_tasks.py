"""
RAG 相关的 Celery 异步任务
包括索引创建、文档处理等耗时操作
"""
import logging
import shutil
from datetime import datetime
from pathlib import Path
from celery import shared_task
from django.conf import settings as django_settings

from Django_xm.apps.knowledge.services.index_service import IndexManager
from Django_xm.apps.knowledge.services.document_service import load_document, load_documents_from_directory
from Django_xm.apps.knowledge.services.splitters import split_documents
from Django_xm.apps.knowledge.services.embedding_service import get_embeddings
from Django_xm.apps.knowledge.models import DocumentIndex, Document
from Django_xm.apps.common.task_manager import (
    get_task_manager,
    TaskStatus,
    update_task_status,
)
from Django_xm.apps.ai_engine.config import settings as app_cfg
from Django_xm.tasks.base import TrackedTask

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
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
):
    """
    异步创建索引任务
    
    Args:
        index_name: 索引名称
        directory_path: 文档目录路径（可选）
        description: 索引描述
        chunk_size: 分块大小
        chunk_overlap: 分块重叠
        user_id: 用户ID（可选，用于用户隔离）
    """
    try:
        logger.info(f"[Celery RAG] 开始创建索引：{index_name}")
        
        tracker = TrackedTask(self)
        tracker.set_task_type('rag_index')
        tracker.mark_started()
        
        manager = IndexManager()
        
        # 检查索引是否已存在
        if manager.index_exists(index_name):
            logger.warning(f"[Celery RAG] 索引已存在：{index_name}")
            tracker.mark_success(result={'status': 'exists', 'index_name': index_name})
            return {'status': 'exists', 'index_name': index_name}
        
        documents = []
        if directory_path:
            dir_path = Path(directory_path)
            if not dir_path.exists():
                logger.error(f"[Celery RAG] 目录不存在：{directory_path}")
                return {'status': 'error', 'error': '目录不存在'}
            
            logger.info(f"[Celery RAG] 加载文档目录：{directory_path}")
            documents = load_documents_from_directory(directory_path)
            tracker.update_progress(30, f'加载了 {len(documents)} 个文档')
            
            if not documents:
                logger.warning(f"[Celery RAG] 目录中没有找到支持的文档：{directory_path}")
                return {'status': 'warning', 'message': '没有找到文档，创建空索引'}
        
        # 创建索引
        if documents:
            logger.info(f"[Celery RAG] 分块文档：{len(documents)} 个")
            chunks = split_documents(
                documents,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap
            )
            
            embeddings = get_embeddings()
            
            logger.info(f"[Celery RAG] 创建向量索引，{len(chunks)} 个分块")
            vector_store = manager.create_index(
                name=index_name,
                documents=chunks,
                embeddings=embeddings,
                description=description,
                overwrite=False
            )
            
            tracker.mark_success(result={
                'index_name': index_name,
                'chunk_count': len(chunks),
                'doc_count': len(documents)
            })
            
            return {
                'status': 'success',
                'index_name': index_name,
                'chunk_count': len(chunks),
                'doc_count': len(documents)
            }
        else:
            manager.create_empty_index(
                name=index_name,
                description=description
            )
            
            tracker.mark_success(result={
                'index_name': index_name,
                'chunk_count': 0,
                'doc_count': 0
            })
            
            return {
                'status': 'success',
                'index_name': index_name,
                'chunk_count': 0,
                'doc_count': 0
            }
            
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
    max_retries=3,
    default_retry_delay=30,
    soft_time_limit=1800,
)
def add_documents_to_index_task(
    self,
    index_name: str,
    file_paths: list,
    user_id: int = None,
):
    tracker = TrackedTask(self)
    tracker.set_task_type('rag_add_docs')
    try:
        logger.info(f"[Celery RAG] 向索引添加文档：{index_name}")
        tracker.mark_started()
        
        manager = IndexManager()
        
        if not manager.index_exists(index_name):
            logger.error(f"[Celery RAG] 索引不存在：{index_name}")
            tracker.mark_failure(error_message='索引不存在')
            return {'status': 'error', 'error': '索引不存在'}
        
        # 加载文档
        all_documents = []
        for file_path in file_paths:
            try:
                docs = load_document(file_path)
                all_documents.extend(docs)
                logger.info(f"[Celery RAG] 加载文档：{file_path}, {len(docs)} 页")
            except Exception as e:
                logger.warning(f"[Celery RAG] 加载文档失败：{file_path}, 错误：{e}")
        
        if not all_documents:
            return {'status': 'warning', 'message': '没有成功加载任何文档'}
        
        # 分块文档
        chunks = split_documents(all_documents)
        logger.info(f"[Celery RAG] 文档分块完成：{len(chunks)} 个分块")
        
        # 添加到索引
        embeddings = get_embeddings()
        count = manager.add_documents(index_name, chunks, embeddings)
        
        logger.info(f"[Celery RAG] 文档添加完成：{count} 个分块")
        
        tracker.mark_success(result={
            'index_name': index_name,
            'chunk_count': count,
            'doc_count': len(all_documents)
        })
        
        return {
            'status': 'success',
            'index_name': index_name,
            'chunk_count': count,
            'doc_count': len(all_documents)
        }
        
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
    max_retries=2,
    default_retry_delay=60,
    soft_time_limit=600,
)
def delete_index_task(self, index_name: str, user_id: int = None):
    tracker = TrackedTask(self)
    tracker.set_task_type('rag_delete_index')
    try:
        logger.info(f"[Celery RAG] 删除索引：{index_name}")
        tracker.mark_started()
        
        manager = IndexManager()
        
        if not manager.index_exists(index_name):
            tracker.mark_success(result={'status': 'not_exists'})
            return {'status': 'not_exists', 'index_name': index_name}
        
        manager.delete_index(index_name)
        
        logger.info(f"[Celery RAG] 索引删除完成：{index_name}")
        tracker.mark_success(result={'index_name': index_name})
        
        return {
            'status': 'success',
            'index_name': index_name
        }
        
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
    max_retries=2,
    default_retry_delay=30,
    soft_time_limit=1200,
)
def update_index_task(self, index_name: str, user_id: int = None):
    tracker = TrackedTask(self)
    tracker.set_task_type('rag_index')
    try:
        logger.info(f"[Celery RAG] 更新索引：{index_name}")
        tracker.mark_started()
        
        manager = IndexManager()
        
        if not manager.index_exists(index_name):
            tracker.mark_failure(error_message='索引不存在')
            return {'status': 'not_exists', 'index_name': index_name}
        
        embeddings = get_embeddings()
        vector_store = manager.load_index(index_name, embeddings)
        
        logger.info(f"[Celery RAG] 索引更新完成：{index_name}")
        tracker.mark_success(result={'index_name': index_name})
        
        return {
            'status': 'success',
            'index_name': index_name
        }
        
    except Exception as exc:
        logger.error(f"[Celery RAG] 更新索引失败：{index_name}, 错误：{exc}", exc_info=True)
        try:
            tracker.mark_failure(error_message=str(exc))
        except Exception:
            pass
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        return {'status': 'error', 'error': str(exc)}
