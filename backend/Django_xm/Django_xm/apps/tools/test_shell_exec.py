"""ShellExecTool 单元测试。

覆盖审批统一后 ShellExecTool 的行为：
1. _arun 方法签名不包含 approved_by_middleware 参数
2. 白名单命令直接执行（无需审批）
3. 非白名单命令也直接执行（审批由 ApprovalMiddleware 统一处理，工具层不参与）
4. ShellExecInput 模型不含 approved_by_middleware 字段

运行方式:
    cd d:\\programming\\langchain\\langchain_xm\backend\\Django_xm
    conda activate langchain_xm
    python manage.py test Django_xm.apps.tools.test_shell_exec --verbosity=2
"""

from __future__ import annotations

import inspect
import os
import unittest
from unittest.mock import patch

# Django 环境初始化（兼容 unittest 直接运行）
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django
import django.apps

if not django.apps.apps.ready:
    django.setup()

from Django_xm.apps.tools.errors import StandardToolResult, ToolStatus
from Django_xm.apps.tools.langchain.shell import (
    ShellExecInput,
    ShellExecTool,
)


class ShellExecSignatureTests(unittest.TestCase):
    """验证 ShellExecTool / ShellExecInput 在阶段一移除 approved_by_middleware 后的契约。"""

    def test_arun_no_approved_by_middleware_param(self):
        """_arun 方法签名不包含 approved_by_middleware 参数。"""
        sig = inspect.signature(ShellExecTool._arun)
        self.assertNotIn(
            "approved_by_middleware",
            sig.parameters,
            "_arun 不应再接受 approved_by_middleware 参数（移除 middleware 注入字段）",
        )

    def test_run_no_approved_by_middleware_param(self):
        """_run 方法签名不包含 approved_by_middleware 参数。"""
        sig = inspect.signature(ShellExecTool._run)
        self.assertNotIn(
            "approved_by_middleware",
            sig.parameters,
            "_run 不应再接受 approved_by_middleware 参数",
        )

    def test_shell_exec_input_no_approved_by_middleware_field(self):
        """ShellExecInput Pydantic 模型不含 approved_by_middleware 字段。"""
        fields = set(ShellExecInput.model_fields.keys())
        self.assertNotIn(
            "approved_by_middleware",
            fields,
            "ShellExecInput 不应再包含 approved_by_middleware 字段",
        )
        # 期望字段集合
        expected_subset = {"command", "timeout", "working_dir"}
        self.assertTrue(
            expected_subset.issubset(fields),
            f"ShellExecInput 应包含核心字段 {expected_subset}, 实际: {fields}",
        )


class ShellExecArunExecutionTests(unittest.IsolatedAsyncioTestCase):
    """验证 _arun 不再发起 interrupt 兜底，所有非白名单命令直接执行。"""

    async def test_arun_whitelist_command_executes_directly(self):
        """白名单命令（echo）直接执行，无需审批。"""
        tool = ShellExecTool()
        fake_result = StandardToolResult(
            content="ok",
            status=ToolStatus.SUCCESS,
            source="shell_exec",
            metadata={"command": "echo hello", "return_code": 0},
        )
        with patch(
            "Django_xm.apps.tools.langchain.shell._execute_command",
            return_value=fake_result,
        ) as mock_exec:
            ret = await tool._arun(command="echo hello")
            mock_exec.assert_called_once()
            # 验证传入的 command 不含 approved_by_middleware
            args, kwargs = mock_exec.call_args
            self.assertEqual(args[0], "echo hello")
            # 返回值为 ToolMessage 字符串
            self.assertIn("ok", str(ret))

    async def test_arun_non_whitelist_command_executes_directly(self):
        """非白名单命令（rm）也直接执行（不再有兜底 interrupt_for_approval 调用）。

        阶段一变更：移除 interrupt_for_approval 兜底，非白名单命令经
        ApprovalMiddleware 审批通过后直接进入 _execute_command。
        """
        tool = ShellExecTool()
        fake_result = StandardToolResult(
            content="removed",
            status=ToolStatus.SUCCESS,
            source="shell_exec",
            metadata={"command": "rm /tmp/x", "return_code": 0},
        )
        with patch(
            "Django_xm.apps.tools.langchain.shell._execute_command",
            return_value=fake_result,
        ) as mock_exec, patch(
            "Django_xm.apps.tools.langchain.shell._is_command_blocked",
            return_value=None,
        ), patch(
            "Django_xm.apps.tools.langchain.shell._is_command_whitelisted",
            return_value=False,
        ):
            await tool._arun(command="rm /tmp/x")
            mock_exec.assert_called_once()
            args, kwargs = mock_exec.call_args
            self.assertEqual(args[0], "rm /tmp/x")

    async def test_arun_blocked_command_returns_error_without_execution(self):
        """危险命令（rm -rf /）被 BLOCKED_PATTERNS 拦截，不调用 _execute_command。"""
        tool = ShellExecTool()
        with patch(
            "Django_xm.apps.tools.langchain.shell._execute_command"
        ) as mock_exec:
            ret = await tool._arun(command="rm -rf /")
            mock_exec.assert_not_called()
            self.assertIn("拦截", str(ret))

    async def test_arun_empty_command_returns_error_without_execution(self):
        """空命令直接返回错误，不调用 _execute_command。"""
        tool = ShellExecTool()
        with patch(
            "Django_xm.apps.tools.langchain.shell._execute_command"
        ) as mock_exec:
            ret = await tool._arun(command="")
            mock_exec.assert_not_called()
            self.assertIn("不能为空", str(ret))


if __name__ == "__main__":
    unittest.main()
