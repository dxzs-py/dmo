"""
知识库业务服务层

封装知识库 CRUD、文档管理、搜索等业务逻辑，
视图层只负责请求解析和响应构建。
"""

import logging
import shutil
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any

from Django_xm.apps.cache_manager.services.cache_service import (
    VectorSearchCacheService,
    invalidate_knowledge_cache,
)
from Django_xm.apps.knowledge.config import settings as app_cfg

from ..models import Document, DocumentIndex
from ..views_utils import get_document_type, get_file_extension, get_original_index_name, get_user_index_name
from .document_service import load_document
from .embedding_service import get_embeddings
from .index_service import IndexManager
from .splitters import split_documents

logger = logging.getLogger(__name__)


def list_knowledge_bases(user) -> list[dict[str, Any]]:
    """获取用户的知识库列表，过滤已删除的索引"""
    deleted_index_names = set(
        DocumentIndex.all_objects.filter(user=user, is_deleted=True).values_list("index_name", flat=True)
    )

    manager = IndexManager()
    all_indexes = manager.list_indexes()

    user_indexes = []
    for idx_data in all_indexes:
        name = idx_data.get("name", "")
        if name.startswith(f"user_{user.id}_"):
            original_name = get_original_index_name(name)
            if original_name in deleted_index_names:
                continue
            user_indexes.append(
                {
                    "id": original_name,
                    "name": original_name,
                    "description": idx_data.get("description", ""),
                    "num_documents": idx_data.get("num_documents", 0),
                    "chunk_count": idx_data.get("num_documents", 0),
                    "created_at": idx_data.get("created_at", ""),
                    "updated_at": idx_data.get("updated_at", ""),
                }
            )

    return user_indexes


def create_knowledge_base(user, name: str, description: str = "") -> dict[str, Any]:
    """
    创建知识库

    Returns:
        包含创建结果的字典，若已存在则返回 existing=True

    Raises:
        ValueError: 知识库名称为空或已存在
    """
    if not name:
        raise ValueError("知识库名称不能为空")

    user_index_name = get_user_index_name(user, name)
    manager = IndexManager()

    if manager.index_exists(user_index_name):
        raise ValueError(f"知识库已存在: {name}")

    manager.create_empty_index(name=user_index_name, description=description)

    existing = DocumentIndex.all_objects.filter(user=user, index_name=name).first()
    if existing:
        if existing.is_deleted:
            existing.is_deleted = False
            existing.deleted_at = None
            existing.description = description
            existing.save()
            index_obj = existing
        else:
            invalidate_knowledge_cache(user_id=user.id)
            return {
                "id": name,
                "name": name,
                "description": existing.description,
                "document_count": existing.document_count,
                "chunk_count": 0,
                "existing": True,
            }
    else:
        index_obj = DocumentIndex(user=user, index_name=name, description=description)
        index_obj.save()

    invalidate_knowledge_cache(user_id=user.id)

    return {
        "id": name,
        "name": name,
        "description": description,
        "document_count": 0,
        "chunk_count": 0,
        "existing": False,
    }


def get_knowledge_base_detail(user, kb_id: str) -> dict[str, Any]:
    """
    获取知识库详情

    Raises:
        FileNotFoundError: 知识库不存在
    """
    user_index_name = get_user_index_name(user, kb_id)
    manager = IndexManager()

    if not manager.index_exists(user_index_name):
        raise FileNotFoundError(f"知识库不存在: {kb_id}")

    stats = manager.get_index_stats(user_index_name)
    metadata = manager._load_metadata(user_index_name) or {}

    return {
        "id": kb_id,
        "name": kb_id,
        "description": metadata.get("description", ""),
        "chunk_count": stats.get("num_documents", 0),
        "store_type": stats.get("store_type", ""),
        "embedding_model": stats.get("embedding_model", ""),
        "created_at": metadata.get("created_at", ""),
        "updated_at": metadata.get("updated_at", ""),
    }


