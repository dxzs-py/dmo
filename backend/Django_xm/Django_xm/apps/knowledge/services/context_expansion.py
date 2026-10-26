"""邻块 / 同章节扩展（small-to-big，Phase 2）。

检索命中的 chunk 只覆盖 1000 字符窗口，答案往往需要上下文才能完整。
本模块在命中块前后 ``window`` 个 chunk 范围内补齐邻块：

- KB 场景（``collection_name``）：一次 SQL 按 ``(source, chunk_index 区间)`` 批量取回
  同集合的相邻块（复用 keyword_fallback 的 join + quote_identifier 写法）；
- 附件场景（``documents``）：在内存按 ``(source, chunk_index)`` 过滤；
- 异常 / 缺元数据 → 退回原始 docs（warning 留痕，绝不阻断检索）。

顺序约定：按「source 首现顺序 + 组内 chunk_index 升序」输出，保证 strict RAG 的
引用编号（按 source 分组）稳定；token 预算由调用方（context_format）统一裁剪。
"""

from __future__ import annotations

import json

from langchain_core.documents import Document

from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.apps.knowledge.config import settings

logger = get_logger(__name__)


def _source_of(doc: Document) -> str:
    metadata = doc.metadata or {}
    return (
        metadata.get("source")
        or metadata.get("file_name")
        or metadata.get("source_filename")
        or ""
    )


def _chunk_index_of(doc: Document) -> int | None:
    raw = (doc.metadata or {}).get("chunk_index")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _fetch_from_pg(collection_name: str, ranges: dict[str, tuple[int, int]]) -> dict[tuple[str, int], Document]:
    """按 source 的 chunk_index 区间批量取回同集合文档（PGVector）。"""
    from django.db import connections

    from Django_xm.apps.knowledge.vector_store.pgvector_backend import (
        get_collection_table_name,
        get_embedding_table_name,
        quote_identifier,
    )

    embedding_table = quote_identifier(get_embedding_table_name())
    collection_table = quote_identifier(get_collection_table_name())
    # 表名经 quote_identifier 白名单，值全部参数化（S608 误报）
    sql = (
        f"SELECT e.document, e.cmetadata FROM {embedding_table} e "  # noqa: S608
        f"JOIN {collection_table} c ON e.collection_id = c.uuid "
        "WHERE c.name = %s "
        "  AND e.cmetadata->>'source' = %s "
        "  AND (e.cmetadata->>'chunk_index')::int BETWEEN %s AND %s"
    )

    found: dict[tuple[str, int], Document] = {}
    with connections["default"].cursor() as cursor:
        for source, (lo, hi) in ranges.items():
            cursor.execute(sql, [collection_name, source, lo, hi])
            for document, cmetadata in cursor.fetchall():
                metadata = cmetadata
                if isinstance(metadata, str):
                    try:
                        metadata = json.loads(metadata)
                    except ValueError:
                        metadata = {}
                metadata = dict(metadata) if isinstance(metadata, dict) else {}
                chunk_index = metadata.get("chunk_index")
                try:
                    index = int(chunk_index)
                except (TypeError, ValueError):
                    continue
                found[(source, index)] = Document(page_content=document, metadata=metadata)
    return found


def _fetch_from_memory(
    documents: list[Document], ranges: dict[str, tuple[int, int]]
) -> dict[tuple[str, int], Document]:
    found: dict[tuple[str, int], Document] = {}
    for doc in documents:
        source = _source_of(doc)
        index = _chunk_index_of(doc)
        if not source or index is None or source not in ranges:
            continue
        lo, hi = ranges[source]
        if lo <= index <= hi:
            found[(source, index)] = doc
    return found


def expand_adjacent(
    docs: list[Document],
    *,
    collection_name: str | None = None,
    documents: list[Document] | None = None,
    window: int | None = None,
) -> list[Document]:
    """按 chunk_index ± window 补齐邻块（small-to-big）。

    Args:
        docs: 已排序的命中文档
        collection_name: KB 索引名（PGVector 场景）
        documents: 附件全量分块（内存场景）
        window: 前后窗口，None 时读 settings.rag_context_window

    Returns:
        扩展后的文档列表；无可用数据源/异常时原样返回
    """
    if not docs:
        return []

    span = settings.rag_context_window if window is None else window
    if span <= 0 or (not collection_name and not documents):
        return list(docs)

    ranges: dict[str, tuple[int, int]] = {}
    for doc in docs:
        source = _source_of(doc)
        index = _chunk_index_of(doc)
        if not source or index is None:
            continue
        lo, hi = ranges.get(source, (index - span, index + span))
        ranges[source] = (min(lo, index - span), max(hi, index + span))

    if not ranges:
        return list(docs)

    found: dict[tuple[str, int], Document] = {
        (_source_of(doc), _chunk_index_of(doc)): doc
        for doc in docs
        if _source_of(doc) and _chunk_index_of(doc) is not None
    }

    try:
        if collection_name:
            found.update(_fetch_from_pg(collection_name, ranges))
        elif documents:
            found.update(_fetch_from_memory(documents, ranges))
    except Exception as e:
        logger.warning(f"邻块扩展失败，使用原始检索结果: {e}")
        return list(docs)

    # 输出顺序：source 首现顺序 + 组内 chunk_index 升序
    ordered_sources: list[str] = []
    for doc in docs:
        source = _source_of(doc)
        if source and source not in ordered_sources:
            ordered_sources.append(source)

    expanded: list[Document] = []
    for source in ordered_sources:
        group = [
            (index, doc)
            for (src, index), doc in found.items()
            if src == source
        ]
        group.sort(key=lambda item: item[0])
        expanded.extend(doc for _index, doc in group)

    # 缺 source/chunk_index 的文档按原顺序追加（不参与扩展）
    seen_ids = {id(doc) for doc in expanded}
    for doc in docs:
        if id(doc) not in seen_ids and (_chunk_index_of(doc) is None or not _source_of(doc)):
            expanded.append(doc)

    if len(expanded) > len(docs):
        logger.debug(f"邻块扩展: {len(docs)} -> {len(expanded)} (window={span})")
    return expanded
