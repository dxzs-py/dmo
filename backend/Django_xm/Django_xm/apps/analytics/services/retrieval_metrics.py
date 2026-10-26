"""RAG 检索灰度指标（服务端埋点）。

为什么需要：P0/P1 改造（阈值 0.35、rerank 窗口、Level 1、hybrid、跨库精排）
的收益必须用线上数据验证，而不仅是离线评测。

设计约束：
- **零阻塞**：埋点经 Celery ``track_event`` 异步落库；broker 不可用 / 序列化
  失败直接丢弃并 debug 留痕，绝不影响检索主链。
- **零契约变更**：只写 ``UserEvent.metadata``，不改任何 HTTP 响应结构；
  前端无需改动（引用点击率属另一类指标，需前端显式上报）。
- **可采样**：``settings.analytics_retrieval_sample_rate``（默认 1.0，缺失即全采样）。

指标口径（metadata）：
- ``hits`` / ``sources``：命中数与来源文件数（hits==0 即空结果率的分子）
- ``degraded``：是否为 embedding 故障关键词降级
- ``cached``：是否命中查询缓存（用于区分缓存率与真实检索）
- ``k`` / ``threshold`` / ``recall_k`` / ``rerank`` / ``hybrid``：当次检索配置指纹
- ``kind``：strict（同步问答）/ stream（流式问答）/ multi_kb（跨库聚合）

使用：
    from Django_xm.apps.analytics.services import retrieval_metrics

    retrieval_metrics.record_retrieval(
        user_id, kind="strict", index_name="user_1_kb", k=4,
        hits=4, degraded=False, duration_ms=123.4,
    )
"""

from __future__ import annotations

import random

from Django_xm.apps.analytics.models import EventCategory, EventType
from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.apps.knowledge.config import settings

logger = get_logger(__name__)


def record_retrieval(
    user_id: int | None,
    *,
    kind: str,
    index_name: str = "",
    k: int | None = None,
    hits: int = 0,
    sources: int | None = None,
    degraded: bool = False,
    duration_ms: float | None = None,
    cached: bool = False,
    error: str = "",
    extra: dict | None = None,
) -> None:
    """投递一条 RAG 检索指标（异步、失败静默）。

    Args:
        user_id: 触发用户；None（系统内部调用）不埋点
        kind: 链路标识 strict / stream / multi_kb
        index_name: 知识库索引名（多库可留空）
        k: 调用方期望的最终文档数
        hits: 命中文档数
        sources: 来源文件数，None 时取 hits
        degraded: 是否为关键词降级结果
        duration_ms: 检索/问答总耗时
        cached: 是否命中查询缓存
        error: 错误摘要（非空表示失败）
        extra: 额外指标（如跨库 searched/failed）
    """
    if not user_id:
        return

    sample_rate = settings.analytics_retrieval_sample_rate
    if sample_rate < 1.0 and random.random() >= sample_rate:  # noqa: S311  # 采样非安全场景
        return

    try:
        from Django_xm.apps.knowledge.rag_settings import get_rag_runtime_settings

        runtime = get_rag_runtime_settings()
    except Exception:
        runtime = {}

    metadata = {
        "kind": kind,
        "k": k,
        "hits": int(hits),
        "sources": int(sources if sources is not None else hits),
        "degraded": bool(degraded),
        "cached": bool(cached),
        "threshold": runtime.get("score_threshold", settings.rag_score_threshold),
        "recall_k": runtime.get("recall_k", settings.rag_recall_k),
        "rerank": bool(runtime.get("use_reranker", settings.retriever_use_reranker)),
        "hybrid": bool(runtime.get("hybrid_enabled", settings.rag_hybrid_enabled)),
    }
    if extra:
        metadata.update(extra)

    payload = {
        "user_id": user_id,
        "event_type": EventType.RAG_QUERY.value,
        "event_category": EventCategory.RAG.value,
        "resource_id": (index_name or "")[:100],
        "resource_type": "knowledge_base",
        "duration_ms": int(duration_ms) if duration_ms is not None else None,
        "is_success": not error,
        "error_message": (error or "")[:500],
        "metadata": metadata,
    }

    try:
        from Django_xm.tasks.analytics_tasks import track_event

        track_event.delay(payload)
    except Exception as e:
        # 埋点不得影响检索主链：broker 不可用/序列化失败一律丢弃
        logger.debug(f"检索埋点投递失败（已忽略）: {e}")