def update_knowledge_base(user, kb_id: str, description: str) -> dict[str, Any]:
    """
    更新知识库描述

    Raises:
        FileNotFoundError: 知识库不存在
    """
    user_index_name = get_user_index_name(user, kb_id)
    manager = IndexManager()

    if not manager.index_exists(user_index_name):
        raise FileNotFoundError(f"知识库不存在: {kb_id}")

    metadata = manager._load_metadata(user_index_name) or {}
    metadata["description"] = description
    metadata["updated_at"] = datetime.now(UTC).isoformat()
    manager._save_metadata(user_index_name, metadata)

    index_obj = DocumentIndex.objects.filter(user=user, index_name=kb_id).first()
    if index_obj:
        index_obj.description = description
        index_obj.save()

    invalidate_knowledge_cache(user_id=user.id)

    return {
        "id": kb_id,
        "name": kb_id,
        "description": description,
    }


def delete_knowledge_base(user, kb_id: str) -> None:
    """
    删除知识库，包括向量索引、数据库记录、上传文件和缓存

    Raises:
        FileNotFoundError: 知识库不存在
    """
    user_index_name = get_user_index_name(user, kb_id)
    manager = IndexManager()

    if not manager.index_exists(user_index_name):
        raise FileNotFoundError(f"知识库不存在: {kb_id}")

    manager.delete_index(user_index_name)

    index_obj = DocumentIndex.objects.filter(user=user, index_name=kb_id).first()
    if index_obj:
        Document.objects.filter(index=index_obj).update(is_deleted=True)
        index_obj.soft_delete()

    upload_dir = Path(app_cfg.data_uploads_path) / user_index_name
    if upload_dir.exists() and upload_dir.is_dir():
        try:
            shutil.rmtree(upload_dir)
        except Exception as e:
            logger.warning(f"删除上传文件目录失败: {e}")

    invalidate_knowledge_cache(user_id=user.id, user_index_name=user_index_name)

    from Django_xm.apps.chat.services.cross_app import clear_knowledge_base_selection

    clear_knowledge_base_selection(user_id=user.id, kb_name=kb_id)


def list_documents(user, kb_id: str) -> list[dict[str, Any]]:
    """
    获取知识库下的文档列表

    Raises:
        FileNotFoundError: 知识库不存在
    """
    user_index_name = get_user_index_name(user, kb_id)
    manager = IndexManager()

    if not manager.index_exists(user_index_name):
        raise FileNotFoundError(f"知识库不存在: {kb_id}")

    upload_dir = Path(app_cfg.data_uploads_path) / user_index_name
    files = []

    if upload_dir.exists():
        for item in upload_dir.iterdir():
            if item.is_file():
                stat = item.stat()
                files.append(
                    {
                        "name": item.name,
                        "size": stat.st_size,
                        "uploaded_at": datetime.fromtimestamp(stat.st_ctime, tz=UTC).isoformat(),
                    }
                )

    logger.info(f"返回 {len(files)} 个文档给用户 {user.username}")
    return files


