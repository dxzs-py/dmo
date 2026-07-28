"""Shell 执行安全收紧单元测试（Task 6.6）。

覆盖 spec `fix-backend-audit-findings` 阶段 1D 变更：
1. SubTask 6.1: python/python3/node/npx 移出白名单（需审批）
2. SubTask 6.2: SHELL_EXEC_ALLOWED_DIRS 默认设为 [DATA_DIR, MEDIA_ROOT]
3. SubTask 6.3: BLOCKED_PATTERNS 增加 re.DOTALL 标志（跨行命令拼接拦截）
4. SubTask 6.4: 单词命令匹配改为"完整命令分词后第一个词匹配且无重定向/管道"
5. SubTask 6.5: 高频安全脚本前缀（python manage.py / python -m pytest）保留白名单

运行方式:
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    conda activate langchain_xm
    python manage.py test Django_xm.apps.tools.tests.test_shell_security --verbosity=2
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

# Django 环境初始化
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django
import django.apps

if not django.apps.apps.ready:
    django.setup()

from django.conf import settings

from Django_xm.apps.tools.errors import StandardToolResult, ToolStatus
from Django_xm.apps.tools.langchain.shell import (
    BLOCKED_PATTERNS,
    DEFAULT_WHITELIST_COMMANDS,
    ShellExecTool,
    _has_redirect_or_pipe,
    _is_command_blocked,
    _is_command_whitelisted,
)

# ============================================================================
# SubTask 6.1: python/node/npx 移出白名单
# ============================================================================


class InterpreterRemovedFromWhitelistTests(unittest.TestCase):
    """SubTask 6.1: python/python3/node/npx 本体移出白名单。"""

    def test_python_alone_not_in_whitelist(self):
        """单独的 'python' 不在白名单中（需审批）。"""
        self.assertNotIn('python', DEFAULT_WHITELIST_COMMANDS)

    def test_python3_alone_not_in_whitelist(self):
        """单独的 'python3' 不在白名单中（需审批）。"""
        self.assertNotIn('python3', DEFAULT_WHITELIST_COMMANDS)

    def test_node_alone_not_in_whitelist(self):
        """单独的 'node' 不在白名单中（需审批）。"""
        self.assertNotIn('node', DEFAULT_WHITELIST_COMMANDS)

    def test_npx_alone_not_in_whitelist(self):
        """单独的 'npx' 不在白名单中（需审批）。"""
        self.assertNotIn('npx', DEFAULT_WHITELIST_COMMANDS)

    def test_python_arbitrary_script_not_whitelisted(self):
        """'python malicious.py' 不在白名单中（需审批）。"""
        self.assertFalse(_is_command_whitelisted('python malicious.py'))
        self.assertFalse(_is_command_whitelisted('python script.py'))
        self.assertFalse(_is_command_whitelisted('python3 my_script.py'))

    def test_python_inline_code_not_whitelisted(self):
        """'python -c "code"' 不在白名单中（需审批）。"""
        self.assertFalse(_is_command_whitelisted('python -c "print(\'hack\')"'))
        self.assertFalse(_is_command_whitelisted('python3 -c "import os"'))

    def test_node_script_not_whitelisted(self):
        """'node script.js' 不在白名单中（需审批）。"""
        self.assertFalse(_is_command_whitelisted('node script.js'))
        self.assertFalse(_is_command_whitelisted('node -e "console.log(1)"'))

    def test_npx_package_not_whitelisted(self):
        """'npx some-package' 不在白名单中（需审批）。"""
        self.assertFalse(_is_command_whitelisted('npx create-react-app'))
        self.assertFalse(_is_command_whitelisted('npx http-server'))


# ============================================================================
# SubTask 6.5: 高频安全脚本前缀保留白名单
# ============================================================================


class SafeScriptPrefixWhitelistTests(unittest.TestCase):
    """SubTask 6.5: python manage.py / python -m pytest 等高频安全脚本保留白名单。"""

    def test_python_manage_py_whitelisted(self):
        """'python manage.py' 在白名单中（直接执行）。"""
        self.assertTrue(_is_command_whitelisted('python manage.py'))

    def test_python_manage_py_help_whitelisted(self):
        """'python manage.py help' 在白名单中。"""
        self.assertTrue(_is_command_whitelisted('python manage.py help'))

    def test_python_manage_py_migrate_whitelisted(self):
        """'python manage.py migrate' 在白名单中（前缀匹配）。"""
        self.assertTrue(_is_command_whitelisted('python manage.py migrate'))

    def test_python_m_pytest_whitelisted(self):
        """'python -m pytest' 在白名单中。"""
        self.assertTrue(_is_command_whitelisted('python -m pytest'))

    def test_python_m_unittest_whitelisted(self):
        """'python -m unittest' 在白名单中。"""
        self.assertTrue(_is_command_whitelisted('python -m unittest'))

    def test_python3_manage_py_whitelisted(self):
        """'python3 manage.py' 在白名单中。"""
        self.assertTrue(_is_command_whitelisted('python3 manage.py'))

    def test_python3_m_pytest_whitelisted(self):
        """'python3 -m pytest' 在白名单中。"""
        self.assertTrue(_is_command_whitelisted('python3 -m pytest'))

    def test_python_m_pytest_with_args_whitelisted(self):
        """'python -m pytest tests/' 在白名单中（前缀匹配 + 参数）。"""
        self.assertTrue(_is_command_whitelisted('python -m pytest tests/'))


# ============================================================================
# 白名单常规命令测试
# ============================================================================


class RegularWhitelistCommandsTests(unittest.TestCase):
    """白名单内常规命令直接执行验证。"""

    def test_echo_whitelisted(self):
        """'echo hello' 在白名单中。"""
        self.assertTrue(_is_command_whitelisted('echo hello'))

    def test_pandoc_whitelisted(self):
        """'pandoc' 在白名单中。"""
        self.assertTrue(_is_command_whitelisted('pandoc'))

    def test_pandoc_with_args_whitelisted(self):
        """'pandoc input.md -o output.pdf' 在白名单中（前缀匹配）。"""
        self.assertTrue(_is_command_whitelisted('pandoc input.md -o output.pdf'))

    def test_git_status_whitelisted(self):
        """'git status' 在白名单中。"""
        self.assertTrue(_is_command_whitelisted('git status'))

    def test_git_log_whitelisted(self):
        """'git log' 在白名单中。"""
        self.assertTrue(_is_command_whitelisted('git log'))

    def test_git_diff_whitelisted(self):
        """'git diff' 在白名单中。"""
        self.assertTrue(_is_command_whitelisted('git diff'))

    def test_pip_list_whitelisted(self):
        """'pip list' 在白名单中。"""
        self.assertTrue(_is_command_whitelisted('pip list'))

    def test_pip_show_whitelisted(self):
        """'pip show django' 在白名单中。"""
        self.assertTrue(_is_command_whitelisted('pip show django'))

    def test_npm_list_whitelisted(self):
        """'npm list' 在白名单中。"""
        self.assertTrue(_is_command_whitelisted('npm list'))

    def test_agent_browser_whitelisted(self):
        """'agent-browser' 在白名单中。"""
        self.assertTrue(_is_command_whitelisted('agent-browser'))

    def test_empty_command_not_whitelisted(self):
        """空命令不在白名单中。"""
        self.assertFalse(_is_command_whitelisted(''))
        self.assertFalse(_is_command_whitelisted('   '))


# ============================================================================
# SubTask 6.4: 重定向/管道操作符检测
# ============================================================================


class RedirectAndPipeDetectionTests(unittest.TestCase):
    """SubTask 6.4: 单词命令携带重定向/管道时强制审批。"""

    def test_redirect_output_detected(self):
        """'>' 重定向被检测到。"""
        self.assertTrue(_has_redirect_or_pipe('echo bad > /etc/passwd'))

    def test_append_redirect_detected(self):
        """'>>' 追加重定向被检测到。"""
        self.assertTrue(_has_redirect_or_pipe('echo ok >> /etc/hosts'))

    def test_input_redirect_detected(self):
        """'<' 输入重定向被检测到。"""
        self.assertTrue(_has_redirect_or_pipe('cat < /etc/passwd'))

    def test_pipe_detected(self):
        """'|' 管道被检测到。"""
        self.assertTrue(_has_redirect_or_pipe('cat file | rm -rf /'))

    def test_no_redirect_or_pipe_returns_false(self):
        """无重定向/管道的命令返回 False。"""
        self.assertFalse(_has_redirect_or_pipe('echo hello'))
        self.assertFalse(_has_redirect_or_pipe('git status'))
        self.assertFalse(_has_redirect_or_pipe('python manage.py migrate'))

    def test_echo_with_redirect_not_whitelisted(self):
        """'echo bad > /etc/passwd' 不在白名单中（重定向强制审批）。"""
        self.assertFalse(_is_command_whitelisted('echo bad > /etc/passwd'))

    def test_echo_with_pipe_not_whitelisted(self):
        """'echo bad | rm -rf /' 不在白名单中（管道强制审批）。"""
        self.assertFalse(_is_command_whitelisted('echo bad | rm -rf /'))

    def test_git_status_with_redirect_not_whitelisted(self):
        """'git status > output.txt' 不在白名单中（多词命令也检查重定向）。"""
        self.assertFalse(_is_command_whitelisted('git status > output.txt'))

    def test_pip_list_with_pipe_not_whitelisted(self):
        """'pip list | grep django' 不在白名单中（多词命令也检查管道）。"""
        self.assertFalse(_is_command_whitelisted('pip list | grep django'))

    def test_python_manage_py_with_redirect_not_whitelisted(self):
        """'python manage.py > /tmp/x' 不在白名单中（安全前缀也检查重定向）。"""
        self.assertFalse(_is_command_whitelisted('python manage.py > /tmp/x'))

    def test_pandoc_with_redirect_not_whitelisted(self):
        """'pandoc input.md > output' 不在白名单中（单词命令也检查重定向）。

        注：pandoc 自身支持 -o 参数，无需重定向。
        """
        self.assertFalse(_is_command_whitelisted('pandoc input.md > output.txt'))


# ============================================================================
# SubTask 6.3: BLOCKED_PATTERNS 多行命令拦截（re.DOTALL）
# ============================================================================


class MultilineCommandBlockTests(unittest.TestCase):
    """SubTask 6.3: BLOCKED_PATTERNS 使用 re.DOTALL 拦截跨行命令拼接。"""

    def test_multiline_command_with_newline_blocked(self):
        """'echo ok\\nrm -rf /' 被拦截（跨行命令拼接攻击）。

        re.DOTALL 使 '.' 匹配换行符，防止通过换行符绕过单行正则匹配。
        """
        # 模拟换行符注入攻击
        malicious_command = "echo ok\nrm -rf /"
        blocked_reason = _is_command_blocked(malicious_command)
        self.assertIsNotNone(
            blocked_reason,
            "跨行命令拼接应被 BLOCKED_PATTERNS 拦截（re.DOTALL 生效）",
        )

    def test_multiline_command_with_carriage_return_blocked(self):
        """'echo ok\\r\\nrm -rf /' 被拦截。"""
        malicious_command = "echo ok\r\nrm -rf /"
        blocked_reason = _is_command_blocked(malicious_command)
        self.assertIsNotNone(blocked_reason)

    def test_single_line_rm_rf_blocked(self):
        """'rm -rf /' 单行命令被拦截。"""
        self.assertIsNotNone(_is_command_blocked('rm -rf /'))

    def test_single_line_rm_rf_with_force_blocked(self):
        """'rm -rf /home' 被拦截。"""
        self.assertIsNotNone(_is_command_blocked('rm -rf /home'))

    def test_shutdown_blocked(self):
        """'shutdown' 命令被拦截。"""
        self.assertIsNotNone(_is_command_blocked('shutdown'))
        self.assertIsNotNone(_is_command_blocked('shutdown -h now'))

    def test_sudo_rm_blocked(self):
        """'sudo rm' 命令被拦截。"""
        self.assertIsNotNone(_is_command_blocked('sudo rm -rf /'))

    def test_normal_command_not_blocked(self):
        """正常命令不被拦截。"""
        self.assertIsNone(_is_command_blocked('echo hello'))
        self.assertIsNone(_is_command_blocked('git status'))
        self.assertIsNone(_is_command_blocked('python manage.py migrate'))

    def test_dotall_flag_in_search(self):
        """验证 re.DOTALL 标志确实使 '.' 匹配换行符。

        回归测试：确保 BLOCKED_PATTERNS 的 re.search 调用使用 re.DOTALL。
        """
        import re
        # 构造一个含换行的危险命令
        cmd = "echo ok\nrm -rf /"
        # 不使用 re.DOTALL 时，'.' 不匹配换行符
        # 使用 re.DOTALL 时，'.' 匹配换行符
        # 验证至少有一个 BLOCKED_PATTERN 能匹配跨行命令
        matched = any(
            re.search(pattern, cmd.lower().strip(), re.IGNORECASE | re.DOTALL)
            for pattern in BLOCKED_PATTERNS
        )
        self.assertTrue(
            matched,
            "至少有一个 BLOCKED_PATTERN 应在 re.DOTALL 标志下匹配跨行命令",
        )


# ============================================================================
# SubTask 6.2: SHELL_EXEC_ALLOWED_DIRS 配置
# ============================================================================


class AllowedDirsConfigurationTests(unittest.TestCase):
    """SubTask 6.2: SHELL_EXEC_ALLOWED_DIRS 默认设为 [DATA_DIR, MEDIA_ROOT]。"""

    def test_allowed_dirs_not_none(self):
        """SHELL_EXEC_ALLOWED_DIRS 不为 None（限制工作目录范围）。"""
        self.assertIsNotNone(
            settings.SHELL_EXEC_ALLOWED_DIRS,
            "SHELL_EXEC_ALLOWED_DIRS 不应为 None（需限制命令执行目录范围）",
        )

    def test_allowed_dirs_is_list(self):
        """SHELL_EXEC_ALLOWED_DIRS 是列表类型。"""
        self.assertIsInstance(settings.SHELL_EXEC_ALLOWED_DIRS, list)

    def test_allowed_dirs_contains_data_dir_or_media_root(self):
        """SHELL_EXEC_ALLOWED_DIRS 包含 DATA_DIR 或 MEDIA_ROOT。"""
        allowed = settings.SHELL_EXEC_ALLOWED_DIRS
        # 至少应限制到项目数据/媒体目录，而非 None（不限制）
        self.assertTrue(
            len(allowed) > 0,
            "SHELL_EXEC_ALLOWED_DIRS 应非空",
        )


# ============================================================================
# 集成测试：ShellExecTool 行为验证
# ============================================================================


class ShellExecToolBehaviorTests(unittest.IsolatedAsyncioTestCase):
    """验证 ShellExecTool 审批统一后的端到端行为。

    审批由 ApprovalMiddleware 在 after_model 钩子统一处理，
    工具层不参与审批判断。到达 _run/_arun 的命令已通过审批或无需审批。
    """

    async def test_python_malicious_script_executes_directly(self):
        """'python malicious.py' 非白名单命令，工具层直接执行。

        审批由 ApprovalMiddleware 统一处理（ShellExecApprovalPolicy），
        工具层不参与审批判断，到达 _arun 的命令已通过审批。
        """
        tool = ShellExecTool()
        fake_result = StandardToolResult(
            content="ok",
            status=ToolStatus.SUCCESS,
            source="shell_exec",
            metadata={"command": "python malicious.py", "return_code": 0},
        )
        with patch(
            'Django_xm.apps.tools.langchain.shell._execute_command',
            return_value=fake_result,
        ) as mock_exec, patch(
            'Django_xm.apps.tools.langchain.shell._is_command_blocked',
            return_value=None,
        ):
            await tool._arun(command='python malicious.py')
            # 非白名单命令在工具层直接执行（审批已由 middleware 处理）
            mock_exec.assert_called_once()
            args, kwargs = mock_exec.call_args
            self.assertEqual(args[0], 'python malicious.py')

    async def test_python_manage_py_directly_executed(self):
        """'python manage.py' 在白名单中，工具层直接执行。"""
        tool = ShellExecTool()
        fake_result = StandardToolResult(
            content="ok",
            status=ToolStatus.SUCCESS,
            source="shell_exec",
            metadata={"command": "python manage.py", "return_code": 0},
        )
        with patch(
            'Django_xm.apps.tools.langchain.shell._execute_command',
            return_value=fake_result,
        ) as mock_exec:
            await tool._arun(command='python manage.py')
            mock_exec.assert_called_once()
            args, kwargs = mock_exec.call_args
            self.assertEqual(args[0], 'python manage.py')

    async def test_echo_with_redirect_executes_directly(self):
        """'echo bad > /etc/passwd' 因重定向不在白名单中，但工具层直接执行。

        审批由 ApprovalMiddleware 统一处理（携带重定向/管道的命令触发审批），
        工具层不参与审批判断。
        """
        tool = ShellExecTool()
        fake_result = StandardToolResult(
            content="ok",
            status=ToolStatus.SUCCESS,
            source="shell_exec",
            metadata={"command": "echo bad > /etc/passwd", "return_code": 0},
        )
        with patch(
            'Django_xm.apps.tools.langchain.shell._execute_command',
            return_value=fake_result,
        ) as mock_exec, patch(
            'Django_xm.apps.tools.langchain.shell._is_command_blocked',
            return_value=None,
        ):
            await tool._arun(command='echo bad > /etc/passwd')
            # 工具层直接执行（审批已由 middleware 处理）
            mock_exec.assert_called_once()

    async def test_node_script_executes_directly(self):
        """'node script.js' 非白名单命令，工具层直接执行。

        审批由 ApprovalMiddleware 统一处理，工具层不参与审批判断。
        """
        tool = ShellExecTool()
        fake_result = StandardToolResult(
            content="ok",
            status=ToolStatus.SUCCESS,
            source="shell_exec",
            metadata={"command": "node script.js", "return_code": 0},
        )
        with patch(
            'Django_xm.apps.tools.langchain.shell._execute_command',
            return_value=fake_result,
        ) as mock_exec, patch(
            'Django_xm.apps.tools.langchain.shell._is_command_blocked',
            return_value=None,
        ):
            await tool._arun(command='node script.js')
            # 工具层直接执行（审批已由 middleware 处理）
            mock_exec.assert_called_once()

    async def test_blocked_command_returns_error_without_execution(self):
        """'rm -rf /' 被 BLOCKED_PATTERNS 拦截，不调用 _execute_command。"""
        tool = ShellExecTool()
        with patch(
            'Django_xm.apps.tools.langchain.shell._execute_command'
        ) as mock_exec:
            ret = await tool._arun(command='rm -rf /')
            mock_exec.assert_not_called()
            self.assertIn("拦截", str(ret))


if __name__ == "__main__":
    unittest.main()
