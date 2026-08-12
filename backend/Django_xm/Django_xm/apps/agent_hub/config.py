from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class AgentType(StrEnum):
    BASE = "base"
    RAG = "rag"
    SAFE_RAG = "safe_rag"
    DEEP_RESEARCH = "deep_research"
    WEB_RESEARCHER = "web_researcher"
    DOC_ANALYST = "doc_analyst"
    REPORT_WRITER = "report_writer"


AGENT_CAPABILITIES_DEFAULT: dict[AgentType, list[str]] = {
    AgentType.BASE: ["context_management", "tool_injection", "guardrails", "rate_limit"],
    AgentType.DEEP_RESEARCH: ["context_management", "tool_injection", "rate_limit"],
    AgentType.RAG: ["context_management", "tool_injection"],
    AgentType.SAFE_RAG: ["context_management", "tool_injection", "guardrails"],
    AgentType.WEB_RESEARCHER: ["rate_limit"],
    AgentType.DOC_ANALYST: ["rate_limit"],
    AgentType.REPORT_WRITER: ["rate_limit"],
}

_SUBAGENT_EXCLUSIVE_FIELDS = ("subagents", "skills", "memory", "permissions", "backend")
_SIMPLE_AGENT_TYPES = {AgentType.BASE, AgentType.RAG, AgentType.SAFE_RAG}
_RAG_TYPES = {AgentType.RAG, AgentType.SAFE_RAG}


@dataclass
class AgentConfig:
    agent_type: AgentType = AgentType.BASE
    model: str | Any | None = None
    provider_id: str | None = None
    model_name: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    special_params: dict | None = None
    enable_deep_thinking: bool = False
    tools: list | None = None
    tool_config: dict | None = None
    system_prompt: str | None = None
    prompt_mode: str = "default"
    middleware: list | None = None
    capabilities: list[str] | None = None
    checkpointer: Any | None = None
    store: Any | None = None
    context_schema: Any | None = None
    response_format: Any | None = None
    cache: Any | None = None
    debug: bool = False
    state_schema: Any | None = None
    interrupt_before: list[str] | None = None
    interrupt_after: list[str] | None = None
    user_id: int | None = None
    session_id: str | None = None
    subagents: list | None = None
    skills: list[str] | None = None
    memory: list[str] | None = None
    permissions: Any | None = None
    backend: Any | None = None
    name: str | None = None
    retriever: Any | None = None
    enable_guardrails: bool = False
    guardrails_strict_mode: bool = False
    enable_pii: bool = False
    enable_human_in_loop: bool = False
    enable_input_validation: bool = False
    enable_output_validation: bool = False
    interrupt_on: dict[str, bool] | None = None
    backend_type: str = "filesystem"
    work_dir: str | None = None
    # 预检快速失败模式：True 时预检未通过抛出 PreflightCheckError，False 时仅 warning 并继续
    fail_fast_on_preflight: bool = False
    # builder.build() 超时（秒）：None 表示不限制（仅推荐调试用），默认 30s
    # 超时后抛出 asyncio.TimeoutError，由调用方决定降级策略
    build_timeout: float | None = 30.0
    _preflight_issues: list[str] | None = field(default=None, repr=False)

    def validate(self) -> None:
        from Django_xm.apps.agent_hub.exceptions import ConfigValidationError

        if self.agent_type in _SIMPLE_AGENT_TYPES:
            for field_name in _SUBAGENT_EXCLUSIVE_FIELDS:
                value = getattr(self, field_name, None)
                if value is not None:
                    logger.warning(
                        "AgentConfig: agent_type=%s 不支持 %s，已忽略",
                        self.agent_type.value,
                        field_name,
                    )
                    object.__setattr__(self, field_name, None)

        if self.agent_type in _RAG_TYPES and self.retriever is None:
            raise ConfigValidationError(f"AgentConfig: agent_type={self.agent_type.value} 必须提供 retriever")

        if self.agent_type == AgentType.SAFE_RAG:
            self.enable_guardrails = True

        if self.temperature is not None:
            if not isinstance(self.temperature, (int, float)) or self.temperature < 0 or self.temperature > 2:
                raise ConfigValidationError(f"AgentConfig: temperature 必须在 0~2 之间，当前值: {self.temperature}")

        if self.max_tokens is not None:
            if not isinstance(self.max_tokens, int) or self.max_tokens < 1:
                raise ConfigValidationError(f"AgentConfig: max_tokens 必须为正整数，当前值: {self.max_tokens}")

        if self.tool_config is not None and not isinstance(self.tool_config, dict):
            raise ConfigValidationError("AgentConfig: tool_config 必须为 dict 类型")

    def resolve_defaults(self) -> None:
        if self.capabilities is None:
            self.capabilities = list(AGENT_CAPABILITIES_DEFAULT.get(self.agent_type, []))

        if self.store is None:
            try:
                from Django_xm.apps.ai_engine.services.checkpointer_factory import get_store

                auto_store = get_store()
                if auto_store is not None:
                    self.store = auto_store
                    logger.debug("AgentConfig: 自动注入 Store（长期记忆）")
            except Exception as e:
                logger.debug(f"AgentConfig: Store 自动注入跳过: {e}")

        if self.checkpointer is None:
            try:
                from Django_xm.apps.ai_engine.services.checkpointer_factory import get_checkpointer

                auto_cp = get_checkpointer()
                if auto_cp is not None:
                    self.checkpointer = auto_cp
                    logger.debug("AgentConfig: 自动注入 Checkpointer")
            except Exception as e:
                logger.debug(f"AgentConfig: Checkpointer 自动注入跳过: {e}")
