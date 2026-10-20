"""
Token 使用追踪器
用于追踪 LLM 的 token 使用情况,为前端 Context 组件提供数据
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.conf import settings as django_settings

from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.common.token_estimator import TokenEstimator

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

        # C-D3：标准 usage_metadata 的明细位于 input/output_token_details 子字典
        # （此前读取顶层 reasoning_tokens/cached_tokens——标准结构中不存在，恒 0）。
        input_details = usage_meta.get("input_token_details") or {}
        output_details = usage_meta.get("output_token_details") or {}
        reasoning = output_details.get("reasoning") or input_details.get("reasoning") or 0
        cached = input_details.get("cache_read") or 0
        if reasoning:
            self.add_reasoning_tokens(reasoning)
        if cached:
            self.add_cached_tokens(cached)

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


# ── 会话累计 token 聚合 provider（阶段 7 票②依赖倒置）─────────────────────────
# ai_engine 不得 import chat.models（.importlinter research-chat-via-facade-only
# 为传递性检查，research→ai_engine→chat 直连边需豁免台账维护，属"豁免=藏违规"）。
# ChatMessage.token_count 聚合实现归位 chat 业务层（chat/services/cross_app.py
# aggregate_session_total_tokens），经运行期 provider 注入——组合根接线在
# chat AppConfig.ready()（先例 context_manager/services/runtime_providers.py，
# 任务 4.1 同款）。创建点分散跨 app（chat stream/loop.py 与 research
# services/adapter.py 均创建 SessionTokenBudgetGuard），故取注册式而非构造注入。
SessionTotalTokensProvider = Callable[[str], int]

_session_total_tokens_provider: SessionTotalTokensProvider | None = None


def set_session_total_tokens_provider(provider: SessionTotalTokensProvider | None) -> None:
    """注册会话累计 token 聚合实现（chat AppConfig.ready() 组合根调用）。"""
    global _session_total_tokens_provider  # noqa: PLW0603 - 运行期 provider 注册点（先例 runtime_providers）
    _session_total_tokens_provider = provider


def get_session_total_tokens_provider() -> SessionTotalTokensProvider | None:
    return _session_total_tokens_provider


def get_session_total_tokens(session_id: str) -> int:
    """聚合单个会话已落库消息的累计 token 数（COST-01 数据源）。

    实现经 ``set_session_total_tokens_provider`` 注入（chat 侧
    aggregate_session_total_tokens：``ChatMessage.token_count`` DB 聚合，
    all_objects 含软删除消息——已消耗 token 不因删消息绕过预算）。

    Args:
        session_id: 聊天会话 ID（ChatSession.session_id）。

    Returns:
        int: 会话累计 token；未注册 provider、会话无消息或聚合失败时
        返回 0（不阻塞主流程）。
    """
    if not session_id:
        return 0
    provider = _session_total_tokens_provider
    if provider is None:
        logger.warning(f"会话累计 token provider 未注册（按 0 处理）: session={session_id}")
        return 0
    try:
        return int(provider(session_id) or 0)
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

        Note:
            同步方法：首次调用会内联执行 DB 聚合（provider 为同步 ORM），
            **禁止在事件循环内调用**——请改用 :meth:`acheck`（fastapi 执行器/
            流式泵均为 async 上下文，冒烟实测同步调用触发
            SynchronousOnlyOperation，基线恒按 0 处理，COST-01 运行时失效）。
        """
        limit = get_session_token_budget_limit()
        if limit <= 0 or not self.session_id:
            return self._disabled_info(limit)
        if self._baseline is None:
            self._baseline = get_session_total_tokens(self.session_id)
        return self._compose(limit, current_request_tokens)

    async def acheck(self, current_request_tokens: int = 0) -> dict[str, Any]:
        """check() 的 async 变体（事件循环内唯一合法入口）。

        DB 基线聚合经 ``asyncio.to_thread`` 离环执行，行为与 check() 等价
        （基线仅加载一次，后续调用复用基线 + 当前请求增量）。
        """
        limit = get_session_token_budget_limit()
        if limit <= 0 or not self.session_id:
            return self._disabled_info(limit)
        if self._baseline is None:
            self._baseline = await asyncio.to_thread(get_session_total_tokens, self.session_id)
        return self._compose(limit, current_request_tokens)

    def _disabled_info(self, limit: int) -> dict[str, Any]:
        return {
            "enabled": False,
            "exceeded": False,
            "session_used": 0,
            "current_request": 0,
            "total": 0,
            "limit": limit,
        }

    def _compose(self, limit: int, current_request_tokens: int) -> dict[str, Any]:
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
