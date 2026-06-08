"""SkillProvider — 统一技能加载器

从 get_tools_for_request_async 中抽取 Skill 加载逻辑，
提供统一的技能工具加载入口。
"""

import logging
from asgiref.sync import sync_to_async
from typing import Optional, List

from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)


class SkillProvider:
    """统一技能加载器

    合并预置 Skill、用户自定义 Skill、SkillPackage 的加载逻辑，
    提供依赖工具自动注入。
    """

    @staticmethod
    async def get_skill_tools(
        user_id: Optional[int] = None,
        available_tools: Optional[List[BaseTool]] = None,
        selected_tools: Optional[List[str]] = None,
    ) -> List[BaseTool]:
        """获取所有活跃技能对应的 BaseTool 列表

        Args:
            user_id: 用户 ID
            available_tools: 当前已加载的工具列表（Skill 执行时从中查找子工具）
            selected_tools: 精确选择的工具名列表，为 None 时加载全部

        Returns:
            SkillBaseTool 实例列表
        """
        from Django_xm.apps.tools.skills.adapter import SkillAdapter

        adapter = SkillAdapter(user_id=user_id)
        # 同步 ORM 查询必须在 sync_to_async 中执行
        all_skill_tools = await sync_to_async(adapter.to_langchain_tools, thread_sensitive=True)(available_tools=available_tools)

        if selected_tools:
            selected_set = set(selected_tools)
            skill_tools = [t for t in all_skill_tools if t.name in selected_set]
        else:
            skill_tools = all_skill_tools

        # 自动注入 Skill 引用的子工具
        if available_tools is not None:
            SkillProvider._inject_dependencies(skill_tools, available_tools)

        return skill_tools

    @staticmethod
    def _inject_dependencies(
        skill_tools: List[BaseTool],
        available_tools: List[BaseTool],
    ) -> None:
        """自动注入 Skill 步骤引用的子工具

        当 Skill 的步骤引用了不在 available_tools 中的工具时，
        从全局工具池查找并注入。
        """
        from Django_xm.apps.tools.skills.tool import SkillBaseTool

        existing_names = {t.name for t in available_tools}
        for skill_tool in skill_tools:
            if not isinstance(skill_tool, SkillBaseTool):
                continue
            for step in skill_tool.spec.steps:
                if step.tool_name not in existing_names:
                    # 从全局工具池查找
                    from Django_xm.apps.tools import get_all_tools
                    for t in get_all_tools():
                        if t.name == step.tool_name:
                            available_tools.append(t)
                            existing_names.add(t.name)
                            break
