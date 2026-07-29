import logging
from collections.abc import Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import BaseTool

from Django_xm.async_utils import run_async

from .base import AgentCapability

logger = logging.getLogger(__name__)


class ContextManagementCapability(AgentCapability):
    @property
    def name(self) -> str:
        return "context_management"

    def build_middleware(self, **kwargs) -> Sequence[AgentMiddleware]:
        try:
            from Django_xm.apps.context_manager.middleware import (
                ContextManagerMiddleware,
            )

            middleware = ContextManagerMiddleware(
                model_name=kwargs.get("model_name"),
                trigger_tokens=kwargs.get("trigger_tokens"),
                keep_messages=kwargs.get("keep_messages"),
                strategy=kwargs.get("strategy", "hybrid"),
                user_id=kwargs.get("user_id"),
                store=kwargs.get("store"),
                thread_id=kwargs.get("thread_id"),
            )
            return [middleware]
        except Exception:
            logger.exception("Failed to build ContextManagerMiddleware")
            return []

    def build_tools(self, **kwargs) -> Sequence[BaseTool]:
        return []

    def build_config(self, **kwargs) -> dict[str, Any]:
        try:
            from Django_xm.apps.context_manager.config import context_settings

            return {
                "compression_enabled": context_settings.compression_enabled,
                "compression_strategy": context_settings.compression_strategy,
                "compression_threshold_ratio": context_settings.compression_threshold_ratio,
                "compression_keep_recent": context_settings.compression_keep_recent,
                "compression_summary_max_length": context_settings.compression_summary_max_length,
            }
        except Exception:
            return {
                "compression_enabled": True,
                "compression_strategy": "hybrid",
                "compression_threshold_ratio": 0.8,
                "compression_keep_recent": 6,
                "compression_summary_max_length": 1500,
            }

    def is_compatible(self, agent_type: str) -> bool:
        return agent_type in ("base", "deep_research", "learning")


class ToolInjectionCapability(AgentCapability):
    @property
    def name(self) -> str:
        return "tool_injection"

    def build_middleware(self, **kwargs) -> Sequence[AgentMiddleware]:
        return []

    def build_tools(self, **kwargs) -> Sequence[BaseTool]:
        tool_config: dict | None = kwargs.get("tool_config")
        if tool_config is None or not tool_config.get("use_tools"):
            return []
        try:
            return run_async(self.build_tools_async(tool_config=tool_config))
        except Exception:
            logger.exception("Failed to build tools for ToolInjectionCapability")
            return []

    async def build_tools_async(self, **kwargs) -> Sequence[BaseTool]:
        tool_config: dict | None = kwargs.get("tool_config")
        if tool_config is None or not tool_config.get("use_tools"):
            return []
        try:
            from Django_xm.apps.tools import TOOL_TIER_STANDARD, get_tools_for_request_async

            tools = await get_tools_for_request_async(
                use_tools=tool_config.get("use_tools", True),
                use_web_search=tool_config.get("use_web_search", False),
                use_mcp=tool_config.get("use_mcp", False),
                selected_mcp_servers=tool_config.get("selected_mcp_servers"),
                selected_tools=tool_config.get("selected_tools"),
                user_id=tool_config.get("user_id"),
                tool_tier=tool_config.get("tool_tier", TOOL_TIER_STANDARD),
            )
            return self._apply_tool_budget(tools, tool_config)
        except Exception:
            logger.exception("Failed to build tools (async)")
            return []

    def _apply_tool_budget(self, tools: list[BaseTool], tool_config: dict) -> list[BaseTool]:
        budget = tool_config.get("tool_token_budget")
        if not budget or not tools:
            return tools

        try:
            from Django_xm.apps.context_manager.services.compression import TokenEstimator

            selected_names = set(tool_config.get("selected_tools") or [])
            selected_mcp = set(tool_config.get("selected_mcp_servers") or [])

            scored: list[tuple] = []
            for tool in tools:
                desc = (tool.description or "")[:500]
                tokens = TokenEstimator.estimate_tokens(desc)
                is_mcp = hasattr(tool, "metadata") and (tool.metadata or {}).get("is_mcp_tool", False)
                if tool.name in selected_names or (is_mcp and tool.name in selected_mcp):
                    priority = 3
                elif is_mcp:
                    priority = 1
                else:
                    priority = 2
                scored.append((priority, tokens, tool))

            scored.sort(key=lambda x: (-x[0], x[1]))

            result: list[BaseTool] = []
            used = 0
            for priority, tokens, tool in scored:
                if used + tokens <= budget or priority == 3:
                    result.append(tool)
                    used += tokens
                else:
                    logger.info(
                        "Tool '%s' skipped: token budget exceeded (used=%d, budget=%d)",
                        tool.name,
                        used,
                        budget,
                    )

            if len(result) < len(tools):
                logger.info(
                    "Tool token budget applied: %d/%d tools kept, %d tokens used / %d budget",
                    len(result),
                    len(tools),
                    used,
                    budget,
                )
            return result
        except Exception:
            logger.exception("Tool token budget check failed")
            return tools

    def build_config(self, **kwargs) -> dict[str, Any]:
        tool_config: dict | None = kwargs.get("tool_config")
        if tool_config:
            return {
                "use_tools": tool_config.get("use_tools", False),
                "use_web_search": tool_config.get("use_web_search", False),
                "use_mcp": tool_config.get("use_mcp", False),
                "selected_tools": tool_config.get("selected_tools", []),
                "selected_mcp_servers": tool_config.get("selected_mcp_servers", []),
            }
        return {
            "use_tools": False,
            "use_web_search": False,
            "use_mcp": False,
            "selected_tools": [],
            "selected_mcp_servers": [],
        }

    def is_compatible(self, agent_type: str) -> bool:
        return agent_type in ("base", "deep_research")


