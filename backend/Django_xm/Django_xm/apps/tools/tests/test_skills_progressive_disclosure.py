"""Task 10：Skills 三级渐进披露验收测试。

覆盖（Anthropic Agent Skills 规范基线）：
- Level 1：agent 工具集构建后，技能清单（system prompt 注入段）仅含
  name + description 元数据，不含 SKILL.md body 内容（特征词断言）；
- Level 2：activate_skill 工具返回 SKILL.md body（信封包裹）、他人技能
  不可见（404 语义）、路径形名称不产生攻击面、更新后实时读出新内容、
  超长 body 截断并附截断标记（fix-review-round2 4.5）；
- Level 3：read_skill_resource 正常读取 references/assets（二进制返回
  base64）、路径穿越与绝对路径拒绝、超长资源截断（4.5）；
- 装配：仅当用户已启用（且被选中）SkillPackage 时装配披露工具，
  用户隔离与 SkillLoader 既有逻辑一致；
- REST 端点（views_mcp.SkillPackageDetailView.get → loader.activate_skill）
  的回归由 test_views_rest.py 保障，本文件不动。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.tools.tests.test_skills_progressive_disclosure --noinput
"""

import asyncio
import os
import shutil
import tempfile
import unittest
import zipfile

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TransactionTestCase
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from Django_xm.async_utils import run_async
from Django_xm.apps.agent_hub.builders._common import _build_skill_metadata_section
from Django_xm.apps.tools.skills.loader import SkillLoader
from Django_xm.apps.tools.skills.provider import SkillProvider
from Django_xm.apps.tools.skills.registry import SkillSpec, SkillStep
from Django_xm.apps.tools.skills.tool import (
    SKILL_INSTRUCTIONS_END,
    SKILL_INSTRUCTIONS_START,
    SKILL_OUTPUT_MAX_CHARS,
    ActivateSkillTool,
    ReadSkillResourceTool,
    SkillBaseTool,
    _truncate_skill_output,
    create_skill_disclosure_tools,
    wrap_skill_instructions,
)

# SKILL.md body 特征词：若出现在 system prompt 注入段即视为全量注入回归
BODY_MARKER_V1 = "SECRET_BODY_MARKER_V1_XYZ"
BODY_MARKER_V2 = "SECRET_BODY_MARKER_V2_XYZ"
REF_MARKER = "REFERENCE_DOC_MARKER_ABC"

SKILL_NAME = "disclosure-skill"


# ---------------------------------------------------------------------------
# 测试夹具
# ---------------------------------------------------------------------------


class _ProbeInput(BaseModel):
    query: str = Field(default="")


class _PlainTool(BaseTool):
    """非技能工具（用于断言元数据段忽略普通工具）"""

    name: str = "plain_probe"
    description: str = "普通工具"
    args_schema: type[BaseModel] = _ProbeInput

    def _run(self, query: str = "") -> str:
        return "ok"


def _make_pipeline_skill(spec_name: str) -> SkillBaseTool:
    spec = SkillSpec(
        name=spec_name,
        description="管道测试技能：执行步骤链",
        steps=[SkillStep(tool_name="plain_probe", args_template={"query": "test"})],
    )
    return SkillBaseTool(spec=spec, available_tools=[])


def _make_zip(zip_path: str, name: str, body: str, with_resources: bool = False) -> str:
    """构造符合规范的技能包 zip（含 SKILL.md 与可选资源）"""
    skill_md = f"---\nname: {name}\ndescription: 渐进披露测试技能包\n---\n\n{body}\n"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(f"{name}/SKILL.md", skill_md)
        if with_resources:
            zf.writestr(f"{name}/references/api.md", f"# API 参考\n{REF_MARKER}\n")
            zf.writestr(f"{name}/scripts/extract.py", "print('skill script')\n")
            zf.writestr(f"{name}/assets/blob.bin", b"\x89PNG\r\n\x1a\n\xff\xfe")  # 非 UTF-8 二进制
    return zip_path


