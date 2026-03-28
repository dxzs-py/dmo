"""
Token 使用追踪器
用于追踪 LLM 的 token 使用情况,为前端 Context 组件提供数据
"""

from typing import Dict, Optional, Any
from dataclasses import dataclass, field

from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)


@dataclass
class TokenUsage:
    """Token 使用统计"""
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cached_input_tokens: int = 0

    def to_dict(self) -> Dict[str, int]:
        """转换为字典格式"""
        return {
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "reasoningTokens": self.reasoning_tokens,
            "cachedInputTokens": self.cached_input_tokens,
        }


MODEL_LIMITS = {
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4-turbo": 128000,
    "gpt-4": 8192,
    "gpt-3.5-turbo": 16385,
    "claude-opus-4-20250514": 200000,
    "claude-sonnet-4-20250514": 200000,
    "claude-3-5-sonnet-20241022": 200000,
    "gemini-2.0-flash-exp": 1000000,
    "gemini-pro": 32768,
}


class UsageTracker:
    def __init__(self, model_id: str = "gpt-4o"):
        self.model_id = model_id
        self.usage = TokenUsage()
        logger.debug(f"📊 初始化 UsageTracker: model_id={model_id}")

    def add_input_tokens(self, count: int):
        """添加输入 token 数量"""
        self.usage.input_tokens += count

    def add_output_tokens(self, count: int):
        """添加输出 token 数量"""
        self.usage.output_tokens += count

    def add_reasoning_tokens(self, count: int):
        """添加推理 token 数量 (仅部分模型支持)"""
        self.usage.reasoning_tokens += count

    def add_cached_tokens(self, count: int):
        """添加缓存命中的 token 数量"""
        self.usage.cached_input_tokens += count

    def update_from_metadata(self, metadata: Dict[str, Any]):
        if not metadata:
            return

        usage_meta = metadata.get("usage_metadata", {})

        if "input_tokens" in usage_meta:
            self.add_input_tokens(usage_meta["input_tokens"])

        if "output_tokens" in usage_meta:
            self.add_output_tokens(usage_meta["output_tokens"])

        if "reasoning_tokens" in usage_meta:
            self.add_reasoning_tokens(usage_meta["reasoning_tokens"])

        if "cached_tokens" in usage_meta:
            self.add_cached_tokens(usage_meta["cached_tokens"])

    def get_total_tokens(self) -> int:
        """获取总 token 数"""
        return (
            self.usage.input_tokens
            + self.usage.output_tokens
            + self.usage.reasoning_tokens
        )

    def get_max_tokens(self) -> int:
        """获取模型的最大 token 限制"""
        return MODEL_LIMITS.get(self.model_id, 128000)

    def get_usage_percentage(self) -> float:
        """获取使用百分比"""
        max_tokens = self.get_max_tokens()
        if max_tokens == 0:
            return 0.0
        return self.get_total_tokens() / max_tokens

    def get_usage_info(self) -> Dict[str, Any]:
        total_tokens = self.get_total_tokens()
        max_tokens = self.get_max_tokens()

        return {
            "usedTokens": total_tokens,
            "maxTokens": max_tokens,
            "usage": self.usage.to_dict(),
            "modelId": self.model_id,
            "percentage": self.get_usage_percentage(),
        }

    def log_summary(self):
        """打印使用情况摘要"""
        info = self.get_usage_info()
        logger.info(
            f"📊 Token 使用统计: "
            f"{info['usedTokens']}/{info['maxTokens']} "
            f"({info['percentage']:.1%}) - "
            f"输入:{self.usage.input_tokens}, "
            f"输出:{self.usage.output_tokens}"
        )


def create_usage_tracker(model_id: Optional[str] = None) -> UsageTracker:
    from Django_xm.apps.core.config import settings

    if model_id is None:
        model_id = getattr(settings, 'openai_model', 'gpt-4o')

    return UsageTracker(model_id=model_id)