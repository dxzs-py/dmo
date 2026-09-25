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
from typing import Any, ClassVar

from langchain_core.messages import BaseMessage
from pydantic import BaseModel, Field

from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.common.messages import content_to_str

try:
    import tiktoken as _tiktoken

    _TIKTOKEN_AVAILABLE = True
except ImportError:
    _tiktoken = None
    _TIKTOKEN_AVAILABLE = False

try:
    from transformers import AutoTokenizer as _AutoTokenizer

    _TRANSFORMERS_AVAILABLE = True
except ImportError:
    _AutoTokenizer = None
    _TRANSFORMERS_AVAILABLE = False

logger = get_logger(__name__)


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
    SLIDING_WINDOW = "sliding_window"
    HYBRID = "hybrid"


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
    long_term_tags: list[str] = field(default_factory=lambda: ["system", "preference", "decision"])

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
    is_incremental: bool = False


class TokenEstimator:
    _MODEL_LIMITS: ClassVar[dict[str, int]] = {
        "gpt-4o": 128000,
        "gpt-4o-mini": 128000,
        "gpt-4-turbo": 128000,
        "gpt-4": 8192,
        "gpt-3.5-turbo": 16385,
        "claude-sonnet-4-20250514": 200000,
        "claude-opus-4-20250514": 200000,
        "claude-3-5-sonnet-20241022": 200000,
        "deepseek-chat": 128000,
        "deepseek-reasoner": 128000,
        "gemini-2.0-flash-exp": 1000000,
        "gemini-pro": 32768,
    }

    _MODEL_ENCODING_MAP: ClassVar[dict[str, str]] = {
        "gpt-4o": "o200k_base",
        "gpt-4o-mini": "o200k_base",
        "gpt-4-turbo": "cl100k_base",
        "gpt-4": "cl100k_base",
        "gpt-3.5-turbo": "cl100k_base",
    }

    _DEFAULT_ENCODING: str = "cl100k_base"

    _encoding_cache: ClassVar[dict[str, Any]] = {}

    # transformers tokenizer 缓存
    _transformers_tokenizer_cache: ClassVar[dict[str, Any]] = {}

    @classmethod
    def _get_encoding(cls, model_name: str = "") -> Any:
        if not _TIKTOKEN_AVAILABLE:
            return None
        encoding_name = cls._DEFAULT_ENCODING
        if model_name:
            for key, enc in cls._MODEL_ENCODING_MAP.items():
                if key in model_name:
                    encoding_name = enc
                    break
        if encoding_name not in cls._encoding_cache:
            try:
                cls._encoding_cache[encoding_name] = _tiktoken.get_encoding(encoding_name)
            except Exception as e:
                logger.warning(f"tiktoken 获取编码失败，编码名={encoding_name}: {e}")
                return None
        return cls._encoding_cache[encoding_name]

    @staticmethod
    def _rough_estimate(text: str) -> int:
        """原始粗略估算（保留向后兼容）"""
        if not text:
            return 0
        chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        other_chars = len(text) - chinese_chars
        return int(chinese_chars * 1.5 + other_chars * 0.25)

    @classmethod
    def _estimate_with_transformers(cls, text: str, model_name: str = "") -> int:
        """尝试使用 HuggingFace transformers tokenizer 进行精确估算"""
        if not _TRANSFORMERS_AVAILABLE or not text:
            return -1  # 返回 -1 表示不可用，与 0（空文本）区分

        # 根据模型名选择合适的 tokenizer
        tokenizer_name = "gpt2"  # 默认使用 GPT-2 tokenizer
        if model_name:
            model_lower = model_name.lower()
            if "deepseek" in model_lower:
                tokenizer_name = "deepseek-ai/deepseek-llm-7b-base"
            elif "claude" in model_lower:
                tokenizer_name = "Xenova/claude-tokenizer"
            elif "qwen" in model_lower:
                tokenizer_name = "Qwen/Qwen2-7B"

        # 使用缓存避免重复加载
        if tokenizer_name not in cls._transformers_tokenizer_cache:
            try:
                cls._transformers_tokenizer_cache[tokenizer_name] = _AutoTokenizer.from_pretrained(tokenizer_name)
            except Exception as e:
                logger.debug(f"transformers tokenizer 加载失败 ({tokenizer_name}): {e}")
                cls._transformers_tokenizer_cache[tokenizer_name] = None

        tokenizer = cls._transformers_tokenizer_cache.get(tokenizer_name)
        if tokenizer is None:
            return -1

        try:
            return len(tokenizer.encode(text))
        except Exception as e:
            logger.debug(f"transformers tokenizer 编码失败: {e}")
            return -1

    @staticmethod
    def _improved_rough_estimate(text: str) -> int:
        """改进的启发式估算：中文字符 x1.5、英文单词 x1.3、标点 x0.5、数字 x0.25/字符"""
        if not text:
            return 0

        import re

        # 中文字符
        chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        chinese_tokens = chinese_chars * 1.5

        # 英文单词（按词计算，每个词约 1.3 token）
        english_words = re.findall(r"[a-zA-Z]+", text)
        english_tokens = len(english_words) * 1.3

        # 标点符号
        punctuation_chars = sum(
            1 for c in text if not c.isalnum() and not c.isspace() and not ("\u4e00" <= c <= "\u9fff")
        )
        punctuation_tokens = punctuation_chars * 0.5

        # 数字（按字符计算，每个字符约 0.25 token）
        digit_chars = sum(1 for c in text if c.isdigit())
        digit_tokens = digit_chars * 0.25

        # 其他字符（空白等，少量开销）
        other_chars = len(text) - chinese_chars - sum(len(w) for w in english_words) - punctuation_chars - digit_chars
        other_tokens = max(0, other_chars) * 0.2

        return int(chinese_tokens + english_tokens + punctuation_tokens + digit_tokens + other_tokens)

    @classmethod
    def estimate_tokens(cls, text: str, model_name: str = "") -> int:
        """估算 token 数：优先 tiktoken → transformers → 改进启发式"""
        if not text:
            return 0

        # 1. 优先使用 tiktoken
        encoding = cls._get_encoding(model_name)
        if encoding is not None:
            try:
                return len(encoding.encode(text))
            except Exception as e:
                logger.debug(f"tiktoken 编码失败，尝试 transformers: {e}")

        # 2. 其次尝试 transformers tokenizer
        transformers_result = cls._estimate_with_transformers(text, model_name)
        if transformers_result >= 0:
            return transformers_result

        # 3. 最后回退到改进的启发式方法
        return cls._improved_rough_estimate(text)

    @staticmethod
    def estimate(text: str) -> int:
        return TokenEstimator.estimate_tokens(text)

    @classmethod
    def estimate_text(cls, text: str, model_name: str = "") -> int:
        """估算文本 token 数：优先 tiktoken → transformers → 改进启发式"""
        return cls.estimate_tokens(text, model_name)

    @classmethod
    def estimate_messages(cls, messages: list[BaseMessage], model_name: str = "") -> int:
        total = 0
        for msg in messages:
            total += cls.estimate_tokens(content_to_str(msg.content), model_name)
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    total += cls.estimate_tokens(str(tc.get("args", {})), model_name)
        return total

    @classmethod
    def estimate_dict_messages(cls, messages: list[dict[str, Any]], model_name: str = "") -> int:
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += cls.estimate_tokens(content, model_name)
            elif isinstance(content, list):
                total += cls.estimate_tokens(content_to_str(content), model_name)
            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                for tc in tool_calls:
                    total += cls.estimate_tokens(str(tc.get("args", tc.get("function", {}))), model_name)
        return total

    @classmethod
    def get_model_limit(cls, model_name: str, default: int = 128000) -> int:
        """查询模型 token 上限。

        Args:
            model_name: 模型名称，按子串匹配 _MODEL_LIMITS 条目。
            default: 未命中或模型名为空时的回退上限。
        """
        if not model_name:
            return default
        for key, limit in cls._MODEL_LIMITS.items():
            if key in model_name:
                return limit
        return default


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
        return unique[:30]

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


