from __future__ import annotations

import logging
from typing import Any

from Django_xm.apps.agent_hub.builders._registry import register_builder
from Django_xm.apps.agent_hub.config import AgentType

logger = logging.getLogger(__name__)


@register_builder(AgentType.DEEP_RESEARCH_CUSTOM)
class CustomWorkflowBuilder:
    async def build(self, config) -> Any:
        from Django_xm.apps.agent_hub.builders._common import build_with_timeout
        return await build_with_timeout(
            self._build_internal, config, "CustomWorkflowBuilder.build",
        )

    async def _build_internal(self, config) -> Any:
        from Django_xm.apps.research.services.deep_agent import DeepResearchAgent

        agent = DeepResearchAgent(
            thread_id=config.session_id,
            model=config.model,
            enable_web_search=config.tool_config.get("use_web_search", True) if config.tool_config else True,
            enable_doc_analysis=config.tool_config.get("use_doc_analysis", True) if config.tool_config else True,
            enable_guardrails=config.enable_guardrails,
            guardrails_strict_mode=config.guardrails_strict_mode,
            middleware=config.middleware,
            user_id=config.user_id,
            provider_id=config.provider_id,
            model_name=config.model_name,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            special_params=config.special_params,
            checkpointer=config.checkpointer,
            tools=config.tools,
            skills=config.skills,
        )
        logger.info("CustomWorkflow (DeepResearchAgent) 创建成功")
        return agent