class _SkillPackageFixtureMixin:
    """安装真实技能包到临时目录 + 创建用户

    注：DB 测试类继承 TransactionTestCase 而非 TestCase——被测路径
    （AsyncToolMixin 的 asyncio.to_thread、asgiref sync_to_async 的
    专用执行器）在线程池中执行 ORM 查询，TestCase 事务对非主线程
    连接不可见；TransactionTestCase 的 autocommit 与生产语义一致。
    """

    def _setup_users(self):
        User = get_user_model()
        self.user1 = User.objects.create_user(username="disclosure_u1", password="x", email="d_u1@example.com")
        self.user2 = User.objects.create_user(username="disclosure_u2", password="x", email="d_u2@example.com")

    def _setup_loader(self):
        self.tmp_base = tempfile.mkdtemp(prefix="skills_disclosure_")
        self.addCleanup(shutil.rmtree, self.tmp_base, ignore_errors=True)
        self.loader = SkillLoader(skills_base_dir=self.tmp_base)

    def _install(self, user, name: str = SKILL_NAME, body: str = BODY_MARKER_V1, with_resources: bool = False):
        from Django_xm.apps.tools.models import ToolCategory

        ToolCategory.objects.get_or_create(code="general", defaults={"name": "通用"})
        self._zip_seq = getattr(self, "_zip_seq", 0) + 1
        zip_path = os.path.join(self.tmp_base, f"{name}-{self._zip_seq}.zip")
        _make_zip(zip_path, name, body, with_resources=with_resources)
        self.addCleanup(lambda p=zip_path: os.path.exists(p) and os.unlink(p))
        ok, msg, _ = self.loader.install_skill_package(zip_path, user)
        self.assertTrue(ok, msg)

    def _make_activate_tool(self, user_id, available_skills=None):
        return ActivateSkillTool(user_id=user_id, available_skills=available_skills or [], skills_loader=self.loader)

    def _make_resource_tool(self, user_id):
        return ReadSkillResourceTool(user_id=user_id, skills_loader=self.loader)


# ---------------------------------------------------------------------------
# Level 1：system prompt 仅含元数据清单
# ---------------------------------------------------------------------------


class SkillMetadataSectionUnitTests(SimpleTestCase):
    """_build_skill_metadata_section 纯函数行为。"""

    def test_mixed_tools_produce_compact_listing(self):
        pipeline_tool = _make_pipeline_skill("pipe-skill")
        activate_tool = ActivateSkillTool(
            user_id=1,
            available_skills=[{"name": "pkg-skill", "description": "技能包技能", "version": "1.0.0", "source": "user"}],
        )

        section = _build_skill_metadata_section([_PlainTool(), pipeline_tool, activate_tool])

        self.assertIsNotNone(section)
        self.assertIn("pipe-skill", section)
        self.assertIn("skill_pipe-skill", section)  # pipeline 技能指向其执行工具
        self.assertIn("pkg-skill", section)
        self.assertIn("activate_skill", section)
        # 不含信封/body 标记（元数据段永远不含指令内容）
        self.assertNotIn(SKILL_INSTRUCTIONS_START.format(name="pkg-skill"), section)
        self.assertNotIn(SKILL_INSTRUCTIONS_END, section)

    def test_no_skill_tools_returns_none(self):
        self.assertIsNone(_build_skill_metadata_section(None))
        self.assertIsNone(_build_skill_metadata_section([]))
        self.assertIsNone(_build_skill_metadata_section([_PlainTool()]))


class SkillOutputTruncationUnitTests(SimpleTestCase):
    """4.5：技能工具输出上限截断（helper 与 ReadSkillResourceTool 单元行为）。"""

    def test_short_content_untouched(self):
        self.assertEqual(_truncate_skill_output("abc", "tail-note"), "abc")

    def test_oversized_content_truncated_with_marker(self):
        original_len = SKILL_OUTPUT_MAX_CHARS + 500
        result = _truncate_skill_output("x" * original_len, "tail-note")

        self.assertIn("技能内容过长已截断", result)
        self.assertIn(str(original_len), result)  # 原始长度
        self.assertIn("tail-note", result)  # 按调用方语境定制的尾注
        self.assertTrue(result.startswith("x" * 100))  # 头部保留
        # 截断后总长受控（截断体 + 标记，不含未截断的原始超限内容）
        self.assertLess(len(result), SKILL_OUTPUT_MAX_CHARS + 1000)

    def test_resource_tool_truncates_oversized_content(self):
        class _StubLoader:
            @staticmethod
            def load_resource(name, resource_path, user_id=None):
                return "y" * (SKILL_OUTPUT_MAX_CHARS + 10)

        tool = ReadSkillResourceTool(user_id=1, skills_loader=_StubLoader())
        result = tool._run(name="pkg", resource_path="references/big.md")

        # 信封完整（开始/结束标记均在），截断标记与分段引导尾注在内
        self.assertIn("技能资源开始", result)
        self.assertIn("[技能资源结束]", result)
        self.assertIn("技能内容过长已截断", result)
        self.assertIn("read_skill_resource", result)
        self.assertLess(len(result), SKILL_OUTPUT_MAX_CHARS + 2000)


