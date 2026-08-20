from __future__ import annotations

import logging
from typing import Any

from Django_xm.apps.agent_hub.builders._registry import register_builder
from Django_xm.apps.agent_hub.config import AgentType

logger = logging.getLogger(__name__)


@register_builder(AgentType.BASE, AgentType.RAG, AgentType.SAFE_RAG)
class BaseAgentBuilder:
    async def build(self, config) -> Any:
        from Django_xm.apps.agent_hub.builders._common import build_with_timeout

        return await build_with_timeout(
            self._build_internal,
            config,
            "BaseAgentBuilder.build",
        )

    async def _build_internal(self, config) -> Any:
        from Django_xm.apps.agent_hub.middleware import build_middleware
        from Django_xm.apps.agent_hub.tool_resolver import resolve_tools

        # 模型已由 AgentFactory 统一解析并写入 config.model
        model = config.model
        tools = await resolve_tools(config)
        middleware_stack = build_middleware(config)

        # 显式注入 ApprovalMiddleware（与 deep_builder 共用 ensure_approval_middleware）
        # 审批机制是核心安全能力，应对所有有工具的 agent 强制启用，与 chat/deep_research/learning 三模块统一
        # （build_middleware 收敛点已保底，此处为双保险）
        try:
            from Django_xm.apps.agent_hub.builders.middleware_utils import ensure_approval_middleware

            ensure_approval_middleware(middleware_stack)
        except Exception as e:
            logger.warning(f"ApprovalMiddleware 注入失败(非致命): {e}")

        # 子 agent 官方支持中间件（与 deep_builder 子 agent 栈统一，
        # 主/子判定 spec D1：configurable.subagent_thread_id 非空 ⇔ 子代理）：
        # - SubAgentNestingMiddleware：注入嵌套层级字段到 state（depth/agent_path/risk_ceiling）。
        #   主 agent（subagent_thread_id 为空）恒写 subagent_depth=0、不注入子代理语义
        #   字段；真子代理 subagent_depth=configurable.depth（适配器 spawn 时已算好）。
        # - SubAgentToolEventMiddleware：仅子代理（subagent_thread_id 非空）转发工具事件，
        #   主 agent 工具事件由主链路唯一发布与持久化（spec D2 单路径）。
        # - SubAgentContentMiddleware：仅子代理捕获正文/中间思考流（主 agent 走主通道）。
        # 无 tools 时不挂（无工具即无子代理能力），避免多余开销。
        #
        # ⚠️ 顺序约束（langchain factory 倒序连边：注册 [..., ToolEvent, Content] →
        # aafter_model 实际执行流 Content → ToolEvent，见 factory.py _add_middleware_edge
        # 逆序链接）。Content 先转发本轮正文（累计到 subagent_contents）、ToolEvent 后
        # 发 PENDING，position = PENDING 瞬间已累计正文长度 → 含本轮文本，与"文本在
        # 前、工具在后"一致。ToolEvent 若排到 Content 之后注册（执行流反转为 ToolEvent
        # 先），position 将少算本轮文本长度 → 前端内联错位。勿调整此顺序。
        if tools:
            try:
                from Django_xm.apps.agent_hub.builders.subagent_support import (
                    SubAgentContentMiddleware,
                    SubAgentNestingMiddleware,
                    SubAgentToolEventMiddleware,
                )

                if not any(isinstance(m, SubAgentNestingMiddleware) for m in middleware_stack):
                    middleware_stack.append(SubAgentNestingMiddleware())
                if not any(isinstance(m, SubAgentToolEventMiddleware) for m in middleware_stack):
                    middleware_stack.append(SubAgentToolEventMiddleware())
                if not any(isinstance(m, SubAgentContentMiddleware) for m in middleware_stack):
                    middleware_stack.append(SubAgentContentMiddleware())
                logger.info("已注入子代理支持中间件到 chat agent 中间件栈")
            except Exception as e:
                logger.warning(f"子代理中间件注入失败(非致命): {e}")

        # 始终构建默认 system_prompt，再追加自定义内容（如研究上下文）
        system_prompt = await self._build_system_prompt(config, tools=tools)
        if config.system_prompt:
            system_prompt = f"{system_prompt}\n\n---\n\n{config.system_prompt}"

        from langchain.agents import create_agent

        agent_kwargs: dict[str, Any] = {
            "model": model,
            "tools": tools,
            "system_prompt": system_prompt,
        }
        if middleware_stack:
            agent_kwargs["middleware"] = middleware_stack

        from Django_xm.apps.agent_hub.builders._common import _build_common_agent_kwargs

        _build_common_agent_kwargs(config, agent_kwargs)

        graph = create_agent(**agent_kwargs)
        logger.info(
            f"BaseAgent 创建成功 "
            f"(type={config.agent_type.value}, tools={len(tools)}, "
            f"middleware={len(middleware_stack)})"
        )
        return graph

    async def _build_system_prompt(self, config, tools=None) -> str:
        prompt_mode = getattr(config, "prompt_mode", "default")

        # 动态上下文链路已提取到 _common.build_dynamic_context_prompt（与 deep_builder 共享），
        # 失败返回 None 时走下方静态回退（与原内联实现行为一致）
        from Django_xm.apps.agent_hub.builders._common import build_dynamic_context_prompt

        prompt = await build_dynamic_context_prompt(config, prompt_mode=prompt_mode, tools=tools)
        if prompt is not None:
            return prompt

        try:
            from Django_xm.apps.ai_engine.prompts.system_prompts import get_system_prompt

            return get_system_prompt(mode=prompt_mode)
        except Exception:
            logger.exception("系统提示词构建完全失败，使用最小回退提示词")
            return "You are a helpful assistant."

    def _build_run_name(self, config, model=None) -> str:
        model_label = config.model_name or ""
        if not model_label:
            if isinstance(model, str):
                model_label = model
            elif model is not None:
                model_label = getattr(model, "model_name", "") or getattr(model, "model", "") or type(model).__name__

        parts = ["BaseAgent", model_label]
        if config.user_id:
            parts.append(f"u{config.user_id}")
        return "/".join(parts)
