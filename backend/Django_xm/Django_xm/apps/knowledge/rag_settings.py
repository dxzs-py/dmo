"""RAG 检索增强的运行时设置（SystemConfig 持久化 + 前端设置页可改）。

设计：
- 配置键 ``rag_retrieval``（SystemConfig JSON），读写经 django cache（TTL 3600s），
  保存后立即生效（``set_value`` 同步更新缓存）；
- **字段白名单 + 类型/范围归一**：非法值回退当前值，未知字段忽略；
- 默认值**动态**读取 ``ai_engine.config.Settings``（保证测试 patch settings 生效），
  SystemConfig 只做覆盖；
- 读取失败（DB 不可达 / 测试环境）→ 回退默认值，绝不阻断检索。

消费点（检索链路不再直接读 ``settings.rag_*``）：
- ``retrieval_service.create_retriever`` / ``create_reranker``（rerank/hybrid/阈值/窗口）
- ``query_expansion``（多路查询开关/数量）
- ``rag_context_pipeline.build_context``（去重/邻块/token 预算）
- ``rag_service.RagService``（检索缓存 TTL / 意图→召回数映射 / 工具预算）
- ``citation_verifier``（引用校验模型）
"""

from __future__ import annotations

from typing import Any

from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.apps.knowledge.config import settings

logger = get_logger(__name__)

RAG_SETTINGS_KEY = "rag_retrieval"

# FlashRank 官方模型列表（避免前端写入不存在的模型导致构造失败）
RERANK_MODELS = (
    "ms-marco-MiniLM-L-12-v2",
    "ms-marco-MultiBERT-L-12",
    "ms-marco-TinyBERT-L-2-v2",
    "ce-esci-MiniLM-L12-v2",
)

DEDUPE_MODES = ("gram", "embedding")
CITATION_MODEL_MODES = ("main", "helper")

# 字段规格：name -> (kind, settings_attr, extra...)
# kind: bool / float01 / int / enum
FIELD_SPECS: dict[str, tuple] = {
    "use_reranker": ("bool", "retriever_use_reranker"),
    "rerank_model": ("enum", "rag_rerank_model", RERANK_MODELS),
    "score_threshold": ("float01", "rag_score_threshold"),
    "recall_k": ("int", "rag_recall_k", 1, 200),
    "rerank_top_n": ("int", "rag_rerank_top_n", 1, 50),
    "hybrid_enabled": ("bool", "rag_hybrid_enabled"),
    "hybrid_dense_weight": ("float01", "rag_hybrid_dense_weight"),
    "hybrid_sparse_weight": ("float01", "rag_hybrid_sparse_weight"),
    "multi_query_enabled": ("bool", "rag_multi_query_enabled"),
    "multi_query_count": ("int", "rag_multi_query_count", 1, 8),
    "citation_verify_enabled": ("bool", "rag_citation_verify_enabled"),
    "citation_verify_model_mode": ("enum", "rag_citation_verify_model_mode", CITATION_MODEL_MODES),
    "dedupe_mode": ("enum", "rag_dedupe_mode", DEDUPE_MODES),
    "dedupe_similarity": ("float01", "rag_dedupe_similarity"),
    "context_window": ("int", "rag_context_window", 0, 3),
    "max_chunks_per_source": ("int", "rag_max_chunks_per_source", 1, 10),
    "doc_max_tokens": ("int", "rag_doc_max_tokens", 50, 4000),
    "context_max_tokens": ("int", "rag_context_max_tokens", 200, 32000),
    "attachment_ttl_days": ("int", "rag_attachment_ttl_days", 1, 365),
    "tool_max_context_tokens": ("int", "rag_tool_max_context_tokens", 200, 8000),
    "tool_include_sources": ("bool", "rag_tool_include_sources"),
    "attachment_collection_prefix": ("str", "rag_attachment_collection_prefix"),
    # Agent/学习/附件检索结果缓存（0=关闭）；索引写入/删除按 collection 失效
    "agent_cache_ttl": ("int", "rag_agent_cache_ttl", 0, 3600),
    # 全局分析意图（总结/整篇/有哪些）：放大召回数并放宽上下文预算
    "comprehensive_k": ("int", "retriever_comprehensive_k", 4, 40),
    "intent_classification_enabled": ("bool", "rag_intent_classification_enabled"),
}


def _to_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
    return default


def _coerce(name: str, value: Any, spec: tuple, fallback: Any) -> Any:
    kind = spec[0]
    try:
        if kind == "bool":
            return _to_bool(value, fallback)
        if kind == "float01":
            number = float(value)
            return min(max(number, 0.0), 1.0)
        if kind == "int":
            number = int(value)
            _, _attr, low, high = spec
            return min(max(number, low), high)
        if kind == "enum":
            allowed = spec[2]
            return value if value in allowed else fallback
        if kind == "str":
            return value if isinstance(value, str) and value.strip() else fallback
    except (TypeError, ValueError, IndexError):
        logger.debug(f"RAG 设置 {name}={value!r} 非法，回退 {fallback!r}")
        return fallback
    return fallback


def _settings_default(spec: tuple) -> Any:
    """动态读取代码默认值（patch settings 后立即生效）。"""
    return getattr(settings, spec[1], None)


def get_rag_runtime_settings() -> dict[str, Any]:
    """读取当前生效的 RAG 设置（SystemConfig 覆盖代码默认值）。

    DB/缓存不可用时返回默认值，绝不抛异常。
    """
    stored: dict[str, Any] = {}
    try:
        from Django_xm.apps.ai_engine.models import SystemConfig

        raw = SystemConfig.get_value(RAG_SETTINGS_KEY, {})
        if isinstance(raw, dict):
            stored = raw
    except Exception as e:
        logger.debug(f"读取 RAG 设置失败，使用默认值: {e}")

    result: dict[str, Any] = {}
    for name, spec in FIELD_SPECS.items():
        default = _settings_default(spec)
        result[name] = _coerce(name, stored.get(name, default), spec, default)
    return result


def save_rag_runtime_settings(payload: dict[str, Any]) -> dict[str, Any]:
    """合并保存 RAG 设置（白名单字段），返回保存后生效的完整配置。"""
    current = get_rag_runtime_settings()
    if not isinstance(payload, dict):
        return current

    for name, spec in FIELD_SPECS.items():
        if name not in payload:
            continue
        current[name] = _coerce(name, payload[name], spec, current[name])

    try:
        from Django_xm.apps.ai_engine.models import SystemConfig

        SystemConfig.set_value(RAG_SETTINGS_KEY, current)
    except Exception as e:
        logger.warning(f"保存 RAG 设置失败: {e}")
    return current
