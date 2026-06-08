from __future__ import annotations
import logging
from typing import Any, List

logger = logging.getLogger(__name__)


class SubAgentBuilder:
    async def build(self, config) -> Any:
        from Django_xm.apps.agent_hub.model_resolver import resolve_model
        from Django_xm.apps.agent_hub.tool_resolver import resolve_tools
        from Django_xm.apps.agent_hub.middleware import build_middleware
        from langchain.agents import create_agent

        model = resolve_model(config)
        tools = await resolve_tools(config)
        middleware_stack = build_middleware(config)

        system_prompt = config.system_prompt or self._get_default_prompt(config)

        agent_kwargs = {
            "model": model,
            "tools": tools,
            "system_prompt": system_prompt,
        }
        if middleware_stack:
            agent_kwargs["middleware"] = middleware_stack

        from Django_xm.apps.agent_hub.builders._common import _build_common_agent_kwargs
        _build_common_agent_kwargs(config, agent_kwargs)

        graph = create_agent(**agent_kwargs)
        logger.info(f"子智能体创建成功 (type={config.agent_type.value})")
        return graph

    def _get_default_prompt(self, config) -> str:
        prompts = {
            "web_researcher": "You are a web research assistant. Search the web for relevant information on the given topic.",
            "doc_analyst": "You are a document analysis assistant. Analyze documents and extract key information.",
            "report_writer": "You are a report writing assistant. Write comprehensive reports based on the provided research findings.",
        }
        return prompts.get(config.agent_type.value, "You are a helpful assistant.")