class Level1MetadataIntegrationTests(TransactionTestCase, _SkillPackageFixtureMixin):
    """集成：get_skill_tools → _build_skill_metadata_section，body 不进 prompt。"""

    def setUp(self):
        self._setup_users()
        self._setup_loader()
        self._install(self.user1, with_resources=True)

    def test_prompt_section_contains_metadata_not_body(self):
        tools = run_async(
            SkillProvider.get_skill_tools(
                user_id=self.user1.id,
                available_tools=[],
                selected_tools=[f"skill_{SKILL_NAME}"],
            )
        )
        section = _build_skill_metadata_section(tools)

        self.assertIsNotNone(section)
        self.assertIn(SKILL_NAME, section)
        self.assertIn("渐进披露测试技能包", section)
        # 核心断言：SKILL.md body 特征词不得出现在 prompt 注入段
        self.assertNotIn(BODY_MARKER_V1, section)

    def test_pipeline_skill_listed_with_execution_hint(self):
        tools = [_make_pipeline_skill("pipe-skill")]
        section = _build_skill_metadata_section(tools)
        self.assertIn("pipe-skill", section)
        self.assertIn("skill_pipe-skill", section)
        self.assertNotIn(BODY_MARKER_V1, section)


# ---------------------------------------------------------------------------
# Level 2：activate_skill 工具
# ---------------------------------------------------------------------------


class ActivateSkillToolTests(TransactionTestCase, _SkillPackageFixtureMixin):
    def setUp(self):
        self._setup_users()
        self._setup_loader()

    def test_activate_returns_wrapped_body(self):
        self._install(self.user1)
        tool = self._make_activate_tool(self.user1.id)

        result = asyncio.run(tool._arun(name=SKILL_NAME))

        self.assertIn(BODY_MARKER_V1, result)
        self.assertIn(SKILL_INSTRUCTIONS_START.format(name=SKILL_NAME), result)
        self.assertIn("以下是技能", result)
        self.assertIn(SKILL_INSTRUCTIONS_END, result)

    def test_activate_other_user_skill_not_found(self):
        """用户隔离：user2 无法激活 user1 的技能包。"""
        self._install(self.user1)
        tool = self._make_activate_tool(self.user2.id)

        result = asyncio.run(tool._arun(name=SKILL_NAME))

        self.assertIn("未找到技能", result)
        self.assertNotIn(BODY_MARKER_V1, result)

    def test_activate_rejects_path_like_name(self):
        """name 含路径片段时仅是查不到 DB 记录，不产生路径攻击面。"""
        self._install(self.user1)
        tool = self._make_activate_tool(self.user1.id)

        for evil_name in ("../etc/passwd", "..\\..\\win", "a/b", ".", ""):
            result = asyncio.run(tool._arun(name=evil_name))
            self.assertIn("未找到技能", result)

    def test_activate_reads_fresh_content_after_update(self):
        """更新技能包后激活实时读出新内容（替代旧指令缓存失效语义）。"""
        self._install(self.user1, body=BODY_MARKER_V1)
        tool = self._make_activate_tool(self.user1.id)
        self.assertIn(BODY_MARKER_V1, asyncio.run(tool._arun(name=SKILL_NAME)))

        self._install(self.user1, body=BODY_MARKER_V2)
        result = asyncio.run(tool._arun(name=SKILL_NAME))
        self.assertIn(BODY_MARKER_V2, result)
        self.assertNotIn(BODY_MARKER_V1, result)

    def test_activate_truncates_oversized_body(self):
        """4.5：超长 SKILL.md body 截断并附标记，信封结束标记完整。"""
        big_body = BODY_MARKER_V1 + "长" * (SKILL_OUTPUT_MAX_CHARS + 1000)
        self._install(self.user1, body=big_body)
        tool = self._make_activate_tool(self.user1.id)

        result = asyncio.run(tool._arun(name=SKILL_NAME))

        self.assertIn(BODY_MARKER_V1, result)  # 头部（含特征词）保留
        self.assertIn("技能内容过长已截断", result)
        self.assertIn("SKILL.md", result)  # 指令语境尾注
        self.assertIn(SKILL_INSTRUCTIONS_END, result)  # 信封结束标记完整
        self.assertLess(len(result), SKILL_OUTPUT_MAX_CHARS + 2000)


# ---------------------------------------------------------------------------
# Level 3：read_skill_resource 工具
# ---------------------------------------------------------------------------


