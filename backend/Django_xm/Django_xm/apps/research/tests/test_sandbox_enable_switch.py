"""任务级沙箱开关链路单测（Task 2：serializer/持久化/执行注入/shell 路由）。

覆盖：
- ResearchStartSerializer 接受 / 缺省 enable_sandbox
- ResearchTask 字段持久化（create_task 显式 True / 缺省 False）
- execute_research_async 将 enable_sandbox 注入 configurable（与 thread_id 同源）
- shell 路由判定组合：开关 on/off × high/非 high（经 _execute_command 透传）

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.research.tests.test_sandbox_enable_switch --noinput
"""

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from asgiref.sync import async_to_sync
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from Django_xm.apps.research.models import ResearchTask
from Django_xm.apps.research.serializers import ResearchStartSerializer
from Django_xm.apps.research.services.research_runner import execute_research_async
from Django_xm.apps.research.services.task_manager import get_task_manager
from Django_xm.apps.tools.errors import ToolStatus
from Django_xm.apps.tools.langchain.shell import _execute_command

SANDBOX_ON = {**settings.SANDBOX_CONFIG, "ENABLED": True}


class ResearchStartSerializerTests(TestCase):
    """Task 2.2：serializer 接受 enable_sandbox 且缺省为 False。"""

    def test_serializer_accepts_enable_sandbox(self):
        """显式传 enable_sandbox=True → validated_data 透传。"""
        serializer = ResearchStartSerializer(data={"query": "测试主题", "enable_sandbox": True})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertIs(serializer.validated_data["enable_sandbox"], True)

    def test_serializer_default_false(self):
        """缺省 → enable_sandbox=False（默认关闭）。"""
        serializer = ResearchStartSerializer(data={"query": "测试主题"})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertIs(serializer.validated_data["enable_sandbox"], False)

    def test_serializer_accepts_false_explicit(self):
        """显式传 False 亦合法。"""
        serializer = ResearchStartSerializer(data={"query": "测试主题", "enable_sandbox": False})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertIs(serializer.validated_data["enable_sandbox"], False)


class ResearchTaskSandboxFieldTests(TestCase):
    """Task 2.1：ResearchTask.enable_sandbox 字段持久化。"""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="sandbox-user", password="pw123456")

    def test_create_task_persists_true(self):
        """create_task(enable_sandbox=True) → DB 字段为 True。"""
        get_task_manager().create_task("t-sandbox-1", "q", enable_sandbox=True, created_by=self.user)
        task = ResearchTask.objects.get(task_id="t-sandbox-1")
        self.assertTrue(task.enable_sandbox)

    def test_create_task_default_false(self):
        """create_task 缺省 → DB 字段为 False。"""
        get_task_manager().create_task("t-sandbox-2", "q", created_by=self.user)
        task = ResearchTask.objects.get(task_id="t-sandbox-2")
        self.assertFalse(task.enable_sandbox)


class ExecuteResearchAsyncSandboxTests(TestCase):
    """Task 2.3：execute_research_async 将 enable_sandbox 注入 configurable。"""

    def _run_with(self, enable_sandbox: bool):
        captured = {}

        class FakeAgent:
            async def astream_research_with_interrupts(
                self, query, config, callbacks=None, on_interrupt=None, resume_command=None
            ):
                captured["config"] = config
                return {"success": True, "final_report": "报告"}

        agent = FakeAgent()
        async_to_sync(execute_research_async)(
            agent,
            "q",
            "tid-sandbox",
            disable_llm_cache=False,
            enable_sandbox=enable_sandbox,
        )
        return captured["config"]["configurable"]

    def test_configurable_injects_enable_sandbox_true(self):
        """enable_sandbox=True → configurable['enable_sandbox'] is True。"""
        configurable = self._run_with(True)
        self.assertIs(configurable["enable_sandbox"], True)
        self.assertEqual(configurable["thread_id"], "tid-sandbox")

    def test_configurable_injects_enable_sandbox_false(self):
        """enable_sandbox=False（默认）→ configurable['enable_sandbox'] is False。"""
        configurable = self._run_with(False)
        self.assertIs(configurable["enable_sandbox"], False)


