"""
Token 使用追踪器
用于追踪 LLM 的 token 使用情况,为前端 Context 组件提供数据
"""

from dataclasses import dataclass
from typing import Any

from django.conf import settings as django_settings

from Django_xm.apps.context_manager.services.compression import TokenEstimator
from Django_xm.apps.core.logging_utils import get_logger

from ..config import settings as app_cfg

logger = get_logger(__name__)


@dataclass
class TokenUsage:
    """Token 使用统计"""

    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cached_input_tokens: int = 0

    def to_dict(self) -> dict[str, int]:
        """转换为字典格式"""
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cached_input_tokens": self.cached_input_tokens,
        }


class UsageTracker:
    def __init__(self, model_id: str | None = None):
        self.model_id = model_id or app_cfg.openai_model
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

    def update_from_metadata(self, metadata: dict[str, Any]):
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
        return self.usage.input_tokens + self.usage.output_tokens + self.usage.reasoning_tokens

    def get_max_tokens(self) -> int:
        """获取模型的最大 token 限制（模型上限统一查询 TokenEstimator 的单一权威表 _MODEL_LIMITS）"""
        return TokenEstimator.get_model_limit(
            self.model_id, default=getattr(django_settings, "AI_DEFAULT_MODEL_TOKEN_LIMIT", 128000)
        )

    def get_usage_percentage(self) -> float:
        """获取使用百分比"""
        max_tokens = self.get_max_tokens()
        if max_tokens == 0:
            return 0.0
        return self.get_total_tokens() / max_tokens

    def get_usage_info(self) -> dict[str, Any]:
        total_tokens = self.get_total_tokens()
        max_tokens = self.get_max_tokens()

        return {
            "used_tokens": total_tokens,
            "max_tokens": max_tokens,
            "usage": self.usage.to_dict(),
            "model_id": self.model_id,
            "percentage": self.get_usage_percentage(),
        }

    def log_summary(self):
        """打印使用情况摘要"""
        info = self.get_usage_info()
        logger.info(
            f"📊 Token 使用统计: "
            f"{info['used_tokens']}/{info['max_tokens']} "
            f"({info['percentage']:.1%}) - "
            f"输入:{self.usage.input_tokens}, "
            f"输出:{self.usage.output_tokens}"
        )


def create_usage_tracker(model_id: str | None = None) -> UsageTracker:
    if model_id is None:
        model_id = app_cfg.openai_model

    return UsageTracker(model_id=model_id)


# ============================================================================
# 会话级 Token 硬预算（COST-01）
# ============================================================================

def get_session_token_budget_limit() -> int:
    """读取会话级 Token 硬预算上限配置。

    Returns:
        int: 预算上限；<=0 表示未启用熔断。
    """
    return int(getattr(app_cfg, "session_token_budget", 0) or 0)


def get_session_total_tokens(session_id: str) -> int:
    """聚合单个会话已落库消息的累计 token 数（COST-01 数据源）。

    数据来源：现有统计链路在消息终态时写入 ``ChatMessage.token_count``
    （chat_service._update_last_message_tokens）。使用 all_objects 包含软删除
    消息——已消耗的 token 不因删消息而绕过预算。

    Args:
        session_id: 聊天会话 ID（ChatSession.session_id）。

    Returns:
        int: 会话累计 token；会话无消息或查询失败时返回 0（不阻塞主流程）。
    """
    if not session_id:
        return 0
    try:
        from django.db.models import Sum

        from Django_xm.apps.chat.models import ChatMessage

        total = ChatMessage.all_objects.filter(session__session_id=session_id).aggregate(
            total=Sum("token_count")
        )["total"]
        return int(total or 0)
    except Exception:
        logger.warning(f"会话累计 token 聚合失败（按 0 处理）: session={session_id}", exc_info=True)
        return 0


class SessionTokenBudgetGuard:
    """会话级 Token 硬预算守卫（COST-01）。

    首次检查时从 DB 读取会话累计基线，之后复用基线 + 当前请求增量
    （避免每轮 LLM 调用重复聚合查询）；当前请求增量来自挂在 graph
    callbacks 上的 TokenUsageCallbackHandler（每轮 LLM 调用结束累加）。
    """

    def __init__(self, session_id: str):
        self.session_id = session_id or ""
        self._baseline: int | None = None  # None = 尚未加载 DB 基线

    @property
    def enabled(self) -> bool:
        """预算未配置（<=0）或无 session_id 时不启用。"""
        return get_session_token_budget_limit() > 0 and bool(self.session_id)

    def check(self, current_request_tokens: int = 0) -> dict[str, Any]:
        """检查会话累计用量是否超限。

        Args:
            current_request_tokens: 当前请求（本次执行）已消耗的 token 增量。

        Returns:
            dict: enabled/exceeded/session_used/current_request/total/limit。
        """
        limit = get_session_token_budget_limit()
        if limit <= 0 or not self.session_id:
            return {
                "enabled": False,
                "exceeded": False,
                "session_used": 0,
                "current_request": 0,
                "total": 0,
                "limit": limit,
            }

        if self._baseline is None:
            self._baseline = get_session_total_tokens(self.session_id)

        current = max(0, int(current_request_tokens or 0))
        total = self._baseline + current
        return {
            "enabled": True,
            "exceeded": total >= limit,
            "session_used": self._baseline,
            "current_request": current,
            "total": total,
            "limit": limit,
        }


def create_session_token_budget_guard(session_id: str | None) -> SessionTokenBudgetGuard:
    """创建会话预算守卫（session_id 为空时守卫自动不启用）。"""
    return SessionTokenBudgetGuard(session_id or "")
