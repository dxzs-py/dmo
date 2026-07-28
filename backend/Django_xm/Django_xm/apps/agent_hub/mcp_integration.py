"""LangGraph + MCP Agent 集成模块

将 MCP 工具无缝集成到 LangGraph Agent 中，支持:
1. 自动加载 MCP 工具到 LangGraph ReAct Agent
2. MCP 工具与内置工具混合使用
3. 流式输出支持
4. Checkpointer 持久化

归属说明（Task 15.2）：
    本模块原位于 ``apps/tools/mcp/langgraph_integration.py``，但
    ``create_mcp_langgraph_agent`` 调用 ``agent_hub.create`` 创建 Agent，
    违反 ``tools``（低层）不应依赖 ``agent_hub``（高层）的分层约束。
    Agent 创建是 Agent Hub 的职责，故迁入 ``agent_hub/``。

依赖方向（迁入后）：
    - ``agent_hub`` → ``tools.mcp``（加载 MCP 工具，高层 → 低层，正确）
    - ``agent_hub`` → ``ai_engine``（获取 LLM，高层 → 中层，正确）
    - ``agent_hub`` → ``agent_hub.create``（同 app，正确）
"""

from collections.abc import Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from Django_xm.apps.agent_hub import AgentConfig, AgentType
from Django_xm.apps.agent_hub import create as agent_hub_create
from Django_xm.apps.core.config import get_logger
from Django_xm.apps.ai_engine.services.llm_factory import get_llm

logger = get_logger(__name__)


async def create_mcp_langgraph_agent(
    model: str | None = None,
    llm: BaseChatModel | None = None,
    tools: Sequence[BaseTool] | None = None,
    mcp_server_names: list[str] | None = None,
    include_mcp_tools: bool = True,
    include_builtin_tools: bool = False,
    use_web_search: bool = False,
    prompt_mode: str = "default",
    session_id: str | None = None,
    user_id: int | None = None,
    checkpointer: Any | None = None,
    enable_human_in_loop: bool = False,
    **kwargs: Any,
):
    """创建集成 MCP 工具的 LangGraph Agent。

    Args:
        model: 模型名称
        llm: 已实例化的 LLM（优先于 model）
        tools: 额外工具列表
        mcp_server_names: 指定加载的 MCP Server 名称列表
        include_mcp_tools: 是否加载 MCP 工具
        include_builtin_tools: 是否加载内置工具
        use_web_search: 是否启用联网搜索
        prompt_mode: 提示词模式
        session_id: 会话 ID
        user_id: 用户 ID
        checkpointer: Checkpointer 实例
        enable_human_in_loop: 是否启用人工介入
    """
    if llm is None:
        llm = get_llm(model)

    all_tools: list[BaseTool] = list(tools) if tools else []

    if include_builtin_tools:
        from Django_xm.apps.tools import get_tools_for_request_async
        builtin_tools = await get_tools_for_request_async(
            use_tools=True,
            use_web_search=use_web_search,
            use_mcp=False,
        )
        all_tools.extend(builtin_tools)
        logger.info(f"内置工具已加载 ({len(builtin_tools)} 个)")

    if include_mcp_tools:
        # 从 tools/mcp 加载 MCP 工具（agent_hub → tools，正确方向）
        from Django_xm.apps.tools.mcp.langgraph_integration import load_mcp_tools_for_langgraph
        mcp_tools = await load_mcp_tools_for_langgraph(
            server_names=mcp_server_names,
        )
        all_tools.extend(mcp_tools)
        logger.info(f"MCP 工具已加载 ({len(mcp_tools)} 个)")

    config = AgentConfig(
        agent_type=AgentType.BASE,
        model=llm,
        tools=all_tools,
        session_id=session_id,
        user_id=user_id,
        checkpointer=checkpointer,
        enable_human_in_loop=enable_human_in_loop,
    )
    agent = await agent_hub_create(config)

    logger.info(
        f"LangGraph MCP Agent 已创建: "
        f"tools={len(all_tools)}, "
        f"mcp_servers={mcp_server_names or 'all'}, "
        f"session={session_id}"
    )

    return agent


async def create_langgraph_with_mcp_checkpointer(
    model: str | None = None,
    session_id: str | None = None,
    user_id: int | None = None,
    **kwargs: Any,
):
    """创建带 MCP Checkpointer 的 LangGraph Agent。"""
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
    "create_langgraph_with_mcp_checkpointer",
    "create_mcp_langgraph_agent",
]
