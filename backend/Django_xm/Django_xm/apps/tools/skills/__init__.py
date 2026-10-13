"""Skills 子系统 — 统一技能管理

基于 Anthropic Agent Skills 规范实现三级渐进式披露：
- Level 1 (Discovery): name + description 元数据进 system prompt 技能清单
- Level 2 (Activation): 模型经 activate_skill 工具按需读取 SKILL.md 指令
- Level 3 (Execution): 模型经 read_skill_resource 工具按需加载
  references/assets/scripts 资源；脚本执行走既有 shell 工具与沙箱

执行工具：
- SkillBaseTool: SkillConfig 工具管道（pipeline 步骤链），调用即执行
- ActivateSkillTool / ReadSkillResourceTool: SkillPackage 的 L2/L3 披露工具
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
    ActivateSkillTool,
    ReadSkillResourceTool,
    SkillBaseTool,
    create_skill_base_tools,
    create_skill_disclosure_tools,
    wrap_skill_instructions,
    wrap_skill_resource,
)

__all__ = [
    "PRESET_SKILLS",
    # Disclosure tools
    "ActivateSkillTool",
    "ReadSkillResourceTool",
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
    "create_skill_disclosure_tools",
    "wrap_skill_instructions",
    "wrap_skill_resource",
]
