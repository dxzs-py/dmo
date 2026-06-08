from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


class AgentType(str, Enum):
    BASE = "base"
    RAG = "rag"
    SAFE_RAG = "safe_rag"
    DEEP_RESEARCH = "deep_research"
    DEEP_RESEARCH_CUSTOM = "deep_research_custom"
    WEB_RESEARCHER = "web_researcher"
    DOC_ANALYST = "doc_analyst"
    REPORT_WRITER = "report_writer"


AGENT_CAPABILITIES_DEFAULT: Dict[AgentType, List[str]] = {
    AgentType.BASE: ["context_management", "tool_injection", "guardrails", "rate_limit"],
    AgentType.DEEP_RESEARCH: ["context_management", "tool_injection", "rate_limit"],
    AgentType.DEEP_RESEARCH_CUSTOM: ["context_management", "tool_injection", "rate_limit"],
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
    model: Optional[Union[str, Any]] = None
    provider_id: Optional[str] = None
    model_name: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    special_params: Optional[Dict] = None
    tools: Optional[List] = None
    tool_config: Optional[Dict] = None
    system_prompt: Optional[str] = None
    prompt_mode: str = "default"
    middleware: Optional[List] = None
    capabilities: Optional[List[str]] = None
    checkpointer: Optional[Any] = None
    store: Optional[Any] = None
    context_schema: Optional[Any] = None
    response_format: Optional[Any] = None
    cache: Optional[Any] = None
    debug: bool = False
    state_schema: Optional[Any] = None
    interrupt_before: Optional[List[str]] = None
    interrupt_after: Optional[List[str]] = None
    user_id: Optional[int] = None
    session_id: Optional[str] = None
    subagents: Optional[List] = None
    skills: Optional[List[str]] = None
    memory: Optional[List[str]] = None
    permissions: Optional[Any] = None
    backend: Optional[Any] = None
    name: Optional[str] = None
    retriever: Optional[Any] = None
    enable_guardrails: bool = False
    guardrails_strict_mode: bool = False
    enable_pii: bool = False
    enable_human_in_loop: bool = False
    enable_input_validation: bool = False
    enable_output_validation: bool = False
    interrupt_on: Optional[Dict[str, bool]] = None
    backend_type: str = "filesystem"
    work_dir: Optional[str] = None

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
            raise ConfigValidationError(
                f"AgentConfig: agent_type={self.agent_type.value} 必须提供 retriever"
            )

        if self.agent_type == AgentType.SAFE_RAG:
            self.enable_guardrails = True

        if self.temperature is not None:
            if not isinstance(self.temperature, (int, float)) or self.temperature < 0 or self.temperature > 2:
                raise ConfigValidationError(
                    f"AgentConfig: temperature 必须在 0~2 之间，当前值: {self.temperature}"
                )

        if self.max_tokens is not None:
            if not isinstance(self.max_tokens, int) or self.max_tokens < 1:
                raise ConfigValidationError(
                    f"AgentConfig: max_tokens 必须为正整数，当前值: {self.max_tokens}"
                )

        if self.tool_config is not None and not isinstance(self.tool_config, dict):
            raise ConfigValidationError(
                f"AgentConfig: tool_config 必须为 dict 类型"
            )

    def resolve_defaults(self) -> None:
        if self.capabilities is None:
            self.capabilities = list(
                AGENT_CAPABILITIES_DEFAULT.get(self.agent_type, [])
            )

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
