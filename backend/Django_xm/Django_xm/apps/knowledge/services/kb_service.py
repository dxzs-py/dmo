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
from Django_xm.common.error_codes import ErrorCode

from ..exceptions import KnowledgeBaseAlreadyExistsError, KnowledgeBaseError
from ..models import Document, IndexMetadata
from ..serializers import INDEX_NAME_PATTERN
from ..views_utils import get_document_type, get_file_extension, get_original_index_name, get_user_index_name
from .document_service import load_document
from .embedding_service import collect_fallback_info, get_embeddings
from .index_service import IndexManager
from .splitters import split_documents

logger = logging.getLogger(__name__)


def _validate_kb_name(name: str) -> None:
    """C-2 深度防御第二层：知识库名称白名单（与 serializer 的 IndexNameValidationMixin 同口径）。

    serializer 挡 HTTP 面；本层独立直挡任何绕过 serializer 的调用面
    （管理命令/内部调用/未来新视图）。名称经 get_user_index_name 拼接后直达磁盘
    路径，`../`/`\\`/`%5c` 等穿越序列一律拒绝。

    Raises:
        ValueError: 名称含路径分隔符/穿越序列，或存在白名单外字符。
    """
    if not name or not INDEX_NAME_PATTERN.match(name):
        raise ValueError(f"知识库名称只能包含字母、数字、下划线、连字符和中文: {name!r}")


