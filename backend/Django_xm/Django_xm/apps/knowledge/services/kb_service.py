"""
知识库业务服务层

封装知识库 CRUD、文档管理、搜索等业务逻辑，
视图层只负责请求解析和响应构建。
"""

import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from django.db.models import Count

from Django_xm.apps.cache_manager.services.cache_service import (
    VectorSearchCacheService,
    invalidate_knowledge_cache,
)
from Django_xm.apps.knowledge.config import settings as app_cfg

from ..exceptions import KnowledgeBaseAlreadyExistsError
from ..models import Document, IndexMetadata
from ..views_utils import get_document_type, get_file_extension, get_original_index_name, get_user_index_name
from .document_service import load_document
from .embedding_service import get_embeddings
from .index_service import IndexManager
from .splitters import split_documents

logger = logging.getLogger(__name__)


def list_knowledge_bases(user) -> list[dict[str, Any]]:
    """获取用户的知识库列表（IndexMetadata 单一来源，objects 自动滤软删墓碑）。

    计数语义（dj-13 修正）：document_count=真实文件数（Document 表派生），
    chunk_count=向量块数（IndexMetadata.num_documents 镜像）。
    """
    prefix = f"user_{user.id}_"
    records = list(
        IndexMetadata.objects.filter(user=user, name__startswith=prefix).only(
            "id", "name", "description", "num_documents", "created_at", "updated_at"
        )
    )

    # 文件数单一权威：Document 表一条聚合查询派生
    doc_counts = {
        row["index_id"]: row["cnt"]
        for row in Document.objects.filter(
            index_id__in=[record.id for record in records], is_deleted=False
        ).annotate(cnt=Count("id")).values("index_id", "cnt")
    }

    user_indexes = []
    for record in records:
        original_name = get_original_index_name(record.name)
        user_indexes.append(
            {
                "id": original_name,
                "name": original_name,
                "description": record.description,
                "document_count": doc_counts.get(record.id, 0),
                "chunk_count": record.num_documents,
                "created_at": record.created_at.isoformat() if record.created_at else "",
                "updated_at": record.updated_at.isoformat() if record.updated_at else "",
            }
        )

    return user_indexes


