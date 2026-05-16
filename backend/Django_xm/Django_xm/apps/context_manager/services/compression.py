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
import logging
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage

from Django_xm.apps.context_manager.config import get_logger

logger = get_logger(__name__)


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

    @property
    def trigger_threshold(self) -> int:
        return int(self.max_context_tokens * self.token_threshold_ratio)


@dataclass
class CompressionResult:
    compressed: bool = False
    original_message_count: int = 0
    original_token_estimate: int = 0
    compressed_token_estimate: int = 0
    summary: Optional[str] = None
    key_entities: List[str] = field(default_factory=list)
    key_decisions: List[str] = field(default_factory=list)
    compression_ratio: float = 0.0
    strategy_used: Optional[CompressionStrategy] = None
    duration_ms: float = 0.0


class TokenEstimator:
    _MODEL_LIMITS: Dict[str, int] = {
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
    }

    @staticmethod
    def estimate(text: str) -> int:
        if not text:
            return 0
        chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        other_chars = len(text) - chinese_chars
        return int(chinese_chars * 1.5 + other_chars * 0.25)

    @staticmethod
    def estimate_messages(messages: List[BaseMessage]) -> int:
        total = 0
        for msg in messages:
            total += TokenEstimator.estimate(msg.content if isinstance(msg.content, str) else str(msg.content))
            if hasattr(msg, 'tool_calls') and msg.tool_calls:
                for tc in msg.tool_calls:
                    total += TokenEstimator.estimate(str(tc.get('args', {})))
        return total

    @staticmethod
    def estimate_dict_messages(messages: List[Dict[str, Any]]) -> int:
        total = 0
        for msg in messages:
            content = msg.get('content', '')
            if isinstance(content, str):
                total += TokenEstimator.estimate(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        total += TokenEstimator.estimate(block.get('text', ''))
                    elif isinstance(block, str):
                        total += TokenEstimator.estimate(block)
            tool_calls = msg.get('tool_calls', [])
            if tool_calls:
                for tc in tool_calls:
                    total += TokenEstimator.estimate(str(tc.get('args', tc.get('function', {}))))
        return total

    @classmethod
    def get_model_limit(cls, model_name: str) -> int:
        if not model_name:
            return 128000
        for key, limit in cls._MODEL_LIMITS.items():
            if key in model_name:
                return limit
        return 128000


class EntityExtractor:
    _STOP_WORDS = frozenset({
        "的", "了", "是", "在", "我", "你", "他", "她", "它", "们",
        "这", "那", "有", "和", "与", "或", "不", "也", "都", "就",
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "i", "you", "he", "she", "it", "we", "they", "me", "him",
        "and", "or", "but", "in", "on", "at", "to", "for", "of",
    })

    @staticmethod
    def extract_from_messages(messages: List[Dict[str, Any]]) -> List[str]:
        entities = []
        for msg in messages:
            role = msg.get('role', '')
            content = msg.get('content', '')
            if isinstance(content, list):
                content = ' '.join(
                    block.get('text', '') if isinstance(block, dict) else str(block)
                    for block in content
                )
            if not isinstance(content, str) or not content.strip():
                continue

            if role == 'user':
                entities.extend(EntityExtractor._extract_user_entities(content))
            elif role == 'assistant':
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
    def _extract_user_entities(text: str) -> List[str]:
        import re
        entities = []

        patterns = [
            r'[""「」]([^""「」]{2,50})[""「」]',
            r'(?:叫做?|名为|称为|是)([^\s，。！？,.!?]{2,20})',
            r'(\d+(?:\.\d+)?)\s*(?:元|万|亿|%|度|米|千克|GB|MB|KB|小时|分钟|天|周|月|年)',
            r'(?:使用|用|选择|配置|设置)([^\s，。！？,.!?]{2,30})',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, text)
            entities.extend(matches)

        sentences = re.split(r'[。！？.!?]', text)
        for s in sentences:
            s = s.strip()
            if 4 <= len(s) <= 40 and not any(w in s for w in ['怎么', '什么', '如何', '为什么']):
                entities.append(s)

        return entities

    @staticmethod
    def _extract_assistant_entities(text: str) -> List[str]:
        import re
        entities = []

        patterns = [
            r'(?:建议|推荐|应该|需要|必须)([^\s，。！？,.!?]{2,30})',
            r'(?:步骤|方法|方案|策略)(?:[一二三四五12345])[:：]?\s*([^\n，。]{2,40})',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, text)
            entities.extend(matches)

        return entities


class ContextCompressionEngine:
    """上下文自动压缩引擎"""

    def __init__(self, config: Optional[CompressionConfig] = None):
        self.config = config or CompressionConfig()
        self._entity_extractor = EntityExtractor()

    def should_compress(self, messages: List[Dict[str, Any]]) -> bool:
        if len(messages) < self.config.keep_recent_messages * 2:
            return False
        total_tokens = TokenEstimator.estimate_dict_messages(messages)
        return total_tokens > self.config.trigger_threshold

    def compress(self, messages: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], CompressionResult]:
        start_time = time.time()
        total_tokens = TokenEstimator.estimate_dict_messages(messages)

        if not self.should_compress(messages):
            return messages, CompressionResult(
                original_message_count=len(messages),
                original_token_estimate=total_tokens,
                compressed_token_estimate=total_tokens,
            )

        if self.config.strategy == CompressionStrategy.SUMMARY:
            result = self._compress_summary(messages)
        elif self.config.strategy == CompressionStrategy.SLIDING_WINDOW:
            result = self._compress_sliding_window(messages)
        else:
            result = self._compress_hybrid(messages)

        result.original_message_count = len(messages)
        result.original_token_estimate = total_tokens
        result.compressed_token_estimate = TokenEstimator.estimate_dict_messages(messages) if not result.compressed else TokenEstimator.estimate_dict_messages(self._build_compressed_messages(messages, result))
        result.duration_ms = (time.time() - start_time) * 1000

        if result.original_token_estimate > 0:
            result.compression_ratio = 1.0 - (result.compressed_token_estimate / result.original_token_estimate)

        logger.info(
            f"上下文压缩: {result.original_message_count} 条消息, "
            f"{result.original_token_estimate} -> {result.compressed_token_estimate} tokens, "
            f"压缩率 {result.compression_ratio:.1%}, 策略={result.strategy_used}, "
            f"耗时 {result.duration_ms:.0f}ms"
        )

        compressed_messages = self._build_compressed_messages(messages, result)
        return compressed_messages, result

    def _compress_summary(self, messages: List[Dict[str, Any]]) -> CompressionResult:
        old_messages = messages[:-self.config.keep_recent_messages]
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

    def _compress_sliding_window(self, messages: List[Dict[str, Any]]) -> CompressionResult:
        return CompressionResult(
            compressed=True,
            key_entities=self._entity_extractor.extract_from_messages(messages) if self.config.entity_aware else [],
            strategy_used=CompressionStrategy.SLIDING_WINDOW,
        )

    def _compress_hybrid(self, messages: List[Dict[str, Any]]) -> CompressionResult:
        old_messages = messages[:-self.config.keep_recent_messages]
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

    def _generate_summary(self, messages: List[Dict[str, Any]], entities: List[str]) -> str:
        if not messages:
            return ""

        conversation_text = self._format_messages(messages)
        if len(conversation_text) > 12000:
            conversation_text = conversation_text[:12000] + "\n...(内容过长已截断)"

        entity_hint = ""
        if entities:
            entity_hint = f"\n\n已知关键实体：{', '.join(entities[:15])}"

        try:
            from Django_xm.apps.ai_engine.services.llm_factory import get_chat_model
            model = get_chat_model()
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
                summary = summary[:self.config.summary_max_length]
            return summary or self._fallback_summary(messages)
        except Exception as e:
            logger.error(f"LLM 生成摘要失败: {e}")
            return self._fallback_summary(messages)

    def _extract_decisions(self, messages: List[Dict[str, Any]]) -> List[str]:
        import re
        decisions = []
        for msg in messages:
            if msg.get('role') != 'assistant':
                continue
            content = msg.get('content', '')
            if not isinstance(content, str):
                continue

            patterns = [
                r'(?:建议|推荐|应该|决定|确认|选择)\s*[：:]\s*([^\n，。]{4,60})',
                r'(?:结论|结果|答案)\s*[是为]\s*([^\n，。]{4,60})',
            ]
            for pattern in patterns:
                matches = re.findall(pattern, content)
                decisions.extend(matches)

        return decisions[:10]

    @staticmethod
    def _format_messages(messages: List[Dict[str, Any]]) -> str:
        lines = []
        for msg in messages:
            role = msg.get('role', 'unknown')
            content = msg.get('content', '')
            if isinstance(content, list):
                content = ' '.join(
                    block.get('text', '') if isinstance(block, dict) else str(block)
                    for block in content
                )
            if not isinstance(content, str) or not content.strip():
                continue

            prefix = {"user": "用户", "assistant": "助手", "system": "系统"}.get(role, role)
            truncated = content[:400] + "..." if len(content) > 400 else content
            lines.append(f"[{prefix}]: {truncated}")

            tool_calls = msg.get('tool_calls', [])
            if tool_calls:
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        tool_name = tc.get('name', tc.get('function', {}).get('name', 'unknown'))
                        lines.append(f"  [工具调用]: {tool_name}")

        return "\n".join(lines)

    @staticmethod
    def _fallback_summary(messages: List[Dict[str, Any]]) -> str:
        topics = []
        for msg in messages:
            if msg.get('role') == 'user':
                content = msg.get('content', '')
                if isinstance(content, str) and content.strip():
                    topics.append(content.strip().split('\n')[0][:80])

        unique_topics = list(dict.fromkeys(topics))[:5]
        return "对话摘要：讨论了 " + "；".join(unique_topics) if unique_topics else ""

    def _build_compressed_messages(
        self,
        original: List[Dict[str, Any]],
        result: CompressionResult,
    ) -> List[Dict[str, Any]]:
        if not result.compressed:
            return original

        compressed = []

        if self.config.preserve_system_messages:
            for msg in original:
                if msg.get('role') == 'system':
                    compressed.append(msg)

        context_parts = []
        if result.summary:
            context_parts.append(f"【对话摘要】\n{result.summary}")
        if result.key_entities:
            context_parts.append(f"【关键实体】{', '.join(result.key_entities[:15])}")
        if result.key_decisions:
            context_parts.append(f"【关键决策】\n" + "\n".join(f"- {d}" for d in result.key_decisions[:8]))

        if context_parts:
            compressed.append({
                'role': 'system',
                'content': '\n\n'.join(context_parts),
            })

        recent = original[-self.config.keep_recent_messages:]
        for msg in recent:
            if msg.get('role') != 'system' or not self.config.preserve_system_messages:
                compressed.append(msg)
            elif msg.get('role') == 'system' and not any(
                m.get('role') == 'system' and m.get('content') == msg.get('content')
                for m in compressed
            ):
                compressed.append(msg)

        return compressed


def create_compression_engine(
    model_name: Optional[str] = None,
    strategy: str = "hybrid",
    threshold_ratio: float = 0.8,
    keep_recent: int = 6,
) -> ContextCompressionEngine:
    model_limit = TokenEstimator.get_model_limit(model_name or "")
    config = CompressionConfig(
        max_context_tokens=model_limit,
        token_threshold_ratio=threshold_ratio,
        keep_recent_messages=keep_recent,
        strategy=CompressionStrategy(strategy),
    )
    return ContextCompressionEngine(config)