def upload_documents(user, kb_id: str, uploaded_files: list) -> dict[str, Any]:
    """
    上传文档到知识库：保存文件、加载文档、分块、向量化、更新索引

    Args:
        user: 用户对象
        kb_id: 知识库名称
        uploaded_files: Django 上传文件对象列表

    Returns:
        包含上传结果的字典

    Raises:
        FileNotFoundError: 知识库不存在
        ValueError: 文件列表为空或无法提取内容
    """
    user_index_name = get_user_index_name(user, kb_id)
    manager = IndexManager()

    if not manager.index_exists(user_index_name):
        raise FileNotFoundError(f"知识库不存在: {kb_id}")

    if not uploaded_files:
        raise ValueError("请选择要上传的文件")

    upload_dir = Path(app_cfg.data_uploads_path) / user_index_name
    upload_dir.mkdir(parents=True, exist_ok=True)

    all_documents = []
    saved_files = []

    for uploaded_file in uploaded_files:
        file_path = upload_dir / uploaded_file.name
        with open(file_path, "wb") as f:
            for chunk in uploaded_file.chunks():
                f.write(chunk)

        docs = load_document(str(file_path))
        all_documents.extend(docs)
        saved_files.append(
            {
                "name": uploaded_file.name,
                "size": file_path.stat().st_size,
            }
        )

    if not all_documents:
        raise ValueError("未能从上传文件中提取内容")

    chunks = split_documents(all_documents)
    # 上传文档时使用索引原有维度约束（如有），确保维度一致
    index_metadata = manager._load_metadata(user_index_name)
    required_dim = index_metadata.get("embedding_dimension") if index_metadata else None
    embeddings = get_embeddings(required_dimension=required_dim)

    count = manager.add_documents(user_index_name, chunks, embeddings)

    # 检查 Embedding 降级事件
    fallback_info = None
    if hasattr(embeddings, "get_fallback_events") and hasattr(embeddings, "get_active_provider_id"):
        events = embeddings.get_fallback_events()
        if events:
            actual_provider = embeddings.get_active_provider_id()
            fallback_info = {
                "events": events,
                "actual_provider": actual_provider,
            }
            # 自动更新 SystemConfig 为实际使用的 provider
            if actual_provider:
                try:
                    from Django_xm.apps.knowledge.config import SystemConfig

                    current_config = SystemConfig.get_value("embedding_provider", {})
                    if current_config.get("provider_id") != actual_provider:
                        current_config["provider_id"] = actual_provider
                        SystemConfig.set_value("embedding_provider", current_config)
                        logger.info(f"Embedding 降级：SystemConfig embedding_provider 已更新为 {actual_provider}")
                except Exception as e:
                    logger.warning(f"Embedding 降级：更新 SystemConfig 失败: {e}")

    index_obj = DocumentIndex.objects.filter(user=user, index_name=kb_id).first()
    if index_obj:
        for file_info in saved_files:
            ext = get_file_extension(file_info["name"])
            doc_type = get_document_type(ext)
            doc = Document(
                index=index_obj,
                filename=file_info["name"],
                file_path=str(upload_dir / file_info["name"]),
                file_type=doc_type,
                file_size=file_info["size"],
                chunk_count=len(chunks) // len(saved_files) if saved_files else 0,
            )
            doc.save()
        index_obj.document_count += len(saved_files)
        index_obj.save()

    invalidate_knowledge_cache(user_id=user.id, user_index_name=user_index_name)

    return {
        "documents_uploaded": len(saved_files),
        "chunks_created": count,
        "files": saved_files,
        "fallback_info": fallback_info,
    }


def delete_document(user, kb_id: str, filename: str) -> dict[str, Any]:
    """
    从知识库删除文档：移除向量索引、删除文件、更新数据库记录

    Raises:
        FileNotFoundError: 知识库或文件不存在
    """
    user_index_name = get_user_index_name(user, kb_id)
    manager = IndexManager()

    if not manager.index_exists(user_index_name):
        raise FileNotFoundError(f"知识库不存在: {kb_id}")

    upload_dir = Path(app_cfg.data_uploads_path) / user_index_name
    file_path = upload_dir / filename

    if not file_path.exists() or not file_path.is_file():
        raise FileNotFoundError(f"文件不存在: {filename}")

    logger.info(f"删除文档: {filename} 从 {user_index_name}")

    try:
        index_metadata = manager._load_metadata(user_index_name)
        required_dim = index_metadata.get("embedding_dimension") if index_metadata else None
        embeddings = get_embeddings(required_dimension=required_dim)
        removed = manager.remove_documents_by_filename(user_index_name, embeddings, filename)
        logger.info(f"从向量索引中删除 {removed} 个文档块")
    except Exception as ve:
        logger.warning(f"从向量索引中删除文档失败: {ve}")

    index_obj = DocumentIndex.objects.filter(user=user, index_name=kb_id).first()
    if index_obj:
        doc = Document.objects.filter(index=index_obj, filename=filename).first()
        if doc:
            doc.soft_delete()
        index_obj.document_count = max(0, index_obj.document_count - 1)
        index_obj.save()

    file_path.unlink()
    logger.info(f"文件已从磁盘删除: {file_path}")

    metadata = manager._load_metadata(user_index_name) or {}
    metadata["updated_at"] = datetime.now(UTC).isoformat()
    if "num_documents" in metadata:
        metadata["num_documents"] = max(0, metadata["num_documents"] - 1)
    manager._save_metadata(user_index_name, metadata)

    invalidate_knowledge_cache(user_id=user.id, user_index_name=user_index_name)

    return {"message": f"文件已删除: {filename}"}


