"""SkillProvider — 统一技能加载器

从 get_tools_for_request_async 中抽取 Skill 加载逻辑，
提供统一的技能工具加载入口。
"""

import logging
import os

from asgiref.sync import sync_to_async
from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)


class SkillProvider:
    """统一技能加载器

    合并预置 Skill、用户自定义 Skill、SkillPackage 的加载逻辑。
    Skill 步骤依赖的工具只允许来自用户当前可用工具集（C9）：
    缺失时不注入、不回退全局池，由执行时返回明确错误。

    SkillPackage 走三级渐进披露（Anthropic Agent Skills 规范）：
    - Level 1：元数据经 ActivateSkillTool.available_skills 进 system prompt 技能清单
    - Level 2/3：装配 activate_skill / read_skill_resource 工具按需读取
      SKILL.md body 与资源文件（用户有已启用 SkillPackage 时才装配）
    """

    @staticmethod
    async def get_skill_tools(
        user_id: int | None = None,
        available_tools: list[BaseTool] | None = None,
        selected_tools: list[str] | None = None,
    ) -> list[BaseTool]:
        """获取所有活跃技能对应的 BaseTool 列表

        Args:
            user_id: 用户 ID
            available_tools: 当前已加载的工具列表（Skill 执行时从中查找子工具）
            selected_tools: 精确选择的工具名列表，为 None 时加载全部

        Returns:
            SkillBaseTool（pipeline 管道）+ 披露工具（activate_skill /
            read_skill_resource，仅当存在可见 SkillPackage 且被选中）列表
        """
        from Django_xm.apps.tools.skills.adapter import SkillAdapter

        adapter = SkillAdapter(user_id=user_id)
        # 同步 ORM 查询必须在 sync_to_async 中执行
        all_skill_tools = await sync_to_async(adapter.to_langchain_tools, thread_sensitive=True)(
            available_tools=available_tools
        )

        selected_set: set[str] | None = None
        if selected_tools:
            selected_set = set(selected_tools)
            skill_tools = [t for t in all_skill_tools if t.name in selected_set]
        else:
            skill_tools = all_skill_tools

        # C9：只校验依赖、不注入——skill 步骤引用的工具若不在用户可用
        # 工具集内，记录警告，技能工具保留加载并在执行时返回明确错误，
        # 禁止回退 get_all_tools() 全局池（旧实现会绕过用户工具选择）
        if available_tools is not None:
            missing_map = SkillProvider._collect_missing_dependencies(skill_tools, available_tools)
            for skill_tool_name, missing in missing_map.items():
                logger.warning(
                    f"技能工具 '{skill_tool_name}' 依赖的工具未启用: {', '.join(missing)}；"
                    "执行时将返回明确错误，请提示用户在工具选择中启用"
                )

        # L2/L3：SkillPackage 渐进披露工具（激活 + 资源读取），被选中才装配
        pkg_skills = await sync_to_async(SkillProvider._collect_package_skills, thread_sensitive=True)(
            user_id, selected_set
        )
        if pkg_skills:
            from Django_xm.apps.tools.skills.tool import create_skill_disclosure_tools

            skill_tools.extend(
                create_skill_disclosure_tools(user_id=user_id, available_skills=pkg_skills)
            )
            logger.info(f"技能披露工具已装配（可激活技能 {len(pkg_skills)} 个）")

        return skill_tools

    @staticmethod
    def _collect_package_skills(user_id: int | None, selected_set: set[str] | None) -> list[dict]:
        """收集用户可见的 active SkillPackage 元数据（Level 1 数据源）

        - 用户边界与 SkillAdapter/Loader 一致：user_id 过滤 + system 来源；
        - selected_set 非 None 时仅保留被选中的包（键 skill_{name}），
          空交集返回空列表（不装配披露工具）；
        - skill_dir 缺失/不存在的包跳过（目录悬挂防护）。

        Returns:
            [{name, description, version, source}]，供 create_skill_disclosure_tools
        """
        from Django_xm.apps.tools.models import SkillPackage

        qs = SkillPackage.objects.filter(status="active")
        if user_id is not None:
            qs = qs.filter(user_id=user_id) | qs.filter(source="system")

        skills: list[dict] = []
        for pkg in qs:
            if not pkg.skill_dir or not os.path.isdir(pkg.skill_dir):
                continue
            if selected_set is not None and f"skill_{pkg.name}" not in selected_set:
                continue
            skills.append(
                {
                    "name": pkg.name,
                    "description": pkg.description or f"Skill: {pkg.name}",
                    "version": pkg.version,
                    "source": pkg.source,
                }
            )
        return skills

    @staticmethod
    def _collect_missing_dependencies(
        skill_tools: list[BaseTool],
        available_tools: list[BaseTool],
    ) -> dict[str, list[str]]:
        """校验 Skill 步骤依赖的工具是否都在当前可用工具集内（C9）

        - 可用性边界 = available_tools（用户已启用/选中的 builtin + 自定义
          + MCP 工具），禁止回退 get_all_tools() 全局池；
        - 纯函数：不修改任何入参列表（旧实现原地 append available_tools）；
        - 先建一次名字索引，消除旧实现嵌套循环内反复重建全工具池的开销。

        Returns:
            {skill 工具名: [缺失的工具名列表]}，全部满足时为空 dict
        """
        from Django_xm.apps.tools.skills.tool import SkillBaseTool

        available_names = {t.name for t in available_tools}
        missing_map: dict[str, list[str]] = {}
        for skill_tool in skill_tools:
            if not isinstance(skill_tool, SkillBaseTool):
                continue
            missing = sorted(
                {step.tool_name for step in skill_tool.spec.steps if step.tool_name not in available_names}
            )
            if missing:
                missing_map[skill_tool.name] = missing
        return missing_map
