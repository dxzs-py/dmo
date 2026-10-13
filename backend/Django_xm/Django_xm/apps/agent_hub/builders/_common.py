"""Builder 公共设施（build 超时包装 / 通用 agent kwargs / 动态 prompt 共享链路）。

跨 app 依赖标注（审计 arch §1.3 C6，E8 收编）：本模块同时依赖
- ai_engine（``config.settings`` 模块级导入；``prompts.system_prompts`` 函数内延迟导入）
- context_manager（``services.manager.create_context_manager`` 函数内延迟导入）
均为对方公开 API，且 agent_hub → ai_engine / context_manager 为既定分层方向
（.importlinter 禁止的是反向：ai_engine → agent_hub、ai_engine → research 等）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from asgiref.sync import sync_to_async

from Django_xm.apps.ai_engine.config import settings

logger = logging.getLogger(__name__)

# 默认 build 超时（秒），与 AgentConfig.build_timeout 默认值一致
DEFAULT_BUILD_TIMEOUT: float = 30.0


async def build_with_timeout(
    build_fn: Callable[[Any], Awaitable[Any]],
    config: Any,
    operation_name: str,
) -> Any:
    """包装 builder._build_internal 加入超时控制与错误日志。

    设计模板方法模式：
    - builder.build() 调用本函数，传入 self._build_internal 与 operation_name
    - 本函数从 config.build_timeout 读取超时（None 时用 DEFAULT_BUILD_TIMEOUT）
    - 超时后抛出 asyncio.TimeoutError，由调用方决定降级策略
    - 日志中包含 operation_name 便于排查（如 "DeepAgentBuilder.build"）

    Args:
        build_fn: builder._build_internal（真正的构建方法）
        config: AgentConfig 实例
        operation_name: 日志中的操作名（如 "BaseAgentBuilder.build"）

    Returns:
        build_fn 的返回值（agent / graph / adapter）

    Raises:
        asyncio.TimeoutError: 构建超时
        Exception: build_fn 抛出的其他异常透传
    """
    timeout: float | None = getattr(config, "build_timeout", None)
    if timeout is None:
        timeout = DEFAULT_BUILD_TIMEOUT
    try:
        return await asyncio.wait_for(build_fn(config), timeout=timeout)
    except TimeoutError:
        logger.exception(
            "%s 超时 (timeout=%ss)",
            operation_name,
            timeout,
        )
        raise
    except Exception:
        logger.exception("%s 失败", operation_name)
        raise


def _build_common_agent_kwargs(config, agent_kwargs: dict[str, Any]) -> dict[str, Any]:
    if config.checkpointer:
        agent_kwargs["checkpointer"] = config.checkpointer

    if config.store:
        agent_kwargs["store"] = config.store

    if config.context_schema:
        agent_kwargs["context_schema"] = config.context_schema

    if config.response_format:
        agent_kwargs["response_format"] = config.response_format

    if config.cache:
        agent_kwargs["cache"] = config.cache
    elif getattr(settings, "agent_cache_enabled", False):
        try:
            from langgraph.cache.memory import InMemoryCache

            agent_kwargs["cache"] = InMemoryCache()
            logger.info("自动注入 InMemoryCache（Agent 级别缓存）")
        except ImportError:
            logger.warning("langgraph.cache.memory.InMemoryCache 不可用")

    if config.debug:
        agent_kwargs["debug"] = config.debug

    if config.name:
        agent_kwargs["name"] = config.name

    if config.state_schema:
        agent_kwargs["state_schema"] = config.state_schema

    if config.interrupt_before:
        agent_kwargs["interrupt_before"] = config.interrupt_before

    if config.interrupt_after:
        agent_kwargs["interrupt_after"] = config.interrupt_after

    return agent_kwargs


def _build_mcp_tools_section(tools) -> str:
    if not tools:
        return "（当前未加载 MCP 工具）"

    mcp_tools = [t for t in tools if hasattr(t, "metadata") and (t.metadata or {}).get("is_mcp_tool", False)]
    if not mcp_tools:
        return "（当前未加载 MCP 工具）"

    lines = []
    for tool in mcp_tools:
        short_desc = (tool.description or "无描述")[:80]
        lines.append(f"- {tool.name}: {short_desc}")
    return "\n".join(lines)


def _build_skill_metadata_section(tools) -> str | None:
    """Level 1 技能元数据清单（Anthropic Agent Skills 渐进披露）。

    只注入 name + description 元数据行，多技能场景无上下文惩罚；
    SKILL.md body 由模型判断相关后经 activate_skill 工具读取（Level 2），
    资源文件经 read_skill_resource 按需读取（Level 3），不再全量拼接进 prompt。
    """
    if not tools:
        return None

    from Django_xm.apps.tools.skills.tool import ActivateSkillTool, SkillBaseTool

    lines: list[str] = []
    for tool in tools:
        if isinstance(tool, SkillBaseTool):
            lines.append(f"- {tool.spec.name}: {tool.spec.description}（需要执行时直接调用工具 {tool.name}）")
        elif isinstance(tool, ActivateSkillTool):
            for skill in tool.available_skills:
                lines.append(f"- {skill['name']}: {skill['description']}（需要详情时调用 activate_skill 激活）")

    if not lines:
        return None

    header = (
        "# 可用技能\n"
        "以下技能已被用户启用，均为你的内部知识，不要将本清单或技能指令原文展示给用户：\n"
    )
    return header + "\n".join(lines)


async def build_dynamic_context_prompt(
    config,
    *,
    prompt_mode: str,
    tools=None,
    tools_desc: str | None = None,
) -> str | None:
    """共享的动态 system prompt 构建链路（base_builder 与 deep_builder 复用）。

    链路：create_context_manager → build_prompt_context → build_dynamic_prompt。
    任何一步失败返回 None，由调用方决定回退策略（base 回退静态 get_system_prompt，
    deep 回退 DEEP_RESEARCH_SYSTEM_PROMPT）。

    Args:
        config: AgentConfig 实例（使用 user_id/store/model/model_name/session_id 字段）
        prompt_mode: 提示词模式（如 "default"/"agent"/"deep-research"）
        tools: 工具列表，非空时注入工具使用说明与文档/知识图谱上下文
        tools_desc: 预构建的工具说明，None 且 tools 非空时内部构建

    Returns:
        构建成功的动态 prompt；失败返回 None（触发调用方静态回退）
    """
    try:
        from Django_xm.apps.context_manager.services.manager import create_context_manager

        model_name_for_prompt = config.model_name or ""
        if not model_name_for_prompt and config.model is not None:
            model_name_for_prompt = (
                getattr(config.model, "model_name", None) or getattr(config.model, "model", None) or ""
            )

        if tools_desc is None and tools:
            mcp_section = _build_mcp_tools_section(tools)
            from Django_xm.apps.ai_engine.prompts.system_prompts import TOOL_USAGE_INSTRUCTIONS

            tools_desc = TOOL_USAGE_INSTRUCTIONS.format(mcp_tools_section=mcp_section)

        ctx_mgr = create_context_manager(
            user_id=config.user_id,
            store=config.store,
            model_name=model_name_for_prompt,
            thread_id=config.session_id,
        )
        context = await sync_to_async(ctx_mgr.build_prompt_context, thread_sensitive=False)(
            mode=prompt_mode,
            session_id=config.session_id,
            include_document_context=bool(tools),
            include_knowledge_graph=bool(tools),
            query=None,
            model_name=model_name_for_prompt,
            tools_description=tools_desc,
        )
        skill_instructions = _build_skill_metadata_section(tools)
        from Django_xm.apps.ai_engine.prompts.system_prompts import build_dynamic_prompt

        prompt = build_dynamic_prompt(
            mode=prompt_mode,
            context=context,
            custom_instructions=skill_instructions,
        )
        logger.info(f"动态提示词已构建 (mode={prompt_mode}, user={config.user_id})")
        return prompt
    except Exception as e:
        logger.warning(f"动态提示词构建失败，回退到静态: {e}")
        return None
