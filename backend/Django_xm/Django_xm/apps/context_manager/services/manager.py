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
from dataclasses import dataclass
from typing import Any

from Django_xm.apps.context_manager.config import context_settings
from Django_xm.apps.context_manager.services.attention_guide import AttentionGuide
from Django_xm.apps.context_manager.services.circuit_breaker import ContextCircuitBreaker
from Django_xm.apps.context_manager.services.compression import (
    CompressionConfig,
    CompressionStrategy,
    ContextCompressionEngine,
    MemoryTier,
    TokenEstimator,
)
from Django_xm.apps.context_manager.services.context_builder import (
    BuildMode,
    ContextBuilder,
    create_context_builder,
)
from Django_xm.apps.context_manager.services.context_pruner import ContextPruner
from Django_xm.apps.context_manager.services.knowledge_graph import (
    ContextKnowledgeGraph,
)
from Django_xm.apps.context_manager.services.token_budget import ContextEfficiencyMetrics, TokenBudgetManager
from Django_xm.apps.core.config import get_logger

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

    model_name: str | None = None

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
        user_id: int | None = None,
        config: ContextManagementConfig | None = None,
        store=None,
        thread_id: str | None = None,
    ):
        self.user_id = user_id
        self.config = config or ContextManagementConfig.from_settings()
        self._store = store
        self._thread_id = thread_id

        self._compression_engine: ContextCompressionEngine | None = None
        self._knowledge_graph: ContextKnowledgeGraph | None = None

        self._token_budget_manager = TokenBudgetManager()
        self._attention_guide = AttentionGuide()
        self._context_pruner = ContextPruner()
        self._circuit_breaker = ContextCircuitBreaker()
        self._efficiency_metrics = ContextEfficiencyMetrics()

        if self.config.compression_enabled:
            model_limit = TokenEstimator.get_model_limit(self.config.model_name or "")
            comp_config = CompressionConfig(
                max_context_tokens=model_limit,
                token_threshold_ratio=self.config.compression_threshold_ratio,
                keep_recent_messages=self.config.compression_keep_recent,
                summary_max_length=self.config.compression_summary_max_length,
                strategy=CompressionStrategy(self.config.compression_strategy),
            )
            self._compression_engine = ContextCompressionEngine(
                comp_config,
                store=store,
                user_id=str(user_id) if user_id else None,
                thread_id=thread_id,
            )

        if self.config.knowledge_graph_enabled:
            self._knowledge_graph = ContextKnowledgeGraph(store=store)

    def prune_messages(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], Any]:
        return self._context_pruner.prune(messages)

    def check_injection(self, text: str) -> bool:
        return self._circuit_breaker.detect_injection(text)

    def allocate_budget(self, model_name: str) -> None:
        self._token_budget_manager.allocate(model_name)

    def record_budget_usage(self, section: str, tokens: int) -> Any:
        return self._token_budget_manager.record_usage(section, tokens)

    def get_budget_usage(self) -> dict[str, Any]:
        return self._token_budget_manager.get_usage()

    def serialize_messages(self, messages: list[dict[str, Any]]) -> str:
        return self._serialize_messages(messages)

    def mark_long_term(
        self,
        messages: list[dict[str, Any]],
        tags: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """根据 role/content 自动标记长期记忆"""
        effective_tags = tags or [t.strip() for t in context_settings.long_term_tags.split(",") if t.strip()]
        marked = []
        for msg in messages:
            # role == "system" 的消息标记为 long_term
            if msg.get("role") == "system":
                marked.append({**msg, "memory_tier": "long_term"})
                continue
            # content 中包含 tags 中关键词的消息标记为 long_term
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    block.get("text", "") if isinstance(block, dict) else str(block) for block in content
                )
            if isinstance(content, str):
                content_lower = content.lower()
                if any(tag.lower() in content_lower for tag in effective_tags):
                    marked.append({**msg, "memory_tier": "long_term"})
                    continue
            # 已标记的保留
            if msg.get("memory_tier") == "long_term":
                marked.append({**msg, "memory_tier": "long_term"})
                continue
            marked.append(msg)
        return marked

    @staticmethod
    def mark_long_term_static(
        messages: list[dict[str, Any]],
        tags: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """静态方法：根据 role/content 自动标记长期记忆（供中间件调用）"""
        if tags is None:
            tags = (
                context_settings.long_term_tags.split(",")
                if context_settings.long_term_tags
                else ["system", "preference", "decision"]
            )

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    block.get("text", "") if isinstance(block, dict) else str(block) for block in content
                )

            # 已标记的跳过
            if msg.get("memory_tier") == MemoryTier.LONG_TERM.value:
                continue

            # system 角色标记为长期记忆
            if role == "system":
                msg["memory_tier"] = MemoryTier.LONG_TERM.value
                continue

            # content 中包含长期记忆关键词
            if tags and isinstance(content, str):
                content_lower = content.lower()
                for tag in tags:
                    if tag.lower() in content_lower:
                        msg["memory_tier"] = MemoryTier.LONG_TERM.value
                        break

        return messages

    def process_messages(
        self,
        messages: list[dict[str, Any]],
        session_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        metadata: dict[str, Any] = {
            "compression": None,
            "knowledge_graph": None,
            "cross_session_context": None,
        }

        # 在压缩前先标记长期记忆
        messages = self.mark_long_term(messages)

        if self._knowledge_graph and self.user_id:
            try:
                entities, relations = self._knowledge_graph.process_conversation(self.user_id, messages)
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
        session_id: str | None = None,
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

    def build_prompt_context(
        self,
        mode: str = "default",
        session_id: str | None = None,
        include_document_context: bool = True,
        include_knowledge_graph: bool = True,
        query: str | None = None,
        model_name: str | None = None,
        tools_description: str | None = None,
    ) -> str:
        _AGENT_MODES = {"agent"}
        _FULL_MODES = {"deep-research"}

        if mode in _AGENT_MODES:
            build_mode = BuildMode.AGENT
        elif mode in _FULL_MODES:
            build_mode = BuildMode.FULL
        else:
            build_mode = BuildMode.CHAT

        builder = ContextBuilder(mode=build_mode)

        if tools_description:
            builder.add_tools(tools_description)

        memory_parts: list[str] = []

        if include_document_context and self.user_id:
            try:
                from Django_xm.apps.attachments.services.document_memory_service import DocumentMemoryService

                doc_service = DocumentMemoryService(store=self._store)
                doc_context = doc_service.build_document_context(self.user_id)
                if doc_context:
                    memory_parts.append(f"{doc_context}\n请在回答时优先参考用户已上传的文档内容。")
                    builder.add_memory(memory_parts[-1])
            except Exception as e:
                logger.debug(f"动态注入文档上下文失败（不影响主流程）: {e}")

        if include_knowledge_graph and self.user_id and query:
            try:
                kg_context = self.get_injection_context(query, session_id=session_id)
                if kg_context:
                    memory_parts.append(kg_context)
                    builder.add_memory(kg_context)
            except Exception as e:
                logger.debug(f"动态注入知识图谱上下文失败（不影响主流程）: {e}")

        result = builder.build()
        result = self._attention_guide.highlight_critical(result)

        if model_name:
            try:
                self._token_budget_manager.allocate(model_name)

                if tools_description:
                    tools_tokens = TokenEstimator.estimate(tools_description)
                    check = self._token_budget_manager.record_usage("tools", tools_tokens)
                    if not check.within_budget:
                        logger.warning(
                            "上下文分区 'tools' 超出 Token 预算: "
                            "used=%d, budget=%d, over_by=%d",
                            check.used,
                            check.budget,
                            check.over_by,
                        )

                memory_content = "\n".join(memory_parts)
                if memory_content:
                    memory_tokens = TokenEstimator.estimate(memory_content)
                    check = self._token_budget_manager.record_usage("memory", memory_tokens)
                    if not check.within_budget:
                        logger.warning(
                            f"上下文分区 'memory' 超出 Token 预算: "
                            f"used={check.used}, budget={check.budget}, over_by={check.over_by}"
                        )
            except Exception as e:
                logger.debug(f"Token 预算检查失败（不影响主流程）: {e}")

        return result

    def build_structured_context(
        self,
        messages: list[Any],
        query: str,
        mode: str = "full",
        model_name: str | None = None,
        tools_description: str | None = None,
        llm=None,
    ) -> dict[str, Any]:
        """构建结构化上下文，包含基础上下文、压缩和预算检查"""
        # 支持 BaseMessage 列表输入（自动转换为 Dict 列表）
        from langchain_core.messages import BaseMessage

        if messages and isinstance(messages[0], BaseMessage):
            messages = self._messages_to_dicts(messages)

        injection_detected = self._circuit_breaker.detect_injection(query)
        self._circuit_breaker.save_checkpoint(messages)
        pruned_messages, prune_result = self._context_pruner.prune(messages)

        # 对 pruned_messages 标记长期记忆
        pruned_messages = self.mark_long_term(pruned_messages)

        resolved_model = model_name or self.config.model_name or ""
        self._token_budget_manager.allocate(resolved_model)

        ctx = self._build_base_context(
            pruned_messages,
            query,
            mode,
            tools_description,
            llm,
        )

        budget_over_sections = self._check_token_budget()
        if budget_over_sections:
            self._apply_compression_if_needed(
                ctx,
                pruned_messages,
                budget_over_sections,
            )
            # 记录压缩效率
            if self._compression_engine is not None:
                compressed_history = ctx.get("history_text", "")
                original_history = self._serialize_messages(pruned_messages)
                original_tokens = TokenEstimator.estimate(original_history)
                compressed_tokens = TokenEstimator.estimate(compressed_history) if compressed_history else 0
                self._efficiency_metrics.record_compression(original_tokens, compressed_tokens)
            budget_over_sections = self._check_token_budget()

            # 压缩后仍超预算，使用语义相关性进一步筛选历史消息
            if budget_over_sections and "history" in budget_over_sections:
                pruned_messages, relevance_removed = self._context_pruner.prune_by_relevance(
                    pruned_messages,
                    query,
                    keep_count=self.config.compression_keep_recent,
                )
                if relevance_removed > 0:
                    ctx = self._build_base_context(
                        pruned_messages,
                        query,
                        mode,
                        tools_description,
                        llm,
                    )
                    self._token_budget_manager.reset_usage()
                    budget_over_sections = self._check_token_budget()

        raw_prompt = ctx["builder"].build()

        # 记录上下文注入效率
        injected_tokens = TokenEstimator.estimate(raw_prompt)
        # 检索结果token数：system_content（知识图谱/跨会话检索）的token数
        retrieved_tokens = TokenEstimator.estimate(ctx["system_content"]) if ctx["system_content"] else 0
        self._efficiency_metrics.record_injection(injected_tokens, retrieved_tokens)

        sections_dict = self._build_sections_dict(ctx)
        structured_prompt = self._attention_guide.reorder_sections(sections_dict)

        circuit_breaker_tripped = self._circuit_breaker.state.tripped
        if circuit_breaker_tripped:
            logger.warning("熔断器已触发，回滚到安全检查点重建上下文")
            structured_prompt = self._rollback_context(ctx, query)

        usage = self._token_budget_manager.get_usage()
        return {
            "structured_prompt": structured_prompt,
            "sections": sections_dict,
            "pruned_messages": pruned_messages,
            "metadata": {
                "budget_usage": usage,
                "prune_result": {
                    "original_count": prune_result.original_count,
                    "pruned_count": prune_result.pruned_count,
                    "deduped_count": prune_result.deduped_count,
                    "filtered_count": prune_result.filtered_count,
                },
                "injection_detected": injection_detected,
                "budget_over_sections": budget_over_sections,
                "circuit_breaker_tripped": circuit_breaker_tripped,
            },
        }

    def _build_base_context(
        self,
        pruned_messages: list[dict[str, Any]],
        query: str,
        mode: str,
        tools_description: str | None,
        llm,
    ) -> dict[str, Any]:
        """构建基础上下文：系统内容、工具描述、历史、记忆、查询"""
        build_mode = BuildMode(mode)
        builder = create_context_builder(mode=mode)

        system_content = self.get_injection_context(query, session_id=None)
        if system_content:
            # 将 system prompt 标记为 LONG_TERM
            builder.add_system(system_content)

        if tools_description and build_mode in (BuildMode.FULL, BuildMode.AGENT):
            builder.add_tools(tools_description)
            self._token_budget_manager.record_usage("tools", TokenEstimator.estimate(tools_description))

        # 将知识图谱上下文中的关键实体标记为 LONG_TERM
        kg_marked_messages = []
        for msg in pruned_messages:
            if msg.get("memory_tier") == "long_term":
                kg_marked_messages.append(msg)
            elif system_content and msg.get("role") == "system":
                # system prompt 标记为 LONG_TERM
                kg_marked_messages.append({**msg, "memory_tier": "long_term"})
            else:
                kg_marked_messages.append(msg)
        pruned_messages = kg_marked_messages

        history_text = self._serialize_messages(pruned_messages)
        if history_text:
            builder.add_history(history_text)
            self._token_budget_manager.record_usage("history", TokenEstimator.estimate(history_text))

        if system_content:
            self._token_budget_manager.record_usage("system", TokenEstimator.estimate(system_content))

        memory_content = ""
        if llm is not None and build_mode in (BuildMode.FULL, BuildMode.AGENT):
            try:
                from Django_xm.apps.context_manager.services.retrieval_augmenter import RetrievalAugmenter

                rewritten = RetrievalAugmenter.hyde_rewrite_sync(query, llm)
                if rewritten and rewritten != query:
                    memory_content = f"[HyDE 改写查询]\n{rewritten}"
                    builder.add_memory(memory_content)
                    self._token_budget_manager.record_usage("memory", TokenEstimator.estimate(memory_content))
            except Exception as e:
                logger.debug(f"RetrievalAugmenter HyDE 改写失败（不影响主流程）: {e}")

        state_block = self._attention_guide.get_state_block()
        if state_block:
            builder.add_state(state_block)

        sanitized_query = self._circuit_breaker.sanitize_user_input(query)
        builder.add_query(sanitized_query)
        self._token_budget_manager.record_usage("query", TokenEstimator.estimate(query))

        return {
            "builder": builder,
            "system_content": system_content,
            "tools_description": tools_description,
            "build_mode": build_mode,
            "memory_content": memory_content,
            "history_text": history_text,
            "sanitized_query": sanitized_query,
        }

    def _apply_compression_if_needed(
        self,
        ctx: dict[str, Any],
        pruned_messages: list[dict[str, Any]],
        budget_over_sections: list[str],
    ) -> None:
        """当 Token 预算超限时，压缩历史消息并重建上下文"""
        if self._compression_engine is None:
            return

        logger.info(f"Token 预算超限分区: {budget_over_sections}, 触发自动压缩")
        compressed_messages, comp_result = self._compression_engine.compress(pruned_messages)
        if not comp_result.compressed:
            return

        builder = ctx["builder"]
        system_content = ctx["system_content"]
        tools_description = ctx["tools_description"]
        build_mode = ctx["build_mode"]
        memory_content = ctx["memory_content"]
        sanitized_query = ctx["sanitized_query"]

        history_text = self._serialize_messages(compressed_messages)
        builder.reset()
        if system_content:
            builder.add_system(system_content)
        if tools_description and build_mode in (BuildMode.FULL, BuildMode.AGENT):
            builder.add_tools(tools_description)
        if memory_content:
            builder.add_memory(memory_content)
        if history_text:
            builder.add_history(history_text)
        state_block = self._attention_guide.get_state_block()
        if state_block:
            builder.add_state(state_block)
        builder.add_query(sanitized_query)

        self._token_budget_manager.reset_usage()
        if system_content:
            self._token_budget_manager.record_usage("system", TokenEstimator.estimate(system_content))
        if tools_description and build_mode in (BuildMode.FULL, BuildMode.AGENT):
            self._token_budget_manager.record_usage("tools", TokenEstimator.estimate(tools_description))
        if memory_content:
            self._token_budget_manager.record_usage("memory", TokenEstimator.estimate(memory_content))
        self._token_budget_manager.record_usage("history", TokenEstimator.estimate(history_text))
        self._token_budget_manager.record_usage("query", TokenEstimator.estimate(sanitized_query))

        ctx["history_text"] = history_text

    def _check_token_budget(self) -> list[str]:
        """检查各分区 Token 预算，返回超限分区列表"""
        budget_over_sections: list[str] = []
        usage = self._token_budget_manager.get_usage()
        for section_name, info in usage.items():
            if info["used"] > info["budget"]:
                budget_over_sections.append(section_name)
        return budget_over_sections

    def _build_sections_dict(self, ctx: dict[str, Any]) -> dict[str, str]:
        """从上下文信息构建分区字典"""
        sections_dict: dict[str, str] = {}
        if ctx["system_content"]:
            sections_dict["system"] = ctx["system_content"]
        if ctx["tools_description"] and ctx["build_mode"] in (BuildMode.FULL, BuildMode.AGENT):
            sections_dict["tools"] = ctx["tools_description"]
        if ctx["memory_content"]:
            sections_dict["memory"] = ctx["memory_content"]
        if ctx["history_text"]:
            sections_dict["history"] = ctx["history_text"]
        sections_dict["user_query"] = ctx["sanitized_query"]
        return sections_dict

    def _rollback_context(self, ctx: dict[str, Any], query: str) -> str:
        """熔断器触发时回滚到安全检查点重建上下文"""
        rollback_messages = self._circuit_breaker.rollback()
        if not rollback_messages:
            return ctx["builder"].build()

        builder = ctx["builder"]
        rollback_history = self._serialize_messages(rollback_messages)
        builder.reset()
        if ctx["system_content"]:
            builder.add_system(ctx["system_content"])
        if rollback_history:
            builder.add_history(rollback_history)
        state_block = self._attention_guide.get_state_block()
        if state_block:
            builder.add_state(state_block)
        builder.add_query(ctx["sanitized_query"])
        return builder.build()

    @staticmethod
    def _messages_to_dicts(messages: list[Any]) -> list[dict[str, Any]]:
        """将 BaseMessage 列表转换为 Dict 列表"""
        from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

        result = []
        for msg in messages:
            if not isinstance(msg, BaseMessage):
                result.append(msg)
                continue

            role_map = {
                HumanMessage: "user",
                AIMessage: "assistant",
                SystemMessage: "system",
                ToolMessage: "tool",
            }
            role = role_map.get(type(msg), "unknown")
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            entry: dict[str, Any] = {"role": role, "content": content}
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                entry["tool_calls"] = msg.tool_calls
            if hasattr(msg, "additional_kwargs") and "memory_tier" in msg.additional_kwargs:
                entry["memory_tier"] = msg.additional_kwargs["memory_tier"]
            result.append(entry)
        return result

    @staticmethod
    def _serialize_messages(messages: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(c.get("text", str(c)) if isinstance(c, dict) else str(c) for c in content)
            parts.append(f"[{role}]: {content}")
        return "\n".join(parts)

    def save_session_context(
        self,
        session_id: str,
        summary: str,
        key_entities: list[str],
        key_decisions: list[str],
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
            # 淘汰超限的旧记忆
            max_entries = context_settings.cross_session_max_entries
            try:
                existing_items = store.search(namespace)
                if existing_items and len(existing_items) >= max_entries:
                    # 按 saved_at 排序，淘汰最旧的
                    sorted_items = sorted(
                        existing_items,
                        key=lambda item: (item.value if hasattr(item, "value") else item).get("saved_at", 0),
                    )
                    evict_count = len(existing_items) - max_entries + 1
                    for item in sorted_items[:evict_count]:
                        item_key = item.key if hasattr(item, "key") else str(item.value.get("session_id", ""))
                        if item_key and item_key != session_id:  # 不淘汰当前要保存的
                            try:
                                store.delete(namespace, item_key)
                                logger.debug(f"淘汰旧会话记忆: key={item_key}")
                            except Exception:  # noqa: S110  # cleanup, 单个旧记忆删除失败不影响整体淘汰
                                pass
            except Exception as e:
                logger.debug(f"跨会话记忆淘汰检查失败（不影响保存）: {e}")

            store.put(namespace, session_id, data)
            logger.debug(f"会话上下文已保存: session={session_id}")
            return True
        except Exception:
            logger.exception("保存会话上下文失败")
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
                data = item.value if hasattr(item, "value") else item
                summary = data.get("summary", "")
                entities = data.get("key_entities", [])
                if summary:
                    relevance = self._compute_relevance(query, summary, entities)
                    if relevance > 0.1:
                        contexts.append((relevance, summary))

            contexts.sort(key=lambda x: x[0], reverse=True)
            if len(items) > context_settings.cross_session_max_entries:
                logger.info(
                    f"跨会话记忆条目数 {len(items)} 超过上限 {context_settings.cross_session_max_entries}，"
                    f"仅使用最相关的 {min(3, len(contexts))} 条"
                )
            if contexts:
                top_summaries = [ctx[1] for ctx in contexts[:3]]
                return "【跨会话上下文】\n" + "\n---\n".join(top_summaries)

        except Exception as e:
            logger.debug(f"跨会话上下文检索失败: {e}")

        return ""

    @staticmethod
    def _compute_relevance(query: str, summary: str, entities: list[str]) -> float:
        """综合评分：0.3 * 词重叠分 + 0.3 * 实体匹配分 + 0.4 * Embedding相似度"""
        query_lower = query.lower()

        # 词重叠分
        summary_words = set(summary.lower().split())
        query_words = set(query_lower.split())
        overlap = summary_words & query_words
        word_overlap_score = len(overlap) / len(query_words) * 1.0 if query_words else 0.0

        # 实体匹配分
        entity_score = 0.0
        for entity in entities:
            if entity.lower() in query_lower:
                entity_score += 0.3
        entity_score = min(1.0, entity_score)

        # Embedding 语义相似度
        embedding_score = ContextManager._compute_embedding_similarity(query, summary)

        # 综合评分
        if embedding_score is not None:
            return min(1.0, 0.3 * word_overlap_score + 0.3 * entity_score + 0.4 * embedding_score)
        else:
            # Embedding 不可用时回退到纯词重叠计算
            score = 0.0
            for entity in entities:
                if entity.lower() in query_lower:
                    score += 0.3
            if query_words:
                score += len(overlap) / len(query_words) * 0.5
            return min(1.0, score)

    @staticmethod
    def _compute_embedding_similarity(text_a: str, text_b: str) -> float | None:
        """使用 Embedding 计算两段文本的余弦相似度，不可用时返回 None"""
        try:
            import numpy as np

            from Django_xm.apps.knowledge.services.embedding_service import get_embeddings

            embeddings = get_embeddings()
            vec_a = embeddings.embed_query(text_a)
            vec_b = embeddings.embed_query(text_b)

            vec_a_arr = np.array(vec_a)
            vec_b_arr = np.array(vec_b)
            norm_a = np.linalg.norm(vec_a_arr)
            norm_b = np.linalg.norm(vec_b_arr)
            if norm_a == 0 or norm_b == 0:
                return None
            return float(np.dot(vec_a_arr, vec_b_arr) / (norm_a * norm_b))
        except Exception as e:
            logger.debug(f"Embedding 语义相似度计算失败，回退词重叠: {e}")
            return None

    def _ensure_store(self):
        from Django_xm.apps.ai_engine.services.checkpointer_factory import ensure_store

        return ensure_store(self)

    def get_stats(self) -> dict[str, Any]:
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

    def get_efficiency_metrics(self) -> dict[str, float]:
        """获取上下文使用效率度量摘要"""
        return self._efficiency_metrics.get_summary()


def create_context_manager(
    user_id: int | None = None,
    store=None,
    model_name: str | None = None,
    thread_id: str | None = None,
) -> ContextManager:
    config = ContextManagementConfig.from_settings()
    config.model_name = model_name
    return ContextManager(user_id=user_id, config=config, store=store, thread_id=thread_id)
