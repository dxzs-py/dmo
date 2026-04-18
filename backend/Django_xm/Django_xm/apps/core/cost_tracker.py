"""
Token 成本追踪系统
参考 claw-code-main 的 usage.rs 实现
支持精确的 Token 使用量追踪和多模型成本估算
"""

import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime

from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)


@dataclass
class ModelPricing:
    input_price_per_million: float
    output_price_per_million: float
    cache_creation_price_per_million: float = 0.0
    cache_read_price_per_million: float = 0.0


MODEL_PRICING: Dict[str, ModelPricing] = {
    "gpt-4o": ModelPricing(
        input_price_per_million=2.5,
        output_price_per_million=10.0,
        cache_creation_price_per_million=1.25,
        cache_read_price_per_million=0.3125,
    ),
    "gpt-4o-mini": ModelPricing(
        input_price_per_million=0.15,
        output_price_per_million=0.6,
        cache_creation_price_per_million=0.075,
        cache_read_price_per_million=0.01875,
    ),
    "gpt-4-turbo": ModelPricing(
        input_price_per_million=10.0,
        output_price_per_million=30.0,
    ),
    "gpt-4": ModelPricing(
        input_price_per_million=30.0,
        output_price_per_million=60.0,
    ),
    "gpt-3.5-turbo": ModelPricing(
        input_price_per_million=0.5,
        output_price_per_million=1.5,
    ),
    "claude-opus-4-20250514": ModelPricing(
        input_price_per_million=15.0,
        output_price_per_million=75.0,
        cache_creation_price_per_million=18.75,
        cache_read_price_per_million=1.5,
    ),
    "claude-sonnet-4-20250514": ModelPricing(
        input_price_per_million=3.0,
        output_price_per_million=15.0,
        cache_creation_price_per_million=3.75,
        cache_read_price_per_million=0.3,
    ),
    "claude-3-5-sonnet-20241022": ModelPricing(
        input_price_per_million=3.0,
        output_price_per_million=15.0,
    ),
    "deepseek-chat": ModelPricing(
        input_price_per_million=0.14,
        output_price_per_million=0.28,
        cache_creation_price_per_million=0.014,
        cache_read_price_per_million=0.014,
    ),
    "deepseek-reasoner": ModelPricing(
        input_price_per_million=0.55,
        output_price_per_million=2.19,
        cache_creation_price_per_million=0.14,
        cache_read_price_per_million=0.14,
    ),
}

DEFAULT_PRICING = ModelPricing(
    input_price_per_million=3.0,
    output_price_per_million=15.0,
)


@dataclass
class CostRecord:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cached_input_tokens: int = 0
    cache_creation_tokens: int = 0
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def calculate_cost(self) -> float:
        pricing = MODEL_PRICING.get(self.model, DEFAULT_PRICING)
        input_cost = (self.input_tokens / 1_000_000) * pricing.input_price_per_million
        output_cost = (self.output_tokens / 1_000_000) * pricing.output_price_per_million
        cache_creation_cost = (
            (self.cache_creation_tokens / 1_000_000) * pricing.cache_creation_price_per_million
        )
        cache_read_cost = (
            (self.cached_input_tokens / 1_000_000) * pricing.cache_read_price_per_million
        )
        return input_cost + output_cost + cache_creation_cost + cache_read_cost

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "reasoningTokens": self.reasoning_tokens,
            "cachedInputTokens": self.cached_input_tokens,
            "cacheCreationTokens": self.cache_creation_tokens,
            "cost": round(self.calculate_cost(), 6),
            "timestamp": self.timestamp,
        }


