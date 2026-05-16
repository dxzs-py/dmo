"""
LangGraph MCP 集成模块

将 MCP 工具无缝集成到 LangGraph Agent 中，支持:
1. 自动加载 MCP 工具到 LangGraph ReAct Agent
2. MCP 工具与内置工具混合使用
3. 流式输出支持
4. Checkpointer 持久化

使用方式:
    from Django_xm.apps.tools.mcp.langgraph_integration import create_mcp_langgraph_agent

    agent = await create_mcp_langgraph_agent(
        model="deepseek-chat",
        session_id="user-session-123",
    )
    result = await agent.ainvoke({"messages": [("user", "你好")]})
"""

from typing import List, Dict, Any, Optional, Sequence
from langchain_core.tools import BaseTool
from langchain_core.language_models import BaseChatModel

from Django_xm.apps.ai_engine.config import get_logger
from Django_xm.apps.ai_engine.services.llm_factory import get_llm
from Django_xm.apps.ai_engine.services.agent_factory import create_base_agent

logger = get_logger(__name__)


async def load_mcp_tools_for_langgraph(
    include_local: bool = True,
    server_names: Optional[List[str]] = None,
) -> List[BaseTool]:
    from . import get_all_mcp_tools, get_mcp_tools

    if server_names:
        tools: List[BaseTool] = []
        for name in server_names:
            server_tools = await get_mcp_tools(server_name=name)
            tools.extend(server_tools)

        if include_local:
            from .local import get_local_mcp_tools
            tools.extend(get_local_mcp_tools())

        return tools

    tools = await get_all_mcp_tools()
    return tools


async def create_mcp_langgraph_agent(
    model: Optional[str] = None,
    llm: Optional[BaseChatModel] = None,
    tools: Optional[Sequence[BaseTool]] = None,
    mcp_server_names: Optional[List[str]] = None,
    include_mcp_tools: bool = True,
    include_local_tools: bool = True,
    include_builtin_tools: bool = False,
    use_advanced_tools: bool = False,
    use_web_search: bool = False,
    prompt_mode: str = "default",
    session_id: Optional[str] = None,
    user_id: Optional[int] = None,
    checkpointer: Optional[Any] = None,
    enable_human_in_loop: bool = False,
    **kwargs: Any,
):
    if llm is None:
        llm = get_llm(model)

    all_tools: List[BaseTool] = list(tools) if tools else []

    if include_builtin_tools:
        from Django_xm.apps.tools import get_tools_for_request
        builtin_tools = get_tools_for_request(
            use_tools=True,
            use_advanced_tools=use_advanced_tools,
            use_web_search=use_web_search,
            use_mcp=False,
        )
        all_tools.extend(builtin_tools)
        logger.info(f"内置工具已加载 ({len(builtin_tools)} 个)")

    if include_mcp_tools:
        mcp_tools = await load_mcp_tools_for_langgraph(
            include_local=include_local_tools,
            server_names=mcp_server_names,
        )
        all_tools.extend(mcp_tools)
        logger.info(f"MCP 工具已加载 ({len(mcp_tools)} 个)")

    agent = create_base_agent(
        model=llm,
        tools=all_tools,
        prompt_mode=prompt_mode,
        session_id=session_id,
        user_id=user_id,
        checkpointer=checkpointer,
        enable_human_in_loop=enable_human_in_loop,
        **kwargs,
    )

    logger.info(
        f"LangGraph MCP Agent 已创建: "
        f"tools={len(all_tools)}, "
        f"mcp_servers={mcp_server_names or 'all'}, "
        f"session={session_id}"
    )

    return agent


async def create_langgraph_with_mcp_checkpointer(
    model: Optional[str] = None,
    session_id: Optional[str] = None,
    user_id: Optional[int] = None,
    **kwargs: Any,
):
    from Django_xm.apps.ai_engine.services.checkpoint_factory import get_checkpointer

    checkpointer = get_checkpointer(session_id=session_id)

    return await create_mcp_langgraph_agent(
        model=model,
        session_id=session_id,
        user_id=user_id,
        checkpointer=checkpointer,
        **kwargs,
    )


__all__ = [
    "load_mcp_tools_for_langgraph",
    "create_mcp_langgraph_agent",
    "create_langgraph_with_mcp_checkpointer",
]