class ReadSkillResourceToolTests(TransactionTestCase, _SkillPackageFixtureMixin):
    def setUp(self):
        self._setup_users()
        self._setup_loader()
        self._install(self.user1, with_resources=True)
        self.tool = self._make_resource_tool(self.user1.id)

    def test_read_reference_ok(self):
        result = asyncio.run(self.tool._arun(name=SKILL_NAME, resource_path="references/api.md"))
        self.assertIn(REF_MARKER, result)
        self.assertIn("技能资源开始", result)
        # 信封标注来源技能与资源路径
        self.assertIn(SKILL_NAME, result)
        self.assertIn("references/api.md", result)

    def test_read_binary_returns_base64(self):
        import base64

        result = asyncio.run(self.tool._arun(name=SKILL_NAME, resource_path="assets/blob.bin"))
        expected = base64.b64encode(b"\x89PNG\r\n\x1a\n\xff\xfe").decode("ascii")
        self.assertIn(expected, result)
        self.assertIn("base64", result)

    def test_traversal_rejected(self):
        result = asyncio.run(self.tool._arun(name=SKILL_NAME, resource_path="../../escape.md"))
        self.assertIn("不存在或路径非法", result)
        self.assertNotIn("TOP_SECRET", result)

    def test_absolute_path_rejected(self):
        evil = os.path.join(self.tmp_base, "outside.md")
        with open(evil, "w", encoding="utf-8") as f:
            f.write("TOP_SECRET_OUTSIDE")
        self.addCleanup(os.unlink, evil)

        result = asyncio.run(self.tool._arun(name=SKILL_NAME, resource_path=evil))
        self.assertIn("不存在或路径非法", result)
        self.assertNotIn("TOP_SECRET_OUTSIDE", result)

    def test_other_user_resource_not_found(self):
        tool = self._make_resource_tool(self.user2.id)
        result = asyncio.run(tool._arun(name=SKILL_NAME, resource_path="references/api.md"))
        self.assertIn("不存在或路径非法", result)
        self.assertNotIn(REF_MARKER, result)


# ---------------------------------------------------------------------------
# 装配策略：被选中才装配，用户隔离
# ---------------------------------------------------------------------------


class DisclosureAssemblyTests(TransactionTestCase, _SkillPackageFixtureMixin):
    def setUp(self):
        self._setup_users()
        self._setup_loader()

    def test_selected_package_assembles_disclosure_tools(self):
        self._install(self.user1)

        tools = run_async(
            SkillProvider.get_skill_tools(
                user_id=self.user1.id,
                available_tools=[],
                selected_tools=[f"skill_{SKILL_NAME}"],
            )
        )
        names = {t.name for t in tools}

        self.assertIn("activate_skill", names)
        self.assertIn("read_skill_resource", names)
        # advisor 模式工具已删除：不再出现 skill_{name} 可执行工具
        self.assertNotIn(f"skill_{SKILL_NAME}", names)
        activate_tool = next(t for t in tools if t.name == "activate_skill")
        self.assertEqual([s["name"] for s in activate_tool.available_skills], [SKILL_NAME])

    def test_unselected_package_not_assembled(self):
        self._install(self.user1)

        tools = run_async(
            SkillProvider.get_skill_tools(
                user_id=self.user1.id,
                available_tools=[],
                selected_tools=["calculator"],
            )
        )
        names = {t.name for t in tools}
        self.assertNotIn("activate_skill", names)
        self.assertNotIn("read_skill_resource", names)

    def test_other_user_package_not_assembled(self):
        self._install(self.user1)

        tools = run_async(
            SkillProvider.get_skill_tools(
                user_id=self.user2.id,
                available_tools=[],
                selected_tools=[f"skill_{SKILL_NAME}"],
            )
        )
        names = {t.name for t in tools}
        self.assertNotIn("activate_skill", names)
        self.assertNotIn("read_skill_resource", names)

    def test_no_package_no_tools(self):
        tools = run_async(
            SkillProvider.get_skill_tools(
                user_id=self.user1.id,
                available_tools=[],
                selected_tools=[f"skill_{SKILL_NAME}"],
            )
        )
        names = {t.name for t in tools}
        self.assertNotIn("activate_skill", names)
        self.assertNotIn("read_skill_resource", names)

    def test_create_disclosure_tools_schema(self):
        """工厂函数产出两个工具，schema 参数符合约定（名字避开 skill_ 前缀）。"""
        tools = create_skill_disclosure_tools(user_id=7, available_skills=[{"name": "x", "description": "y"}])
        self.assertEqual([t.name for t in tools], ["activate_skill", "read_skill_resource"])
        activate = tools[0]
        schema_names = set(activate.args_schema.model_fields.keys())
        self.assertEqual(schema_names, {"name"})
        resource = tools[1]
        self.assertEqual(set(resource.args_schema.model_fields.keys()), {"name", "resource_path"})
        # 指令/资源内容不直接补发聊天流
        self.assertFalse(activate.metadata.get("output_to_chat", True))

    def test_wrap_skill_instructions_envelope(self):
        wrapped = wrap_skill_instructions("demo", "BODY")
        self.assertIn(SKILL_INSTRUCTIONS_START.format(name="demo"), wrapped)
        self.assertIn("BODY", wrapped)
        self.assertTrue(wrapped.endswith(SKILL_INSTRUCTIONS_END))


if __name__ == "__main__":
    unittest.main()
