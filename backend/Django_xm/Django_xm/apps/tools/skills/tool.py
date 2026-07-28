"""SkillBaseTool — 统一技能工具

根据 SkillSpec.mode 字段分发执行策略：
- pipeline: 自动执行步骤链（原 ToolPipelineTool）
- advisor: 返回 SKILL.md 指令内容（原 SkillTool）
- hybrid: 先加载指令注入上下文，再执行步骤链

替代原有的 ToolPipelineTool 和 SkillTool，统一入口。
"""

import json
import logging
import os
import re
import time
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from Django_xm.apps.tools.base import AsyncToolMixin
from Django_xm.apps.tools.skills.registry import SkillSpec

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 指令缓存（advisor 模式）
# ---------------------------------------------------------------------------

_instruction_cache: dict[str, tuple[float, str]] = {}
_CACHE_TTL: float = 300.0  # 5 分钟


# ---------------------------------------------------------------------------
# 输入 Schema
# ---------------------------------------------------------------------------

class SkillBaseToolInput(BaseModel):
    """SkillBaseTool 输入 schema"""
    query: str = Field(description="技能执行的初始输入参数")
    args: dict = Field(default_factory=dict, description="额外参数")
    mode: str = Field(default="pipeline", description="技能执行模式: pipeline/advisor/hybrid")


# ---------------------------------------------------------------------------
# SkillBaseTool
# ---------------------------------------------------------------------------

