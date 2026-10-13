"""
上下文自动压缩引擎

基于 LangChain 核心组件实现智能上下文压缩：
1. Token 阈值检测 - 当上下文超过模型 token 限制的 80% 时自动触发
2. 实体感知摘要 - 保留关键实体、关系和决策信息
3. 分层压缩策略 - 摘要层 + 关键信息层 + 近期消息层
4. 可配置压缩参数 - 阈值、保留优先级、摘要长度等

参考：
- https://docs.langchain.com/oss/python/langchain/memory#conversation-summary
- https://docs.langchain.com/oss/python/langgraph/persistence
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from pydantic import BaseModel, Field

from Django_xm.apps.context_manager.config import context_settings
from Django_xm.apps.context_manager.services.runtime_providers import (
    get_compression_model_provider,
    get_evaluation_model_provider,
)
from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.common.messages import content_to_str
from Django_xm.common.token_estimator import TokenEstimator

logger = get_logger(__name__)

# 实体/决策抽取的合并去重容量上限（save_turn_summary 增量合并沿用同一常量，勿双份硬编码）
EXTRACTED_ENTITIES_LIMIT = 30
EXTRACTED_DECISIONS_LIMIT = 10


class SummaryQuality(BaseModel):
    completeness: float = Field(default=0.0, ge=0, le=1, description="完整性评分")
    accuracy: float = Field(default=0.0, ge=0, le=1, description="准确性评分")
    conciseness: float = Field(default=0.0, ge=0, le=1, description="简洁性评分")
    overall: float = Field(default=0.0, ge=0, le=1, description="综合评分")
    passed: bool = Field(default=False, description="是否通过质量阈值")


class MemoryTier(Enum):
    """记忆层级：LONG_TERM 永不压缩，SHORT_TERM 可压缩"""

    LONG_TERM = "long_term"
    SHORT_TERM = "short_term"


class CompressionStrategy(Enum):
    SUMMARY = "summary"
    HYBRID = "hybrid"


def parse_compression_strategy(value: str) -> CompressionStrategy:
    """解析压缩策略配置值（manager.py 加载唯一入口）。

    sliding_window 策略已删除（spec fix-review-round2 4.2，纯滑窗无摘要、
    压缩信息全部丢失）：存量配置命中时抛出携带迁移说明的明确 ValueError，
    由加载方记录错误并回退默认策略——不做静默兼容。
    """
    try:
        return CompressionStrategy(value)
    except ValueError:
        raise ValueError(
            f"压缩策略 '{value}' 无效或已移除（sliding_window 已删除，"
            f"可选值: {[s.value for s in CompressionStrategy]}）"
        ) from None


@dataclass
class CompressionConfig:
    token_threshold_ratio: float = 0.8
    max_context_tokens: int = 128000
    keep_recent_messages: int = 6
    summary_max_length: int = 1500
    key_info_max_length: int = 800
    strategy: CompressionStrategy = CompressionStrategy.HYBRID
    preserve_system_messages: bool = True
    preserve_tool_results: bool = True
    entity_aware: bool = True

    @property
    def trigger_threshold(self) -> int:
        return int(self.max_context_tokens * self.token_threshold_ratio)


@dataclass
class CompressionResult:
    compressed: bool = False
    original_message_count: int = 0
    original_token_estimate: int = 0
    compressed_token_estimate: int = 0
    summary: str | None = None
    key_entities: list[str] = field(default_factory=list)
    key_decisions: list[str] = field(default_factory=list)
    compression_ratio: float = 0.0
    strategy_used: CompressionStrategy | None = None
    duration_ms: float = 0.0
    quality: SummaryQuality | None = None
    # 4.4：质量门禁不达标、摘要已降级为【原文关键片段】保留时置位，
    # 经 manager metadata → chat 消费端转为用户可见降级提示
    degraded: bool = False


# TokenEstimator 已下沉 Django_xm.common.token_estimator（任务 4.1 环 1 破环），
# 顶部 import 再导出仅为兼容既有消费方（manager/token_budget/rag_retrieval/
# context_service 及测试）的导入路径，指向同一类对象，类级 mock.patch 行为不变。


class EntityExtractor:
    _STOP_WORDS = frozenset(
        {
            "的",
            "了",
            "是",
            "在",
            "我",
            "你",
            "他",
            "她",
            "它",
            "们",
            "这",
            "那",
            "有",
            "和",
            "与",
            "或",
            "不",
            "也",
            "都",
            "就",
            "the",
            "a",
            "an",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "i",
            "you",
            "he",
            "she",
            "it",
            "we",
            "they",
            "me",
            "him",
            "and",
            "or",
            "but",
            "in",
            "on",
            "at",
            "to",
            "for",
            "of",
        }
    )

    @staticmethod
    def extract_from_messages(messages: list[dict[str, Any]]) -> list[str]:
        entities = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = content_to_str(content)
            if not isinstance(content, str) or not content.strip():
                continue

            if role == "user":
                entities.extend(EntityExtractor._extract_user_entities(content))
            elif role == "assistant":
                entities.extend(EntityExtractor._extract_assistant_entities(content))

        seen = set()
        unique = []
        for e in entities:
            key = e.lower()
            if key not in seen and key not in EntityExtractor._STOP_WORDS:
                seen.add(key)
                unique.append(e)
        return unique[:EXTRACTED_ENTITIES_LIMIT]

    @staticmethod
    def _extract_user_entities(text: str) -> list[str]:
        import re

        entities = []

        patterns = [
            r'[""「」]([^""「」]{2,50})[""「」]',
            r"(?:叫做?|名为|称为|是)([^\s，。！？,.!?]{2,20})",
            r"(\d+(?:\.\d+)?)\s*(?:元|万|亿|%|度|米|千克|GB|MB|KB|小时|分钟|天|周|月|年)",
            r"(?:使用|用|选择|配置|设置)([^\s，。！？,.!?]{2,30})",
        ]
        for pattern in patterns:
            matches = re.findall(pattern, text)
            entities.extend(matches)

        sentences = re.split(r"[。！？.!?]", text)
        for s in sentences:
            stripped = s.strip()
            if 4 <= len(stripped) <= 40 and not any(w in stripped for w in ["怎么", "什么", "如何", "为什么"]):
                entities.append(stripped)

        return entities

    @staticmethod
    def _extract_assistant_entities(text: str) -> list[str]:
        import re

        entities = []

        patterns = [
            r"(?:建议|推荐|应该|需要|必须)([^\s，。！？,.!?]{2,30})",
            r"(?:步骤|方法|方案|策略)(?:[一二三四五12345])[:：]?\s*([^\n，。]{2,40})",
        ]
        for pattern in patterns:
            matches = re.findall(pattern, text)
            entities.extend(matches)

        return entities


def messages_to_dicts(messages: list[Any]) -> list[dict[str, Any]]:
    """BaseMessage → dict 适配层单源（统一引擎的双语义输入支持，phase6a G3 归一）。

    保留 id / tool_calls / memory_tier / tool_call_id 字段，
    保证消息身份跨序列化不丢失（manager._serialize_messages /
    mark_long_term / ContextPruner 等消费方仅读固定键）。
    非 BaseMessage 条目（调用方已自行构造的 dict）原样透传。
    """
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
        content = content_to_str(msg.content)
        entry: dict[str, Any] = {"role": role, "content": content}
        if hasattr(msg, "id") and msg.id:
            entry["id"] = msg.id
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            entry["tool_calls"] = msg.tool_calls
        if hasattr(msg, "additional_kwargs") and "memory_tier" in msg.additional_kwargs:
            entry["memory_tier"] = msg.additional_kwargs["memory_tier"]
        if isinstance(msg, ToolMessage):
            entry["tool_call_id"] = msg.tool_call_id
        result.append(entry)
    return result


class ContextCompressionEngine:
    def __init__(self, config: CompressionConfig | None = None):
        self.config = config or CompressionConfig()
        self._entity_extractor = EntityExtractor()

    def _get_compression_model(self):
        """获取压缩用的轻量 LLM（依赖倒置：解析链由 ai_engine 侧注入）。

        提供者经 runtime_providers 由 ai_engine AppConfig.ready() 注册
        （ai_engine.services.compression_bridge.resolve_compression_model），
        解析优先级（与历史内联实现逐级等价）：
        1. context_settings.compression_lightweight_model（用户显式指定的轻量模型）
        2. 辅助模型（get_helper_model，带 fallback 包装）
        3. SystemConfig 默认模型
        4. 回退到 ``get_chat_model()`` 默认值

        未注册时抛 RuntimeError，由调用方既有 try/except 走本地摘要降级。
        """
        provider = get_compression_model_provider()
        if provider is None:
            raise RuntimeError(
                "压缩模型提供者未注册：context_manager 禁止 import ai_engine（环 1 契约），"
                "应由 ai_engine apps.ready() 经 runtime_providers 接线"
            )
        return provider(context_settings.compression_lightweight_model)

    def _classify_memory_tier(self, msg: dict[str, Any]) -> "MemoryTier":
        """分类消息的记忆层级（仅认结构化标记，不做内容关键词启发式）

        与 ContextManagerMiddleware._is_long_term_message 同语义：
        - role == "system" → LONG_TERM
        - 显式 memory_tier == "long_term" 标记 → LONG_TERM
        - 其他 → SHORT_TERM（内容含 "system/decision" 等词的消息正常压缩）
        """
        if msg.get("role") == "system":
            return MemoryTier.LONG_TERM
        if msg.get("memory_tier") == "long_term":
            return MemoryTier.LONG_TERM
        return MemoryTier.SHORT_TERM

    def should_compress(self, messages: list[dict[str, Any]]) -> bool:
        # 用全部消息计算 token，判断是否超限
        total_tokens = TokenEstimator.estimate_dict_messages(messages)
        if total_tokens <= self.config.trigger_threshold:
            return False
        # 但仍要求有足够的 SHORT_TERM 消息可供压缩
        short_term_messages = [msg for msg in messages if self._classify_memory_tier(msg) == MemoryTier.SHORT_TERM]
        if len(short_term_messages) > self.config.keep_recent_messages:
            return True
        # 少量超长消息场景（fix-review-round2 4.1）：SHORT_TERM 条数不足保底窗口
        # （≤ keep_recent）但总量已超阈值时，若可压缩部分 token 已超阈值仍触发，
        # 避免"消息数不够 → 永不压缩"与"总量超限"的死锁。
        # 可压缩部分口径与 _compressible_old_messages 一致：count ≤ keep_recent 时
        # 保底窗口无法满足，全部 SHORT_TERM 视为可压缩；输出侧仍经
        # _build_compressed_messages 保留最近 keep_recent 条原文，保底语义不变。
        if not short_term_messages:
            return False
        compressible_tokens = TokenEstimator.estimate_dict_messages(short_term_messages)
        return compressible_tokens > self.config.trigger_threshold

    def _compressible_old_messages(self, short_term_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """取压缩范围（摘要输入的"旧消息"），_compress_summary/_compress_hybrid/
        compress 质量门禁三处共用单一口径。

        - count > keep_recent：取 [:-keep_recent]，保底最近 keep_recent 条不参与摘要；
        - count ≤ keep_recent：保底窗口无法满足，返回全部 SHORT_TERM
          （此前 _compress_summary/_compress_hybrid 用纯 [:-keep] 切片在该场景
          得到空列表、摘要恒为空，与质量门禁口径不一致——4.1 一并统一）。
        输出侧（_build_compressed_messages）始终保留 original[-keep_recent:] 原文，
        "最近消息不丢失"的保底语义不变。
        """
        keep = self.config.keep_recent_messages
        if len(short_term_messages) > keep:
            return short_term_messages[:-keep]
        return short_term_messages

    def compress(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], CompressionResult]:
        start_time = time.time()
        total_tokens = TokenEstimator.estimate_dict_messages(messages)

        # 分离 LONG_TERM 和 SHORT_TERM 消息
        long_term_messages = []
        short_term_messages = []
        for msg in messages:
            if self._classify_memory_tier(msg) == MemoryTier.LONG_TERM:
                long_term_messages.append(msg)
            else:
                short_term_messages.append(msg)

        if not self.should_compress(messages):
            return messages, CompressionResult(
                original_message_count=len(messages),
                original_token_estimate=total_tokens,
                compressed_token_estimate=total_tokens,
            )

        if self.config.strategy == CompressionStrategy.SUMMARY:
            result = self._compress_summary(short_term_messages, long_term_messages)
        else:
            result = self._compress_hybrid(short_term_messages, long_term_messages)

        # 质量门禁：不合格时先重建降级摘要，再统一构建 compressed_messages，
        # 保证降级内容（【原文关键片段】）真实进入输出消息列表
        if result.compressed and result.summary:
            old_messages = self._compressible_old_messages(short_term_messages)
            result.quality = self.evaluate_summary_quality(old_messages, result.summary)
            if not result.quality.passed or result.quality.overall < 0.6:
                logger.warning(
                    f"摘要质量不达标: overall={result.quality.overall:.2f}, "
                    f"completeness={result.quality.completeness:.2f}, "
                    f"accuracy={result.quality.accuracy:.2f}, "
                    f"conciseness={result.quality.conciseness:.2f}"
                )
                degraded = self._build_degraded_summary(old_messages)
                if degraded:
                    result.summary = degraded
                    result.degraded = True

        result.original_message_count = len(messages)
        result.original_token_estimate = total_tokens
        compressed_messages = self._build_compressed_messages(messages, result, long_term_messages)
        result.compressed_token_estimate = (
            TokenEstimator.estimate_dict_messages(compressed_messages) if result.compressed else total_tokens
        )
        result.duration_ms = (time.time() - start_time) * 1000

        if result.original_token_estimate > 0:
            result.compression_ratio = 1.0 - (result.compressed_token_estimate / result.original_token_estimate)

        logger.info(
            f"上下文压缩: {result.original_message_count} 条消息, "
            f"{result.original_token_estimate} -> {result.compressed_token_estimate} tokens, "
            f"压缩率 {result.compression_ratio:.1%}, 策略={result.strategy_used}, "
            f"耗时 {result.duration_ms:.0f}ms"
        )

        return compressed_messages, result

    @staticmethod
    def _build_degraded_summary(messages: list[dict[str, Any]]) -> str:
        """质量门禁不合格时的降级摘要：原文关键片段逐字保留"""
        key_fragments = []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str) and content.strip():
                fragment = content.strip()[:100]
                if fragment:
                    key_fragments.append(f"[{msg.get('role', '?')}]: {fragment}")
        if not key_fragments:
            return ""
        return "【原文关键片段】\n" + "\n".join(key_fragments[:10])

    def summarize_long_text(self, text: str, instruction: str) -> str:
        """统一引擎文本摘要能力（ARCH-07：供 load_research_context 等旁路复用）。

        使用与压缩摘要一致的模型解析链（轻量模型 → 辅助模型 → SystemConfig 默认 → 兜底），
        消除旁路自建 get_chat_model 的双摘要实现。

        异常语义：模型解析/调用失败时异常向上抛出，由调用方决定降级策略
        （如 load_research_context 的外层 except 返回 None）；
        摘要为空时返回空串，由调用方回退原文截断。
        """
        if not text or not text.strip():
            return ""
        model = self._get_compression_model()
        prompt = f"{instruction}\n\n{text}"
        response = model.invoke([{"role": "user", "content": prompt}])
        summary = getattr(response, "content", "") or ""
        return summary.strip()

    def _compress_summary(
        self,
        messages: list[dict[str, Any]],
        long_term_messages: list[dict[str, Any]] | None = None,
    ) -> CompressionResult:
        old_messages = self._compressible_old_messages(messages)
        entities = self._entity_extractor.extract_from_messages(old_messages) if self.config.entity_aware else []
        summary = self._generate_summary(old_messages, entities)
        decisions = self._extract_decisions(old_messages)

        return CompressionResult(
            compressed=True,
            summary=summary,
            key_entities=entities,
            key_decisions=decisions,
            strategy_used=CompressionStrategy.SUMMARY,
        )

    def _compress_hybrid(
        self,
        messages: list[dict[str, Any]],
        long_term_messages: list[dict[str, Any]] | None = None,
    ) -> CompressionResult:
        old_messages = self._compressible_old_messages(messages)
        entities = self._entity_extractor.extract_from_messages(old_messages) if self.config.entity_aware else []
        summary = self._generate_summary(old_messages, entities)
        decisions = self._extract_decisions(old_messages)

        return CompressionResult(
            compressed=True,
            summary=summary,
            key_entities=entities,
            key_decisions=decisions,
            strategy_used=CompressionStrategy.HYBRID,
        )

    def _generate_summary(self, messages: list[dict[str, Any]], entities: list[str]) -> str:
        if not messages:
            return ""

        conversation_text = self._format_messages(messages)
        if len(conversation_text) > 12000:
            conversation_text = conversation_text[:12000] + "\n...(内容过长已截断)"

        entity_hint = ""
        if entities:
            entity_hint = f"\n\n已知关键实体：{', '.join(entities[:15])}"

        try:
            model = self._get_compression_model()
            prompt = (
                "请将以下对话历史压缩为结构化摘要，严格保留以下信息：\n"
                "1. 【核心话题】讨论的主要问题和主题\n"
                "2. 【关键结论】已得出的重要结论和决策\n"
                "3. 【用户需求】用户明确表达的需求和偏好\n"
                "4. 【工具使用】调用的工具及获取的关键信息\n"
                "5. 【待办事项】尚未完成或需要跟进的事项\n\n"
                f"对话内容：\n{conversation_text}\n"
                f"{entity_hint}\n\n"
                "请用中文生成不超过400字的结构化摘要："
            )
            response = model.invoke([{"role": "user", "content": prompt}])
            summary = getattr(response, "content", "")
            if summary and len(summary) > self.config.summary_max_length:
                summary = summary[: self.config.summary_max_length]
            return summary or self._fallback_summary(messages)
        except Exception:
            logger.exception("LLM 生成摘要失败")
            return self._fallback_summary(messages)

    @staticmethod
    def _extract_decisions(messages: list[dict[str, Any]]) -> list[str]:
        import re

        decisions = []
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content", "")
            if not isinstance(content, str):
                continue

            patterns = [
                r"(?:建议|推荐|应该|决定|确认|选择)\s*[：:]\s*([^\n，。]{4,60})",
                r"(?:结论|结果|答案)\s*[是为]\s*([^\n，。]{4,60})",
            ]
            for pattern in patterns:
                matches = re.findall(pattern, content)
                decisions.extend(matches)

        return decisions[:EXTRACTED_DECISIONS_LIMIT]

    # 公开决策抽取入口（spec D2 跨会话记忆写入等旁路复用唯一实现，消除私有访问）
    extract_decisions = _extract_decisions

    def _format_messages(self, messages: list[dict[str, Any]]) -> str:
        """格式化消息为摘要输入文本（A11：preserve_tool_results 接线）

        config.preserve_tool_results=True 时，工具结果消息（role=tool）不截断：
        工具输出是摘要提示词【工具使用】段落的信息源，逐条 400 字符截断会让
        摘要丢失工具关键结果。单条超长仍受 _generate_summary 的全局输入上限
        （12000 字符）与压缩输出 summary_max_length 兜底。
        """
        preserve_tool_results = self.config.preserve_tool_results
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = content_to_str(content)
            if not isinstance(content, str) or not content.strip():
                continue

            prefix = {"user": "用户", "assistant": "助手", "system": "系统"}.get(role, role)
            if role == "tool" and preserve_tool_results:
                truncated = content
            else:
                truncated = content[:400] + "..." if len(content) > 400 else content
            lines.append(f"[{prefix}]: {truncated}")

            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        tool_name = tc.get("name", tc.get("function", {}).get("name", "unknown"))
                        lines.append(f"  [工具调用]: {tool_name}")

        return "\n".join(lines)

    @staticmethod
    def _fallback_summary(messages: list[dict[str, Any]]) -> str:
        topics = []
        for msg in messages:
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    topics.append(content.strip().split("\n")[0][:80])

        unique_topics = list(dict.fromkeys(topics))[:5]
        return "对话摘要：讨论了 " + "；".join(unique_topics) if unique_topics else ""

    def evaluate_summary_quality(
        self,
        messages: list[dict[str, Any]],
        summary: str,
    ) -> SummaryQuality:
        """评估摘要质量。

        失败语义（明确失败，禁止伪造默认通过）：
        - 评估模型提供者未注册 / 模型初始化或调用失败 → 返回 passed=False
          的零分结果（评估不可用视为未通过），调用方据此走降级重建；
        - 摘要为空 → passed=False；
        - 无原始消息可比对 → 无法评估，视为未通过（passed=False），
          调用方据此走降级重建，禁止无依据的摘要默认通过。
        """
        if not summary or not summary.strip():
            return SummaryQuality(completeness=0.0, accuracy=0.0, conciseness=0.0, overall=0.0, passed=False)

        if not messages:
            return SummaryQuality(completeness=0.0, accuracy=0.0, conciseness=0.0, overall=0.0, passed=False)

        conversation_text = self._format_messages(messages)
        if len(conversation_text) > 10000:
            conversation_text = conversation_text[:10000] + "\n...(内容过长已截断)"

        provider = get_evaluation_model_provider()
        if provider is None:
            logger.warning("LLM 质量评估不可用: 评估模型提供者未注册（评估视为未通过）")
            return SummaryQuality(completeness=0.0, accuracy=0.0, conciseness=0.0, overall=0.0, passed=False)

        prompt = (
            "请评估以下对话摘要的质量，从三个维度打分（0-1）：\n"
            "1. 完整性（completeness）：摘要是否涵盖了对话中的主要话题、关键结论和用户需求\n"
            "2. 准确性（accuracy）：摘要内容是否与原始对话一致，有无歪曲或错误\n"
            "3. 简洁性（conciseness）：摘要是否简洁精炼，无冗余信息\n\n"
            f"原始对话：\n{conversation_text}\n\n"
            f"压缩摘要：\n{summary}\n\n"
            "请给出评分，overall 为三个维度的加权平均（完整性0.4、准确性0.4、简洁性0.2），"
            "passed 为 overall >= 0.6 时为 True。"
        )

        try:
            model = provider()
            structured_model = model.with_structured_output(SummaryQuality)
            quality: SummaryQuality = structured_model.invoke([{"role": "user", "content": prompt}])
            return quality
        except Exception as e:
            logger.warning(f"LLM 质量评估失败（评估视为未通过）: {e}")
            return SummaryQuality(completeness=0.0, accuracy=0.0, conciseness=0.0, overall=0.0, passed=False)

    def _build_compressed_messages(
        self,
        original: list[dict[str, Any]],
        result: CompressionResult,
        long_term_messages: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        if not result.compressed:
            return original

        long_term = long_term_messages or []
        compressed = []

        # LONG_TERM 消息始终保留在输出中
        for msg in long_term:
            compressed.append(msg)

        if self.config.preserve_system_messages:
            for msg in original:
                if msg.get("role") == "system" and msg not in long_term:
                    compressed.append(msg)

        context_parts = []
        if result.summary:
            context_parts.append(f"【对话摘要】\n{result.summary}")
        if result.key_entities:
            context_parts.append(f"【关键实体】{', '.join(result.key_entities[:15])}")
        if result.key_decisions:
            context_parts.append("【关键决策】\n" + "\n".join(f"- {d}" for d in result.key_decisions[:8]))

        if context_parts:
            compressed.append(
                {
                    "role": "system",
                    "content": "\n\n".join(context_parts),
                }
            )

        recent = original[-self.config.keep_recent_messages :]
        # 去重：跳过已在 long_term 或 compressed 中存在的消息
        existing_contents = {m.get("content") for m in compressed if m.get("role") == "system"}
        for msg in recent:
            if msg.get("role") != "system" or not self.config.preserve_system_messages:
                # 跳过已在 long_term 中的消息
                if self._classify_memory_tier(msg) == MemoryTier.LONG_TERM and any(
                    m.get("content") == msg.get("content") for m in compressed
                ):
                    continue
                compressed.append(msg)
            elif msg.get("role") == "system" and msg.get("content") not in existing_contents:
                compressed.append(msg)
                existing_contents.add(msg.get("content"))

        return compressed
