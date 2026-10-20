"""Task 9（Skill 管道修复四项）回归测试。

覆盖：
- C7：allowed_tools 死字段已删除（SkillSpec / SkillPackage 均无此字段）；
- C8：SkillBaseTool._run 在事件循环内同步调用时，超时立即返回错误结果，
  不阻塞至后台任务真正完成；
- C9：Skill 依赖只在用户可用工具集内校验——缺失不注入全局池、不改动
  available_tools 入参，执行时返回明确的"需要哪些工具"错误；
- C10：安装失败无悬挂 DB 记录/目录（更新失败恢复旧目录与旧记录）。

注：Task 10 删除全量注入后指令缓存（_instruction_cache）已移除，
对应的缓存失效测试迁移至 test_skills_progressive_disclosure.py
（激活工具实时读盘语义）。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.tools.tests.test_skills_pipeline_fixes --noinput
"""

import asyncio
import os
import shutil
import tempfile
import time
import unittest
import zipfile
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from Django_xm.apps.tools.skills.loader import SkillLoader
from Django_xm.apps.tools.skills.provider import SkillProvider
from Django_xm.apps.tools.skills.registry import SkillSpec, SkillStep
from Django_xm.apps.tools.skills.tool import SkillBaseTool
from Django_xm.async_utils import run_async

_TOOL_MODULE = "Django_xm.apps.tools.skills.tool"
_GLOBAL_POOL = "Django_xm.apps.tools.get_all_tools"
_PROVIDER_MODULE = "Django_xm.apps.tools.skills.provider"


# ---------------------------------------------------------------------------
# 测试用探测工具
# ---------------------------------------------------------------------------


class _ProbeInput(BaseModel):
    query: str = Field(default="")


class _InstantTool(BaseTool):
    """即时返回的探测工具"""

    name: str = "instant"
    description: str = "即时返回"
    args_schema: type[BaseModel] = _ProbeInput

    def _run(self, query: str = "") -> str:
        return "ok"

    async def _arun(self, query: str = "") -> str:
        return "ok"


class _SleepTool(BaseTool):
    """可控时长的慢工具（C8 超时测试用）"""

    name: str = "slow_probe"
    description: str = "慢工具"
    delay: float = 0.0
    args_schema: type[BaseModel] = _ProbeInput

    def _run(self, query: str = "") -> str:
        time.sleep(self.delay)
        return "done"

    async def _arun(self, query: str = "") -> str:
        await asyncio.sleep(self.delay)
        return "done"


def _make_pipeline_skill(spec_name: str, tool_names: list[str]) -> SkillBaseTool:
    """构造 pipeline 模式 SkillBaseTool（available_tools 置空，由调用方按场景装配）"""
    spec = SkillSpec(
        name=spec_name,
        description="测试技能",
        steps=[SkillStep(tool_name=n, args_template={"query": "test"}) for n in tool_names],
    )
    return SkillBaseTool(spec=spec, available_tools=[])


# ---------------------------------------------------------------------------
# C7：allowed_tools 死字段删除
# ---------------------------------------------------------------------------


class AllowedToolsFieldRemovedTests(SimpleTestCase):
    """结构性守卫：allowed_tools 字段已从 SkillSpec 与 SkillPackage 删除。"""

    def test_skill_spec_has_no_allowed_tools_field(self):
        self.assertNotIn("allowed_tools", SkillSpec.model_fields)

    def test_skill_package_model_has_no_allowed_tools_field(self):
        from Django_xm.apps.tools.models import SkillPackage

        field_names = {f.name for f in SkillPackage._meta.get_fields()}
        self.assertNotIn("allowed_tools", field_names)


# ---------------------------------------------------------------------------
# C8：_run 超时非阻塞真生效
# ---------------------------------------------------------------------------