class SkillBaseTool(AsyncToolMixin, BaseTool):
    """统一技能工具

    根据 SkillSpec.mode 字段分发执行策略：
    - pipeline: 自动执行步骤链（原 ToolPipelineTool）
    - advisor: 返回 SKILL.md 指令内容（原 SkillTool）
    - hybrid: 先加载指令注入上下文，再执行步骤链
    """

    spec: SkillSpec = Field(exclude=True)
    available_tools: list = Field(default_factory=list, exclude=True)
    tool_map: dict = Field(default_factory=dict, exclude=True)
    args_schema: type[BaseModel] = SkillBaseToolInput

    def __init__(self, spec: SkillSpec, available_tools: list | None = None, **kwargs: Any):
        kwargs.setdefault("name", f"skill_{spec.name}")
        if spec.mode == "pipeline" and spec.steps:
            steps_desc = " → ".join(s.tool_name for s in spec.steps)
            kwargs.setdefault("description", f"{spec.description} 执行步骤: {steps_desc}")
        else:
            kwargs.setdefault("description", spec.description)
        tools = available_tools or []
        tmap = {t.name: t for t in tools if hasattr(t, 'name')}

        class _Input(SkillBaseToolInput):
            mode: str = Field(default=spec.mode, description="技能执行模式")

        kwargs.setdefault("args_schema", _Input)
        super().__init__(spec=spec, available_tools=tools, tool_map=tmap, **kwargs)

    # ------------------------------------------------------------------
    # 同步 / 异步入口
    # ------------------------------------------------------------------

    def _run(self, query: str, args: dict | None = None, mode: str | None = None) -> str:
        """同步执行技能（安全处理事件循环）"""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, self._dispatch(query, args or {}))
                try:
                    return future.result(timeout=120)
                except concurrent.futures.TimeoutError:
                    logger.error(f"技能 '{self.spec.name}' 执行超时（120秒）")
                    return f"技能 '{self.spec.name}' 执行超时（120秒）"
        return asyncio.run(self._dispatch(query, args or {}))

    async def _arun(self, query: str, args: dict | None = None, mode: str | None = None) -> str:
        """异步执行技能（直接调用，避免嵌套事件循环）"""
        return await self._dispatch(query, args or {})

    async def _dispatch(self, query: str, extra_args: dict) -> str:
        """根据 mode 分发执行"""
        mode = self.spec.mode
        if mode == "pipeline":
            return await self._execute_pipeline(query, extra_args)
        elif mode == "advisor":
            return self._execute_advisor()
        elif mode == "hybrid":
            return await self._execute_hybrid(query, extra_args)
        else:
            logger.warning(f"未知技能模式 '{mode}'，降级为 pipeline")
            return await self._execute_pipeline(query, extra_args)

    # ------------------------------------------------------------------
    # Pipeline 模式
    # ------------------------------------------------------------------

    async def _execute_pipeline(self, query: str, extra_args: dict) -> str:
        """按步骤执行技能（pipeline 模式）"""
        context: dict[str, Any] = {"query": query, **extra_args}
        prev_result: Any = None

        for idx, step in enumerate(self.spec.steps):
            if step.condition and not self._evaluate_condition(step.condition, context):
                continue

            tool = self._find_tool(step.tool_name)
            if tool is None:
                return f"技能 '{self.spec.name}' 步骤 {idx} 失败: 工具 '{step.tool_name}' 未找到"

            resolved_args = self._resolve_args(step.args_template, context, prev_result)

            try:
                tool_result = await tool.ainvoke(resolved_args)
            except Exception as e:
                logger.error(f"技能 '{self.spec.name}' 步骤 {idx} 执行失败: {e}")
                return f"技能 '{self.spec.name}' 步骤 {idx} 失败: {e}"

            if isinstance(tool_result, str):
                try:
                    prev_result = json.loads(tool_result)
                except (json.JSONDecodeError, TypeError):
                    prev_result = tool_result
            else:
                prev_result = tool_result

            context[f"step_{idx}_result"] = prev_result
            context["prev_result"] = prev_result

        return str(prev_result) if prev_result is not None else "技能执行完成，无返回结果"

    # ------------------------------------------------------------------
    # Advisor 模式
    # ------------------------------------------------------------------

    def _execute_advisor(self) -> str:
        """返回技能激活确认（advisor 模式）

        advisor 模式的 SKILL.md 指令已通过 _build_skill_instructions() 注入系统提示词，
        工具仅需返回简洁确认消息。Agent 应根据系统提示词中的技能指令自主决策，
        不再重复调用此工具。
        """
        return "技能已激活，请根据系统提示词中的技能指令处理用户请求。"

    # ------------------------------------------------------------------
    # Hybrid 模式
    # ------------------------------------------------------------------

    async def _execute_hybrid(self, query: str, extra_args: dict) -> str:
        """先执行步骤链，再返回结果（hybrid 模式）

        指令内容已在系统提示词中注入，工具仅返回步骤链执行结果。
        """
        hybrid_args = {**extra_args, "skill_instructions": self._load_skill_instructions()}
        pipeline_result = await self._execute_pipeline(query, hybrid_args)
        return pipeline_result

    # ------------------------------------------------------------------
    # 参数解析
    # ------------------------------------------------------------------

    def _resolve_args(self, template: dict, context: dict, prev_result: Any) -> dict:
        """解析参数模板，替换变量引用"""
        resolved: dict[str, Any] = {}
        full_context = {**context, "prev_result": prev_result}
        for key, value in template.items():
            if isinstance(value, str):
                resolved[key] = self._interpolate(value, full_context)
            elif isinstance(value, dict):
                resolved[key] = self._resolve_args(value, full_context, prev_result)
            else:
                resolved[key] = value
        return resolved

    def _interpolate(self, template_str: str, context: dict) -> str:
        """字符串插值：替换 {var} 和 {var.attr} 形式的占位符"""
        def replacer(match: re.Match) -> str:
            expr = match.group(1)
            try:
                parts = expr.split(".")
                value = context
                for part in parts:
                    if isinstance(value, dict):
                        value = value.get(part, "")
                    elif hasattr(value, part):
                        value = getattr(value, part)
                    else:
                        return match.group(0)
                return str(value)
            except Exception:
                return match.group(0)

        return re.sub(r"\{(\w+(?:\.\w+)*)\}", replacer, template_str)

    # ------------------------------------------------------------------
    # 安全条件求值（不使用 eval）
    # ------------------------------------------------------------------

    def _evaluate_condition(self, condition: str, context: dict) -> bool:
        """安全求值条件表达式（不使用 eval）

        支持格式:
        - variable == value
        - variable != value
        - and / or 连接多个比较（and 优先级高于 or）

        value 支持：无引号单词、双引号字符串、单引号字符串
        """
        try:
            or_groups = re.split(r'\s+or\s+', condition)
            for or_group in or_groups:
                and_parts = re.split(r'\s+and\s+', or_group)
                all_true = True
                for part in and_parts:
                    part = part.strip()
                    match = re.match(
                        r'(\w+)\s*(==|!=)\s*("([^"]*)"|\'([^\']*)\'|(\w+))',
                        part,
                    )
                    if not match:
                        logger.warning(f"条件表达式格式不合法，已跳过: {part}")
                        all_true = False
                        break

                    var_name = match.group(1)
                    op = match.group(2)
                    comp_value = match.group(4) if match.group(4) is not None else (
                        match.group(5) if match.group(5) is not None else (
                            match.group(6) if match.group(6) is not None else ''
                        )
                    )

                    var_value = context.get(var_name, '')

                    if op == '==':
                        part_result = str(var_value) == comp_value
                    else:
                        part_result = str(var_value) != comp_value

                    if not part_result:
                        all_true = False
                        break

                if all_true:
                    return True

            return False
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 工具查找
    # ------------------------------------------------------------------

    def _find_tool(self, tool_name: str) -> BaseTool | None:
        """从 tool_map 中查找工具（O(1) 字典查找）"""
        return self.tool_map.get(tool_name)

    # ------------------------------------------------------------------
    # SKILL.md 指令加载（advisor 模式，带缓存）
    # ------------------------------------------------------------------

    def _load_skill_instructions(self) -> str:
        """加载 SKILL.md 的指令内容（带 TTL 缓存）

        Returns:
            SKILL.md 的 Markdown body（指令部分），加载失败返回错误信息
        """
        if not self.spec.skill_dir:
            return f"[错误] Skill '{self.spec.name}' 未配置 skill_dir"

        cache_key = f"{self.spec.name}:{self.spec.skill_dir}"
        now = time.time()

        # 检查缓存
        if cache_key in _instruction_cache:
            cached_at, cached_body = _instruction_cache[cache_key]
            if now - cached_at < _CACHE_TTL:
                return cached_body

        # 缓存未命中，从文件加载
        skill_md_path = os.path.join(self.spec.skill_dir, 'SKILL.md')
        if not os.path.isfile(skill_md_path):
            return f"[错误] Skill '{self.spec.name}' 的 SKILL.md 文件不存在"

        try:
            with open(skill_md_path, encoding='utf-8') as f:
                content = f.read()

            body = self._extract_body(content)
            if body:
                # 写入缓存
                _instruction_cache[cache_key] = (now, body)
                return body
            return f"[警告] Skill '{self.spec.name}' 的 SKILL.md 没有指令内容"

        except Exception as e:
            logger.error(f"加载 Skill '{self.spec.name}' 指令失败: {e}")
            return f"[错误] 加载 Skill '{self.spec.name}' 指令失败: {e}"

    @staticmethod
    def _extract_body(content: str) -> str:
        """从 SKILL.md 内容中提取 body（指令部分，不含 frontmatter）"""
        pattern = re.compile(r'^---\s*\n.*?\n---\s*\n?(.*)', re.DOTALL)
        match = pattern.match(content)
        if match:
            return match.group(1).strip()
        return content.strip()

    # ------------------------------------------------------------------
    # Level 3 资源加载
    # ------------------------------------------------------------------

    def _load_resource(self, resource_path: str) -> str | None:
        """按需加载 Skill 资源文件（Level 3 Execution）

        Args:
            resource_path: 相对于 Skill 根目录的文件路径

        Returns:
            文件内容，未找到返回 None
        """
        if not self.spec.skill_dir:
            return None
        from Django_xm.apps.tools.skills.loader import SkillLoader
        loader = SkillLoader()
        return loader.load_resource(self.spec.name, resource_path)


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------

def create_skill_base_tools(
    tools: list[BaseTool],
    user_id: int | None = None,
) -> list[BaseTool]:
    """从 SkillRegistryService 获取所有活跃技能，创建 SkillBaseTool 实例列表

    Args:
        tools: Agent 可用的工具列表，SkillBaseTool 执行时从中查找子工具
        user_id: 用户 ID，为 None 时仅加载预置技能

    Returns:
        SkillBaseTool 实例列表
    """
    from Django_xm.apps.tools.skills.registry import SkillRegistryService

    skill_tools: list[BaseTool] = []
    for spec in SkillRegistryService.get_skills(user_id):
        skill_tools.append(SkillBaseTool(spec=spec, available_tools=tools))
    return skill_tools
