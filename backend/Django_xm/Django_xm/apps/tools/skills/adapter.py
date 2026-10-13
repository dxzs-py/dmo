"""SkillAdapter — 三框架统一适配层

将 SkillSpec 统一转换为 LangChain / LangGraph / DeepAgent 所需格式。
"""

import os

from langchain_core.tools import BaseTool


class SkillAdapter:
    """三框架统一适配层

    提供四个标准接口，将 SkillSpec 转换为各框架所需的格式：
    - to_langchain_tools() → List[BaseTool]  (LangChain Agent，pipeline 管道工具)
    - to_langgraph_tools() → List[BaseTool]  (LangGraph ToolNode)
    - to_deep_agent_skills() → List[str]     (DeepAgent skills 参数)
    - to_discovery_metadata() → List[Dict]   (Level 1 元数据，供 API 展示)

    SkillPackage 不再生成工具（旧 advisor 全量注入模式已删除）：
    其 Level 2/3 披露由 SkillProvider 装配的 activate_skill /
    read_skill_resource 工具承担。
    """

    def __init__(self, user_id: int | None = None):
        self.user_id = user_id

    def to_langchain_tools(
        self,
        available_tools: list[BaseTool] | None = None,
    ) -> list[BaseTool]:
        """转换为 LangChain Agent 可用的 BaseTool 列表（仅 pipeline 管道技能）

        Args:
            available_tools: Agent 可用的工具列表，SkillBaseTool 执行时从中查找子工具

        Returns:
            SkillBaseTool 实例列表（预置技能 + 用户 SkillConfig）
        """
        from Django_xm.apps.tools.skills.registry import SkillRegistryService
        from Django_xm.apps.tools.skills.tool import SkillBaseTool

        skills = SkillRegistryService.get_skills(self.user_id)
        tools: list[BaseTool] = []
        for spec in skills:
            tools.append(SkillBaseTool(spec=spec, available_tools=available_tools or []))

        return tools

    def to_langgraph_tools(
        self,
        available_tools: list[BaseTool] | None = None,
    ) -> list[BaseTool]:
        """转换为 LangGraph ToolNode 兼容的 BaseTool 列表

        LangGraph ToolNode 与 LangChain BaseTool 完全兼容，
        因此直接调用 to_langchain_tools()。

        Args:
            available_tools: Agent 可用的工具列表

        Returns:
            BaseTool 列表
        """
        return self.to_langchain_tools(available_tools)

    def to_deep_agent_skills(self, selected_skill_names: list[str] | None = None) -> list[str]:
        from Django_xm.apps.tools.models import SkillPackage

        if not selected_skill_names:
            return []

        qs = SkillPackage.objects.filter(status="active", name__in=selected_skill_names)
        if self.user_id is not None:
            qs = qs.filter(user_id=self.user_id) | qs.filter(source="system")

        skill_dirs = []
        for pkg in qs:
            if pkg.skill_dir and os.path.isdir(pkg.skill_dir):
                skill_dirs.append(pkg.skill_dir)

        return skill_dirs