def search_knowledge_base(user, kb_id: str, query: str, top_k: int = 5) -> list[dict[str, Any]]:
    """
    在知识库中搜索

    Raises:
        ValueError: 查询内容为空
        FileNotFoundError: 知识库不存在
    """
    if not query:
        raise ValueError("查询内容不能为空")

    user_index_name = get_user_index_name(user, kb_id)
    manager = IndexManager()

    if not manager.index_exists(user_index_name):
        raise FileNotFoundError(f"知识库不存在: {kb_id}")

    from ..vector_store import search_vector_store

    index_metadata = manager._load_metadata(user_index_name)
    required_dim = index_metadata.get("embedding_dimension") if index_metadata else None
    embeddings = get_embeddings(required_dimension=required_dim)
    vector_store = manager.load_index(user_index_name, embeddings)

    cached_results = VectorSearchCacheService.get_cached_search(query, user_index_name, top_k)
    if cached_results is not None:
        logger.info("向量搜索缓存命中")
        return cached_results

    try:
        results = search_vector_store(vector_store, query, k=top_k)
    except Exception as e:
        # 维度不匹配时，可能是 SQLAlchemy MetaData 缓存了旧的列定义
        # 需要清理 MetaData、释放连接后重试
        err_msg = str(e)
        if "different vector dimensions" in err_msg:
            logger.warning(f"检测到维度不匹配，清理缓存后重试: {err_msg}")
            # 释放旧 VectorStore 的 SQLAlchemy engine 连接池
            if hasattr(vector_store, "_engine") and vector_store._engine:
                vector_store._engine.dispose()
            # 关闭 Django 数据库连接
            from django.db import connections

            connections.close_all()
            # 清理 SQLAlchemy MetaData 缓存（根因：ALTER TABLE 后列类型缓存未更新）
            try:
                from langchain_postgres.vectorstores import Base

                Base.metadata.clear()
            except Exception:  # noqa: S110  # cleanup, 缓存清理失败不影响重试主流程
                pass
            # 清理 IndexManager 缓存
            IndexManager._cache.clear()
            vector_store = manager.load_index(user_index_name, embeddings)
            results = search_vector_store(vector_store, query, k=top_k)
        else:
            raise
    search_results = []
    for doc, score in results:
        item = {
            "content": doc.page_content,
            "source": doc.metadata.get("source", ""),
            "score": float(score) if score is not None else 0,
        }
        search_results.append(item)
    VectorSearchCacheService.cache_search_result(query, search_results, user_index_name, top_k)

    return search_results


def rebuild_index_from_source_files(
    user,
    kb_name: str,
    provider_id: str | None = None,
    embeddings=None,
) -> None:
    """从原始上传文件重建知识库索引

    当向量数据丢失或内存备份恢复失败时，从磁盘上的原始文件重新构建索引。
    原始文件路径从 Document.file_path 获取。

    Args:
        user: 用户对象
        kb_name: 索引全名（如 user_1_test2）
        provider_id: 可选，指定 embedding provider
        embeddings: 可选，已创建的 Embeddings 实例
    """
    # 从索引全名提取短名（user_1_test2 → test2）
    original_name = get_original_index_name(kb_name)
    index_obj = DocumentIndex.objects.filter(user=user, index_name=original_name).first()
    if not index_obj:
        raise FileNotFoundError(f"知识库不存在: {kb_name}")

    docs = Document.objects.filter(index=index_obj, is_deleted=False)

    # 逐个从原始文件加载文档
    all_documents = []
    for doc_record in docs:
        file_path = Path(doc_record.file_path)
        if not file_path.exists():
            logger.warning(f"原始文件不存在: {file_path}")
            continue
        try:
            loaded = load_document(str(file_path))
            all_documents.extend(loaded)
        except Exception as e:
            logger.warning(f"加载原始文件失败: {file_path} - {e}")

    if not all_documents:
        raise ValueError(f"无法从原始文件加载任何文档: {kb_name}")

    # 分块
    chunks = split_documents(all_documents)

    # 获取 Embeddings
    if embeddings is None:
        embeddings = get_embeddings(
            preferred_provider=provider_id,
            use_cache=False,
        )

    # 创建索引（overwrite=True 以防索引残留）
    manager = IndexManager()
    manager.create_index(
        name=kb_name,
        documents=chunks,
        embeddings=embeddings,
        description=index_obj.description or "",
        store_type="pgvector",
        overwrite=True,
    )

    # 清除缓存
    invalidate_knowledge_cache(user_id=user.id, user_index_name=kb_name)
