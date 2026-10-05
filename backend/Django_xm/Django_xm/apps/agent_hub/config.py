from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from Django_xm.apps.ai_engine.config import settings as _ai_engine_settings

logger = logging.getLogger(__name__)


class AgentType(StrEnum):
    BASE = "base"
    RAG = "rag"
    SAFE_RAG = "safe_rag"
    DEEP_RESEARCH = "deep_research"


# AgentType → CapabilityRegistry agent-type 键映射（唯一真源，禁止在他处复制）。
# 消费方：tool_resolver.resolve_tools / middleware.build_middleware。此前两处
# 各持一份字面量副本（spec E2，arch 审计 §5.2 定案）：新增 AgentType 漏改任一
# 副本会静默 fallback "base"，已收敛至此与 AgentType 枚举同文件维护。
# 语义：CapabilityRegistry 的 is_compatible 键空间为 base/deep_research/learning
# （ai_engine/capabilities/builtin.py），rag/safe_rag 在该层复用 base 装配；
# 键集合必须与 AgentType 成员一一对应（新增枚举值时同步本表，
# test_agent_type_mapping 有防漂移断言）。
AGENT_TYPE_CAPABILITY_KEY: dict[str, str] = {
    AgentType.BASE.value: "base",
    AgentType.RAG.value: "base",
    AgentType.SAFE_RAG.value: "base",
    AgentType.DEEP_RESEARCH.value: "deep_research",
}


def resolve_agent_type_str(config) -> str:
    """解析 AgentConfig 在 CapabilityRegistry 层使用的 agent-type 键。

    Args:
        config: AgentConfig（读取 agent_type）。

    Returns:
        注册表层 agent-type 键；未知 agent_type 值静默回退 "base"
        （与收敛前两份副本行为一致，防漂移由测试断言兜底）。
    """
    return AGENT_TYPE_CAPABILITY_KEY.get(config.agent_type.value, "base")


# 能力默认表：唯一权威定义在 ai_engine/config.py 的 Settings.AGENT_CAPABILITIES_DEFAULT
# （ARCH-06 双源合并，已删除本地硬编码副本），此处导入同一对象引用。
# 子代理运行时统一以 AgentType.BASE 构建（spawn.py / langgraph_adapter.py），
# 其角色提示由 subagent_runtime/registry.py + research/prompts.py 承担。
# 同步断言由 Django_xm.apps.ai_engine.tests.test_capability_defaults 保证。
AGENT_CAPABILITIES_DEFAULT: dict[str, list[str]] = _ai_engine_settings.AGENT_CAPABILITIES_DEFAULT

_SUBAGENT_EXCLUSIVE_FIELDS = ("subagents", "skills", "memory", "permissions", "backend")
_SIMPLE_AGENT_TYPES = {AgentType.BASE, AgentType.RAG, AgentType.SAFE_RAG}
_RAG_TYPES = {AgentType.RAG, AgentType.SAFE_RAG}


@dataclass
class AgentConfig:
    agent_type: AgentType = AgentType.BASE
    # 模型实例仅由 AgentFactory 内部经 resolve_model 写回（输出通道），
    # 外部传入会被 validate() 拒绝；外部通过 provider_id/model_name 指定模型
    model: BaseChatModel | None = None
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

        if self.model is not None:
            raise ConfigValidationError(
                "AgentConfig.model 由 AgentFactory 内部填充（统一经 get_chat_model 创建带 fallback 的包装实例），"
                "禁止外部传入模型实例或字符串；请通过 provider_id / model_name 指定模型"
            )

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
            self.capabilities = list(AGENT_CAPABILITIES_DEFAULT.get(self.agent_type.value, []))

        if self.store is None:
            try:
                from Django_xm.apps.ai_engine.services.checkpointer_factory import get_store

                auto_store = get_store()
                if auto_store is not None:
                    self.store = auto_store
                    logger.debug("AgentConfig: 自动注入 Store（长期记忆）")
            except Exception as e:
                logger.debug(f"AgentConfig: Store 自动注入跳过: {e}")

        # checkpointer 不在此处注入：异步 checkpointer 的获取是异步的，同步方法无法 await。
        # 统一由 AgentFactory.create()（async）在 resolve_defaults 之后注入，
        # 避免子代理走同步 get_checkpointer() 后用 astream 触发 NotImplementedError。