class SkillRunTimeoutTests(SimpleTestCase):
    """事件循环内同步调用 _run 的超时行为。

    R3 1.2：管道步骤现经审批门禁（step_gate），本组只测超时语义——
    统一打桩门禁为全放行（审批语义由 test_skill_step_approval 覆盖）。
    """

    @staticmethod
    def _allow_all_gate():
        from Django_xm.apps.agent_hub.approval.step_gate import SkillStepGateResult

        async def _allow_all(*args, **kwargs):
            return SkillStepGateResult(True, "", "test-step-id", None, False)

        return mock.patch(
            "Django_xm.apps.agent_hub.approval.step_gate.gate_skill_step_tool_call", _allow_all
        )

    def test_timeout_returns_promptly_without_blocking(self):
        """超时立即返回错误结果；不被 with-shutdown 阻塞至任务完成。"""
        skill_tool = _make_pipeline_skill("timeout-skill", ["slow_probe"])
        skill_tool.available_tools = [_SleepTool(delay=1.0)]
        skill_tool.tool_map = {"slow_probe": skill_tool.available_tools[0]}

        async def _call():
            return skill_tool._run("q")

        with mock.patch(f"{_TOOL_MODULE}._SKILL_EXEC_TIMEOUT", 0.2), self._allow_all_gate():
            start = time.monotonic()
            result = asyncio.run(_call())
            elapsed = time.monotonic() - start

        self.assertIn("超时", result)
        self.assertLess(elapsed, 0.8, f"_run 耗时 {elapsed:.2f}s，超时未立即返回（仍被阻塞）")

    def test_normal_completion_unaffected(self):
        """正常完成路径不受重构影响，结果照常返回。"""
        skill_tool = _make_pipeline_skill("fast-skill", ["instant"])
        skill_tool.available_tools = [_InstantTool()]
        skill_tool.tool_map = {"instant": skill_tool.available_tools[0]}

        async def _call():
            return skill_tool._run("q")

        with self._allow_all_gate():
            result = asyncio.run(_call())
        self.assertEqual(result, "ok")


# ---------------------------------------------------------------------------
# C9：依赖只在用户可用工具集内校验
# ---------------------------------------------------------------------------


class SkillDependencyValidationTests(SimpleTestCase):
    """_collect_missing_dependencies：只校验、不注入、不改入参。"""

    def test_missing_dependency_reported_without_pool_fallback(self):
        """缺失依赖被报告；即使全局池存在同名工具也禁止回退（tripwire）。"""
        skill_tool = _make_pipeline_skill("dep-skill", ["needs_a", "needs_b"])
        available = [_InstantTool()]

        with mock.patch(_GLOBAL_POOL, side_effect=AssertionError("禁止回退全局工具池")):
            missing_map = SkillProvider._collect_missing_dependencies([skill_tool], available)

        self.assertEqual(missing_map, {"skill_dep-skill": ["needs_a", "needs_b"]})
        # 入参列表未被原地修改
        self.assertEqual([t.name for t in available], ["instant"])

    def test_satisfied_dependency_reported_empty(self):
        """依赖全部在可用集内 → 空 map。"""
        skill_tool = _make_pipeline_skill("dep-skill", ["instant"])
        available = [_InstantTool()]

        self.assertEqual(SkillProvider._collect_missing_dependencies([skill_tool], available), {})
        self.assertEqual([t.name for t in available], ["instant"])

    def test_non_skill_tools_ignored(self):
        """非 SkillBaseTool 条目跳过校验。"""
        available = [_InstantTool()]
        self.assertEqual(SkillProvider._collect_missing_dependencies(list(available), available), {})

    def test_get_skill_tools_keeps_tool_without_mutating_inputs(self):
        """集成：依赖缺失的技能工具保留加载（执行时报错），available_tools 不被修改。"""
        skill_tool = _make_pipeline_skill("dep-skill", ["ghost_tool"])

        with mock.patch("Django_xm.apps.tools.skills.adapter.SkillAdapter") as adapter_cls, mock.patch(
            f"{_PROVIDER_MODULE}.SkillProvider._collect_package_skills", return_value=[]
        ):
            adapter_cls.return_value.to_langchain_tools = mock.Mock(return_value=[skill_tool])

            available = [_InstantTool()]
            with self.assertLogs("Django_xm.apps.tools.skills.provider", level="WARNING") as logs:
                result = run_async(
                    SkillProvider.get_skill_tools(
                        user_id=None,
                        available_tools=available,
                        selected_tools=["skill_dep-skill"],
                    )
                )

        self.assertEqual(result, [skill_tool])
        self.assertEqual([t.name for t in available], ["instant"])
        self.assertTrue(any("未启用" in line for line in logs.output))


