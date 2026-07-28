"""SkillRegistryService — 基于 Django ORM 的技能注册服务

统一管理预置技能（内存 SkillSpec）和用户自定义技能（SkillConfig ORM），
替代原有的 ToolPipelineRegistry 内存单例。

设计决策：
- 预置技能保持为 Python 定义的 SkillSpec 对象（内存），无需系统用户
- 用户技能从 SkillConfig ORM 查询
- get_skills() 合并两者返回
"""

import logging

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------------------

class SkillStep(BaseModel):
    """技能步骤定义"""
    tool_name: str = Field(description="工具名称")
    args_template: dict = Field(default_factory=dict, description="参数模板，支持 {prev_result} 引用上一步结果")
    condition: str | None = Field(default=None, description="执行条件表达式")
    resource_path: str | None = Field(default=None, description="Level 3 资源文件路径（相对于 Skill 根目录）")


class SkillSpec(BaseModel):
    """统一技能定义模型"""
    name: str = Field(description="技能名称")
    description: str = Field(description="技能描述")
    mode: str = Field(default="pipeline", description="执行模式: pipeline/advisor/hybrid")
    steps: list[SkillStep] = Field(default_factory=list, description="执行步骤（pipeline/hybrid 模式）")
    skill_dir: str | None = Field(default=None, description="Skill 包文件系统路径（advisor 模式）")
    allowed_tools: str | None = Field(default=None, description="预批准工具列表")
    version: str = Field(default="1.0.0")
    source: str = Field(default="user", description="来源: system/user")


# ---------------------------------------------------------------------------
# 预置技能定义
# ---------------------------------------------------------------------------

PRESET_SKILLS: list[dict] = [
    {
        "name": "web_research",
        "description": "网络研究技能：搜索关键词 → 抓取相关网页 → 总结关键信息。适用场景：需要深入了解某个话题。",
        "mode": "pipeline",
        "steps": [
            {"tool_name": "web_search", "args_template": {"query": "{query}"}},
            {"tool_name": "web_fetch", "args_template": {"url": "{prev_result.top_url}"}},
        ],
    },
    {
        "name": "document_qa",
        "description": "文档问答技能：读取附件内容 → 基于内容回答问题。适用场景：基于上传文档回答问题。",
        "mode": "pipeline",
        "steps": [
            {"tool_name": "attachment_reader", "args_template": {"query": "{query}"}},
        ],
    },
]


def _build_preset_specs() -> list[SkillSpec]:
    """将 PRESET_SKILLS 字典列表转为 SkillSpec 对象列表"""
    specs: list[SkillSpec] = []
    for preset in PRESET_SKILLS:
        steps = [
            SkillStep(**step_data) for step_data in preset.get("steps", [])
        ]
        specs.append(SkillSpec(
            name=preset["name"],
            description=preset["description"],
            mode=preset.get("mode", "pipeline"),
            steps=steps,
            version="1.0.0",
            source="system",
        ))
    return specs


# ---------------------------------------------------------------------------
# SkillRegistryService
# ---------------------------------------------------------------------------

class SkillRegistryService:
    """基于 Django ORM 的技能注册服务

    - 预置技能：类级别缓存的 SkillSpec 列表（内存）
    - 用户技能：从 SkillConfig ORM 查询
    - get_skills() 合并两者
    """

    _preset_specs: list[SkillSpec] | None = None

    # ---- 预置技能 ----

    @classmethod
    def _ensure_presets(cls) -> list[SkillSpec]:
        """懒加载预置技能定义（幂等）"""
        if cls._preset_specs is None:
            cls._preset_specs = _build_preset_specs()
        return cls._preset_specs

    @classmethod
    def get_presets(cls) -> list[SkillSpec]:
        """返回所有预置技能定义"""
        return list(cls._ensure_presets())

    @classmethod
    def register_preset(
        cls,
        name: str,
        description: str,
        mode: str = "pipeline",
        steps: list[dict] | None = None,
    ) -> SkillSpec:
        """注册预置技能（幂等，同名则更新）

        Args:
            name: 技能名称
            description: 技能描述
            mode: 执行模式
            steps: 步骤字典列表

        Returns:
            注册的 SkillSpec
        """
        presets = cls._ensure_presets()
        skill_steps = [SkillStep(**s) for s in (steps or [])]
        spec = SkillSpec(
            name=name,
            description=description,
            mode=mode,
            steps=skill_steps,
            version="1.0.0",
            source="system",
        )
        # 同名替换
        for i, existing in enumerate(presets):
            if existing.name == name:
                presets[i] = spec
                return spec
        presets.append(spec)
        return spec

    # ---- 查询 ----

    @classmethod
    def get_skills(cls, user_id: int | None = None) -> list[SkillSpec]:
        """返回活跃技能列表（预置 + 用户自定义）

        Args:
            user_id: 用户 ID，为 None 时仅返回预置技能

        Returns:
            SkillSpec 列表
        """
        skills = cls.get_presets()

        if user_id is not None:
            try:
                from Django_xm.apps.tools.models import SkillConfig
                qs = SkillConfig.objects.filter(status='active', user_id=user_id)
                for config in qs:
                    try:
                        skills.append(config.to_skill_definition())
                    except Exception as e:
                        logger.warning(f"加载用户技能 '{config.name}' 失败: {e}")
            except Exception as e:
                logger.warning(f"查询用户技能失败: {e}")

        return skills

    @classmethod
    def search(cls, query: str, user_id: int | None = None) -> list[SkillSpec]:
        """按名称/描述搜索技能

        Args:
            query: 搜索关键词
            user_id: 用户 ID

        Returns:
            匹配的 SkillSpec 列表
        """
        query_lower = query.lower()
        all_skills = cls.get_skills(user_id)
        return [
            skill for skill in all_skills
            if query_lower in skill.name.lower() or query_lower in skill.description.lower()
        ]

    @classmethod
    def unregister(cls, name: str, user_id: int) -> bool:
        """移除用户自定义技能

        预置技能不可通过此方法移除。

        Args:
            name: 技能名称
            user_id: 用户 ID

        Returns:
            是否成功移除
        """
        try:
            from Django_xm.apps.tools.models import SkillConfig
            deleted, _ = SkillConfig.objects.filter(
                name=name, user_id=user_id
            ).delete()
            return deleted > 0
        except Exception as e:
            logger.warning(f"移除用户技能 '{name}' 失败: {e}")
            return False

    @classmethod
    def get(cls, name: str, user_id: int | None = None) -> SkillSpec | None:
        """按名称获取技能

        优先从预置技能查找，再从用户技能查找。

        Args:
            name: 技能名称
            user_id: 用户 ID

        Returns:
            SkillSpec 或 None
        """
        # 先查预置
        for preset in cls.get_presets():
            if preset.name == name:
                return preset

        # 再查用户
        if user_id is not None:
            try:
                from Django_xm.apps.tools.models import SkillConfig
                config = SkillConfig.objects.filter(
                    name=name, user_id=user_id, status='active'
                ).first()
                if config:
                    return config.to_skill_definition()
            except Exception as e:
                logger.warning(f"查询技能 '{name}' 失败: {e}")

        return None