class GuardrailsCapability(AgentCapability):
    @property
    def name(self) -> str:
        return "guardrails"

    def build_middleware(self, **kwargs) -> Sequence[AgentMiddleware]:
        try:
            from Django_xm.apps.ai_engine.guardrails import build_middleware_stack

            return build_middleware_stack(
                enable_guardrails=kwargs.get("enable_guardrails", True),
                enable_pii=kwargs.get("enable_pii", False),
                enable_human_in_loop=kwargs.get("enable_human_in_loop", False),
                enable_rate_limit=kwargs.get("enable_rate_limit", False),
                guardrails_strict=kwargs.get("guardrails_strict", False),
                pii_reject=kwargs.get("pii_reject", False),
                approval_tools=kwargs.get("approval_tools"),
                on_approval_request=kwargs.get("on_approval_request"),
                extra_middleware=kwargs.get("extra_middleware"),
            )
        except Exception:
            logger.exception("Failed to build guardrails middleware")
            return []

    def build_tools(self, **kwargs) -> Sequence[BaseTool]:
        return []

    def build_config(self, **kwargs) -> dict[str, Any]:
        try:
            from Django_xm.apps.ai_engine.config import settings

            return {
                "guardrails_enabled": settings.guardrails_enabled,
                "guardrails_strict_mode": settings.guardrails_strict_mode,
                "guardrails_enable_pii": settings.guardrails_enable_pii,
                "guardrails_enable_human_in_loop": settings.guardrails_enable_human_in_loop,
                "guardrails_max_message_count": settings.guardrails_max_message_count,
            }
        except Exception:
            return {
                "guardrails_enabled": False,
                "guardrails_strict_mode": False,
                "guardrails_enable_pii": False,
                "guardrails_enable_human_in_loop": False,
                "guardrails_max_message_count": 100,
            }

    def is_compatible(self, agent_type: str) -> bool:
        return agent_type == "base"


class RateLimitCapability(AgentCapability):
    @property
    def name(self) -> str:
        return "rate_limit"

    def build_middleware(self, **kwargs) -> Sequence[AgentMiddleware]:
        try:
            from Django_xm.apps.ai_engine.guardrails import create_rate_limit_middleware

            agent_type = kwargs.get("agent_type", "base")
            # 深度研究需要更多步骤，使用更宽松的参数
            if agent_type == "deep_research":
                middleware = create_rate_limit_middleware(
                    max_total_calls=kwargs.get("max_total_calls", 500),
                    max_calls_per_second=kwargs.get("max_calls_per_second", 5.0),
                    consecutive_same_tool_limit=kwargs.get("consecutive_same_tool_limit", 15),
                    llm_judge_max_calls=kwargs.get("llm_judge_max_calls", 5),
                    min_calls_before_check=kwargs.get("min_calls_before_check", 25),
                    task_id=kwargs.get("task_id"),
                )
            else:
                middleware = create_rate_limit_middleware(
                    max_total_calls=kwargs.get("max_total_calls", 300),
                    max_calls_per_second=kwargs.get("max_calls_per_second", 5.0),
                    task_id=kwargs.get("task_id"),
                )
            return [middleware]
        except Exception:
            logger.exception("Failed to build rate limit middleware")
            return []

    def build_tools(self, **kwargs) -> Sequence[BaseTool]:
        return []

    def build_config(self, **kwargs) -> dict[str, Any]:
        return {
            "max_total_calls": kwargs.get("max_total_calls", 300),
            "max_calls_per_second": kwargs.get("max_calls_per_second", 5.0),
            "exact_dup_window": kwargs.get("exact_dup_window", 5),
            "exact_dup_threshold": kwargs.get("exact_dup_threshold", 3),
            "consecutive_same_tool_limit": kwargs.get("consecutive_same_tool_limit", 8),
        }

    def is_compatible(self, agent_type: str) -> bool:
        return True


class GroqCompatCapability(AgentCapability):
    @property
    def name(self) -> str:
        return "groq_compat"

    def build_middleware(self, **kwargs) -> Sequence[AgentMiddleware]:
        try:
            from Django_xm.apps.ai_engine.guardrails.middleware import (
                GroqToolCallCompatMiddleware,
            )

            return [GroqToolCallCompatMiddleware()]
        except Exception:
            logger.exception("Failed to build GroqToolCallCompatMiddleware")
            return []

    def build_tools(self, **kwargs) -> Sequence[BaseTool]:
        return []

    def build_config(self, **kwargs) -> dict[str, Any]:
        return {}

    def is_compatible(self, agent_type: str) -> bool:
        return True


BUILTIN_CAPABILITIES: list[AgentCapability] = [
    ContextManagementCapability(),
    ToolInjectionCapability(),
    GuardrailsCapability(),
    RateLimitCapability(),
    GroqCompatCapability(),
]
