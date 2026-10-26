"""RAG 上下文管线（C1）：去重 → 邻块 → token 预算 → sources/citations。

设计目标：
- 页面 strict、Agent 工具、学习节点、附件共用同一套上下文处理；
- 引用编号按「source 首现 + chunk_index 升序」稳定，页面与工具一致；
- token 预算可选（strict 的 prompt 格式化另有预算，C1 阶段不重复截断）。

消费方：
- ``strict_rag_chain._enhance_context``（apply_budget=False，保持逐字节一致）
- ``RagService.retrieve``（Agent/学习/附件，apply_budget=True）
"""

from __future__ import annotations

from dataclasses import dataclass, field

from langchain_core.documents import Document

from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.apps.knowledge.rag_settings import get_rag_runtime_settings
from Django_xm.apps.knowledge.services.context_expansion import expand_adjacent
from Django_xm.apps.knowledge.services.context_format import (
    dedupe_documents,
    fit_documents_by_tokens,
)

logger = get_logger(__name__)


@dataclass
class ContextResult:
    docs: list[Document]
    sources: list[dict] = field(default_factory=list)
    citations: list[dict] = field(default_factory=list)
    budget_exceeded: bool = False


def _source_key(doc: Document) -> str:
    metadata = doc.metadata or {}
    return (
        metadata.get("source")
        or metadata.get("file_name")
        or metadata.get("source_filename")
        or "未知"
    )


def group_by_source(docs: list[Document]) -> list[list[Document]]:
    """按 source 首现分组，组内按 chunk_index 升序（引用编号权威源）。"""
    groups: dict[str, list[Document]] = {}
    for doc in docs:
        groups.setdefault(_source_key(doc), []).append(doc)
    return [
        sorted(group, key=lambda d: (d.metadata.get("chunk_index") is None, d.metadata.get("chunk_index") or 0))
        for group in groups.values()
    ]


def build_sources(docs: list[Document], *, default_scope: str = "persistent_kb") -> list[dict]:
    """生成稳定引用编号与来源元数据（页面/工具共用结构）。"""
    sources: list[dict] = []
    for idx, group in enumerate(group_by_source(docs), 1):
        first = group[0]
        metadata = first.metadata or {}
        sources.append(
            {
                "citation": idx,
                "source": _source_key(first),
                "chunk_indexes": [d.metadata.get("chunk_index") for d in group],
                "score": metadata.get("score"),
                "degraded": bool(metadata.get("degraded")),
                "scope": metadata.get("_rag_scope", default_scope),
            }
        )
    return sources


def _embedder_for_dedupe(mode: str):
    if mode != "embedding":
        return None
    try:
        from Django_xm.apps.knowledge.services.embedding_service import get_embeddings

        return get_embeddings()
    except Exception as e:
        logger.warning(f"上下文去重 embedding 不可用，回退 gram: {e}")
        return None


def build_context(
    docs: list[Document],
    *,
    collection_name: str | None = None,
    documents: list[Document] | None = None,
    apply_budget: bool = True,
    max_total_tokens: int | None = None,
    default_scope: str = "persistent_kb",
    runtime: dict | None = None,
) -> ContextResult:
    """执行去重 → 邻块 →（可选）token 预算，返回文档与 sources。"""
    if not docs:
        return ContextResult(docs=[])

    settings = runtime or get_rag_runtime_settings()
    dedupe_mode = settings["dedupe_mode"]
    embedder = _embedder_for_dedupe(dedupe_mode)

    try:
        docs = dedupe_documents(
            docs,
            max_per_source=settings["max_chunks_per_source"],
            similarity_threshold=settings["dedupe_similarity"],
            mode=dedupe_mode,
            embedder=embedder,
        )
    except Exception as e:
        logger.warning(f"上下文去重失败，使用原始结果: {e}")

    try:
        docs = expand_adjacent(
            docs,
            collection_name=collection_name,
            documents=documents,
            window=settings["context_window"],
        )
    except Exception as e:
        logger.warning(f"邻块扩展失败，使用去重后结果: {e}")

    budget_exceeded = False
    if apply_budget:
        docs, budget_exceeded = fit_documents_by_tokens(
            docs,
            max_doc_tokens=settings["doc_max_tokens"],
            max_total_tokens=max_total_tokens or settings["context_max_tokens"],
        )

    sources = build_sources(docs, default_scope=default_scope)
    return ContextResult(
        docs=docs,
        sources=sources,
        citations=list(sources),
        budget_exceeded=budget_exceeded,
    )


def merge_context(
    docs: list[Document],
    *,
    apply_budget: bool = True,
    max_total_tokens: int | None = None,
    default_scope: str = "persistent_kb",
    runtime: dict | None = None,
) -> ContextResult:
    """跨 scope 合并阶段：只做 token 预算 + sources（不再去重/邻块）。

    为什么不能直接复用 ``build_context``：每个 scope 内部已经完成
    “去重 → 邻块 → 预算”，合并后再去重一次会被 ``max_chunks_per_source``
    （默认 2）按源收口——单源 KB 的邻块被整批砍掉，工具只回 2 块
    （实测：comprehensive 应该 22 块，旧实现只给 2 块，模型因此反复检索）。

    去重也无需在合并层重做：不同 scope 的 source 前缀不同（user_{id}_{kb} /
    attach_{thread}），同一块文本不会以同一 source 重现。
    """
    if not docs:
        return ContextResult(docs=[])

    settings = runtime or get_rag_runtime_settings()
    budget_exceeded = False
    if apply_budget:
        docs, budget_exceeded = fit_documents_by_tokens(
            docs,
            max_doc_tokens=settings["doc_max_tokens"],
            max_total_tokens=max_total_tokens or settings["context_max_tokens"],
        )

    sources = build_sources(docs, default_scope=default_scope)
    return ContextResult(
        docs=docs,
        sources=sources,
        citations=list(sources),
        budget_exceeded=budget_exceeded,
    )
