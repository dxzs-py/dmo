"""
工具管理服务

从 chat_service.py 拆分出的工具获取和管理逻辑：
- Agent 模式工具获取（动态判断是否使用工具）
- 深度研究模式额外工具获取（MCP + 用户选择）
"""
import logging
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


class ToolService:

    def __init__(self, user_id: Optional[int] = None):
        self.user_id = user_id

    async def get_tools(self, data: Dict[str, Any]) -> List:
        from Django_xm.apps.ai_engine.capabilities import registry
        from Django_xm.apps.tools import TOOL_TIER_STANDARD

        mode = data.get('mode', 'agent')

        if mode == 'deep-research':
            return []

        use_web_search = data.get('use_web_search', False)
        use_mcp = data.get('use_mcp', False)
        selected_mcp_servers = data.get('selected_mcp_servers')
        selected_tools = data.get('selected_tools')
        use_knowledge_base = data.get('use_knowledge_base', False)
        tool_tier = data.get('tool_tier', TOOL_TIER_STANDARD)

        has_any_tool_enabled = bool(
            use_web_search or use_mcp or use_knowledge_base or selected_tools
        )
        use_tools = has_any_tool_enabled

        logger.info(f"[GetTools] mode={mode}, use_tools={use_tools}, use_web={use_web_search}, use_mcp={use_mcp}, tier={tool_tier}")

        if not use_tools:
            return []

        capabilities = registry.get_default_capabilities("base")

        if "tool_injection" in capabilities:
            tool_config = {
                "use_tools": True,
                "use_web_search": use_web_search,
                "use_mcp": use_mcp,
                "selected_tools": selected_tools,
                "selected_mcp_servers": selected_mcp_servers,
                "user_id": self.user_id,
                "tool_tier": tool_tier,
            }
            tools = await registry.build_tools_for_agent_async("base", capabilities, tool_config=tool_config)
            logger.info(f"[GetTools] 通过 CapabilityRegistry 返回 {len(tools)} 个工具")
        else:
            from Django_xm.apps.tools import get_tools_for_request_async
            tools = await get_tools_for_request_async(
                use_tools=True,
                use_web_search=use_web_search,
                use_mcp=use_mcp,
                selected_mcp_servers=selected_mcp_servers,
                selected_tools=selected_tools,
                user_id=self.user_id,
                tool_tier=tool_tier,
            )
            logger.info(f"[GetTools] 回退直接加载 {len(tools)} 个工具")

        extra_tools = data.get('_extra_tools', [])
        if extra_tools:
            existing_names = {t.name for t in tools}
            for t in extra_tools:
                if t.name not in existing_names:
                    tools.append(t)
                    existing_names.add(t.name)

        logger.info(f"[GetTools] 最终返回 {len(tools)} 个工具: {[t.name for t in tools]}")
        return tools

    async def get_deep_research_tools(self, data: dict) -> list:
        from Django_xm.apps.ai_engine.capabilities import registry
        from Django_xm.apps.tools import TOOL_TIER_EXTENDED

        use_mcp = data.get('use_mcp', False)
        selected_mcp_servers = data.get('selected_mcp_servers', [])
        selected_tools = data.get('selected_tools', [])

        if not use_mcp and not selected_tools:
            return []

        capabilities = registry.get_default_capabilities("deep_research")

        if "tool_injection" in capabilities:
            tool_config = {
                "use_tools": True,
                "use_web_search": False,
                "use_mcp": use_mcp,
                "selected_tools": selected_tools,
                "selected_mcp_servers": selected_mcp_servers,
                "user_id": self.user_id,
                "tool_tier": TOOL_TIER_EXTENDED,
            }
            extra_tools = await registry.build_tools_for_agent_async("deep_research", capabilities, tool_config=tool_config)
            logger.info(f"[DeepResearchTools] 通过 CapabilityRegistry 返回 {len(extra_tools)} 个工具")
        else:
            from Django_xm.apps.tools import get_tools_for_request_async
            extra_tools = []
            if use_mcp and selected_mcp_servers:
                mcp_tools = await get_tools_for_request_async(
                    use_tools=True, use_web_search=False, use_mcp=True,
                    attachment_ids=None, selected_tools=None,
                    selected_mcp_servers=selected_mcp_servers,
                    user_id=self.user_id,
                    tool_tier=TOOL_TIER_EXTENDED,
                )
                extra_tools.extend(mcp_tools)
            if selected_tools:
                user_tools = await get_tools_for_request_async(
                    use_tools=True, use_web_search=False, use_mcp=False,
                    attachment_ids=None, selected_tools=selected_tools,
                    user_id=self.user_id,
                    tool_tier=TOOL_TIER_EXTENDED,
                )
                extra_tools.extend(user_tools)

        return extra_tools
