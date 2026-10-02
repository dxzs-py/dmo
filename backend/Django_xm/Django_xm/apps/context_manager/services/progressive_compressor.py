import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar

from Django_xm.apps.context_manager.config import context_settings
from Django_xm.apps.context_manager.services.compression import (
    CompressionConfig,
    CompressionStrategy,
    ContextCompressionEngine,
    MemoryTier,
    TokenEstimator,
)
from Django_xm.apps.context_manager.services.context_pruner import ContextPruner

logger = logging.getLogger(__name__)


class CompressionLevel(Enum):
    LEVEL_1_BASELINE = "level_1_baseline"
    LEVEL_2_SUMMARY = "level_2_summary"
    LEVEL_3_RELEVANCE = "level_3_relevance"
    LEVEL_4_AGGRESSIVE = "level_4_aggressive"


@dataclass
class ProgressiveCompressionResult:
    level: CompressionLevel
    original_tokens: int
    compressed_tokens: int
    compression_ratio: float
    messages: list[dict[str, Any]]
    summary: str | None = None
    key_entities: list[str] = field(default_factory=list)
    key_decisions: list[str] = field(default_factory=list)
    strategy_used: str | None = None


class ProgressiveCompressor:
    """渐进式压缩薄壳（ARCH-07 合并后）。

    压缩语义唯一归属统一引擎 ContextCompressionEngine：
    - Level 2 摘要 → engine.compress_incremental；
    - Level 4 激进 → engine.compress_aggressive（原壳内重复的实体/决策
      抽取正则与硬编码 keep_recent 已收编进引擎，配置单源 context_settings）；
    - Level 1/3 的剪枝/相关性过滤仍复用 ContextPruner（剪枝非压缩语义）。

    类名与公共方法签名（__init__ / determine_level / compress）保持不变，
    ContextManagerMiddleware 无感复用。
    """

    # 配置单源（ARCH-07）：分级阈值唯一来源 context_settings，
    # 消除壳内硬编码默认值（0.5/0.75/0.9）与配置项的重复定义。
    _DEFAULT_THRESHOLDS: ClassVar[dict[CompressionLevel, float]] = {
        CompressionLevel.LEVEL_1_BASELINE: 0.0,
        CompressionLevel.LEVEL_2_SUMMARY: context_settings.compression_level_2_threshold,
        CompressionLevel.LEVEL_3_RELEVANCE: context_settings.compression_level_3_threshold,
        CompressionLevel.LEVEL_4_AGGRESSIVE: context_settings.compression_level_4_threshold,
    }

    def __init__(
        self,
        model_name: str = "",
        store: Any | None = None,
        user_id: str | None = None,
        level_thresholds: dict[CompressionLevel, float] | None = None,
        thread_id: str | None = None,
    ):
        self._model_name = model_name
        self._store = store
        self._user_id = user_id
        self._thread_id = thread_id
        self._level_thresholds = level_thresholds or dict(self._DEFAULT_THRESHOLDS)
        # 显式传入的 level_thresholds 缺省项用配置单源默认值补齐
        for level, default in self._DEFAULT_THRESHOLDS.items():
            self._level_thresholds.setdefault(level, default)

        self._pruner = ContextPruner()

        model_limit = TokenEstimator.get_model_limit(model_name)
        config = CompressionConfig(
            max_context_tokens=model_limit,
            token_threshold_ratio=context_settings.compression_threshold_ratio,
            keep_recent_messages=context_settings.compression_keep_recent,
            summary_max_length=context_settings.compression_summary_max_length,
            strategy=CompressionStrategy(context_settings.compression_strategy),
        )
        self._engine = ContextCompressionEngine(config, store=store, user_id=user_id, thread_id=thread_id)

    def determine_level(self, current_tokens: int, max_tokens: int) -> CompressionLevel:
        if max_tokens <= 0:
            return CompressionLevel.LEVEL_1_BASELINE
        ratio = current_tokens / max_tokens
        if ratio >= self._level_thresholds[CompressionLevel.LEVEL_4_AGGRESSIVE]:
            return CompressionLevel.LEVEL_4_AGGRESSIVE
        if ratio >= self._level_thresholds[CompressionLevel.LEVEL_3_RELEVANCE]:
            return CompressionLevel.LEVEL_3_RELEVANCE
        if ratio >= self._level_thresholds[CompressionLevel.LEVEL_2_SUMMARY]:
            return CompressionLevel.LEVEL_2_SUMMARY
        return CompressionLevel.LEVEL_1_BASELINE

    def compress(
        self,
        messages: list[dict[str, Any]],
        query: str | None = None,
    ) -> tuple[list[dict[str, Any]], ProgressiveCompressionResult]:
        try:
            total_tokens = TokenEstimator.estimate_dict_messages(messages)
            max_tokens = TokenEstimator.get_model_limit(self._model_name)
            level = self.determine_level(total_tokens, max_tokens)

            if level == CompressionLevel.LEVEL_1_BASELINE:
                if total_tokens / max_tokens < self._level_thresholds[CompressionLevel.LEVEL_2_SUMMARY]:
                    compressed_msgs, _ = self._pruner.prune(messages)
                    compressed_tokens = TokenEstimator.estimate_dict_messages(compressed_msgs)
                    return compressed_msgs, ProgressiveCompressionResult(
                        level=level,
                        original_tokens=total_tokens,
                        compressed_tokens=compressed_tokens,
                        compression_ratio=1.0 - (compressed_tokens / total_tokens) if total_tokens > 0 else 0.0,
                        messages=compressed_msgs,
                        strategy_used="baseline_prune",
                    )
                return self._compress_level_1(messages)

            if level == CompressionLevel.LEVEL_2_SUMMARY:
                return self._compress_level_2(messages)
            if level == CompressionLevel.LEVEL_3_RELEVANCE:
                return self._compress_level_3(messages, query or "")
            if level == CompressionLevel.LEVEL_4_AGGRESSIVE:
                return self._compress_level_4(messages, query)

            return messages, ProgressiveCompressionResult(
                level=level,
                original_tokens=total_tokens,
                compressed_tokens=total_tokens,
                compression_ratio=0.0,
                messages=messages,
            )
        except Exception:
            logger.exception("渐进式压缩失败")
            total_tokens = TokenEstimator.estimate_dict_messages(messages)
            return messages, ProgressiveCompressionResult(
                level=CompressionLevel.LEVEL_1_BASELINE,
                original_tokens=total_tokens,
                compressed_tokens=total_tokens,
                compression_ratio=0.0,
                messages=messages,
            )

    def _compress_level_1(
        self,
        messages: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], ProgressiveCompressionResult]:
        total_tokens = TokenEstimator.estimate_dict_messages(messages)
        try:
            compressed_msgs, _ = self._pruner.prune(messages)
            compressed_tokens = TokenEstimator.estimate_dict_messages(compressed_msgs)
            return compressed_msgs, ProgressiveCompressionResult(
                level=CompressionLevel.LEVEL_1_BASELINE,
                original_tokens=total_tokens,
                compressed_tokens=compressed_tokens,
                compression_ratio=1.0 - (compressed_tokens / total_tokens) if total_tokens > 0 else 0.0,
                messages=compressed_msgs,
                strategy_used="baseline_prune",
            )
        except Exception:
            logger.exception("Level 1 压缩失败")
            return messages, ProgressiveCompressionResult(
                level=CompressionLevel.LEVEL_1_BASELINE,
                original_tokens=total_tokens,
                compressed_tokens=total_tokens,
                compression_ratio=0.0,
                messages=messages,
            )

    def _compress_level_2(
        self,
        messages: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], ProgressiveCompressionResult]:
        total_tokens = TokenEstimator.estimate_dict_messages(messages)
        try:
            marked = self._mark_long_term(messages)
            compressed_dicts, result = self._engine.compress_incremental(marked)
            compressed_tokens = TokenEstimator.estimate_dict_messages(compressed_dicts)
            return compressed_dicts, ProgressiveCompressionResult(
                level=CompressionLevel.LEVEL_2_SUMMARY,
                original_tokens=total_tokens,
                compressed_tokens=compressed_tokens,
                compression_ratio=1.0 - (compressed_tokens / total_tokens) if total_tokens > 0 else 0.0,
                messages=compressed_dicts,
                summary=result.summary,
                key_entities=result.key_entities or [],
                key_decisions=result.key_decisions or [],
                strategy_used="incremental_summary",
            )
        except Exception:
            logger.exception("Level 2 压缩失败")
            return messages, ProgressiveCompressionResult(
                level=CompressionLevel.LEVEL_2_SUMMARY,
                original_tokens=total_tokens,
                compressed_tokens=total_tokens,
                compression_ratio=0.0,
                messages=messages,
            )

    def _compress_level_3(
        self,
        messages: list[dict[str, Any]],
        query: str,
    ) -> tuple[list[dict[str, Any]], ProgressiveCompressionResult]:
        total_tokens = TokenEstimator.estimate_dict_messages(messages)
        try:
            level2_msgs, level2_result = self._compress_level_2(messages)

            long_term_msgs = [m for m in level2_msgs if m.get("memory_tier") == MemoryTier.LONG_TERM.value]
            short_term_msgs = [m for m in level2_msgs if m.get("memory_tier") != MemoryTier.LONG_TERM.value]

            if not query or not short_term_msgs:
                compressed_tokens = TokenEstimator.estimate_dict_messages(level2_msgs)
                return level2_msgs, ProgressiveCompressionResult(
                    level=CompressionLevel.LEVEL_3_RELEVANCE,
                    original_tokens=total_tokens,
                    compressed_tokens=compressed_tokens,
                    compression_ratio=1.0 - (compressed_tokens / total_tokens) if total_tokens > 0 else 0.0,
                    messages=level2_msgs,
                    summary=level2_result.summary,
                    key_entities=level2_result.key_entities,
                    key_decisions=level2_result.key_decisions,
                    strategy_used="relevance_filter",
                )

            keep_count = max(context_settings.compression_keep_recent, len(short_term_msgs) // 2)
            relevant_short, _ = self._pruner.prune_by_relevance(short_term_msgs, query, keep_count)

            compressed = long_term_msgs + relevant_short
            compressed_tokens = TokenEstimator.estimate_dict_messages(compressed)
            return compressed, ProgressiveCompressionResult(
                level=CompressionLevel.LEVEL_3_RELEVANCE,
                original_tokens=total_tokens,
                compressed_tokens=compressed_tokens,
                compression_ratio=1.0 - (compressed_tokens / total_tokens) if total_tokens > 0 else 0.0,
                messages=compressed,
                summary=level2_result.summary,
                key_entities=level2_result.key_entities,
                key_decisions=level2_result.key_decisions,
                strategy_used="relevance_filter",
            )
        except Exception:
            logger.exception("Level 3 压缩失败")
            return messages, ProgressiveCompressionResult(
                level=CompressionLevel.LEVEL_3_RELEVANCE,
                original_tokens=total_tokens,
                compressed_tokens=total_tokens,
                compression_ratio=0.0,
                messages=messages,
            )

    def _compress_level_4(
        self,
        messages: list[dict[str, Any]],
        query: str | None = None,
    ) -> tuple[list[dict[str, Any]], ProgressiveCompressionResult]:
        total_tokens = TokenEstimator.estimate_dict_messages(messages)
        try:
            # 压缩语义委托统一引擎（ARCH-07）：激进压缩 = 规则实体/决策 + 最近 N 条 + query 兜底
            marked = self._mark_long_term(messages)
            compressed_dicts, engine_result = self._engine.compress_aggressive(marked, query=query)
            compressed_tokens = TokenEstimator.estimate_dict_messages(compressed_dicts)
            return compressed_dicts, ProgressiveCompressionResult(
                level=CompressionLevel.LEVEL_4_AGGRESSIVE,
                original_tokens=total_tokens,
                compressed_tokens=compressed_tokens,
                compression_ratio=1.0 - (compressed_tokens / total_tokens) if total_tokens > 0 else 0.0,
                messages=compressed_dicts,
                key_entities=engine_result.key_entities or [],
                key_decisions=engine_result.key_decisions or [],
                strategy_used="aggressive",
            )
        except Exception:
            logger.exception("Level 4 压缩失败")
            return messages, ProgressiveCompressionResult(
                level=CompressionLevel.LEVEL_4_AGGRESSIVE,
                original_tokens=total_tokens,
                compressed_tokens=total_tokens,
                compression_ratio=0.0,
                messages=messages,
            )

    @staticmethod
    def _mark_long_term(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        from Django_xm.apps.context_manager.services.manager import ContextManager

        tags = context_settings.long_term_tags.split(",") if context_settings.long_term_tags else None
        return ContextManager.mark_long_term_static(messages, tags)
