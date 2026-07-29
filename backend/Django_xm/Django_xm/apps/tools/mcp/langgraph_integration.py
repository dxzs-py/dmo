"""MCP 工具加载器（供 LangGraph Agent 使用）

本模块仅负责加载 MCP 工具为 LangChain BaseTool 列表。

归属说明（Task 15.2）：
    原 ``langgraph_integration.py`` 还包含 ``create_mcp_langgraph_agent`` 与
    ``create_langgraph_with_mcp_checkpointer``，它们调用 ``agent_hub.create``
    创建 Agent，违反 ``tools → agent_hub`` 分层。这两个函数已迁至
    ``agent_hub/mcp_integration.py``。

    本模块保留 ``load_mcp_tools_for_langgraph``（仅加载工具，不创建 Agent），
    供 ``agent_hub.mcp_integration`` 与其他调用方使用。
"""

from langchain_core.tools import BaseTool

from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)


async def load_mcp_tools_for_langgraph(
    server_names: list[str] | None = None,
) -> list[BaseTool]:
    """加载 MCP 工具为 LangChain BaseTool 列表。

    Args:
        server_names: 指定加载的 MCP Server 名称列表；为 None 时加载全部

    Returns:
        MCP 工具列表
    """
    from . import get_all_mcp_tools, get_mcp_tools

    if server_names:
        tools: list[BaseTool] = []
        for name in server_names:
            server_tools = await get_mcp_tools(server_name=name)
            tools.extend(server_tools)
        return tools

    tools = await get_all_mcp_tools()
    return tools


__all__ = [
    "load_mcp_tools_for_langgraph",
]