def _ensure_path_within(path: Path, container: Path) -> None:
    """C-2 路径容器检查：resolve 后必须仍位于 container 内（防文件名单独穿越）。

    Raises:
        ValueError: 解析路径逃逸容器目录。
    """
    if not path.resolve().is_relative_to(container.resolve()):
        raise ValueError(f"路径越界，已拒绝: {path.name!r}")


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
        ValueError: 知识库名称为空或含非法字符（C-2 白名单）
        KnowledgeBaseAlreadyExistsError: 向量索引残留（无实体行的同名向量集合）
    """
    _validate_kb_name(name)

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
    if manager.index_exists(user_index_name, user_id=user.id):
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

    # B5：失效必须带 user_index_name——invalidate_knowledge_cache 只在传入时
    # 才执行 QueryCacheService/VectorSearchCacheService.invalidate_index_queries，
    # 只传 user_id 是空转（软删墓碑恢复路径下该索引名的历史查询缓存需真实失效）
    invalidate_knowledge_cache(user_id=user.id, user_index_name=user_index_name)

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

    # B5：实际受损场景——描述变更后若不失效查询缓存，TTL 内相关查询仍返回
    # 旧描述上下文的答案。只传 user_id 时 invalidate_knowledge_cache 空转。
    invalidate_knowledge_cache(user_id=user.id, user_index_name=user_index_name)

    return {
        "id": kb_id,
        "name": kb_id,
        "description": description,
    }


def delete_knowledge_base(user, kb_id: str) -> None:
    """
    删除知识库：向量索引/上传目录硬删，IndexMetadata 实体与 Document 行软删（墓碑）

    B4 三态一致（对齐 delete_document 的 E5-04 防护）：向量删除失败即整体失败，
    DB 行与上传目录原状保留，用户可直接重试——旧实现忽略 delete_index 的
    fail-soft 返回值继续软删 + rmtree，PG 故障时向量残留 + 同名重建恒抛
    KnowledgeBaseAlreadyExistsError 且无自愈路径。

    Raises:
        FileNotFoundError: 知识库不存在
        KnowledgeBaseError: 向量索引删除失败（SERVICE_UNAVAILABLE，可重试）
    """
    user_index_name = get_user_index_name(user, kb_id)
    index_meta = IndexMetadata.objects.filter(name=user_index_name).first()

    if not index_meta:
        raise FileNotFoundError(f"知识库不存在: {kb_id}")

    manager = IndexManager()

    # 先探测向量侧存在性：pgvector 的 delete 对"集合/表不存在"同样 fail-soft
    # 返回 False（无操作≠失败），该情形放行删除（幂等二次删除），避免制造
    # "向量本已不在却永远删不掉"的新死角；仅"应删而删失败"才阻断。
    # 探测本身异常（PG 不可达等）按删除失败处理。
    try:
        vector_exists = manager.get_backend(manager.get_store_type(user_index_name, user_id=user.id)).exists(
            user_index_name
        )
    except Exception as pe:
        logger.exception(f"向量存在性探测失败，按向量删除失败处理: {user_index_name}")
        raise KnowledgeBaseError(
            "向量索引删除失败，知识库未删除，请稍后重试",
            business_code=ErrorCode.SERVICE_UNAVAILABLE,
        ) from pe

    deleted = manager.delete_index(user_index_name)
    if not deleted and vector_exists:
        logger.error(f"知识库向量索引删除失败，DB 行与上传目录保持原状（可重试）: {user_index_name}")
        raise KnowledgeBaseError(
            "向量索引删除失败，知识库未删除，请稍后重试",
            business_code=ErrorCode.SERVICE_UNAVAILABLE,
        )

    Document.objects.filter(index=index_meta).update(is_deleted=True)
    index_meta.soft_delete()

    upload_dir = Path(app_cfg.data_uploads_path) / user_index_name
    if upload_dir.exists() and upload_dir.is_dir():
        try:
            shutil.rmtree(upload_dir)
        except Exception as e:
            logger.warning(f"删除上传文件目录失败: {e}")

    invalidate_knowledge_cache(user_id=user.id, user_index_name=user_index_name)

    # A-2 依赖反转：knowledge 不再 import chat.cross_app（上行依赖），
    # 改发 core.signals.knowledge_base_deleted，chat 侧 receiver 订阅清理。
    # Django signal 同步分发，行为与原直接调用等价（receiver 异常向上传播）。
    from Django_xm.apps.core.signals import knowledge_base_deleted

    knowledge_base_deleted.send(sender=None, user_id=user.id, kb_name=kb_id)


def list_documents(user, kb_id: str) -> list[dict[str, Any]]:
    """
    获取知识库下的文档列表

    Raises:
        FileNotFoundError: 知识库不存在
    """
    _validate_kb_name(kb_id)
    user_index_name = get_user_index_name(user, kb_id)
    manager = IndexManager()

    if not manager.index_exists(user_index_name, user_id=user.id):
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
        ValueError: 文件列表为空 / 名称或文件名非法（C-2 白名单 + 容器检查）
    """
    _validate_kb_name(kb_id)
    user_index_name = get_user_index_name(user, kb_id)
    manager = IndexManager()

    if not manager.index_exists(user_index_name, user_id=user.id):
        raise FileNotFoundError(f"知识库不存在: {kb_id}")

    if not uploaded_files:
        raise ValueError("请选择要上传的文件")

    upload_dir = Path(app_cfg.data_uploads_path) / user_index_name
    upload_dir.mkdir(parents=True, exist_ok=True)

    saved_files = []
    for uploaded_file in uploaded_files:
        file_path = upload_dir / uploaded_file.name
        # C-2：文件名由客户端控制，写入前容器检查（不依赖 Django 的文件名清洗）
        _ensure_path_within(file_path, upload_dir)
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
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> dict[str, Any]:
    """处理已落盘的上传文件：加载文档、分块、向量化、更新索引。

    由 Celery 异步任务调用（upload_documents_task），也可被同步 upload_documents 复用。

    Args:
        user: 用户对象
        kb_id: 知识库名称
        file_names: upload 目录下的文件名列表（save_uploaded_files 已落盘）
        progress_callback: 可选进度回调 callable(progress: int, message: str)
        chunk_size: 可选分块大小（B13 参数接线：None 时经 split_documents
            落到全局配置默认值）
        chunk_overlap: 可选分块重叠（None 时同上）

    Returns:
        包含上传结果的字典（B6：含 documents_failed/failed_files 部分成功报告）

    Raises:
        FileNotFoundError: 知识库不存在
        ValueError: 所有文件均处理失败（附各文件失败原因）
    """
    user_index_name = get_user_index_name(user, kb_id)
    manager = IndexManager()
    index_meta = IndexMetadata.objects.filter(name=user_index_name).first()

    if not index_meta:
        raise FileNotFoundError(f"知识库不存在: {kb_id}")

    upload_dir = Path(app_cfg.data_uploads_path) / user_index_name

    # B6：逐文件容错（对齐 document_service.load_directory 口径）——坏文件跳过
    # 并记录失败原因，不再让单坏文件 raise 拖垮整批（all-or-nothing 旧行为）；
    # 全部失败才整体失败（确定性错误，Celery 侧按 E5-05 秒级终态不重试）。
    all_documents = []
    saved_files = []
    failed_files: list[dict[str, str]] = []
    for name in file_names:
        file_path = upload_dir / name
        if not file_path.is_file():
            logger.warning(f"上传文件不存在，跳过: {name}")
            failed_files.append({"name": name, "reason": "文件不存在"})
            continue
        try:
            docs = load_document(str(file_path))
            if not docs:
                raise ValueError("未能提取到任何内容")  # noqa: TRY301 -- 与 load_document 异常共用同一失败出口
        except Exception as e:
            logger.warning(f"文件加载失败，跳过: {name} - {e}")
            failed_files.append({"name": name, "reason": str(e)})
            continue
        all_documents.extend(docs)
        saved_files.append(
            {
                "name": name,
                "size": file_path.stat().st_size,
            }
        )

    if progress_callback:
        progress_callback(30, f"文档加载完成：{len(saved_files)} 成功 / {len(failed_files)} 失败")

    if not all_documents:
        detail = "; ".join(f"{f['name']}: {f['reason']}" for f in failed_files)
        raise ValueError(f"所有上传文件均处理失败: {detail}" if detail else "未能从上传文件中提取内容")

    # B13：chunk 参数接线——仅传非 None 值（未提供时 split_documents 落到
    # 全局配置默认值，调用形状与旧行为逐字一致）
    split_kwargs: dict[str, int] = {}
    if chunk_size is not None:
        split_kwargs["chunk_size"] = chunk_size
    if chunk_overlap is not None:
        split_kwargs["chunk_overlap"] = chunk_overlap
    chunks = split_documents(all_documents, **split_kwargs)

    if progress_callback:
        progress_callback(60, f"分块完成：{len(chunks)} 个文本块")

    # 上传文档时使用索引原有维度约束（如有），确保维度一致
    index_metadata = manager.load_metadata(user_index_name, user_id=user.id)
    required_dim = index_metadata.get("embedding_dimension") if index_metadata else None
    embeddings = get_embeddings(required_dimension=required_dim)

    if progress_callback:
        progress_callback(75, "正在向量化并写入索引")

    # C-5 替换语义（先删后加）：同名文件旧 chunk 的稳定 ID 含内容哈希，
    # 内容变化 → 新 ID → ON CONFLICT DO NOTHING 不会去重旧版本，直接追加
    # 会导致新旧两版内容同时可检索。对每个上传文件先清向量侧同名旧块
    # （新文件为无害 no-op，统一执行替换语义）；清理失败不阻断上传
    # （写入侧幂等护栏仍在），仅 warning 留痕。
    # B6：仅对成功加载的文件执行——坏文件本轮不写入新内容，旧版本向量
    # 保留（与"跳过坏文件"语义一致，清了旧版再写失败会丢数据）。
    for file_info in saved_files:
        name = file_info["name"]
        try:
            manager.remove_documents_by_filename(user_index_name, embeddings, name)
        except Exception as e:
            logger.warning(f"重传替换：向量侧同名清理失败（新内容仍会写入）: {name} - {e}")

    count = manager.add_documents(user_index_name, chunks, embeddings)

    # B-5 收口：SystemConfig 同步逻辑单源在 embedding_service.collect_fallback_info，
    # 上传编排只透传 fallback_info，不再内嵌配置管理。
    fallback_info = collect_fallback_info(embeddings)

    # C-5：chunk_count 按文件归属统计（旧实现 len(chunks)//len(saved_files)
    # 均值分摊恒失真）；Document 行先软删旧同名行再插新行——表无 (index, filename)
    # 唯一约束，直接插入会让同名文件行数逐次累积。
    chunks_by_file: dict[str, int] = {}
    for chunk in chunks:
        source_name = chunk.metadata.get("file_name") or Path(chunk.metadata.get("source", "")).name
        chunks_by_file[source_name] = chunks_by_file.get(source_name, 0) + 1

    for file_info in saved_files:
        name = file_info["name"]
        for old_row in Document.objects.filter(index=index_meta, filename=name, is_deleted=False):
            old_row.soft_delete()

        ext = get_file_extension(name)
        doc_type = get_document_type(ext)
        doc = Document(
            index=index_meta,
            filename=name,
            file_path=str(upload_dir / name),
            file_type=doc_type,
            file_size=file_info["size"],
            chunk_count=chunks_by_file.get(name, 0),
        )
        doc.save()

    invalidate_knowledge_cache(user_id=user.id, user_index_name=user_index_name)

    if progress_callback:
        progress_callback(100, "上传完成")

    return {
        "documents_uploaded": len(saved_files),
        # B6：部分成功报告——失败清单随返回值/任务结果向上传递
        # （Celery 任务 mark_success(result=...) 与 return {"status": "success", **result}），
        # 供任务频道/前端区分"全部成功"与"部分成功（N 个失败）"
        "documents_failed": len(failed_files),
        "failed_files": failed_files,
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
        ValueError: 知识库名称或文件名非法（C-2 白名单 + 容器检查）
    """
    _validate_kb_name(kb_id)
    user_index_name = get_user_index_name(user, kb_id)
    upload_dir = Path(app_cfg.data_uploads_path) / user_index_name
    file_path = upload_dir / filename
    # C-2：filename 单独穿越（kb_id 合法）同样逃逸 uploads 根目录，unlink 前容器检查
    _ensure_path_within(file_path, upload_dir)

    manager = IndexManager()
    index_meta = IndexMetadata.objects.filter(name=user_index_name).first()

    if not index_meta:
        raise FileNotFoundError(f"知识库不存在: {kb_id}")

    if not file_path.exists() or not file_path.is_file():
        raise FileNotFoundError(f"文件不存在: {filename}")

    logger.info(f"删除文档: {filename} 从 {user_index_name}")

    # E5-04 三态一致：向量删除失败即整体失败——旧实现吞异常后继续删物理文件/
    # 软删 DB 行并返回成功，造成孤儿向量 + 后续同名重传新旧内容并存 + 假
    # "文件已删除"成功响应。失败时文件与 DB 行原状保留，用户可直接重试。
    try:
        index_metadata = manager.load_metadata(user_index_name, user_id=user.id)
        required_dim = index_metadata.get("embedding_dimension") if index_metadata else None
        embeddings = get_embeddings(required_dimension=required_dim)
        removed = manager.remove_documents_by_filename(user_index_name, embeddings, filename)
        logger.info(f"从向量索引中删除 {removed} 个文档块")
    except Exception as ve:
        logger.exception(f"向量删除失败，保持文件与数据库记录不变（可重试）: {filename}")
        raise KnowledgeBaseError(
            "向量删除失败，文件未删除，请稍后重试",
            business_code=ErrorCode.SERVICE_UNAVAILABLE,
        ) from ve

    doc = Document.objects.filter(index=index_meta, filename=filename).first()
    if doc:
        doc.soft_delete()

    file_path.unlink()
    logger.info(f"文件已从磁盘删除: {file_path}")

    invalidate_knowledge_cache(user_id=user.id, user_index_name=user_index_name)

    return {"message": f"文件已删除: {filename}"}


def search_knowledge_base(user, kb_id: str, query: str, top_k: int = 5) -> list[dict[str, Any]]:
    """
    在知识库中搜索（ARCH-08 入口收敛）

    检索内核（打分检索 + 维度不匹配自愈重试）统一委托
    UnifiedRagPipeline.search_scored；本函数保留缓存读写与扁平化
    返回结构 [{content, source, score}]（kb_service 对外契约，分层非重复）。

    Raises:
        ValueError: 查询内容为空
        FileNotFoundError: 知识库不存在
    """
    if not query:
        raise ValueError("查询内容不能为空")

    user_index_name = get_user_index_name(user, kb_id)
    manager = IndexManager()

    if not manager.index_exists(user_index_name, user_id=user.id):
        raise FileNotFoundError(f"知识库不存在: {kb_id}")

    # RP-08：缓存读前置到 store 构建链（metadata 读取/embeddings 实例化/load_index）之前，
    # 命中时跳过全部构建开销（含一次可能的 PG engine 建立）；归属校验（index_exists）保留在前
    cached_results = VectorSearchCacheService.get_cached_search(query, user_index_name, top_k)
    if cached_results is not None:
        logger.info("向量搜索缓存命中")
        return cached_results

    # B-1：store 构建编排（metadata/维度探测/embeddings/load_index/自愈重建回调）
    # 经 load_user_vector_store 单一门面（原内联四步删除）
    from .retrieval_service import load_user_vector_store

    loaded = load_user_vector_store(user.id, kb_id)
    if loaded is None:
        raise FileNotFoundError(f"知识库不存在: {kb_id}")
    vector_store, _index_metadata, required_dim, reload_vector_store = loaded

    from .rag_retrieval import UnifiedRagPipeline

    results = UnifiedRagPipeline.search_scored(
        vector_store,
        query,
        k=top_k,
        # 3.2：KB 主链路相似度阈值——pgvector distance 已在检索内核归一为
        # 0~1 similarity，与附件路径（_ScoreThresholdRetriever）同一
        # rag_score_threshold 口径；低于阈值的低相关块不再冒充检索命中
        score_threshold=app_cfg.rag_score_threshold,
        reload_vector_store=reload_vector_store,
        required_dim=required_dim,
        # C1/KV-2 + C-2arch：score_semantics 由后端 native_score_semantics 声明收敛
        # （pgvector=distance，其余=similarity），调用方不再按 store_type 硬判
        score_semantics=manager.get_score_semantics(user_index_name),
    )

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
        # C-2arch：按实际配置解析 store_type（此前硬编码 "pgvector"，配置切走后重建行为偏离）
        store_type=manager.get_store_type(kb_name, user_id=user.id),
        overwrite=True,
    )

    # 清除缓存
    invalidate_knowledge_cache(user_id=user.id, user_index_name=kb_name)