class SkillDependencyExecutionErrorTests(SimpleTestCase):
    """C9 配套：依赖缺失时执行返回明确的"需要哪些工具"错误。"""

    def test_missing_tool_error_lists_required_tools(self):
        skill_tool = _make_pipeline_skill("dep-skill", ["ghost_tool", "instant"])
        skill_tool.available_tools = [_InstantTool()]
        skill_tool.tool_map = {"instant": skill_tool.available_tools[0]}

        result = asyncio.run(skill_tool._arun("q"))

        self.assertIn("ghost_tool", result)
        self.assertIn("该技能需要以下工具", result)
        self.assertIn("ghost_tool, instant", result)


# ---------------------------------------------------------------------------
# C10：安装失败回滚 + 指令缓存失效
# ---------------------------------------------------------------------------


class SkillInstallRollbackTests(TestCase):
    """install_skill_package 事务化：失败不留悬挂记录/目录，更新失败可回滚。"""

    def setUp(self):
        self.tmp_base = tempfile.mkdtemp(prefix="skills_base_")
        self.addCleanup(shutil.rmtree, self.tmp_base, ignore_errors=True)
        self.loader = SkillLoader(skills_base_dir=self.tmp_base)
        self.user = get_user_model().objects.create_user(
            username="skill_fix_u", password="x", email="skill_fix_u@example.com"
        )
        from Django_xm.apps.tools.models import ToolCategory

        ToolCategory.objects.get_or_create(code="general", defaults={"name": "通用"})

    def _make_zip(self, name: str, body: str) -> str:
        # 同名 zip 可能多次创建（更新场景 v1/v2），文件名加序号避免 cleanup 重复 unlink
        self._zip_seq = getattr(self, "_zip_seq", 0) + 1
        zip_path = os.path.join(self.tmp_base, f"{name}-{self._zip_seq}.zip")
        skill_md = f"---\nname: {name}\ndescription: 测试技能包\n---\n\n{body}\n"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(f"{name}/SKILL.md", skill_md)
        self.addCleanup(os.unlink, zip_path)
        return zip_path

    def _read_body(self, skill_dir: str) -> str:
        with open(os.path.join(skill_dir, "SKILL.md"), encoding="utf-8") as f:
            return f.read()

    def _assert_no_leftovers(self, parent_dir: str):
        leftovers = [n for n in os.listdir(parent_dir) if ".tmp-" in n or ".bak-" in n]
        self.assertEqual(leftovers, [], f"存在临时/备份残留: {leftovers}")

    def test_install_success_creates_record_and_dir(self):
        zip_path = self._make_zip("roll-skill", "v1 指令")
        ok, msg, _meta = self.loader.install_skill_package(zip_path, self.user)
        self.assertTrue(ok, msg)

        from Django_xm.apps.tools.models import SkillPackage

        pkg = SkillPackage.objects.get(user=self.user, name="roll-skill")
        self.assertTrue(os.path.isfile(os.path.join(pkg.skill_dir, "SKILL.md")))
        self.assertEqual(pkg.status, "active")
        self.assertIn("v1 指令", self._read_body(pkg.skill_dir))
        self._assert_no_leftovers(os.path.dirname(pkg.skill_dir))

    def test_fresh_install_db_failure_no_dangling_record(self):
        """全新安装在 DB 写入阶段失败 → 无记录、无残留目录。"""
        zip_path = self._make_zip("fail-fresh", "v1 指令")

        from Django_xm.apps.tools.models import SkillPackage

        target_dir = os.path.join(self.tmp_base, str(self.user.id), "fail-fresh")
        with mock.patch(
            "Django_xm.apps.tools.models.SkillPackage.objects.update_or_create",
            side_effect=RuntimeError("db down"),
        ):
            ok, msg, _ = self.loader.install_skill_package(zip_path, self.user)

        self.assertFalse(ok)
        self.assertIn("db down", msg)
        self.assertFalse(SkillPackage.objects.filter(user=self.user, name="fail-fresh").exists())
        self.assertFalse(os.path.isdir(target_dir))
        self._assert_no_leftovers(os.path.dirname(target_dir))

    def test_update_failure_restores_old_dir_and_record(self):
        """更新失败 → 旧目录内容恢复、旧 DB 记录原样，无悬挂 skill_dir。"""
        from Django_xm.apps.tools.models import SkillPackage

        zip_v1 = self._make_zip("roll-skill", "v1 指令")
        ok, msg, _ = self.loader.install_skill_package(zip_v1, self.user)
        self.assertTrue(ok, msg)

        pkg = SkillPackage.objects.get(user=self.user, name="roll-skill")
        old_dir = pkg.skill_dir
        old_body = self._read_body(old_dir)

        zip_v2 = self._make_zip("roll-skill", "v2 指令")
        with mock.patch(
            "Django_xm.apps.tools.models.SkillPackage.objects.update_or_create",
            side_effect=RuntimeError("db down"),
        ):
            ok2, msg2, _ = self.loader.install_skill_package(zip_v2, self.user)

        self.assertFalse(ok2)
        self.assertIn("db down", msg2)
        pkg.refresh_from_db()
        self.assertEqual(pkg.skill_dir, old_dir)
        self.assertEqual(self._read_body(pkg.skill_dir), old_body)
        self._assert_no_leftovers(os.path.dirname(old_dir))