def create_knowledge_base(user, name: str, description: str = "") -> dict[str, Any]:
    """
    创建知识库（IndexMetadata.all_objects 为查重权威）

    分支语义：
    - 活跃实体行 → existing=True 透传（幂等创建，不触碰向量索引）
    - 软删墓碑 → 恢复（is_deleted/deleted_at 复位、status 重置 empty、
      num_documents 清零、description 更新）+ 重建向量索引，existing=False
    - 无实体行 + 向量残留 → KnowledgeBaseAlreadyExistsError（异常态，语义保留）
    - 全新 → 显式落 user 归属后创建向量索引

    Returns:
        包含创建结果的字典，若已存在则返回 existing=True

    Raises:
        ValueError: 知识库名称为空
        KnowledgeBaseAlreadyExistsError: 向量索引残留（无实体行的同名向量集合）
    """
    if not name:
        raise ValueError("知识库名称不能为空")

    user_index_name = get_user_index_name(user, name)
    manager = IndexManager()

    existing_meta = IndexMetadata.all_objects.filter(name=user_index_name).first()

    if existing_meta and not existing_meta.is_deleted:
        # 活跃实体行：existing=True 透传（计数派生，与列表语义一致）
        return {
            "id": name,
            "name": name,
            "description": existing_meta.description,
            "document_count": Document.objects.filter(index=existing_meta, is_deleted=False).count(),
            "chunk_count": existing_meta.num_documents,
            "existing": True,
        }

    # 无活跃实体行时校验向量残留（软删墓碑 + 向量删除失败同名重建同样拒绝）
    if manager.index_exists(user_index_name):
        raise KnowledgeBaseAlreadyExistsError(f"知识库已存在: {name}", name)

    if existing_meta:
        # 命中软删墓碑：恢复实体（向量索引已随删除流程清理，此处重建）
        existing_meta.is_deleted = False
        existing_meta.deleted_at = None
        existing_meta.user = user
        existing_meta.description = description
        existing_meta.status = IndexMetadata.IndexStatus.EMPTY
        existing_meta.num_documents = 0
        existing_meta.save()
    else:
        # 全新实体：显式落 user 归属（IndexManager._save_metadata_to_db 无 user 上下文，
        # 该根源缺陷在此修复；后续 create_empty_index 的 update_or_create 只补元数据）
        IndexMetadata.objects.create(user=user, name=user_index_name, description=description)

    manager.create_empty_index(name=user_index_name, description=description)

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
    获取知识库详情（IndexMetadata 实体 + 向量库 stats）

    Raises:
        FileNotFoundError: 知识库不存在
    """
    user_index_name = get_user_index_name(user, kb_id)
    index_meta = IndexMetadata.objects.filter(name=user_index_name).first()

    if not index_meta:
        raise FileNotFoundError(f"知识库不存在: {kb_id}")

    manager = IndexManager()
    stats = manager.get_index_stats(user_index_name)

    return {
        "id": kb_id,
        "name": kb_id,
        "description": index_meta.description,
        "document_count": Document.objects.filter(index=index_meta, is_deleted=False).count(),
        "chunk_count": stats.get("num_documents", 0),
        "store_type": stats.get("store_type", ""),
        "embedding_model": stats.get("embedding_model", ""),
        "created_at": index_meta.created_at.isoformat() if index_meta.created_at else "",
        "updated_at": index_meta.updated_at.isoformat() if index_meta.updated_at else "",
    }


def update_knowledge_base(user, kb_id: str, description: str) -> dict[str, Any]:
    """
    更新知识库描述（IndexMetadata 单点写，pgvector 元数据即库表）

    Raises:
        FileNotFoundError: 知识库不存在
    """
    user_index_name = get_user_index_name(user, kb_id)
    index_meta = IndexMetadata.objects.filter(name=user_index_name).first()

    if not index_meta:
        raise FileNotFoundError(f"知识库不存在: {kb_id}")

    index_meta.description = description
    index_meta.save(update_fields=["description", "updated_at"])

    invalidate_knowledge_cache(user_id=user.id)

    return {
        "id": kb_id,
        "name": kb_id,
        "description": description,
    }


def delete_knowledge_base(user, kb_id: str) -> None:
    """
    删除知识库：向量索引/上传目录硬删，IndexMetadata 实体与 Document 行软删（墓碑）

    Raises:
        FileNotFoundError: 知识库不存在
    """
    user_index_name = get_user_index_name(user, kb_id)
    index_meta = IndexMetadata.objects.filter(name=user_index_name).first()

    if not index_meta:
        raise FileNotFoundError(f"知识库不存在: {kb_id}")

    manager = IndexManager()
    manager.delete_index(user_index_name)

    Document.objects.filter(index=index_meta).update(is_deleted=True)
    index_meta.soft_delete()

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


def save_uploaded_files(user, kb_id: str, uploaded_files: list) -> list[dict[str, Any]]:
    """保存上传文件到知识库 upload 目录（轻量同步操作）。

    仅负责文件落盘，文档加载/分块/向量化由 process_uploaded_documents 处理
    （Celery 异步任务中执行），避免长耗时阻塞 HTTP 请求。

    Args:
        user: 用户对象
        kb_id: 知识库名称
        uploaded_files: Django 上传文件对象列表

    Returns:
        已保存文件的列表 [{"name", "size"}, ...]

    Raises:
        FileNotFoundError: 知识库不存在
        ValueError: 文件列表为空
    """
    user_index_name = get_user_index_name(user, kb_id)
    manager = IndexManager()

    if not manager.index_exists(user_index_name):
        raise FileNotFoundError(f"知识库不存在: {kb_id}")

    if not uploaded_files:
        raise ValueError("请选择要上传的文件")

    upload_dir = Path(app_cfg.data_uploads_path) / user_index_name
    upload_dir.mkdir(parents=True, exist_ok=True)

    saved_files = []
    for uploaded_file in uploaded_files:
        file_path = upload_dir / uploaded_file.name
        with open(file_path, "wb") as f:
            for chunk in uploaded_file.chunks():
                f.write(chunk)
        saved_files.append(
            {
                "name": uploaded_file.name,
                "size": file_path.stat().st_size,
            }
        )

    return saved_files


def process_uploaded_documents(
    user,
    kb_id: str,
    file_names: list,
    progress_callback=None,
) -> dict[str, Any]:
    """处理已落盘的上传文件：加载文档、分块、向量化、更新索引。

    由 Celery 异步任务调用（upload_documents_task），也可被同步 upload_documents 复用。

    Args:
        user: 用户对象
        kb_id: 知识库名称
        file_names: upload 目录下的文件名列表（save_uploaded_files 已落盘）
        progress_callback: 可选进度回调 callable(progress: int, message: str)

    Returns:
        包含上传结果的字典

    Raises:
        FileNotFoundError: 知识库不存在
        ValueError: 无法提取内容
    """
    user_index_name = get_user_index_name(user, kb_id)
    manager = IndexManager()
    index_meta = IndexMetadata.objects.filter(name=user_index_name).first()

    if not index_meta:
        raise FileNotFoundError(f"知识库不存在: {kb_id}")

    upload_dir = Path(app_cfg.data_uploads_path) / user_index_name

    all_documents = []
    saved_files = []
    for name in file_names:
        file_path = upload_dir / name
        if not file_path.is_file():
            raise ValueError(f"上传文件不存在: {name}")
        docs = load_document(str(file_path))
        all_documents.extend(docs)
        saved_files.append(
            {
                "name": name,
                "size": file_path.stat().st_size,
            }
        )

    if progress_callback:
        progress_callback(30, f"文档加载完成：{len(saved_files)} 个文件")

    if not all_documents:
        raise ValueError("未能从上传文件中提取内容")

    chunks = split_documents(all_documents)

    if progress_callback:
        progress_callback(60, f"分块完成：{len(chunks)} 个文本块")

    # 上传文档时使用索引原有维度约束（如有），确保维度一致
    index_metadata = manager._load_metadata(user_index_name)
    required_dim = index_metadata.get("embedding_dimension") if index_metadata else None
    embeddings = get_embeddings(required_dimension=required_dim)

    if progress_callback:
        progress_callback(75, "正在向量化并写入索引")

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

    for file_info in saved_files:
        ext = get_file_extension(file_info["name"])
        doc_type = get_document_type(ext)
        doc = Document(
            index=index_meta,
            filename=file_info["name"],
            file_path=str(upload_dir / file_info["name"]),
            file_type=doc_type,
            file_size=file_info["size"],
            chunk_count=len(chunks) // len(saved_files) if saved_files else 0,
        )
        doc.save()

    invalidate_knowledge_cache(user_id=user.id, user_index_name=user_index_name)

    if progress_callback:
        progress_callback(100, "上传完成")

    return {
        "documents_uploaded": len(saved_files),
        "chunks_created": count,
        "files": saved_files,
        "fallback_info": fallback_info,
    }


def upload_documents(user, kb_id: str, uploaded_files: list) -> dict[str, Any]:
    """
    上传文档到知识库（同步版本）：保存文件、加载文档、分块、向量化、更新索引

    由 save_uploaded_files + process_uploaded_documents 组合实现。
    视图层默认走异步 Celery 任务（见 views_kb.KnowledgeBaseDocumentListView.post），
    本函数保留供内部/测试同步调用。

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
    saved_files = save_uploaded_files(user, kb_id, uploaded_files)
    file_names = [f["name"] for f in saved_files]
    return process_uploaded_documents(user, kb_id, file_names)