class CostTracker:
    """成本追踪器 - 追踪整个会话或请求的 Token 使用量和成本"""

    def __init__(self, default_model: str = "gpt-4o"):
        self.default_model = default_model
        self.records: List[CostRecord] = []
        self._current_record: Optional[CostRecord] = None

    def start_record(self, model: Optional[str] = None) -> CostRecord:
        model = model or self.default_model
        self._current_record = CostRecord(model=model)
        return self._current_record

    def finish_record(self) -> Optional[CostRecord]:
        if self._current_record is None:
            return None
        record = self._current_record
        self.records.append(record)
        self._current_record = None
        return record

    def update_from_metadata(self, metadata: Dict[str, Any], model: Optional[str] = None):
        if not metadata:
            return

        if self._current_record is None:
            self.start_record(model)

        usage_meta = metadata.get("usage_metadata", {})

        if "input_tokens" in usage_meta:
            self._current_record.input_tokens += usage_meta["input_tokens"]
        if "output_tokens" in usage_meta:
            self._current_record.output_tokens += usage_meta["output_tokens"]
        if "reasoning_tokens" in usage_meta:
            self._current_record.reasoning_tokens += usage_meta["reasoning_tokens"]
        if "cached_tokens" in usage_meta:
            self._current_record.cached_input_tokens += usage_meta["cached_tokens"]
        if "cache_creation_input_tokens" in usage_meta:
            self._current_record.cache_creation_tokens += usage_meta["cache_creation_input_tokens"]

        if model and self._current_record.model != model:
            self._current_record.model = model

    def get_total_cost(self) -> float:
        return sum(r.calculate_cost() for r in self.records)

    def get_total_tokens(self) -> Dict[str, int]:
        totals = {
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "cached_input_tokens": 0,
            "cache_creation_tokens": 0,
        }
        for record in self.records:
            totals["input_tokens"] += record.input_tokens
            totals["output_tokens"] += record.output_tokens
            totals["reasoning_tokens"] += record.reasoning_tokens
            totals["cached_input_tokens"] += record.cached_input_tokens
            totals["cache_creation_tokens"] += record.cache_creation_tokens
        return totals

    def get_summary(self) -> Dict[str, Any]:
        totals = self.get_total_tokens()
        total_cost = self.get_total_cost()
        return {
            "totalCost": round(total_cost, 6),
            "totalCostFormatted": f"${total_cost:.4f}",
            "tokens": {
                "input": totals["input_tokens"],
                "output": totals["output_tokens"],
                "reasoning": totals["reasoning_tokens"],
                "cachedInput": totals["cached_input_tokens"],
                "cacheCreation": totals["cache_creation_tokens"],
                "total": (
                    totals["input_tokens"]
                    + totals["output_tokens"]
                    + totals["reasoning_tokens"]
                ),
            },
            "recordCount": len(self.records),
            "models": list(set(r.model for r in self.records)),
            "records": [r.to_dict() for r in self.records[-10:]],
        }

    def log_summary(self):
        summary = self.get_summary()
        logger.info(
            f"💰 成本统计: {summary['totalCostFormatted']} | "
            f"Token: 输入={summary['tokens']['input']}, "
            f"输出={summary['tokens']['output']}, "
            f"推理={summary['tokens']['reasoning']} | "
            f"调用次数: {summary['recordCount']}"
        )


def create_cost_tracker(model: Optional[str] = None) -> CostTracker:
    from Django_xm.apps.core.config import settings
    model = model or getattr(settings, 'openai_model', 'gpt-4o')
    return CostTracker(default_model=model)


def get_model_pricing(model: str) -> Dict[str, Any]:
    pricing = MODEL_PRICING.get(model, DEFAULT_PRICING)
    return {
        "model": model,
        "inputPricePerMillion": pricing.input_price_per_million,
        "outputPricePerMillion": pricing.output_price_per_million,
        "cacheCreationPricePerMillion": pricing.cache_creation_price_per_million,
        "cacheReadPricePerMillion": pricing.cache_read_price_per_million,
    }


def get_all_model_pricing() -> Dict[str, Dict[str, Any]]:
    return {model: get_model_pricing(model) for model in MODEL_PRICING}