class SkillZipSlipTests(TestCase):
    """F1：_extract_skill_zip 成员路径校验（Zip Slip）与 ZIP bomb 上限。

    失败语义：任一恶意/超限成员即整体失败（不产半成品、不静默丢成员），
    抛错经 install_skill_package 既有 except 路径回滚并返回 (False, msg, None)。
    """

    def setUp(self):
        self.tmp_base = tempfile.mkdtemp(prefix="skills_slip_")
        self.addCleanup(shutil.rmtree, self.tmp_base, ignore_errors=True)
        self.loader = SkillLoader(skills_base_dir=self.tmp_base)
        self.user = get_user_model().objects.create_user(
            username="skill_slip_u", password="x", email="skill_slip_u@example.com"
        )
        from Django_xm.apps.tools.models import ToolCategory

        ToolCategory.objects.get_or_create(code="general", defaults={"name": "通用"})

    def _make_zip(self, name: str, extra_members: dict[str, bytes] | None = None) -> str:
        self._zip_seq = getattr(self, "_zip_seq", 0) + 1
        zip_path = os.path.join(self.tmp_base, f"{name}-{self._zip_seq}.zip")
        skill_md = f"---\nname: {name}\ndescription: 测试技能包\n---\n\nbody\n"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(f"{name}/SKILL.md", skill_md)
            for member, data in (extra_members or {}).items():
                zf.writestr(member, data)
        self.addCleanup(os.unlink, zip_path)
        return zip_path

    def _assert_install_rejected(self, zip_path: str, name: str, msg_fragment: str):
        ok, msg, _ = self.loader.install_skill_package(zip_path, self.user)
        self.assertFalse(ok)
        self.assertIn(msg_fragment, msg)
        from Django_xm.apps.tools.models import SkillPackage

        self.assertFalse(SkillPackage.objects.filter(user=self.user, name=name).exists())

    def test_parent_traversal_member_rejected(self):
        """'../' 成员逃逸到 dest 之外 → 整体失败，逃逸文件不落盘。"""
        zip_path = self._make_zip("slip-a", {"slip-a/../../escape.txt": b"evil"})
        self._assert_install_rejected(zip_path, "slip-a", "Zip Slip")
        # dest = {base}/{uid}/slip-a.tmp-x，relative = "../../escape.txt"
        # 逃逸落点应为 {base}/escape.txt
        self.assertFalse(os.path.exists(os.path.join(self.tmp_base, "escape.txt")))
        leftovers = [n for n in os.listdir(self.tmp_base) if ".tmp-" in n]
        self.assertEqual(leftovers, [], f"存在解压残留: {leftovers}")

    def test_nested_prefix_traversal_member_rejected(self):
        """前缀剥离后的嵌套 '../' 穿越同样被拦（sub/../../../ 逃逸到 base）。"""
        zip_path = self._make_zip("slip-b", {"slip-b/sub/../../../escape2.txt": b"evil"})
        self._assert_install_rejected(zip_path, "slip-b", "Zip Slip")
        self.assertFalse(os.path.exists(os.path.join(self.tmp_base, "escape2.txt")))

    def test_absolute_member_rejected(self):
        """绝对路径成员（POSIX '/x'，Windows 下同样 reset join 前缀）被拒。"""
        zip_path = self._make_zip("slip-c", {"/abs_evil.txt": b"evil"})
        self._assert_install_rejected(zip_path, "slip-c", "Zip Slip")

    def test_zip_bomb_file_size_limit(self):
        from Django_xm.apps.tools.skills import loader as loader_mod

        zip_path = self._make_zip("bomb-a", {"bomb-a/data.bin": b"x" * 100})
        with mock.patch.object(loader_mod, "SKILL_ZIP_MAX_FILE_SIZE", 10):
            self._assert_install_rejected(zip_path, "bomb-a", "单文件解压体积超限")

    def test_zip_bomb_total_size_limit(self):
        from Django_xm.apps.tools.skills import loader as loader_mod

        zip_path = self._make_zip("bomb-b", {"bomb-b/a.bin": b"x" * 100, "bomb-b/b.bin": b"y" * 100})
        with mock.patch.object(loader_mod, "SKILL_ZIP_MAX_TOTAL_SIZE", 50):
            self._assert_install_rejected(zip_path, "bomb-b", "总解压体积超限")

    def test_zip_bomb_entry_count_limit(self):
        from Django_xm.apps.tools.skills import loader as loader_mod

        extras = {f"bomb-c/f{i}.txt": b"x" for i in range(3)}  # SKILL.md + 3 = 4 members
        zip_path = self._make_zip("bomb-c", extras)
        with mock.patch.object(loader_mod, "SKILL_ZIP_MAX_ENTRIES", 2):
            self._assert_install_rejected(zip_path, "bomb-c", "成员数超限")

    def test_legitimate_multi_member_install_unaffected(self):
        """正常多文件/嵌套目录包不被误伤；包内 '../' 归一化后仍在 dest 内的成员放行。"""
        zip_path = self._make_zip(
            "multi-a",
            {
                "multi-a/scripts/run.py": b"print('hi')",
                "multi-a/references/guide.md": "指南".encode(),
                "multi-a/sub/../ok.txt": b"stay-inside",  # 归一化后落在 dest 根，合法
            },
        )
        ok, msg, _ = self.loader.install_skill_package(zip_path, self.user)
        self.assertTrue(ok, msg)
        from Django_xm.apps.tools.models import SkillPackage

        pkg = SkillPackage.objects.get(user=self.user, name="multi-a")
        self.assertTrue(os.path.isfile(os.path.join(pkg.skill_dir, "scripts", "run.py")))
        self.assertTrue(os.path.isfile(os.path.join(pkg.skill_dir, "ok.txt")))