class ShellSandboxRoutingTests(TestCase):
    """Task 2.4：shell 工具路由读取任务级开关（on/off × high/非 high）。"""

    @override_settings(SANDBOX_CONFIG=SANDBOX_ON)
    def test_enabled_high_routes_to_sandbox(self):
        """开关开启 + HIGH 命令 → should_sandbox(enable_sandbox=True)，execute 透传 thread_id。"""
        with patch("Django_xm.common.sandbox.sandbox_executor") as mock_se:
            mock_se.should_sandbox.return_value = True
            mock_se.execute.return_value = MagicMock(
                stdout="ok", stderr="", return_code=0, timed_out=False, sandboxed=True, error=""
            )
            result = _execute_command("rm -rf /tmp/x", enable_sandbox=True, thread_id="tid-1")

        self.assertEqual(result.status, ToolStatus.SUCCESS)
        mock_se.should_sandbox.assert_called_once_with("rm -rf /tmp/x", enable_sandbox=True)
        call_kwargs = mock_se.execute.call_args.kwargs
        self.assertIs(call_kwargs["enable_sandbox"], True)
        self.assertEqual(call_kwargs["thread_id"], "tid-1")

    @override_settings(SANDBOX_CONFIG=SANDBOX_ON)
    def test_enabled_non_high_skips_sandbox(self):
        """开关开启但命令非 HIGH → 判定为 False，不触达 execute（本机执行）。"""
        with patch("Django_xm.common.sandbox.sandbox_executor") as mock_se:
            mock_se.should_sandbox.return_value = False
            with patch("Django_xm.apps.tools.langchain.shell.subprocess.Popen") as mock_popen:
                fake = MagicMock()
                fake.communicate.return_value = ("ls-out", "")
                fake.returncode = 0
                mock_popen.return_value = fake
                result = _execute_command("ls", enable_sandbox=True, thread_id="tid-1")

        self.assertEqual(result.status, ToolStatus.SUCCESS)
        self.assertIn("ls-out", result.content)
        mock_se.execute.assert_not_called()

    @override_settings(SANDBOX_CONFIG=SANDBOX_ON)
    def test_disabled_high_skips_sandbox(self):
        """开关关闭（默认）+ HIGH 命令 → 本机执行，不触达 execute（行为不变）。"""
        with patch("Django_xm.common.sandbox.sandbox_executor") as mock_se:
            mock_se.should_sandbox.return_value = False
            with patch("Django_xm.apps.tools.langchain.shell.subprocess.Popen") as mock_popen:
                fake = MagicMock()
                fake.communicate.return_value = ("rm-out", "")
                fake.returncode = 0
                mock_popen.return_value = fake
                result = _execute_command("rm -rf /tmp/x", enable_sandbox=False, thread_id="tid-1")

        self.assertEqual(result.status, ToolStatus.SUCCESS)
        mock_se.execute.assert_not_called()
        mock_se.should_sandbox.assert_called_once_with("rm -rf /tmp/x", enable_sandbox=False)

    def test_default_off_skips_sandbox(self):
        """完全缺省（config 无开关）→ enable_sandbox=False，本机执行。"""
        with patch("Django_xm.common.sandbox.sandbox_executor") as mock_se:
            mock_se.should_sandbox.return_value = False
            with patch("Django_xm.apps.tools.langchain.shell.subprocess.Popen") as mock_popen:
                fake = MagicMock()
                fake.communicate.return_value = ("out", "")
                fake.returncode = 0
                mock_popen.return_value = fake
                result = _execute_command("echo hi")

        self.assertEqual(result.status, ToolStatus.SUCCESS)
        mock_se.should_sandbox.assert_called_once_with("echo hi", enable_sandbox=False)

    @override_settings(SANDBOX_CONFIG=SANDBOX_ON)
    def test_sandbox_error_fail_closed_no_local_fallback(self):
        """沙箱执行返回 fail-closed 错误 → 直接返回错误结果，绝不静默本机降级。"""
        from Django_xm.apps.tools.langchain.shell import _sandbox_result_to_standard
        from Django_xm.common.sandbox.executor import SandboxResult

        sr = SandboxResult(
            stdout="",
            stderr="",
            return_code=1,
            sandboxed=False,
            error="沙箱容器执行失败（已尝试重建一次）：xxx。请检查 Docker 服务，或关闭沙箱后重试。",
        )
        std = _sandbox_result_to_standard(sr, "cmd")
        self.assertEqual(std.status, ToolStatus.ERROR)
        self.assertIn("沙箱执行失败", std.content)
        self.assertIn("检查 Docker", std.content)
