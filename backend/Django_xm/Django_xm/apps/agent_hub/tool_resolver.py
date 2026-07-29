from __future__ import annotations

import logging

from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)


async def resolve_tools(config) -> list[BaseTool]:
    if config.tools is not None and len(config.tools) > 0:
        logger.info(f"使用显式工具集 ({len(config.tools)} 个)")
        return list(config.tools)

    tools = []

    try:
        from Django_xm.apps.ai_engine.capabilities import registry

        capabilities = config.capabilities or registry.get_default_capabilities(_get_agent_type_str(config))
        if "tool_injection" in capabilities:
            built_tools = await registry.build_tools_for_agent_async(
                _get_agent_type_str(config),
                capabilities,
                tool_config=config.tool_config,
                user_id=config.user_id,
            )
            tools.extend(built_tools)
            logger.info(f"通过 CapabilityRegistry 加载 {len(built_tools)} 个工具")
    except Exception as e:
        logger.warning(f"CapabilityRegistry 工具加载失败: {e}")

    if config.agent_type.value in {"rag", "safe_rag"}:
        if config.retriever is not None:
            from langchain.tools.retriever import create_retriever_tool

            retriever_tool = create_retriever_tool(
                config.retriever,
                "knowledge_base",
                "Search for information in the knowledge base",
            )
            tools.append(retriever_tool)
            logger.info("已注入 RAG retriever 工具")

    if not tools:
        try:
            from Django_xm.apps.tools import get_core_tools

            tools = get_core_tools()
            logger.info(f"回退到核心工具集 ({len(tools)} 个)")
        except Exception:
            logger.exception("核心工具集加载也失败")
            tools = []

    return tools


def _get_agent_type_str(config) -> str:
    type_map = {
        "base": "base",
        "rag": "base",
        "safe_rag": "base",
        "deep_research": "deep_research",
        "deep_research_custom": "deep_research",
        "web_researcher": "base",
        "doc_analyst": "base",
        "report_writer": "base",
    }
    return type_map.get(config.agent_type.value, "base")
