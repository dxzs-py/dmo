"""Agent 工具解析：显式工具集 / capability 注入 / retriever 接线。"""

from __future__ import annotations

import logging

from langchain_core.tools import BaseTool

from Django_xm.apps.agent_hub.config import resolve_agent_type_str

logger = logging.getLogger(__name__)


def _retriever_tool(config) -> BaseTool | None:
    """把 AgentConfig.retriever 转成唯一的知识库检索工具（D4 接线）。"""
    retriever = getattr(config, "retriever", None)
    if retriever is None:
        return None

    from Django_xm.apps.knowledge.services.kb_search_tool import (
        KbToolBundle,
        build_knowledge_base_search_tool,
    )
    from Django_xm.apps.knowledge.services.rag_service import RagScope

    collection = getattr(retriever, "collection_name", None) or "agent_kb"
    bundle = KbToolBundle(
        kb_id="agent_kb",
        name="agent_kb",
        description="",
        scope=RagScope(
            kind="persistent_kb",
            name=collection,
            user_id=getattr(config, "user_id", None),
        ),
        retriever=retriever,
        vector_store=getattr(retriever, "vector_store", None),
    )
    return build_knowledge_base_search_tool(bundles={"agent_kb": bundle}, k=5)


async def resolve_tools(config) -> list[BaseTool]:
    tools: list[BaseTool] = []

    retriever_tool = _retriever_tool(config)
    if retriever_tool is not None:
        tools.append(retriever_tool)
        logger.info("AgentConfig.retriever 已接线为 knowledge_base_search")

    if config.tools is not None and len(config.tools) > 0:
        logger.info(f"使用显式工具集 ({len(config.tools)} 个)")
        tools.extend(config.tools)
        return tools

    built_tools: list[BaseTool] = []
    try:
        from Django_xm.apps.ai_engine.capabilities import registry

        capabilities = config.capabilities or registry.get_default_capabilities(
            resolve_agent_type_str(config)
        )
        if "tool_injection" in capabilities:
            built_tools = await registry.build_tools_for_agent_async(
                resolve_agent_type_str(config),
                capabilities,
                tool_config=config.tool_config,
                user_id=config.user_id,
            )
            logger.info(f"通过 CapabilityRegistry 加载 {len(built_tools)} 个工具")
    except Exception as e:
        logger.warning(f"CapabilityRegistry 工具加载失败: {e}")

    if not built_tools:
        try:
            from Django_xm.apps.tools import get_core_tools

            built_tools = get_core_tools()
            logger.info(f"回退到核心工具集 ({len(built_tools)} 个)")
        except (ImportError, ModuleNotFoundError):
            logger.exception("核心工具集加载失败（导入错误，永久故障）")
            built_tools = []
        except Exception:
            logger.exception("核心工具集加载失败（临时故障）")
            raise

    tools.extend(built_tools)
    return tools