class ContextCompressionEngine:
    _embedding_cache: ClassVar[dict[str, list[float]]] = {}

    def __init__(
        self,
        config: CompressionConfig | None = None,
        store: Any | None = None,
        user_id: str | None = None,
        thread_id: str | None = None,
    ):
        self.config = config or CompressionConfig()
        self._entity_extractor = EntityExtractor()
        self._rolling_summary: str | None = None
        self._last_compressed_index: int = 0
        self._store = store
        self._user_id = user_id
        self._thread_id = thread_id

        if self._store is not None and self._user_id is not None:
            self._load_state()

    def _get_compression_model(self):
        """获取压缩用的轻量 LLM。

        解析优先级：
        1. context_settings.compression_lightweight_model（用户显式指定的轻量模型）
        2. 辅助模型（get_helper_model，带 fallback 包装）
        3. SystemConfig 默认模型
        4. 回退到 ``get_chat_model()`` 默认值
        """
        from Django_xm.apps.ai_engine.services.llm_factory import (
            get_chat_model,
            get_helper_model,
            get_model_string,
            get_system_default_chat_model,
        )
        from Django_xm.apps.context_manager.config import context_settings

        # 1. 用户显式指定的轻量模型（如 "deepseek:deepseek-chat"）
        lightweight_model = context_settings.compression_lightweight_model
        if lightweight_model:
            try:
                return get_chat_model(lightweight_model)
            except Exception:
                # 轻量模型创建失败时回退到下一个选项
                logger.debug("轻量模型 %s 创建失败，回退到辅助模型", lightweight_model)

        # 2. 辅助模型（带 fallback 包装，轻量任务优先）
        try:
            helper = get_helper_model()
            if helper is not None:
                return helper
        except Exception:
            # 辅助模型获取失败时回退到 SystemConfig 默认模型
            logger.debug("获取辅助模型失败，回退到 SystemConfig 默认模型")

        # 3. SystemConfig 默认模型
        try:
            system_default = get_system_default_chat_model()
            if system_default and system_default.get("provider_id"):
                return get_chat_model(
                    model_provider=system_default["provider_id"],
                    model_name=system_default.get("model_name"),
                )
        except Exception:
            # SystemConfig 默认模型获取失败时回退到最终兜底
            logger.debug("获取 SystemConfig 默认模型失败，回退到最终兜底")

        # 4. 最终兜底：默认 provider/model
        return get_chat_model(get_model_string())

    def _classify_memory_tier(self, msg: dict[str, Any]) -> "MemoryTier":
        """分类消息的记忆层级"""
        # role == "system" → LONG_TERM
        if msg.get("role") == "system":
            return MemoryTier.LONG_TERM
        # msg 中有 memory_tier 键且值为 "long_term" → LONG_TERM
        if msg.get("memory_tier") == "long_term":
            return MemoryTier.LONG_TERM
        # msg 的 content 中包含 long_term_tags 中的关键词 → LONG_TERM
        content = msg.get("content", "")
        if isinstance(content, list):
            content = content_to_str(content)
        if isinstance(content, str):
            content_lower = content.lower()
            for tag in self.config.long_term_tags:
                if tag.lower() in content_lower:
                    return MemoryTier.LONG_TERM
        # 其他 → SHORT_TERM
        return MemoryTier.SHORT_TERM

    def should_compress(self, messages: list[dict[str, Any]]) -> bool:
        # 用全部消息计算 token，判断是否超限
        total_tokens = TokenEstimator.estimate_dict_messages(messages)
        if total_tokens <= self.config.trigger_threshold:
            return False
        # 但仍要求有足够的 SHORT_TERM 消息可供压缩
        short_term_count = sum(1 for msg in messages if self._classify_memory_tier(msg) == MemoryTier.SHORT_TERM)
        return short_term_count >= self.config.keep_recent_messages

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
        elif self.config.strategy == CompressionStrategy.SLIDING_WINDOW:
            result = self._compress_sliding_window(short_term_messages, long_term_messages)
        else:
            result = self._compress_hybrid(short_term_messages, long_term_messages)

        result.original_message_count = len(messages)
        result.original_token_estimate = total_tokens
        compressed_messages = self._build_compressed_messages(messages, result, long_term_messages)
        result.compressed_token_estimate = (
            TokenEstimator.estimate_dict_messages(compressed_messages) if result.compressed else total_tokens
        )
        result.duration_ms = (time.time() - start_time) * 1000

        if result.original_token_estimate > 0:
            result.compression_ratio = 1.0 - (result.compressed_token_estimate / result.original_token_estimate)

        if result.compressed and result.summary:
            old_messages = (
                short_term_messages[: -self.config.keep_recent_messages]
                if len(short_term_messages) > self.config.keep_recent_messages
                else short_term_messages
            )
            result.quality = self.evaluate_summary_quality(old_messages, result.summary)
            if result.quality.overall < 0.6:
                logger.warning(
                    f"摘要质量不达标: overall={result.quality.overall:.2f}, "
                    f"completeness={result.quality.completeness:.2f}, "
                    f"accuracy={result.quality.accuracy:.2f}, "
                    f"conciseness={result.quality.conciseness:.2f}"
                )
                key_fragments = []
                for msg in old_messages:
                    content = msg.get("content", "")
                    if isinstance(content, str) and content.strip():
                        fragment = content.strip()[:100]
                        if fragment:
                            key_fragments.append(f"[{msg.get('role', '?')}]: {fragment}")
                if key_fragments:
                    result.summary = "【原文关键片段】\n" + "\n".join(key_fragments[:10])

        logger.info(
            f"上下文压缩: {result.original_message_count} 条消息, "
            f"{result.original_token_estimate} -> {result.compressed_token_estimate} tokens, "
            f"压缩率 {result.compression_ratio:.1%}, 策略={result.strategy_used}, "
            f"耗时 {result.duration_ms:.0f}ms"
        )

        # 压缩成功后更新 last_compressed_index 并持久化
        if result.compressed:
            self._last_compressed_index = len(short_term_messages)
            self._persist_state()

        return compressed_messages, result

    def _compress_summary(
        self,
        messages: list[dict[str, Any]],
        long_term_messages: list[dict[str, Any]] | None = None,
    ) -> CompressionResult:
        old_messages = messages[: -self.config.keep_recent_messages]
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

    def _compress_sliding_window(
        self,
        messages: list[dict[str, Any]],
        long_term_messages: list[dict[str, Any]] | None = None,
    ) -> CompressionResult:
        return CompressionResult(
            compressed=True,
            key_entities=self._entity_extractor.extract_from_messages(messages) if self.config.entity_aware else [],
            strategy_used=CompressionStrategy.SLIDING_WINDOW,
        )

    def _compress_hybrid(
        self,
        messages: list[dict[str, Any]],
        long_term_messages: list[dict[str, Any]] | None = None,
    ) -> CompressionResult:
        old_messages = messages[: -self.config.keep_recent_messages]
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

    def _extract_decisions(self, messages: list[dict[str, Any]]) -> list[str]:
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

        return decisions[:10]

    @staticmethod
    def _format_messages(messages: list[dict[str, Any]]) -> str:
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = content_to_str(content)
            if not isinstance(content, str) or not content.strip():
                continue

            prefix = {"user": "用户", "assistant": "助手", "system": "系统"}.get(role, role)
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
        if not summary or not summary.strip():
            return SummaryQuality(completeness=0.0, accuracy=0.0, conciseness=0.0, overall=0.0, passed=False)

        if not messages:
            return SummaryQuality(completeness=0.7, accuracy=0.7, conciseness=0.7, overall=0.7, passed=True)

        conversation_text = self._format_messages(messages)
        if len(conversation_text) > 10000:
            conversation_text = conversation_text[:10000] + "\n...(内容过长已截断)"

        try:
            from Django_xm.apps.ai_engine.services.llm_factory import get_chat_model, get_helper_model

            model = get_helper_model() or get_chat_model()
            structured_model = model.with_structured_output(SummaryQuality)
        except Exception as e:
            logger.warning(f"LLM 质量评估初始化失败，返回默认评分: {e}")
            return SummaryQuality(completeness=0.7, accuracy=0.7, conciseness=0.7, overall=0.7, passed=True)

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
            quality: SummaryQuality = structured_model.invoke([{"role": "user", "content": prompt}])
            return quality
        except Exception as e:
            logger.warning(f"LLM 质量评估失败，返回默认评分: {e}")
            return SummaryQuality(completeness=0.7, accuracy=0.7, conciseness=0.7, overall=0.7, passed=True)

    def get_rolling_summary(self) -> str | None:
        return self._rolling_summary

    def reset_rolling_summary(self) -> None:
        self._rolling_summary = None
        self._last_compressed_index = 0
        self._persist_state()

    def _persist_state(self) -> None:
        """将压缩状态持久化到 LangGraph Store"""
        if self._store is None or self._user_id is None:
            return

        # 按 thread_id 隔离：不同会话的压缩状态互不干扰
        state_key = f"engine_state_{self._thread_id}" if self._thread_id else "engine_state"
        namespace = (str(self._user_id), "compression_state")
        data = {
            "rolling_summary": self._rolling_summary,
            "last_compressed_index": self._last_compressed_index,
        }
        try:
            self._store.put(namespace, state_key, data)
            logger.debug(f"压缩状态已持久化: user={self._user_id}, thread={self._thread_id}")
        except Exception as e:
            logger.warning(f"压缩状态持久化失败: {e}")

    def _load_state(self) -> None:
        """从 LangGraph Store 加载压缩状态"""
        if self._store is None or self._user_id is None:
            return

        # 按 thread_id 隔离：不同会话的压缩状态互不干扰
        state_key = f"engine_state_{self._thread_id}" if self._thread_id else "engine_state"
        namespace = (str(self._user_id), "compression_state")
        try:
            item = self._store.get(namespace, state_key)
            if item and hasattr(item, "value"):
                data = item.value
                self._rolling_summary = data.get("rolling_summary")
                self._last_compressed_index = data.get("last_compressed_index", 0)
                logger.debug(
                    f"压缩状态已恢复: user={self._user_id}, "
                    f"thread={self._thread_id}, "
                    f"last_index={self._last_compressed_index}"
                )
        except Exception as e:
            logger.warning(f"压缩状态加载失败: {e}")

    def compress_incremental(
        self,
        messages: list[dict[str, Any]],
        keep_recent: int | None = None,
    ) -> tuple[list[dict[str, Any]], CompressionResult]:
        start_time = time.time()
        total_tokens = TokenEstimator.estimate_dict_messages(messages)
        n_keep = keep_recent if keep_recent is not None else self.config.keep_recent_messages

        # 分离 LONG_TERM 和 SHORT_TERM 消息
        long_term_messages = []
        short_term_messages = []
        for msg in messages:
            if self._classify_memory_tier(msg) == MemoryTier.LONG_TERM:
                long_term_messages.append(msg)
            else:
                short_term_messages.append(msg)

        if len(short_term_messages) <= n_keep:
            return messages, CompressionResult(
                original_message_count=len(messages),
                original_token_estimate=total_tokens,
                compressed_token_estimate=total_tokens,
                is_incremental=True,
            )

        new_start = self._last_compressed_index
        split_point = len(short_term_messages) - n_keep

        # last_index 超出当前消息范围时重置
        if new_start > len(short_term_messages):
            logger.warning(f"last_compressed_index={new_start} 超出消息范围 ({len(short_term_messages)})，重置为 0")
            self._last_compressed_index = 0
            new_start = 0

        if new_start >= split_point:
            # last_index 已覆盖所有可压缩消息，但 token 仍超限时强制重新压缩
            if total_tokens > self.config.trigger_threshold:
                logger.warning(
                    f"增量压缩已覆盖但 token 仍超限 ({total_tokens} > {self.config.trigger_threshold})，"
                    f"强制从索引 0 重新压缩"
                )
                new_start = 0
                self._last_compressed_index = 0
            else:
                return messages, CompressionResult(
                    original_message_count=len(messages),
                    original_token_estimate=total_tokens,
                    compressed_token_estimate=total_tokens,
                    is_incremental=True,
                )

        new_messages = short_term_messages[new_start:split_point]
        entities = self._entity_extractor.extract_from_messages(new_messages) if self.config.entity_aware else []
        summary = self._generate_incremental_summary(new_messages, entities, self._rolling_summary)
        decisions = self._extract_decisions(new_messages)

        self._rolling_summary = summary
        self._last_compressed_index = split_point

        result = CompressionResult(
            compressed=True,
            summary=summary,
            key_entities=entities,
            key_decisions=decisions,
            strategy_used=self.config.strategy,
            is_incremental=True,
        )

        result.original_message_count = len(messages)
        result.original_token_estimate = total_tokens
        compressed_messages = self._build_compressed_messages(messages, result, long_term_messages)
        result.compressed_token_estimate = TokenEstimator.estimate_dict_messages(compressed_messages)
        result.duration_ms = (time.time() - start_time) * 1000

        if result.original_token_estimate > 0:
            result.compression_ratio = 1.0 - (result.compressed_token_estimate / result.original_token_estimate)

        logger.info(
            f"增量压缩: {len(new_messages)} 条新消息, "
            f"{result.original_token_estimate} -> {result.compressed_token_estimate} tokens, "
            f"压缩率 {result.compression_ratio:.1%}, "
            f"耗时 {result.duration_ms:.0f}ms"
        )

        # 持久化压缩状态到 Store
        self._persist_state()

        return compressed_messages, result

    def _generate_incremental_summary(
        self,
        new_messages: list[dict[str, Any]],
        entities: list[str],
        existing_summary: str | None,
    ) -> str:
        if not new_messages:
            return existing_summary or ""

        conversation_text = self._format_messages(new_messages)
        if len(conversation_text) > 8000:
            conversation_text = conversation_text[:8000] + "\n...(内容过长已截断)"

        entity_hint = ""
        if entities:
            entity_hint = f"\n\n已知关键实体：{', '.join(entities[:15])}"

        try:
            model = self._get_compression_model()

            if existing_summary:
                prompt = (
                    "请将以下对话内容压缩为结构化摘要，严格保留：\n"
                    "1. 【核心话题】讨论的主要问题和主题\n"
                    "2. 【关键结论】已得出的重要结论和决策\n"
                    "3. 【用户需求】用户明确表达的需求和偏好\n"
                    "4. 【工具使用】调用的工具及获取的关键信息\n"
                    "5. 【待办事项】尚未完成或需要跟进的事项\n\n"
                    f"对话内容：\n{conversation_text}\n"
                    f"{entity_hint}\n\n"
                    "请用中文生成不超过200字的结构化摘要："
                )
                response = model.invoke([{"role": "user", "content": prompt}])
                new_summary = getattr(response, "content", "")

                if new_summary:
                    merged = self._merge_incremental_summary(existing_summary, new_summary)
                    if len(merged) > self.config.summary_max_length:
                        merged = merged[: self.config.summary_max_length]
                    return merged
                return existing_summary
            else:
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
                return summary or self._fallback_summary(new_messages)
        except Exception:
            logger.exception("LLM 增量摘要生成失败")
            fallback = self._fallback_summary(new_messages)
            if existing_summary:
                return existing_summary + "\n" + fallback if fallback else existing_summary
            return fallback

    def _merge_incremental_summary(self, existing: str, new_part: str) -> str:
        if not existing:
            return new_part
        if not new_part:
            return existing

        merged = f"{existing}\n\n【后续更新】\n{new_part}"

        if len(merged) > self.config.summary_max_length:
            keep_new_len = len(new_part) + 20
            max_existing_len = self.config.summary_max_length - keep_new_len
            if max_existing_len > 100:
                existing = existing[:max_existing_len]
                if not existing.endswith(("。", "，", "；", "：", ".", ",")):
                    last_period = max(
                        existing.rfind("。"),
                        existing.rfind("."),
                        existing.rfind("\n"),
                    )
                    if last_period > max_existing_len // 2:
                        existing = existing[: last_period + 1]
                merged = f"{existing}\n\n【后续更新】\n{new_part}"
            else:
                merged = new_part

        return merged

    def _get_embedding(self, text: str) -> list[float] | None:
        cache_key = text[:200]
        if cache_key in self._embedding_cache:
            return self._embedding_cache[cache_key]

        try:
            from Django_xm.apps.knowledge.services.embedding_service import get_embeddings

            embeddings = get_embeddings()
            vec = embeddings.embed_query(text[:500])
            if len(self._embedding_cache) < 1000:
                self._embedding_cache[cache_key] = vec
            return vec
        except Exception:
            return None

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
