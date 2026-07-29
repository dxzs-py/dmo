"""Skills 子系统 — 统一技能管理

基于 LangChain tool calling 机制实现渐进式披露：
- Level 1 (Discovery): name + description 注入 Agent 工具列表
- Level 2 (Activation): Agent 调用时加载完整指令/执行步骤链
- Level 3 (Execution): 按需加载 scripts/references/assets 资源

三种执行模式：
- pipeline: 自动执行步骤链
- advisor: 返回 SKILL.md 指令，Agent 自主决策
- hybrid: 先加载指令，再执行步骤链
"""

from Django_xm.apps.tools.skills.adapter import SkillAdapter
from Django_xm.apps.tools.skills.loader import SkillLoader
from Django_xm.apps.tools.skills.provider import SkillProvider
from Django_xm.apps.tools.skills.registry import (
    PRESET_SKILLS,
    SkillRegistryService,
    SkillSpec,
    SkillStep,
)
from Django_xm.apps.tools.skills.tool import (
    SkillBaseTool,
    create_skill_base_tools,
)

__all__ = [
    "PRESET_SKILLS",
    # Adapter
    "SkillAdapter",
    # Tool
    "SkillBaseTool",
    # Loader
    "SkillLoader",
    # Provider
    "SkillProvider",
    # Registry
    "SkillRegistryService",
    "SkillSpec",
    # Models
    "SkillStep",
    "create_skill_base_tools",
]
