"""Agent / 学习 / 附件检索结果缓存（``rag_agent_cache_ttl`` 驱动）。

为什么要这层缓存：
- Agent 一轮里常对同一 scope 反复检索（多工具并行、同一问题重问、跨轮追问），
  而 **embedding 调用 + PG 向量查询** 是固定开销；命中即复用文档列表。
- 页面 strict 链路已有自己的缓存轨道（``VectorSearchCacheService``），
  本模块只服务统一内核 ``RagService``，两条轨道键前缀不同、互不干扰。

键：``rag_agent_docs:{collection}:{sha256(query)[:24]}:k{k}:{intent}``
- ``intent`` 参与键：precise 与 comprehensive 召回参数不同，结果不可混用；
- 只缓存**非空**文档列表（空结果是瞬时状态，缓存会把"暂时没有"钉住 TTL）。

失效：
- ``RagService.index_scope`` / ``delete_scope`` 按 collection 前缀清理；
- 知识库上传/删除走 ``kb_service.invalidate_knowledge_cache`` 一并清理。
"""

from __future__ import annotations

import hashlib

from langchain_core.documents import Document

from Django_xm.apps.cache_manager.services.cache_service import (
    CACHE_PREFIX_AGENT_DOCS,
    CacheService,
)
from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)


def generate_agent_docs_key(
    collection_name: str, query: str, k: int, intent: str
) -> str:
    """生成检索结果缓存键（collection + query 摘要 + k + intent）。"""
    query_hash = hashlib.sha256(query.encode("utf-8"), usedforsecurity=False).hexdigest()[:24]
    return f"{CACHE_PREFIX_AGENT_DOCS}:{collection_name}:{query_hash}:k{k}:{intent}"


def get_cached_documents(
    *, collection_name: str | None, query: str, k: int, intent: str
) -> list[Document] | None:
    """读取缓存文档；未命中/数据不合法返回 None。

    缓存内部以 ``{"content": str, "metadata": dict}`` 存（不 pickle Document，
    避免类结构变动导致旧缓存反序列化失败）。
    """
    if not collection_name or not query:
        return None
    raw = CacheService.get(generate_agent_docs_key(collection_name, query, k, intent))
    if not isinstance(raw, list) or not raw:
        return None

    docs: list[Document] = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        content = item.get("content")
        if not isinstance(content, str):
            return None
        metadata = item.get("metadata")
        docs.append(
            Document(page_content=content, metadata=metadata if isinstance(metadata, dict) else {})
        )
    return docs


def cache_documents(
    *, collection_name: str | None, query: str, k: int, intent: str, ttl: int, docs: list[Document]
) -> None:
    """写入缓存（ttl <= 0 或文档为空时跳过）。失败静默降级，不影响检索。"""
    if not collection_name or not query or ttl <= 0 or not docs:
        return
    payload = [
        {"content": doc.page_content, "metadata": dict(doc.metadata or {})} for doc in docs
    ]
    CacheService.set(generate_agent_docs_key(collection_name, query, k, intent), payload, ttl)


def invalidate_collection(collection_name: str | None) -> int:
    """清理某个 collection 的全部检索结果缓存（索引写入/删除后调用）。"""
    if not collection_name:
        return 0
    pattern = f"{CACHE_PREFIX_AGENT_DOCS}:{collection_name}:*"
    try:
        count = CacheService.delete_pattern(pattern)
    except Exception as e:  # pragma: no cover - 缓存故障不阻断主流程
        logger.warning(f"清理检索结果缓存失败（已忽略）: {collection_name}, {e}")
        return 0
    if count:
        logger.info(f"检索结果缓存已失效: {collection_name}, 清除 {count} 个键")
    return count