class SkillResourcePrefixIsolationTests(TestCase):
    """Task 10 遗留收尾：load_resource 前缀防护——同用户技能名互为前缀隔离。

    旧实现裸 startswith("…/alpha") 无分隔符收尾，"../alpha2/SKILL.md"
    归一化后落在 "…/alpha2/…" 仍命中前缀 → 跨技能泄漏；新实现
    resolve().is_relative_to() 带分隔符边界，拒绝。
    """

    def setUp(self):
        self.tmp_base = tempfile.mkdtemp(prefix="skills_prefix_")
        self.addCleanup(shutil.rmtree, self.tmp_base, ignore_errors=True)
        self.loader = SkillLoader(skills_base_dir=self.tmp_base)
        self.user = get_user_model().objects.create_user(
            username="skill_prefix_u", password="x", email="skill_prefix_u@example.com"
        )
        from Django_xm.apps.tools.models import ToolCategory

        ToolCategory.objects.get_or_create(code="general", defaults={"name": "通用"})
        for name, marker in (("alpha", "ALPHA_SECRET_V1"), ("alpha2", "ALPHA2_MARKER_V1")):
            zip_path = os.path.join(self.tmp_base, f"{name}.zip")
            skill_md = f"---\nname: {name}\ndescription: 前缀隔离测试\n---\n\n{marker}\n"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr(f"{name}/SKILL.md", skill_md)
            ok, msg, _ = self.loader.install_skill_package(zip_path, self.user)
            self.assertTrue(ok, msg)
            os.unlink(zip_path)

    def test_prefix_sibling_skill_resource_blocked(self):
        """"alpha" 读 "../alpha2/SKILL.md" → None（旧实现会泄漏 alpha2 内容）。"""
        result = self.loader.load_resource("alpha", "../alpha2/SKILL.md", user_id=self.user.id)
        self.assertIsNone(result)

    def test_own_resource_and_general_traversal_still_work(self):
        own = self.loader.load_resource("alpha", "SKILL.md", user_id=self.user.id)
        self.assertIsNotNone(own)
        self.assertIn("ALPHA_SECRET_V1", own)
        self.assertNotIn("ALPHA2_MARKER_V1", own)
        self.assertIsNone(self.loader.load_resource("alpha", "../../escape.md", user_id=self.user.id))


if __name__ == "__main__":
    unittest.main()
