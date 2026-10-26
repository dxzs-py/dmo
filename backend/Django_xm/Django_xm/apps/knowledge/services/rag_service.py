"""RAG 统一服务（C1）：检索 → 上下文管线 → 结构化结果。

职责边界：
- 只负责「检索 + 上下文 + 结果封装」，不生成答案、不做引用校验；
- 页面 strict、Agent 工具、学习节点、附件共用；
- C1 阶段由调用方传入 retriever；C3 增加 ScopeResolver 后支持按 scope 自动加载。

入口：
- ``retrieve``：单 query（Agent/学习/附件）
- ``retrieve_many``：多 query + RRF（页面 strict 的多路查询）

Scope：
- ``persistent_kb``：知识库集合（``user_{id}_{kb}``）
- ``session_attachment``：会话附件集合（``attach_{thread}``）
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.apps.knowledge.services.rag_context_pipeline import build_context, merge_context

logger = get_logger(__name__)


@dataclass(frozen=True)
class RagScope:
    kind: Literal["persistent_kb", "session_attachment"]
    name: str
    user_id: int | None = None
    kb_ids: tuple[str, ...] | None = None


@dataclass
class RagRequest:
    query: str
    scope: RagScope
    k: int = 6
    apply_budget: bool = True
    metrics_kind: str | None = None
    # 检索意图：auto（默认，规则分类）/ precise / comprehensive（全局分析）
    intent: str = "auto"


@dataclass
class RagResult:
    docs: list[Document]
    sources: list[dict] = field(default_factory=list)
    citations: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    degraded: bool = False
    budget_exceeded: bool = False


def attachment_scope(thread_id: str) -> RagScope:
    """会话附件 scope（collection 名由服务端生成，不接受 LLM 传参）。"""
    import re

    from Django_xm.apps.knowledge.rag_settings import get_rag_runtime_settings

    prefix = get_rag_runtime_settings()["attachment_collection_prefix"] or "attach_"
    safe = re.sub(r"[^0-9A-Za-z_]", "_", thread_id)[:60]
    return RagScope(kind="session_attachment", name=f"{prefix}{safe}")


class RagService:
    """RAG 统一入口（检索 + 上下文）。"""

    def retrieve(
        self,
        request: RagRequest,
        *,
        retriever: BaseRetriever,
        vector_store=None,
    ) -> RagResult:
        """单 query 检索（Agent/学习/附件）。

        意图分发：
        - precise：沿用调用方传入的 retriever（原有行为）；
        - comprehensive（总结/整篇/有哪些等）：基于同一个已加载的 vector_store
          现构 MMR 宽召回检索器（不重新加载索引、不额外调 LLM）。
        缓存：intent 参与缓存键，命中时跳过 embedding + 向量查询。
        """
        from Django_xm.apps.knowledge.rag_settings import get_rag_runtime_settings
        from Django_xm.apps.knowledge.services import retrieval_cache
        from Django_xm.apps.knowledge.services.query_intent import (
            COMPREHENSIVE,
            resolve_intent,
        )
        from Django_xm.apps.knowledge.services.rag_retrieval import UnifiedRagPipeline

        runtime = get_rag_runtime_settings()
        intent = resolve_intent(request.intent, request.query)
        collection_name = request.scope.name or None

        started = time.perf_counter()
        docs: list[Document] = []
        cache_hit = False
        cache_ttl = int(runtime.get("agent_cache_ttl") or 0)

        # 生效 k 必须在查缓存前确定：comprehensive 会放大召回数，
        # 读写缓存键必须同口径（否则写入用 comp_k、读用原 k，永不命中）。
        comprehensive = intent == COMPREHENSIVE and vector_store is not None
        effective_k = (
            int(runtime.get("comprehensive_k") or 0) or request.k
            if comprehensive
            else request.k
        )

        if cache_ttl > 0:
            cached = retrieval_cache.get_cached_documents(
                collection_name=collection_name, query=request.query, k=effective_k, intent=intent
            )
            if cached is not None:
                docs, cache_hit = cached, True

        if not cache_hit:
            if comprehensive:
                from Django_xm.apps.knowledge.services.retrieval_service import (
                    build_comprehensive_retriever,
                )

                retriever = build_comprehensive_retriever(vector_store, k=effective_k)

            pipeline = UnifiedRagPipeline(scenario="knowledge_base")
            docs = pipeline.retrieve_documents_from_retriever(
                retriever=retriever,
                query=request.query,
                vector_store=vector_store,
                raise_on_error=True,
                with_scores=True,
                k=effective_k,
            )
            # 降级结果（embedding 故障 / 关键词兜底）不缓存：避免把瞬时降级
            # 钉在 TTL 窗口内（降级标记由检索内核写入 metadata.degraded）
            degraded_result = any(
                bool((doc.metadata or {}).get("degraded")) for doc in docs
            )
            if not degraded_result:
                retrieval_cache.cache_documents(
                    collection_name=collection_name,
                    query=request.query,
                    k=effective_k,
                    intent=intent,
                    ttl=cache_ttl,
                    docs=docs,
                )

        return self._finalize(
            docs,
            request.scope,
            request.apply_budget,
            started,
            k=effective_k,
            metrics_kind=request.metrics_kind,
            intent=intent,
            cached=cache_hit,
        )

    def retrieve_many(
        self,
        queries: list[str],
        *,
        retriever: BaseRetriever,
        scope: RagScope,
        vector_store=None,
        k: int = 6,
        apply_budget: bool = False,
        metrics_kind: str | None = None,
    ) -> RagResult:
        """多 query 检索 + RRF（页面 strict 的 HyDE/MQ 链路）。"""
        from Django_xm.apps.knowledge.services.rag_retrieval import retrieve_multi_query

        started = time.perf_counter()
        docs = retrieve_multi_query(retriever, queries, vector_store=vector_store, k=k)
        return self._finalize(
            docs, scope, apply_budget, started, k=k, metrics_kind=metrics_kind
        )

    def retrieve_scopes(
        self,
        query: str,
        *,
        bundles: list[tuple[RagScope, BaseRetriever, object | None]],
        k: int = 6,
        apply_budget: bool = True,
        metrics_kind: str | None = None,
        intent: str = "auto",
    ) -> RagResult:
        """跨 scope 检索（Agent 跨库工具）：逐 scope 检索 + 合并 + 统一去重/预算。"""
        from Django_xm.apps.knowledge.services.query_intent import (
            COMPREHENSIVE,
            resolve_intent,
        )

        started = time.perf_counter()
        intent_resolved = resolve_intent(intent, query)
        merged: list[Document] = []
        degraded = False
        cache_flags: list[bool] = []
        for scope, retriever, vector_store in bundles:
            result = self.retrieve(
                RagRequest(
                    query=query,
                    scope=scope,
                    k=k,
                    apply_budget=True,
                    intent=intent_resolved,
                ),
                retriever=retriever,
                vector_store=vector_store,
            )
            merged.extend(result.docs)
            degraded = degraded or result.degraded
            cache_flags.append(bool(result.metrics.get("cached")))
        all_cached = bool(cache_flags) and all(cache_flags)

        if not merged:
            return RagResult(
                docs=[],
                metrics={
                    "scope": "persistent_kb",
                    "hits": 0,
                    "sources": 0,
                    "degraded": degraded,
                    "budget_exceeded": False,
                    "cached": all_cached,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
                degraded=degraded,
            )

        # 每个 scope 内部已完成邻块；合并后只做去重与总预算。
        from Django_xm.apps.knowledge.rag_settings import get_rag_runtime_settings

        runtime = get_rag_runtime_settings()
        # 全局分析需要更多原文：总预算按页面级（更大）口径
        merge_budget = (
            runtime["context_max_tokens"]
            if intent_resolved == COMPREHENSIVE
            else runtime["tool_max_context_tokens"]
        )
        context = merge_context(
            merged,
            apply_budget=apply_budget,
            max_total_tokens=merge_budget if apply_budget else None,
            default_scope="persistent_kb",
            runtime=runtime,
        )
        metrics = {
            "scope": "persistent_kb",
            "hits": len(context.docs),
            "sources": len(context.sources),
            "degraded": degraded,
            "budget_exceeded": context.budget_exceeded,
            "cached": all_cached,
            "intent": intent_resolved,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        logger.debug(f"RagService.retrieve_scopes: {metrics}")
        if bundles:
            self._record_metrics(
                bundles[0][0],
                k=k,
                hits=len(context.docs),
                sources=len(context.sources),
                degraded=degraded,
                started=started,
                metrics_kind=metrics_kind,
                cached=all_cached,
            )
        return RagResult(
            docs=context.docs,
            sources=context.sources,
            citations=context.citations,
            metrics=metrics,
            degraded=degraded,
            budget_exceeded=context.budget_exceeded,
        )

    def retrieve_for_scope(self, request: RagRequest) -> RagResult:
        """按 scope 自动加载 collection + 构建 retriever（附件/独立 Agent 使用）。"""
        from Django_xm.apps.knowledge.services.embedding_service import get_embeddings
        from Django_xm.apps.knowledge.services.index_service import IndexManager
        from Django_xm.apps.knowledge.services.retrieval_service import create_retriever

        store = IndexManager().load_index(request.scope.name, get_embeddings())
        if store is None:
            return RagResult(
                docs=[],
                metrics={"scope": request.scope.kind, "hits": 0, "sources": 0, "degraded": False},
            )
        retriever = create_retriever(store, k=request.k, score_threshold=0.0)
        return self.retrieve(request, retriever=retriever, vector_store=store)

    def index_scope(
        self,
        scope: RagScope,
        documents: list[Document],
        *,
        overwrite: bool = True,
        description: str | None = None,
    ) -> None:
        """写入 scope 对应 collection（附件首次索引/重建）。"""
        from Django_xm.apps.knowledge.services import retrieval_cache
        from Django_xm.apps.knowledge.services.bm25_index import invalidate_bm25_cache
        from Django_xm.apps.knowledge.services.embedding_service import get_embeddings
        from Django_xm.apps.knowledge.services.index_service import IndexManager

        IndexManager().create_index(
            name=scope.name,
            documents=documents,
            embeddings=get_embeddings(),
            description=description or f"scope:{scope.kind}",
            store_type="pgvector",
            overwrite=overwrite,
        )
        invalidate_bm25_cache(scope.name)
        # 索引内容已变：检索结果缓存必须失效（否则 TTL 内返回旧文档）
        retrieval_cache.invalidate_collection(scope.name)

    def delete_scope(self, scope: RagScope) -> None:
        """删除 scope 对应 collection + 实体元数据 + 相关缓存。"""
        from Django_xm.apps.knowledge.models import IndexMetadata
        from Django_xm.apps.knowledge.services import retrieval_cache
        from Django_xm.apps.knowledge.services.bm25_index import invalidate_bm25_cache
        from Django_xm.apps.knowledge.services.index_service import IndexManager

        try:
            IndexManager().delete_index(scope.name)
        finally:
            invalidate_bm25_cache(scope.name)
            retrieval_cache.invalidate_collection(scope.name)
            # IndexManager.delete_index 约定不删实体行（由调用方负责）：
            # 残留的 IndexMetadata 会让 index_exists 误判为“索引仍存在”，
            # 导致附件懒重建（_ensure_index）跳过重建。
            try:
                IndexMetadata.objects.filter(name=scope.name).delete()
            except Exception as e:  # pragma: no cover - 元数据清理失败不阻断
                logger.warning(f"删除 scope 元数据失败（已忽略）: {scope.name}, {e}")

    def _finalize(
        self,
        docs: list[Document],
        scope: RagScope,
        apply_budget: bool,
        started: float,
        *,
        k: int = 6,
        metrics_kind: str | None = None,
        intent: str = "auto",
        cached: bool = False,
    ) -> RagResult:
        collection_name = scope.name or None
        from Django_xm.apps.knowledge.rag_settings import get_rag_runtime_settings

        runtime = get_rag_runtime_settings()
        # 全局分析（总结/整篇）需要更多原文：预算按页面级（更大）口径
        from Django_xm.apps.knowledge.services.query_intent import COMPREHENSIVE

        budget = runtime["context_max_tokens"] if intent == COMPREHENSIVE else runtime["tool_max_context_tokens"]
        context = build_context(
            docs,
            collection_name=collection_name,
            apply_budget=apply_budget,
            max_total_tokens=budget if apply_budget else None,
            default_scope=scope.kind,
        )

        degraded = any(bool((doc.metadata or {}).get("degraded")) for doc in context.docs)
        metrics = {
            "scope": scope.kind,
            "hits": len(context.docs),
            "sources": len(context.sources),
            "degraded": degraded,
            "budget_exceeded": context.budget_exceeded,
            "cached": cached,
            "intent": intent,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        logger.debug(f"RagService: {metrics}")

        self._record_metrics(
            scope,
            k=k,
            hits=len(context.docs),
            sources=len(context.sources),
            degraded=degraded,
            started=started,
            metrics_kind=metrics_kind,
            cached=cached,
        )

        return RagResult(
            docs=context.docs,
            sources=context.sources,
            citations=context.citations,
            metrics=metrics,
            degraded=degraded,
            budget_exceeded=context.budget_exceeded,
        )

    def _record_metrics(
        self,
        scope: RagScope,
        *,
        k: int,
        hits: int,
        sources: int,
        degraded: bool,
        started: float,
        metrics_kind: str | None,
        cached: bool = False,
    ) -> None:
        """统一埋点（仅 Agent/学习/附件显式传 kind；页面已有独立埋点）。"""
        if not metrics_kind or not scope.user_id:
            return
        try:
            from Django_xm.apps.analytics.services.retrieval_metrics import record_retrieval

            record_retrieval(
                scope.user_id,
                kind=metrics_kind,
                index_name=scope.name,
                k=k,
                hits=hits,
                sources=sources,
                degraded=degraded,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                cached=cached,
            )
        except Exception as e:
            logger.debug(f"RAG 埋点失败（已忽略）: {e}")
