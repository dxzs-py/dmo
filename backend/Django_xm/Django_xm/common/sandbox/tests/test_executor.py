"""SandboxExecutor 单测（mock subprocess，不依赖真实 Docker）。

覆盖（Task 1.6）：
- 容器复用：同一 thread_id 仅创建一次容器，命令经 docker exec 顺序复用
- 损坏自愈：exec 失败（容器被删）→ rm -f + 重建一次 → 重试成功
- 二次失败 fail-closed：重建后仍失败 → 返回明确错误（不静默降级本机）
- 路径映射：docker exec 工作目录 = /workspace/research/{thread_id}
- 清理：cleanup_sandbox 调用 docker rm -f
- 全局 SANDBOX_ENABLED=False（test.py 默认）：本机执行，不触任何 docker 命令

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.common.sandbox.tests.test_executor --noinput
"""

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from Django_xm.common.sandbox.executor import SandboxExecutor, container_name

# 显式开启全局沙箱（test.py 默认 ENABLED=False，此覆盖仅作用于用它的测试）
SANDBOX_ON = {**settings.SANDBOX_CONFIG, "ENABLED": True}


def _join(args) -> str:
    """将 subprocess 参数规范化为可判定的命令串。"""
    if isinstance(args, (list, tuple)):
        return " ".join(str(a) for a in args)
    return str(args)


