"""
上下文管理器 - 统一接口

整合压缩引擎、知识图谱、Store 持久化，
提供跨会话的上下文复用和可配置管理策略。

核心能力：
1. 统一的上下文管理入口
2. 跨会话上下文复用（通过 Store 持久化）
3. 可配置的压缩阈值、保留优先级等参数
4. 自动触发压缩和知识图谱更新
5. 为 Agent 动态注入上下文

参考：
- https://docs.langchain.com/oss/python/langchain/memory
- https://docs.langchain.com/oss/python/langgraph/persistence
"""

import time
import logging
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass

from Django_xm.apps.context_manager.config import context_settings, get_logger
from Django_xm.apps.context_manager.services.compression import (
    ContextCompressionEngine,
    CompressionConfig,
    CompressionResult,
    CompressionStrategy,
    TokenEstimator,
)
from Django_xm.apps.context_manager.services.knowledge_graph import (
    ContextKnowledgeGraph,
    Entity,
    Relation,
)

logger = get_logger(__name__)


@dataclass
class ContextManagementConfig:
    compression_enabled: bool = True
    compression_strategy: str = "hybrid"
    compression_threshold_ratio: float = 0.8
    compression_keep_recent: int = 6
    compression_summary_max_length: int = 1500

    knowledge_graph_enabled: bool = True
    knowledge_graph_max_hops: int = 2
    knowledge_graph_max_entities: int = 20

    cross_session_enabled: bool = True
    cross_session_max_context_length: int = 2000

    model_name: Optional[str] = None

    @classmethod
    def from_settings(cls) -> "ContextManagementConfig":
        return cls(
            compression_enabled=context_settings.compression_enabled,
            compression_strategy=context_settings.compression_strategy,
            compression_threshold_ratio=context_settings.compression_threshold_ratio,
            compression_keep_recent=context_settings.compression_keep_recent,
            compression_summary_max_length=context_settings.compression_summary_max_length,
            knowledge_graph_enabled=context_settings.kg_enabled,
            knowledge_graph_max_hops=context_settings.kg_max_hops,
            knowledge_graph_max_entities=context_settings.kg_max_entities,
            cross_session_enabled=context_settings.cross_session_enabled,
            cross_session_max_context_length=context_settings.cross_session_max_context_length,
        )


