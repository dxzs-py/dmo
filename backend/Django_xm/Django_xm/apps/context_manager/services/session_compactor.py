import logging
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass

from Django_xm.apps.config_center.config import get_logger
from Django_xm.apps.context_manager.services.compression import (
    ContextCompressionEngine,
    CompressionConfig,
    CompressionStrategy,
    TokenEstimator,
)

logger = get_logger(__name__)

COMPACT_TOKEN_THRESHOLD = 80000
COMPACT_KEEP_RECENT_MESSAGES = 6
COMPACT_SUMMARY_MAX_LENGTH = 2000


@dataclass
class CompactResult:
    compressed: bool
    original_message_count: int
    kept_message_count: int
    summary: Optional[str] = None
    summary_token_estimate: int = 0


class SessionCompactor:

    def __init__(self, token_threshold: int = COMPACT_TOKEN_THRESHOLD):
        self.token_threshold = token_threshold
        config = CompressionConfig(
            max_context_tokens=token_threshold,
            token_threshold_ratio=1.0,
            keep_recent_messages=COMPACT_KEEP_RECENT_MESSAGES,
            summary_max_length=COMPACT_SUMMARY_MAX_LENGTH,
            strategy=CompressionStrategy.SUMMARY,
            entity_aware=False,
        )
        self._engine = ContextCompressionEngine(config)

    @staticmethod
    def estimate_tokens(text: str) -> int:
        return TokenEstimator.estimate(text)

    def should_compact(self, messages: List[Dict[str, Any]]) -> bool:
        if len(messages) < COMPACT_KEEP_RECENT_MESSAGES * 2:
            return False
        total_tokens = TokenEstimator.estimate_dict_messages(messages)
        return total_tokens > self.token_threshold

    def compact(
        self,
        messages: List[Dict[str, Any]],
        keep_recent: int = COMPACT_KEEP_RECENT_MESSAGES,
    ) -> CompactResult:
        if not self.should_compact(messages):
            return CompactResult(
                compressed=False,
                original_message_count=len(messages),
                kept_message_count=len(messages),
            )

        if len(messages) <= keep_recent:
            return CompactResult(
                compressed=False,
                original_message_count=len(messages),
                kept_message_count=len(messages),
            )

        compressed_messages, result = self._engine.compress(messages)

        summary = result.summary or ""
        summary_tokens = TokenEstimator.estimate(summary) if summary else 0

        logger.info(
            f"会话压缩完成: {result.original_message_count} 条旧消息 -> 摘要 "
            f"({summary_tokens} tokens), 保留 {keep_recent} 条最近消息"
        )

        return CompactResult(
            compressed=True,
            original_message_count=len(messages),
            kept_message_count=keep_recent,
            summary=summary,
            summary_token_estimate=summary_tokens,
        )


def apply_compaction_to_chat_history(
    chat_history: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Optional[CompactResult]]:
    compactor = SessionCompactor()
    result = compactor.compact(chat_history)

    if not result.compressed or not result.summary:
        return chat_history, result

    summary_message = {
        'role': 'system',
        'content': f"以下是之前对话的压缩摘要：\n\n{result.summary}",
    }

    recent_messages = chat_history[-COMPACT_KEEP_RECENT_MESSAGES:]
    compacted = [summary_message] + recent_messages

    return compacted, result