def delete_document(user, kb_id: str, filename: str) -> dict[str, Any]:
    """
    从知识库删除文档：移除向量索引、删除文件、更新数据库记录

    Raises:
        FileNotFoundError: 知识库或文件不存在
    """
    user_index_name = get_user_index_name(user, kb_id)
    manager = IndexManager()
    index_meta = IndexMetadata.objects.filter(name=user_index_name).first()

    if not index_meta:
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

    doc = Document.objects.filter(index=index_meta, filename=filename).first()
    if doc:
        doc.soft_delete()

    file_path.unlink()
    logger.info(f"文件已从磁盘删除: {file_path}")

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
            # 统一重置 PGVector 缓存（clear metadata + 重置 _classes + 重建）
            # 修复旧代码"只 clear metadata 不清 _classes"导致重试仍用旧维度类的缓存不一致
            from Django_xm.apps.knowledge.vector_store.pgvector_runtime import (
                reset_pgvector_cache,
            )

            reset_pgvector_cache(dimension=required_dim)
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
    index_meta = IndexMetadata.objects.filter(user=user, name=kb_name).first()
    if not index_meta:
        raise FileNotFoundError(f"知识库不存在: {kb_name}")

    docs = Document.objects.filter(index=index_meta, is_deleted=False)

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
        description=index_meta.description or "",
        store_type="pgvector",
        overwrite=True,
    )

    # 清除缓存
    invalidate_knowledge_cache(user_id=user.id, user_index_name=kb_name)
