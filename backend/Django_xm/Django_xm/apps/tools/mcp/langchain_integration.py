"""
LangChain Agent MCP 集成模块

将 MCP 工具集成到 LangChain Agent 中，支持:
1. ReAct Agent with MCP tools
2. OpenAI Functions Agent with MCP tools
3. 自定义 Agent Executor with MCP tools
4. 工具选择和过滤

使用方式:
    from Django_xm.apps.tools.mcp.langchain_integration import create_mcp_react_agent

    agent = await create_mcp_react_agent(
        model="deepseek-chat",
        mcp_server_names=["sequential-thinking"],
    )
    result = await agent.ainvoke({"input": "分析这个问题"})
"""

from typing import List, Dict, Any, Optional, Sequence
from langchain_core.tools import BaseTool
from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from Django_xm.apps.ai_engine.config import get_logger
from Django_xm.apps.ai_engine.services.llm_factory import get_llm

logger = get_logger(__name__)


async def load_mcp_tools_for_langchain(
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


async def create_mcp_react_agent(
    model: Optional[str] = None,
    llm: Optional[BaseChatModel] = None,
    tools: Optional[Sequence[BaseTool]] = None,
    mcp_server_names: Optional[List[str]] = None,
    include_mcp_tools: bool = True,
    include_local_tools: bool = True,
    include_builtin_tools: bool = False,
    use_advanced_tools: bool = False,
    use_web_search: bool = False,
    verbose: bool = False,
    **kwargs: Any,
):
    try:
        from langchain.agents import create_react_agent, AgentExecutor
    except ImportError:
        from langchain.agents import initialize_agent, AgentType
        logger.warning("使用旧版 langchain.agents API")

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

    if include_mcp_tools:
        mcp_tools = await load_mcp_tools_for_langchain(
            include_local=include_local_tools,
            server_names=mcp_server_names,
        )
        all_tools.extend(mcp_tools)

    if not all_tools:
        logger.warning("未加载任何工具，LangChain Agent 可能无法正常工作")

    prompt = ChatPromptTemplate.from_messages([
        ("system", (
            "你是一个智能助手，可以使用以下工具来帮助用户解决问题。\n"
            "请根据用户的问题选择合适的工具，并给出详细的回答。\n"
            "如果不需要使用工具，请直接回答用户的问题。"
        )),
        MessagesPlaceholder("chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])

    try:
        agent = create_react_agent(llm, all_tools, prompt)
        agent_executor = AgentExecutor(
            agent=agent,
            tools=all_tools,
            verbose=verbose,
            handle_parsing_errors=True,
            max_iterations=10,
            **kwargs,
        )
    except NameError:
        agent_executor = initialize_agent(
            all_tools,
            llm,
            agent=AgentType.STRUCTURED_CHAT_ZERO_SHOT_REACT_DESCRIPTION,
            verbose=verbose,
            handle_parsing_errors=True,
            max_iterations=10,
            **kwargs,
        )

    logger.info(
        f"LangChain ReAct Agent 已创建: "
        f"tools={len(all_tools)}, "
        f"mcp_servers={mcp_server_names or 'all'}"
    )

    return agent_executor


async def create_mcp_openai_functions_agent(
    model: Optional[str] = None,
    llm: Optional[BaseChatModel] = None,
    tools: Optional[Sequence[BaseTool]] = None,
    mcp_server_names: Optional[List[str]] = None,
    include_mcp_tools: bool = True,
    include_local_tools: bool = True,
    verbose: bool = False,
    **kwargs: Any,
):
    try:
        from langchain.agents import create_openai_tools_agent, AgentExecutor
    except ImportError:
        logger.error("create_openai_tools_agent 不可用，请使用 create_mcp_react_agent")
        return None

    if llm is None:
        llm = get_llm(model)

    all_tools: List[BaseTool] = list(tools) if tools else []

    if include_mcp_tools:
        mcp_tools = await load_mcp_tools_for_langchain(
            include_local=include_local_tools,
            server_names=mcp_server_names,
        )
        all_tools.extend(mcp_tools)

    prompt = ChatPromptTemplate.from_messages([
        ("system", "你是一个智能助手，可以使用工具来帮助用户解决问题。"),
        MessagesPlaceholder("chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])

    agent = create_openai_tools_agent(llm, all_tools, prompt)
    agent_executor = AgentExecutor(
        agent=agent,
        tools=all_tools,
        verbose=verbose,
        handle_parsing_errors=True,
        **kwargs,
    )

    logger.info(
        f"LangChain OpenAI Functions Agent 已创建: "
        f"tools={len(all_tools)}, "
        f"mcp_servers={mcp_server_names or 'all'}"
    )

    return agent_executor


async def create_mcp_structured_chat_agent(
    model: Optional[str] = None,
    llm: Optional[BaseChatModel] = None,
    tools: Optional[Sequence[BaseTool]] = None,
    mcp_server_names: Optional[List[str]] = None,
    include_mcp_tools: bool = True,
    include_local_tools: bool = True,
    verbose: bool = False,
    **kwargs: Any,
):
    try:
        from langchain.agents import create_structured_chat_agent, AgentExecutor
    except ImportError:
        logger.error("create_structured_chat_agent 不可用")
        return None

    if llm is None:
        llm = get_llm(model)

    all_tools: List[BaseTool] = list(tools) if tools else []

    if include_mcp_tools:
        mcp_tools = await load_mcp_tools_for_langchain(
            include_local=include_local_tools,
            server_names=mcp_server_names,
        )
        all_tools.extend(mcp_tools)

    prompt = ChatPromptTemplate.from_messages([
        ("system", (
            "你是一个智能助手，可以使用工具来帮助用户解决问题。\n"
            "请根据用户的问题选择合适的工具。"
        )),
        MessagesPlaceholder("chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])

    agent = create_structured_chat_agent(llm, all_tools, prompt)
    agent_executor = AgentExecutor(
        agent=agent,
        tools=all_tools,
        verbose=verbose,
        handle_parsing_errors=True,
        **kwargs,
    )

    logger.info(
        f"LangChain Structured Chat Agent 已创建: "
        f"tools={len(all_tools)}, "
        f"mcp_servers={mcp_server_names or 'all'}"
    )

    return agent_executor


__all__ = [
    "load_mcp_tools_for_langchain",
    "create_mcp_react_agent",
    "create_mcp_openai_functions_agent",
    "create_mcp_structured_chat_agent",
]
