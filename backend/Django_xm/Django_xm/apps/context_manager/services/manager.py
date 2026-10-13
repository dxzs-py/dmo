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
    EXTRACTED_DECISIONS_LIMIT,
    EXTRACTED_ENTITIES_LIMIT,
    CompressionConfig,
    CompressionStrategy,
    ContextCompressionEngine,
    EntityExtractor,
    TokenEstimator,
    messages_to_dicts,
    parse_compression_strategy,
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
from Django_xm.apps.context_manager.services.runtime_providers import (
    get_document_context_provider,
    get_embedding_provider,
)
from Django_xm.apps.context_manager.services.token_budget import TokenBudgetManager, trim_messages_to_budget
from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.common.messages import content_to_str

logger = get_logger(__name__)

# 跨会话记忆摘要指令（spec D2/P2）：本轮压缩与增量合并共用 summarize_long_text 通道
_TURN_SUMMARY_INSTRUCTION = (
    "请将以下一轮对话的要点压缩为不超过3句话的中文摘要，"
    "保留用户意图、关键结论与偏好信息，直接输出摘要文本，不要任何额外说明。"
)
_TURN_SUMMARY_MERGE_INSTRUCTION = (
    "以下是同一会话的历史累积摘要与本轮新增摘要，请将两者合并为不超过5句话的中文累积摘要，"
    "保留历史关键决策与实体，并纳入本轮新要点，直接输出合并后的摘要文本，不要任何额外说明。"
)
# 合并去重后的容量上限（单一权威常量：compression.py 抽取截断与本处合并共用）
_CROSS_SESSION_ENTITY_LIMIT = EXTRACTED_ENTITIES_LIMIT
_CROSS_SESSION_DECISION_LIMIT = EXTRACTED_DECISIONS_LIMIT


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

        if self.config.compression_enabled:
            model_limit = TokenEstimator.get_model_limit(self.config.model_name or "")
            try:
                strategy = parse_compression_strategy(self.config.compression_strategy)
            except ValueError as exc:
                # 存量配置含已删除策略（sliding_window）等非法值：明确报错并回退
                # 默认策略，不留静默兼容（spec fix-review-round2 4.2）
                logger.error(
                    "压缩策略配置无效，已回退默认策略 'hybrid'（请修正 compression_strategy 配置）: %s",
                    exc,
                )
                strategy = CompressionStrategy.HYBRID
            comp_config = CompressionConfig(
                max_context_tokens=model_limit,
                token_threshold_ratio=self.config.compression_threshold_ratio,
                keep_recent_messages=self.config.compression_keep_recent,
                summary_max_length=self.config.compression_summary_max_length,
                strategy=strategy,
            )
            self._compression_engine = ContextCompressionEngine(comp_config)

        if self.config.knowledge_graph_enabled:
            self._knowledge_graph = ContextKnowledgeGraph(store=store)

    def prune_messages(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], Any]:
        return self._context_pruner.prune(messages)

    def check_injection(self, text: str) -> bool:
        return self._circuit_breaker.detect_injection(text)

    def allocate_budget(self, model_name: str) -> None:
        self._token_budget_manager.allocate(model_name)

    def mark_long_term(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """结构化标记长期记忆（唯一实现，禁止内容关键词启发式）

        标记语义与 ContextManagerMiddleware._is_long_term_message 对齐：
        - role == "system" → 标记 memory_tier=long_term（system prompt 永不压缩）
        - 已显式标记 memory_tier=long_term 的消息保留标记
        - 其余消息不做任何内容匹配——含 "system/decision" 等关键词的普通
          消息正常参与压缩（关键词误标会阻止压缩、使上下文只增不减）
        """
        marked = []
        for msg in messages:
            if msg.get("role") == "system":
                marked.append({**msg, "memory_tier": "long_term"})
                continue
            if msg.get("memory_tier") == "long_term":
                marked.append(msg)
                continue
            marked.append(msg)
        return marked

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
                # 环 1 消边：原函数内 import attachments.document_memory_service
                # （其内部依赖 ai_engine.llm_factory，构成传递违规），改经
                # runtime_providers 注入（attachments apps.ready() 注册）。
                document_context_provider = get_document_context_provider()
                if document_context_provider is None:
                    logger.debug("文档上下文提供者未注册，跳过文档记忆注入")
                else:
                    doc_context = document_context_provider(self.user_id, self._store)
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
    ) -> dict[str, Any]:
        """构建结构化上下文，包含基础上下文、压缩、相关性筛选与预算最终裁剪（A12 兜底）"""
        # 支持 BaseMessage 列表输入（自动转换为 Dict 列表）
        from langchain_core.messages import BaseMessage

        if messages and isinstance(messages[0], BaseMessage):
            messages = self._messages_to_dicts(messages)

        injection_detected = self._circuit_breaker.detect_injection(query)
        pruned_messages, prune_result = self._context_pruner.prune(messages)

        # 对 pruned_messages 标记长期记忆
        pruned_messages = self.mark_long_term(pruned_messages)

        resolved_model = model_name or self.config.model_name or ""
        self._token_budget_manager.allocate(resolved_model)

        ctx = self._build_base_context(pruned_messages, query, mode, tools_description)

        budget_over_sections = self._check_token_budget()
        compression_degraded = False
        if budget_over_sections:
            compression_degraded = self._apply_compression_if_needed(ctx, pruned_messages, budget_over_sections)
            budget_over_sections = self._check_token_budget()

            # 压缩后仍超预算，使用语义相关性进一步筛选历史消息（保时序，见 context_pruner）
            if budget_over_sections and "history" in budget_over_sections:
                pruned_messages, relevance_removed = self._context_pruner.prune_by_relevance(
                    pruned_messages,
                    query,
                    keep_count=self.config.compression_keep_recent,
                )
                if relevance_removed > 0:
                    # 先清零旧用量再重建：_build_base_context 内部会重新记录各分区用量
                    # （原实现在重建之后 reset，将新记录清零导致后续预算检查恒为未超——顺带修正）
                    self._token_budget_manager.reset_usage()
                    ctx = self._build_base_context(pruned_messages, query, mode, tools_description)
                    budget_over_sections = self._check_token_budget()

        # A12 最终裁剪兜底：压缩 + 相关性筛选后 history 仍超预算时，按预算截断最旧消息
        budget_trimmed = {"trimmed": False, "removed_count": 0}
        if "history" in budget_over_sections:
            history_budget = self._token_budget_manager.get_section_budget("history")
            pruned_messages, trim_result = trim_messages_to_budget(
                pruned_messages,
                history_budget,
                keep_recent=self.config.compression_keep_recent,
                # 4.4 token 校准：resolved_model 在上方 allocate 时已确定
                model_name=resolved_model,
            )
            if trim_result.trimmed:
                logger.warning(
                    f"压缩与相关性筛选后仍超 Token 预算，最终裁剪最旧消息: "
                    f"移除 {trim_result.removed_count} 条, "
                    f"{trim_result.original_tokens} -> {trim_result.trimmed_tokens} tokens"
                )
                budget_trimmed = {"trimmed": True, "removed_count": trim_result.removed_count}
                self._token_budget_manager.reset_usage()
                ctx = self._build_base_context(pruned_messages, query, mode, tools_description)
                budget_over_sections = self._check_token_budget()

        sections_dict = self._build_sections_dict(ctx)
        structured_prompt = self._attention_guide.reorder_sections(sections_dict)

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
                "budget_trimmed": budget_trimmed,
                # 4.4：摘要质量门禁降级标记（消费端 context_service → chat 转用户可见提示）
                "compression_degraded": compression_degraded,
            },
        }

    def _build_base_context(
        self,
        pruned_messages: list[dict[str, Any]],
        query: str,
        mode: str,
        tools_description: str | None,
    ) -> dict[str, Any]:
        """构建基础上下文：系统内容、工具描述、历史、查询

        （A15 清理：原 HyDE 改写文本被当作 memory 注入（RetrievalAugmenter 误用，
        HyDE 本意是检索改写，真实消费方在 knowledge.strict_rag_chain），该注入链
        与恒空的 <state> 块注入已移除。）
        """
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

        sanitized_query = self._circuit_breaker.sanitize_user_input(query)
        builder.add_query(sanitized_query)
        self._token_budget_manager.record_usage("query", TokenEstimator.estimate(query))

        return {
            "builder": builder,
            "system_content": system_content,
            "tools_description": tools_description,
            "build_mode": build_mode,
            "history_text": history_text,
            "sanitized_query": sanitized_query,
        }

    def _apply_compression_if_needed(
        self,
        ctx: dict[str, Any],
        pruned_messages: list[dict[str, Any]],
        budget_over_sections: list[str],
    ) -> bool:
        """当 Token 预算超限时，压缩历史消息并重建上下文。

        返回是否发生"质量门禁降级摘要"（4.4：供 build_structured_context
        写入 metadata，经消费端转为用户可见提示）。
        """
        if self._compression_engine is None:
            return False

        logger.info(f"Token 预算超限分区: {budget_over_sections}, 触发自动压缩")
        compressed_messages, comp_result = self._compression_engine.compress(pruned_messages)
        if not comp_result.compressed:
            return False

        builder = ctx["builder"]
        system_content = ctx["system_content"]
        tools_description = ctx["tools_description"]
        build_mode = ctx["build_mode"]
        sanitized_query = ctx["sanitized_query"]

        history_text = self._serialize_messages(compressed_messages)
        builder.reset()
        if system_content:
            builder.add_system(system_content)
        if tools_description and build_mode in (BuildMode.FULL, BuildMode.AGENT):
            builder.add_tools(tools_description)
        if history_text:
            builder.add_history(history_text)
        builder.add_query(sanitized_query)

        self._token_budget_manager.reset_usage()
        if system_content:
            self._token_budget_manager.record_usage("system", TokenEstimator.estimate(system_content))
        if tools_description and build_mode in (BuildMode.FULL, BuildMode.AGENT):
            self._token_budget_manager.record_usage("tools", TokenEstimator.estimate(tools_description))
        self._token_budget_manager.record_usage("history", TokenEstimator.estimate(history_text))
        self._token_budget_manager.record_usage("query", TokenEstimator.estimate(sanitized_query))

        ctx["history_text"] = history_text
        return bool(comp_result.degraded)

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
        if ctx["history_text"]:
            sections_dict["history"] = ctx["history_text"]
        sections_dict["user_query"] = ctx["sanitized_query"]
        return sections_dict

    @staticmethod
    def _messages_to_dicts(messages: list[Any]) -> list[dict[str, Any]]:
        """BaseMessage → dict：委托统一适配层单源（phase6a G3 归一）。

        行为修复（G3 漂移）：原实现缺 id / tool_call_id 字段（消息身份丢失），
        现与 compression 单源对齐——序列化产物保留消息身份。消费方
        （_serialize_messages / mark_long_term / ContextPruner）仅读
        role/content/name/pinned/memory_tier 等固定键，新增字段无副作用；
        若产物进入 add_messages reducer 往返，id 保障替换而非追加。
        非 BaseMessage 条目原样透传（原宽输入语义已并入单源）。
        """
        return messages_to_dicts(messages)

    @staticmethod
    def _serialize_messages(messages: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = content_to_str(content)
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
            # 淘汰超限的旧记忆。4.2：search 显式传 limit=max_entries+1——
            # Store.search 默认 limit=10 < max_entries（默认 20），长度判定
            # 永不达阈值、淘汰永不触发；+1 使"超限溢出"可检测（存量超限数据
            # 每次保存净淘汰 1 条，逐步收敛到上限）。
            max_entries = context_settings.cross_session_max_entries
            try:
                existing_items = store.search(namespace, limit=max_entries + 1)
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

    def _load_session_context_data(self, session_id: str) -> dict[str, Any] | None:
        """读取该会话已保存的跨会话记忆条目（spec P2 增量合并读侧）。

        返回 save_session_context 写入的原始结构（session_id/summary/
        key_entities/key_decisions/saved_at），无条目或读取失败一律返回 None
        （按无旧摘要处理，增量合并自动降级为纯本轮摘要写入），不向外抛出。
        """
        if not self.user_id:
            return None
        store = self._ensure_store()
        if store is None:
            return None
        namespace = (str(self.user_id), "session_contexts")
        try:
            item = store.get(namespace, session_id)
        except Exception as e:
            logger.debug(f"读取旧会话摘要失败（按无旧摘要处理）: session={session_id}, error={e}")
            return None
        if item is None:
            return None
        data = item.value if hasattr(item, "value") else item
        return data if isinstance(data, dict) else None

    @staticmethod
    def _merge_unique_values(old_values: Any, new_values: Any, limit: int) -> list[str]:
        """新旧实体/决策合并去重（A14：新值优先——更近期的偏好/结论排前并覆盖旧权重）。

        合并顺序 = 本轮新值在前 + 历史旧值在后；同名值（忽略大小写与首尾空白）
        以新值文本为准；超出 limit 时按该顺序保序淘汰尾部（最旧的值先被淘汰），
        不会再出现旧值恒占前排导致新增偏好无法生效的问题。
        """
        merged: list[str] = []
        seen: set[str] = set()
        for value in list(new_values or []) + list(old_values or []):
            text = str(value or "").strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(text)
        return merged[:limit]

    def process_conversation(self, user_id: int, messages: list[dict[str, Any]]) -> bool:
        """对话知识图谱更新（fix-review-round2 1.3 数据入口接线）。

        从本轮对话提取实体/关系并增量合并入 Store（ContextKnowledgeGraph.
        process_conversation：规则抽取 + merge_graph 分 key upsert，无 LLM 调用）。
        此前该方法全仓零调用，图谱恒空。调用方：chat finalizer 跨会话记忆
        写入链路（chat_executor_core._save_cross_session_memory，与
        save_turn_summary 同链路、已在事件循环外线程执行）。

        降级语义：kg 未启用/图谱管理器未构建/无 user_id → 返回 False；
        全程异常吞没仅 warning 留痕，不阻塞调用方收尾主链路。
        """
        if not self._knowledge_graph or not user_id:
            return False
        try:
            self._knowledge_graph.process_conversation(user_id, messages)
            return True
        except Exception:
            logger.warning(f"知识图谱对话处理失败（不阻塞主链路）: user={user_id}", exc_info=True)
            return False

    def load_turn_summary_snapshot(self, session_id: str) -> dict[str, Any]:
        """锁内快照读取入口（round3 2.8）：公开薄包装 _load_session_context_data。

        调用方（chat_executor_core._write）在 ChatSession 行锁内读取旧摘要
        快照（纯 DB 读），锁外 LLM 合并基于该快照进行；无条目返回 {}。
        """
        return self._load_session_context_data(session_id) or {}

    def prepare_turn_summary(
        self,
        session_id: str,
        user_message: str,
        assistant_content: str,
        old_data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """摘要准备阶段（round3 2.8，锁外执行——含 LLM 网络调用，禁止持锁）。

        职责：本轮摘要生成（LLM）→ 基于快照的累积合并（LLM，失败/空输出
        降级为「快照摘要 + 本轮摘要」拼接保留累积，4.3）→ 实体/决策规则
        抽取与新旧合并去重。全程只读不写。

        Args:
            old_data: 调用方在锁内读取的快照（load_turn_summary_snapshot）；
                None 时自行无锁读取（save_turn_summary 单调用路径）。

        Returns:
            prepared dict {summary, turn_summary, snapshot_summary, key_entities,
            key_decisions}；跳过/失败返回 None（调用方不写库）。
        """
        if not self.config.cross_session_enabled or not self.user_id:
            return None

        user_text = (user_message or "").strip()
        assistant_text = (assistant_content or "").strip()
        if not user_text or not assistant_text:
            return None

        try:
            engine = self._compression_engine or ContextCompressionEngine(CompressionConfig())
            turn_text = f"用户：{user_text}\n\n助手：{assistant_text}"

            # 1) 本轮摘要（LLM，锁外）
            try:
                turn_summary = (engine.summarize_long_text(turn_text, _TURN_SUMMARY_INSTRUCTION) or "").strip()
            except Exception:
                logger.warning(f"本轮摘要生成失败，跳过跨会话记忆写入: session={session_id}", exc_info=True)
                return None
            if not turn_summary:
                logger.debug(f"会话摘要为空，跳过跨会话记忆写入: session={session_id}")
                return None

            # 2) 快照（调用方锁内读取或此处无锁读取——仅作合并输入）
            if old_data is None:
                old_data = self._load_session_context_data(session_id) or {}
            old_summary = str(old_data.get("summary") or "").strip()

            # 3) 合并摘要（LLM，锁外）：快照旧摘要 + 本轮摘要 → 累积摘要；
            #    合并 LLM 失败/返回空 → 降级为「历史摘要 + 本轮摘要」拼接
            #    保留累积（4.3：此前降级仅保存本轮摘要，历史摘要被覆盖丢失）。
            final_summary = turn_summary
            if old_summary:
                merge_text = f"【历史累积摘要】\n{old_summary}\n\n【本轮新增摘要】\n{turn_summary}"
                merged = ""
                try:
                    merged = (engine.summarize_long_text(merge_text, _TURN_SUMMARY_MERGE_INSTRUCTION) or "").strip()
                except Exception:
                    logger.warning(f"摘要合并调用失败: session={session_id}", exc_info=True)
                if merged:
                    final_summary = merged
                else:
                    logger.warning(
                        f"摘要合并不可用，降级为历史摘要+本轮摘要拼接保留累积: session={session_id}"
                    )
                    final_summary = f"{old_summary}\n{turn_summary}"

            # 4) 实体/决策新旧合并去重（规则抽取，非 LLM；保持现有抽取上限）
            turn_messages = [
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": assistant_text},
            ]
            merged_entities = self._merge_unique_values(
                old_data.get("key_entities"),
                EntityExtractor.extract_from_messages(turn_messages),
                _CROSS_SESSION_ENTITY_LIMIT,
            )
            merged_decisions = self._merge_unique_values(
                old_data.get("key_decisions"),
                ContextCompressionEngine.extract_decisions(turn_messages),
                _CROSS_SESSION_DECISION_LIMIT,
            )

            return {
                "summary": final_summary,
                "turn_summary": turn_summary,
                "snapshot_summary": old_summary,
                "key_entities": merged_entities,
                "key_decisions": merged_decisions,
            }
        except Exception:
            logger.warning(f"跨会话摘要准备异常（兜底吞没，不阻塞调用方）: session={session_id}", exc_info=True)
            return None

    def commit_turn_summary(self, session_id: str, prepared: dict[str, Any]) -> bool:
        """摘要写回阶段（round3 2.8，锁内执行——调用方持 ChatSession 行锁）。

        二次校验：重读当前摘要与 prepare 时的快照比对——
        - 一致（无并发写）→ 写入 LLM 合并结果；
        - 已被并发修改 → 保底拼接（当前累积摘要 + 本轮摘要，无 LLM）写入，
          并发双方内容均不丢失（优于末写覆盖），warning 留痕。

        本方法只做 DB/Store 读写，无网络调用；实体/决策在修改分支按
        A14 新值优先二次合并。
        """
        try:
            current = self._load_session_context_data(session_id) or {}
            current_summary = str(current.get("summary") or "").strip()
            snapshot_summary = str(prepared.get("snapshot_summary") or "").strip()

            if current_summary != snapshot_summary and current_summary:
                # 快照过期：并发方已先写入 → 保底拼接（无 LLM，不丢更新）
                fallback_summary = f"{current_summary}\n{prepared['turn_summary']}".strip()
                merged_entities = self._merge_unique_values(
                    current.get("key_entities"),
                    prepared.get("key_entities"),
                    _CROSS_SESSION_ENTITY_LIMIT,
                )
                merged_decisions = self._merge_unique_values(
                    current.get("key_decisions"),
                    prepared.get("key_decisions"),
                    _CROSS_SESSION_DECISION_LIMIT,
                )
                logger.warning(f"跨会话摘要并发写检测（快照过期，降级保底拼接）: session={session_id}")
                return self.save_session_context(
                    session_id=session_id,
                    summary=fallback_summary,
                    key_entities=merged_entities,
                    key_decisions=merged_decisions,
                )

            return self.save_session_context(
                session_id=session_id,
                summary=prepared["summary"],
                key_entities=prepared["key_entities"],
                key_decisions=prepared["key_decisions"],
            )
        except Exception:
            logger.warning(f"跨会话摘要写回异常（兜底吞没，不阻塞调用方）: session={session_id}", exc_info=True)
            return False

    def save_turn_summary(
        self,
        session_id: str,
        user_message: str,
        assistant_content: str,
    ) -> bool:
        """会话终态摘要记忆写入（spec D2 + P2 增量合并）：prepare + commit 编排。

        round3 2.8 拆分说明：本方法为无外部锁的单调用兼容路径（领域层测试/
        独立调用方），prepare（LLM，锁外语义）→ commit（二次校验写回）。
        生产链路（chat_executor_core._write）为保证并发安全直接编排两阶段：
        锁内读快照 → 锁外 prepare → 锁内 commit，LLM 网络调用不持行锁。

        降级链：无旧摘要 → 纯本轮摘要（现状逻辑）；本轮摘要生成失败/为空 →
        返回 False 跳过写入；合并 LLM 调用失败/返回空 → 降级为「历史摘要 +
        本轮摘要」拼接保存（4.3：累积不丢失，warning 留痕）。全程异常不向外抛
        （调用方 chat finalizer 已有降级包裹，此处再兜底），统一返回 False。
        """
        try:
            prepared = self.prepare_turn_summary(session_id, user_message, assistant_content)
            if prepared is None:
                return False
            return self.commit_turn_summary(session_id, prepared)
        except Exception:
            logger.warning(f"跨会话记忆写入异常（兜底吞没，不阻塞调用方）: session={session_id}", exc_info=True)
            return False

    def _load_cross_session_context(self, query: str) -> str:
        store = self._ensure_store()
        if store is None or not self.user_id:
            return ""

        namespace = (str(self.user_id), "session_contexts")
        try:
            # 4.2：参与相关性评分的条目数窗口配置化（原硬编码 items[:5]），
            # search 层直接限流，避免大命名空间全量拉取后切片。
            window = context_settings.cross_session_context_window
            items = store.search(namespace, limit=window)
            if not items:
                return ""

            contexts = []
            for item in items:
                data = item.value if hasattr(item, "value") else item
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
        import numpy as np

        # 环 1 消边：原函数内 import knowledge.embedding_service（经
        # knowledge.config re-export 传递触达 ai_engine），改经 runtime_providers
        # 注入（knowledge apps.ready() 注册）。
        embedding_provider = get_embedding_provider()
        if embedding_provider is None:
            logger.debug("Embedding 语义相似度计算失败，回退词重叠: Embedding 提供者未注册")
            return None
        try:
            embeddings = embedding_provider()
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
        """获取 LangGraph Store（环 1 消边：原直连 ai_engine checkpointer_factory，
        现经 runtime_providers 注入的 Store 工厂，语义与原 ensure_store 逐句等价）。"""
        from Django_xm.apps.context_manager.services.runtime_providers import get_store_provider

        if self._store is not None:
            return self._store
        provider = get_store_provider()
        if provider is None:
            logger.warning("Store 提供者未注册（ai_engine apps.ready() 接线缺失），按不可用处理")
            return None
        try:
            self._store = provider()
            return self._store
        except Exception as e:
            logger.warning(f"Store 不可用: {e}")
            return None

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


def create_context_manager(
    user_id: int | None = None,
    store=None,
    model_name: str | None = None,
    thread_id: str | None = None,
) -> ContextManager:
    config = ContextManagementConfig.from_settings()
    config.model_name = model_name
    return ContextManager(user_id=user_id, config=config, store=store, thread_id=thread_id)