class ContextManager:
    """上下文管理器 - 统一接口"""

    def __init__(
        self,
        user_id: Optional[int] = None,
        config: Optional[ContextManagementConfig] = None,
        store=None,
    ):
        self.user_id = user_id
        self.config = config or ContextManagementConfig.from_settings()
        self._store = store

        self._compression_engine: Optional[ContextCompressionEngine] = None
        self._knowledge_graph: Optional[ContextKnowledgeGraph] = None

        if self.config.compression_enabled:
            model_limit = TokenEstimator.get_model_limit(self.config.model_name or "")
            comp_config = CompressionConfig(
                max_context_tokens=model_limit,
                token_threshold_ratio=self.config.compression_threshold_ratio,
                keep_recent_messages=self.config.compression_keep_recent,
                summary_max_length=self.config.compression_summary_max_length,
                strategy=CompressionStrategy(self.config.compression_strategy),
            )
            self._compression_engine = ContextCompressionEngine(comp_config)

        if self.config.knowledge_graph_enabled:
            self._knowledge_graph = ContextKnowledgeGraph(store=store)

    def process_messages(
        self,
        messages: List[Dict[str, Any]],
        session_id: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        metadata: Dict[str, Any] = {
            "compression": None,
            "knowledge_graph": None,
            "cross_session_context": None,
        }

        if self._knowledge_graph and self.user_id:
            try:
                entities, relations = self._knowledge_graph.process_conversation(
                    self.user_id, messages
                )
                metadata["knowledge_graph"] = {
                    "entity_count": len(entities),
                    "relation_count": len(relations),
                }
            except Exception as e:
                logger.warning(f"知识图谱更新失败（不影响主流程）: {e}")

        if self._compression_engine:
            compressed_messages, result = self._compression_engine.compress(messages)
            metadata["compression"] = {
                "compressed": result.compressed,
                "original_tokens": result.original_token_estimate,
                "compressed_tokens": result.compressed_token_estimate,
                "ratio": result.compression_ratio,
                "strategy": result.strategy_used.value if result.strategy_used else None,
                "key_entities": result.key_entities[:10],
            }
            return compressed_messages, metadata

        return messages, metadata

    def get_injection_context(
        self,
        query: str,
        session_id: Optional[str] = None,
    ) -> str:
        parts = []

        if self._knowledge_graph and self.user_id and self.config.knowledge_graph_enabled:
            try:
                kg_context = self._knowledge_graph.get_context_for_query(
                    self.user_id,
                    query,
                    max_hops=self.config.knowledge_graph_max_hops,
                    max_entities=self.config.knowledge_graph_max_entities,
                )
                if kg_context:
                    parts.append(kg_context)
            except Exception as e:
                logger.debug(f"知识图谱上下文检索失败: {e}")

        if self.config.cross_session_enabled and self.user_id:
            try:
                cross_ctx = self._load_cross_session_context(query)
                if cross_ctx:
                    parts.append(cross_ctx)
            except Exception as e:
                logger.debug(f"跨会话上下文加载失败: {e}")

        return "\n\n".join(parts) if parts else ""

    def save_session_context(
        self,
        session_id: str,
        summary: str,
        key_entities: List[str],
        key_decisions: List[str],
    ) -> bool:
        if not self.config.cross_session_enabled or not self.user_id:
            return False

        store = self._ensure_store()
        if store is None:
            return False

        namespace = (str(self.user_id), "session_contexts")
        data = {
            "session_id": session_id,
            "summary": summary,
            "key_entities": key_entities,
            "key_decisions": key_decisions,
            "saved_at": time.time(),
        }
        try:
            store.put(namespace, session_id, data)
            logger.debug(f"会话上下文已保存: session={session_id}")
            return True
        except Exception as e:
            logger.error(f"保存会话上下文失败: {e}")
            return False

    def _load_cross_session_context(self, query: str) -> str:
        store = self._ensure_store()
        if store is None or not self.user_id:
            return ""

        namespace = (str(self.user_id), "session_contexts")
        try:
            items = store.search(namespace)
            if not items:
                return ""

            contexts = []
            for item in items[:5]:
                data = item.value if hasattr(item, 'value') else item
                summary = data.get("summary", "")
                entities = data.get("key_entities", [])
                if summary:
                    relevance = self._compute_relevance(query, summary, entities)
                    if relevance > 0.1:
                        contexts.append((relevance, summary))

            contexts.sort(key=lambda x: x[0], reverse=True)
            if contexts:
                top_summaries = [ctx[1] for ctx in contexts[:3]]
                return "【跨会话上下文】\n" + "\n---\n".join(top_summaries)

        except Exception as e:
            logger.debug(f"跨会话上下文检索失败: {e}")

        return ""

    @staticmethod
    def _compute_relevance(query: str, summary: str, entities: List[str]) -> float:
        query_lower = query.lower()
        score = 0.0
        for entity in entities:
            if entity.lower() in query_lower:
                score += 0.3
        summary_words = set(summary.lower().split())
        query_words = set(query_lower.split())
        overlap = summary_words & query_words
        if query_words:
            score += len(overlap) / len(query_words) * 0.5
        return min(1.0, score)

    def _ensure_store(self):
        from Django_xm.apps.ai_engine.services.checkpointer_factory import ensure_store
        return ensure_store(self)

    def get_stats(self) -> Dict[str, Any]:
        stats = {
            "user_id": self.user_id,
            "compression_enabled": self.config.compression_enabled,
            "knowledge_graph_enabled": self.config.knowledge_graph_enabled,
            "cross_session_enabled": self.config.cross_session_enabled,
        }
        if self._knowledge_graph and self.user_id:
            entities, relations = self._knowledge_graph.get_full_graph(self.user_id)
            stats["kg_entity_count"] = len(entities)
            stats["kg_relation_count"] = len(relations)
        return stats


def create_context_manager(
    user_id: Optional[int] = None,
    store=None,
    model_name: Optional[str] = None,
) -> ContextManager:
    config = ContextManagementConfig.from_settings()
    config.model_name = model_name
    return ContextManager(user_id=user_id, config=config, store=store)