def _fake_run(calls, *, exec_rc=0, exec_stderr="", run_rc=0, rm_rc=0):
    """构造 subprocess.run side_effect：按 docker 子命令分发固定结果。"""

    def _side_effect(args, *a, **kw):
        calls.append(_join(args))
        joined = _join(args)
        if "docker exec" in joined:
            return MagicMock(returncode=exec_rc, stdout="exec-out", stderr=exec_stderr)
        if "docker run" in joined:
            return MagicMock(returncode=run_rc, stdout="container-id", stderr="")
        if "docker rm" in joined:
            return MagicMock(returncode=rm_rc, stdout="", stderr="")
        if "docker info" in joined:
            return MagicMock(returncode=0, stdout="24.0.0", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    return _side_effect


class SandboxExecutorTests(SimpleTestCase):
    """长驻容器复用 / 自愈 / fail-closed / 清理行为。"""

    # ------------------------------------------------------------------
    # 路由判定（任务级开关 × 风险等级）
    # ------------------------------------------------------------------
    @override_settings(SANDBOX_CONFIG=SANDBOX_ON)
    def test_should_sandbox_high_command_enabled(self):
        """全局启用 + 开关开启 + HIGH 命令 → 进沙箱。"""
        executor = SandboxExecutor()
        self.assertTrue(executor.should_sandbox("rm -rf /tmp/x", enable_sandbox=True))

    @override_settings(SANDBOX_CONFIG=SANDBOX_ON)
    def test_should_sandbox_non_high_command(self):
        """全局启用 + 开关开启 + 非 HIGH 命令 → 本机。"""
        executor = SandboxExecutor()
        self.assertFalse(executor.should_sandbox("ls", enable_sandbox=True))

    @override_settings(SANDBOX_CONFIG=SANDBOX_ON)
    def test_should_sandbox_switch_off(self):
        """开关关闭（默认）即使 HIGH 命令也走本机（非故障）。"""
        executor = SandboxExecutor()
        self.assertFalse(executor.should_sandbox("rm -rf /tmp/x", enable_sandbox=False))

    def test_should_sandbox_global_disabled(self):
        """全局 SANDBOX_ENABLED=False（test.py 默认）：开关开启也走本机。"""
        executor = SandboxExecutor()
        self.assertFalse(executor.should_sandbox("rm -rf /tmp/x", enable_sandbox=True))

    # ------------------------------------------------------------------
    # 容器复用
    # ------------------------------------------------------------------
    @override_settings(SANDBOX_CONFIG=SANDBOX_ON)
    def test_container_reused_executed_once_created(self):
        """同一 thread_id 连续多条命令：docker run 仅一次，docker exec 多次复用。"""
        executor = SandboxExecutor()
        executor._docker_available = True
        calls = []
        with patch(
            "Django_xm.common.sandbox.executor.subprocess.run",
            side_effect=_fake_run(calls),
        ):
            r1 = executor.execute("echo hi", enable_sandbox=True, thread_id="t1")
            r2 = executor.execute("echo hi2", enable_sandbox=True, thread_id="t1")

        self.assertEqual(r1.return_code, 0)
        self.assertEqual(r2.return_code, 0)
        run_calls = [c for c in calls if "docker run" in c]
        exec_calls = [c for c in calls if "docker exec" in c]
        self.assertEqual(len(run_calls), 1, f"docker run 应只执行一次: {calls}")
        self.assertEqual(len(exec_calls), 2)

    @override_settings(SANDBOX_CONFIG=SANDBOX_ON)
    def test_exec_uses_mapped_workdir(self):
        """docker exec 工作目录正确映射为 /workspace/research/{thread_id}。"""
        executor = SandboxExecutor()
        executor._docker_available = True
        calls = []
        host_wd = str(settings.DATA_DIR / "research" / "t4")
        with patch(
            "Django_xm.common.sandbox.executor.subprocess.run",
            side_effect=_fake_run(calls),
        ):
            executor.execute("cmd", working_dir=host_wd, enable_sandbox=True, thread_id="t4")

        exec_call = next(c for c in calls if "docker exec" in c)
        parts = exec_call.split(" ")
        self.assertIn("-w", parts)
        self.assertEqual(parts[parts.index("-w") + 1], "/workspace/research/t4")

    @override_settings(SANDBOX_CONFIG=SANDBOX_ON)
    def test_mount_uses_data_dir(self):
        """docker run 挂载仅 DATA_DIR（不挂载整个项目根）。"""
        executor = SandboxExecutor()
        executor._docker_available = True
        calls = []
        with patch(
            "Django_xm.common.sandbox.executor.subprocess.run",
            side_effect=_fake_run(calls),
        ):
            executor.execute("cmd", enable_sandbox=True, thread_id="t-mount")

        run_call = next(c for c in calls if "docker run" in c)
        self.assertIn(f"{settings.DATA_DIR}:/workspace", run_call)
        self.assertNotIn(str(settings.PROJECT_ROOT) + ":", run_call)

    # ------------------------------------------------------------------
    # 损坏自愈（重建一次重试成功）
    # ------------------------------------------------------------------
    @override_settings(SANDBOX_CONFIG=SANDBOX_ON)
    def test_container_self_heal_retry_success(self):
        """首次 exec 失败（No such container）→ rm -f + 重建 → 重试成功。"""
        executor = SandboxExecutor()
        executor._docker_available = True
        calls = []
        state = {"exec_fail": True}

        def _side_effect(args, *a, **kw):
            calls.append(_join(args))
            joined = _join(args)
            if "docker exec" in joined:
                if state["exec_fail"]:
                    state["exec_fail"] = False
                    return MagicMock(returncode=125, stdout="", stderr="No such container: abc")
                return MagicMock(returncode=0, stdout="healed", stderr="")
            if "docker run" in joined:
                return MagicMock(returncode=0, stdout="container-id", stderr="")
            if "docker rm" in joined:
                return MagicMock(returncode=0, stdout="", stderr="")
            if "docker info" in joined:
                return MagicMock(returncode=0, stdout="24.0.0", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "Django_xm.common.sandbox.executor.subprocess.run", side_effect=_side_effect
        ):
            result = executor.execute("cmd", enable_sandbox=True, thread_id="t2")

        self.assertEqual(result.return_code, 0)
        self.assertEqual(result.stdout, "healed")
        run_calls = [c for c in calls if "docker run" in c]
        rm_calls = [c for c in calls if "docker rm" in c]
        self.assertEqual(len(run_calls), 2, f"重建一次应再 docker run: {calls}")
        self.assertEqual(len(rm_calls), 1)

    # ------------------------------------------------------------------
    # 二次失败 fail-closed（不静默降级本机）
    # ------------------------------------------------------------------
    @override_settings(SANDBOX_CONFIG=SANDBOX_ON)
    def test_second_failure_fail_closed_no_local_fallback(self):
        """重建后 exec 仍失败 → 返回明确错误，绝不降级本机（Popen 未调用）。"""
        executor = SandboxExecutor()
        executor._docker_available = True

        def _side_effect(args, *a, **kw):
            joined = _join(args)
            if "docker exec" in joined:
                return MagicMock(returncode=125, stdout="", stderr="No such container: abc")
            if "docker run" in joined:
                return MagicMock(returncode=0, stdout="container-id", stderr="")
            if "docker rm" in joined:
                return MagicMock(returncode=0, stdout="", stderr="")
            if "docker info" in joined:
                return MagicMock(returncode=0, stdout="24.0.0", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "Django_xm.common.sandbox.executor.subprocess.run", side_effect=_side_effect
        ) as mock_run, patch(
            "Django_xm.common.sandbox.executor.subprocess.Popen"
        ) as mock_popen:
            result = executor.execute("cmd", enable_sandbox=True, thread_id="t3")

        self.assertNotEqual(result.return_code, 0)
        self.assertTrue(result.error, "fail-closed 必须返回错误原因")
        self.assertIn("检查 Docker", result.error)
        self.assertIn("关闭沙箱", result.error)
        self.assertFalse(result.sandboxed)
        # 绝不静默降级本机：Popen（本机执行路径）未被调用
        mock_popen.assert_not_called()
        run_calls = [c for c in mock_run.call_args_list if "docker run" in _join(c.args[0])]
        self.assertEqual(len(run_calls), 2, "首次创建 + 重建一次")

    @override_settings(SANDBOX_CONFIG=SANDBOX_ON)
    def test_docker_unavailable_fail_closed(self):
        """开关开启但 Docker 不可用 → fail-closed 错误（不降级本机）。"""
        executor = SandboxExecutor()
        with patch(
            "Django_xm.common.sandbox.executor._is_docker_available", return_value=False
        ), patch("Django_xm.common.sandbox.executor.subprocess.Popen") as mock_popen:
            result = executor.execute("cmd", enable_sandbox=True, thread_id="t-docker-down")

        self.assertTrue(result.error)
        self.assertIn("Docker 不可用", result.error)
        mock_popen.assert_not_called()

    # ------------------------------------------------------------------
    # 全局未启用（test.py 默认）：本机执行不触 docker
    # ------------------------------------------------------------------
    def test_global_disabled_uses_local_no_docker(self):
        """SANDBOX_ENABLED=False：即使开关开启也走本机，且不执行任何 docker 命令。"""
        executor = SandboxExecutor()
        fake_process = MagicMock()
        fake_process.communicate.return_value = ("local-out", "")
        fake_process.returncode = 0
        with patch(
            "Django_xm.common.sandbox.executor.subprocess.run"
        ) as mock_run, patch(
            "Django_xm.common.sandbox.executor.subprocess.Popen", return_value=fake_process
        ):
            result = executor.execute("echo hi", enable_sandbox=True, thread_id="t6")

        self.assertFalse(result.sandboxed)
        self.assertEqual(result.stdout, "local-out")
        self.assertEqual(result.return_code, 0)
        # 未触发任何 docker 命令（含 docker info 探测）
        mock_run.assert_not_called()

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------
    def test_cleanup_sandbox_removes_container(self):
        """cleanup_sandbox：docker rm -f 对应容器并清空进程内注册。"""
        executor = SandboxExecutor()
        executor._containers["t5"] = 1.0
        calls = []
        with patch(
            "Django_xm.common.sandbox.executor.subprocess.run", side_effect=_fake_run(calls)
        ):
            executor.cleanup_sandbox("t5")

        rm_call = [c for c in calls if "docker rm" in c]
        self.assertEqual(len(rm_call), 1)
        self.assertIn(container_name("t5"), rm_call[0])
        self.assertNotIn("t5", executor._containers)
