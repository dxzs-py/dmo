"""shell/工具安全加固测试（fix-review-round3 Task 1：1.1/1.3/1.4/1.5/1.6/1.7/1.9/1.10/1.11/1.12）。

对应 tasks.md Task 1：
- 1.1 curl 白名单收紧：输出/配置 flag、URL 协议白名单、输出目标同源校验
- 1.3 自定义工具换码审核回退：PUT 改码 → approval_status=pending + 响应携带
- 1.4 shell 相对路径绕过：相对路径 resolve 后校验，`cat .env` 不得免审批
- 1.5 _get_allowed_exec_dirs 空 → fail-closed + error 留痕
- 1.6 pandoc 参数级校验；agent-browser / python -m pytest·unittest 移出白名单
- 1.7 受限命名空间：pathlib 剔除、精确模块匹配（urllib.request 同根逃逸封堵）
- 1.9 MCP token 加密字段：密钥缺失拒绝保存（不落明文）
- 1.10 僵尸进程：_reap_after_kill wait + SIGKILL 兜底
- 1.11 attachment_rag_search 返回值包裹 wrap_external_content
- 1.12 注册一致性：导出/装配/审批策略/只读白名单五处集合一致

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.tools.tests.test_tool_security_round3 --noinput
"""

import os
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from Django_xm.apps.agent_hub.approval.policies import (
    READ_ONLY_TOOL_WHITELIST,
    ShellExecApprovalPolicy,
    UnregisteredToolApprovalPolicy,
)
from Django_xm.apps.agent_hub.approval.step_gate import resolve_tool_policy
from Django_xm.apps.tools.langchain.shell import (
    _get_allowed_exec_dirs,
    _is_command_whitelisted,
    _reap_after_kill,
)

# ────────────────────────────────────────────────────────────────────────
# 1.1 curl 白名单收紧
# ────────────────────────────────────────────────────────────────────────


class CurlTighteningTests(SimpleTestCase):
    """R3 1.1：curl 输出/配置 flag、URL 协议白名单、输出目标同源校验。"""

    def setUp(self):
        self.policy = ShellExecApprovalPolicy()

    def _override(self, **kw):
        overrides = {"SHELL_EXEC_WHITELIST": ["curl"]}
        overrides.update(kw)
        return override_settings(**overrides)

    def test_output_flags_require_approval(self):
        """-o/--output/--output-dir/-O/-J/-K/--config 及粘连形态 → 走审批。"""
        commands = [
            "curl https://example.com -o /tmp/out.html",
            "curl -o/tmp/evil.sh https://example.com",
            "curl https://example.com --output /tmp/out.html",
            "curl --output=/tmp/evil.sh https://example.com",
            "curl --output-dir /tmp https://example.com",
            "curl -O https://example.com/payload.sh",
            "curl -J -O https://example.com/payload.sh",
            "curl -K /tmp/commands.cfg https://example.com",
            "curl --config /tmp/commands.cfg https://example.com",
            "curl --config=/tmp/commands.cfg https://example.com",
            "curl -sSo /tmp/evil.sh https://example.com",
            "curl -OJ https://example.com/payload.sh",
            "curl -c cookies.txt https://example.com",
            "curl -D /tmp/headers.txt https://example.com",
        ]
        with self._override():
            for cmd in commands:
                self.assertFalse(_is_command_whitelisted(cmd), f"应走审批: {cmd!r}")
                self.assertTrue(self.policy.should_approve({"command": cmd}), f"应判需审批: {cmd!r}")

    def test_output_target_within_allowed_dirs_whitelisted(self):
        """1.1③：-o 输出目标在允许目录内 → 仍免审批（写目标同源校验通过）。"""
        with tempfile.TemporaryDirectory() as tmp, self._override(SHELL_EXEC_ALLOWED_DIRS=[tmp], DATA_DIR=tmp):
            self.assertTrue(_is_command_whitelisted(f"curl https://example.com -o {tmp}/out.html"))
            self.assertTrue(_is_command_whitelisted("curl https://example.com -o out.html", working_dir=tmp))

    def test_output_target_outside_allowed_dirs_requires_approval(self):
        """-o 输出目标越界 → 走审批（同源校验失败）。"""
        with tempfile.TemporaryDirectory() as tmp, self._override(SHELL_EXEC_ALLOWED_DIRS=[tmp], DATA_DIR=tmp):
            self.assertFalse(_is_command_whitelisted("curl https://example.com -o /etc/cron.d/evil"))
            self.assertFalse(_is_command_whitelisted("curl https://example.com -o ../escape.html", working_dir=tmp))

    def test_url_scheme_whitelist(self):
        """URL 协议白名单 http/https：file/gopher/dict/ftp/telnet/ldap 等全部拦截。"""
        commands = [
            "curl file:///etc/passwd",
            "curl gopher://127.0.0.1:6379/_INFO",
            "curl dict://127.0.0.1:6379/CONFIG",
            "curl ftp://evil.com/file",
            "curl telnet://127.0.0.1:6379",
            "curl ldap://evil.com/x",
            "curl sftp://evil.com/x",
        ]
        with self._override():
            for cmd in commands:
                self.assertFalse(_is_command_whitelisted(cmd), f"应走审批: {cmd!r}")

    def test_http_https_and_schemeless_urls_whitelisted(self):
        """防误伤：http/https/无 scheme（curl 缺省按 http）仍放行。"""
        with self._override():
            self.assertTrue(_is_command_whitelisted("curl http://example.com"))
            self.assertTrue(_is_command_whitelisted("curl https://example.com/a?b=1"))
            self.assertTrue(_is_command_whitelisted("curl example.com"))

    def test_value_flags_not_treated_as_url(self):
        """防误伤：取值 flag（-H/-m/-A 等）的值不参与协议校验。"""
        with self._override():
            self.assertTrue(_is_command_whitelisted("curl -H 'X-Debug: 1' https://example.com"))
            self.assertTrue(_is_command_whitelisted("curl -m 30 https://example.com"))
            self.assertTrue(_is_command_whitelisted("curl -sS -L https://example.com"))

    def test_xget_glued_forms_no_regression(self):
        """回归：-XGET 粘连既有放行用例不回退（round2 语义保持）。"""
        with self._override():
            self.assertTrue(_is_command_whitelisted("curl -XGET https://example.com"))
            self.assertTrue(_is_command_whitelisted("curl -X GET https://example.com"))
            self.assertTrue(_is_command_whitelisted("curl --request=GET https://example.com"))


# ────────────────────────────────────────────────────────────────────────
# 1.4 相对路径绕过 + 1.5 空 allowed dirs fail-closed
# ────────────────────────────────────────────────────────────────────────


class RelativePathBypassTests(SimpleTestCase):
    """R3 1.4：相对路径 resolve 后校验允许目录；敏感文件一律走审批。"""

    def setUp(self):
        self.policy = ShellExecApprovalPolicy()

    def test_relative_path_outside_allowed_dirs_requires_approval(self):
        """cwd 不在允许目录时，相对路径不再免审批（原恒 True 的绕过封堵）。"""
        with (
            tempfile.TemporaryDirectory() as tmp,
            override_settings(SHELL_EXEC_WHITELIST=["cat"], SHELL_EXEC_ALLOWED_DIRS=[tmp], DATA_DIR=tmp),
        ):
            self.assertFalse(
                _is_command_whitelisted("cat .env"),
                "相对路径 resolve 后越界（cwd 不在允许目录）应走审批",
            )
            self.assertTrue(self.policy.should_approve({"command": "cat .env"}))

    def test_relative_path_within_working_dir_whitelisted(self):
        """working_dir 在允许目录内：相对路径 resolve 后命中 → 放行（防误伤）。"""
        with (
            tempfile.TemporaryDirectory() as tmp,
            override_settings(SHELL_EXEC_WHITELIST=["cat"], SHELL_EXEC_ALLOWED_DIRS=[tmp], DATA_DIR=tmp),
        ):
            self.assertTrue(_is_command_whitelisted("cat notes.txt", working_dir=tmp))

    def test_sensitive_file_requires_approval_even_within_allowed_dirs(self):
        """`cat .env` 类敏感文件即使在允许目录内也走审批。"""
        with (
            tempfile.TemporaryDirectory() as tmp,
            override_settings(SHELL_EXEC_WHITELIST=["cat", "type"], SHELL_EXEC_ALLOWED_DIRS=[tmp], DATA_DIR=tmp),
        ):
            self.assertFalse(_is_command_whitelisted("cat .env", working_dir=tmp))
            self.assertFalse(_is_command_whitelisted("cat .env.local", working_dir=tmp))
            self.assertFalse(_is_command_whitelisted("cat config/id_rsa", working_dir=tmp))
            self.assertFalse(_is_command_whitelisted("cat server.pem", working_dir=tmp))
            self.assertTrue(self.policy.should_approve({"command": "cat .env"}))
            # 防误伤：普通文件不受影响
            self.assertTrue(_is_command_whitelisted("cat app.log", working_dir=tmp))

    def test_assess_risk_passes_working_dir(self):
        """ShellExecApprovalPolicy.assess_risk 透传 working_dir（审批与执行同源）。

        注：白名单含 cat 与 type——Windows 上平台适配把 cat 改写为 type
        后再判定（适配前移语义），自定义白名单需两者都登记。
        """
        with tempfile.TemporaryDirectory() as tmp, override_settings(
            SHELL_EXEC_WHITELIST=["cat", "type"], SHELL_EXEC_ALLOWED_DIRS=[tmp], DATA_DIR=tmp
        ):
            # cwd（测试进程目录）不在允许目录 → 无 working_dir 需审批
            self.assertTrue(self.policy.should_approve({"command": "cat notes.txt"}))
            # working_dir 在允许目录 → 免审批
            self.assertFalse(self.policy.should_approve({"command": "cat notes.txt", "working_dir": tmp}))


class EmptyAllowedDirsFailClosedTests(SimpleTestCase):
    """R3 1.5：SHELL_EXEC_ALLOWED_DIRS/DATA_DIR 均空 → fail-closed + error 留痕。"""

    def test_empty_allowed_dirs_rejects_read_commands(self):
        with override_settings(SHELL_EXEC_WHITELIST=["cat"], SHELL_EXEC_ALLOWED_DIRS=[], DATA_DIR=None):
            self.assertEqual(_get_allowed_exec_dirs(), [])
            with self.assertLogs("Django_xm.apps.tools.langchain.shell", level="ERROR") as logs:
                self.assertFalse(_is_command_whitelisted("cat any.txt"))
            self.assertIn("fail-closed", "".join(logs.output))

    def test_empty_allowed_dirs_rejects_curl_output(self):
        with override_settings(SHELL_EXEC_WHITELIST=["curl"], SHELL_EXEC_ALLOWED_DIRS=[], DATA_DIR=None):
            self.assertFalse(_is_command_whitelisted("curl https://example.com -o out.html"))


# ────────────────────────────────────────────────────────────────────────
# 1.6 pandoc 参数级校验 + agent-browser/pytest 移出白名单
# ────────────────────────────────────────────────────────────────────────


class WhitelistScopeTests(SimpleTestCase):
    """R3 1.6：白名单范围收缩。"""

    def setUp(self):
        self.policy = ShellExecApprovalPolicy()

    def test_pandoc_stdout_conversion_whitelisted(self):
        """防误伤：stdout 纯转换（无 -o）+ 输入文件在允许目录内 → 放行。"""
        with tempfile.TemporaryDirectory() as tmp, override_settings(
            SHELL_EXEC_WHITELIST=["pandoc"], SHELL_EXEC_ALLOWED_DIRS=[tmp], DATA_DIR=tmp
        ):
            self.assertTrue(_is_command_whitelisted(f"pandoc {tmp}/in.md -f markdown -t html"))
            self.assertTrue(_is_command_whitelisted(f"pandoc -s {tmp}/in.md", working_dir=tmp))

    def test_pandoc_output_flags_require_approval(self):
        """pandoc -o/--output 写文件与 --lua-filter/--filter 代码执行面走审批。"""
        with tempfile.TemporaryDirectory() as tmp, override_settings(
            SHELL_EXEC_WHITELIST=["pandoc"], SHELL_EXEC_ALLOWED_DIRS=[tmp], DATA_DIR=tmp
        ):
            for cmd in [
                f"pandoc {tmp}/in.md -o {tmp}/out.html",
                f"pandoc {tmp}/in.md --output {tmp}/out.html",
                f"pandoc {tmp}/in.md --lua-filter {tmp}/evil.lua",
                f"pandoc {tmp}/in.md --filter {tmp}/evil.py",
            ]:
                self.assertFalse(_is_command_whitelisted(cmd), f"应走审批: {cmd!r}")

    def test_pandoc_input_outside_allowed_dirs_requires_approval(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(
            SHELL_EXEC_WHITELIST=["pandoc"], SHELL_EXEC_ALLOWED_DIRS=[tmp], DATA_DIR=tmp
        ):
            self.assertFalse(_is_command_whitelisted("pandoc /etc/secret.md -t html"))

    def test_agent_browser_removed_from_whitelist(self):
        """agent-browser（截图写文件/页面内 JS 执行）移出白名单 → CONTROLLED。"""
        self.assertTrue(self.policy.should_approve({"command": "agent-browser open https://example.com"}))
        self.assertFalse(_is_command_whitelisted("agent-browser open https://example.com"))

    def test_pytest_unittest_removed_from_whitelist(self):
        """python -m pytest/unittest（测试即任意代码执行）移出白名单 → CONTROLLED。"""
        for cmd in [
            "python -m pytest",
            "python -m pytest data/uploads/evil_test.py",
            "python3 -m pytest",
            "python -m unittest",
            "python3 -m unittest",
        ]:
            self.assertFalse(_is_command_whitelisted(cmd), f"应走审批: {cmd!r}")
            self.assertTrue(self.policy.should_approve({"command": cmd}))

    def test_manage_py_test_no_regression(self):
        """manage.py test（点分模块路径约束，不能指向任意文件）仍放行。"""
        self.assertTrue(_is_command_whitelisted("python manage.py test Django_xm.apps.tools --noinput"))


# ────────────────────────────────────────────────────────────────────────
# 1.7 受限命名空间 pathlib 逃逸
# ────────────────────────────────────────────────────────────────────────


class RestrictedNamespaceTests(SimpleTestCase):
    """R3 1.7：pathlib 剔除 + 精确模块匹配。"""

    def _validate(self, code: str):
        from Django_xm.apps.tools import _validate_tool_code_safety

        return _validate_tool_code_safety(code)

    def test_pathlib_import_rejected(self):
        """pathlib 整体剔除：`from pathlib import Path` → 拒绝（Path.read_text 逃逸封堵）。"""
        is_safe, msg = self._validate(
            "from pathlib import Path\n"
            "from langchain_core.tools import tool\n"
            "@tool\ndef t() -> str:\n    return Path('/etc/passwd').read_text()\n"
        )
        self.assertFalse(is_safe)
        self.assertIn("pathlib", msg)

    def test_urllib_request_rejected_exact_match(self):
        """精确模块匹配：urllib.parse 白名单不再放行 urllib.request（同根逃逸封堵）。"""
        is_safe, msg = self._validate("import urllib.request\n")
        self.assertFalse(is_safe)
        self.assertIn("urllib.request", msg)

    def test_urllib_parse_still_allowed(self):
        """防误伤：精确命中的 urllib.parse 仍放行。"""
        is_safe, _ = self._validate("import urllib.parse\nfrom urllib.parse import quote\n")
        self.assertTrue(is_safe)

    def test_plain_allowed_imports_still_allowed(self):
        is_safe, _ = self._validate("import json\nimport math\nfrom datetime import datetime\n")
        self.assertTrue(is_safe)


# ────────────────────────────────────────────────────────────────────────
# 1.9 MCP token 加密字段拒绝明文落库
# ────────────────────────────────────────────────────────────────────────


class EncryptedFieldRejectsPlaintextTests(SimpleTestCase):
    """R3 1.9：FIELD_ENCRYPTION_KEY 缺失 → get_prep_value 抛错拒绝保存。"""

    def _field(self):
        from Django_xm.apps.tools.fields import EncryptedCharField

        return EncryptedCharField(max_length=500)

    def test_missing_key_rejects_save(self):
        field = self._field()
        env = {k: v for k, v in os.environ.items() if k != "FIELD_ENCRYPTION_KEY"}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                field.get_prep_value("plaintext-token")
            self.assertIn("FIELD_ENCRYPTION_KEY", str(ctx.exception))
            self.assertIn("重新录入", str(ctx.exception))

    def test_with_key_encrypts_plaintext(self):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        field = self._field()
        with mock.patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": key}):
            stored = field.get_prep_value("plaintext-token")
            self.assertNotEqual(stored, "plaintext-token")
            self.assertEqual(Fernet(key.encode()).decrypt(stored.encode()).decode(), "plaintext-token")

    def test_with_key_roundtrips_existing_token(self):
        """已是合法 Fernet token 的值不重复加密。"""
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        token = Fernet(key.encode()).encrypt(b"plain").decode()
        field = self._field()
        with mock.patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": key}):
            self.assertEqual(field.get_prep_value(token), token)


# ────────────────────────────────────────────────────────────────────────
# 1.10 僵尸进程兜底回收
# ────────────────────────────────────────────────────────────────────────


class _FakeProcess:
    """_reap_after_kill 夹具：可控的 wait 行为记录。"""

    def __init__(self, wait_results: list, pid: int = 4242):
        self.pid = pid
        self._wait_results = list(wait_results)
        self.calls: list[str] = []

    def wait(self, timeout=None):
        self.calls.append(f"wait({timeout})")
        result = self._wait_results.pop(0)
        if result == "timeout":
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
        return result

    def kill(self):
        self.calls.append("kill")


class ReapAfterKillTests(SimpleTestCase):
    """R3 1.10：强杀后兜底回收（wait + SIGKILL 升级）。"""

    def test_wait_success_returns_without_kill(self):
        proc = _FakeProcess(wait_results=[0])
        _reap_after_kill(proc, is_windows=True)
        self.assertEqual(proc.calls, ["wait(5)"])

    def test_wait_timeout_escalates_to_kill_then_waits(self):
        """wait 超时 → process.kill()（Windows 路径）→ 再 wait。"""
        proc = _FakeProcess(wait_results=["timeout", 0])
        _reap_after_kill(proc, is_windows=True)
        self.assertEqual(proc.calls, ["wait(5)", "kill", "wait(5)"])

    def test_unix_killpg_sigkill_fallback(self):
        """Unix：SIGTERM 后仍存活 → killpg SIGKILL 升级 → 再 wait。"""
        import signal

        proc = _FakeProcess(wait_results=["timeout", 0])
        fake_killpg_calls: list = []
        sigkill_sentinel = object()  # Windows 无 SIGKILL，注入哨兵对象

        def fake_killpg(_pgid, sig):
            fake_killpg_calls.append(sig)

        # create=True：Windows 无 os.getpgid/os.killpg/signal.SIGKILL，patch 后 hasattr 为真
        with mock.patch("os.getpgid", return_value=1234, create=True), mock.patch.object(
            os, "killpg", fake_killpg, create=True
        ), mock.patch.object(signal, "SIGKILL", sigkill_sentinel, create=True):
            _reap_after_kill(proc, is_windows=False)
        self.assertEqual(fake_killpg_calls, [sigkill_sentinel])
        self.assertEqual(proc.calls, ["wait(5)", "wait(5)"])

    def test_unrecoverable_process_logs_error(self):
        """极端情况：SIGKILL 后仍不退出 → error 留痕（不抛异常）。"""
        proc = _FakeProcess(wait_results=["timeout", "timeout"])
        with mock.patch("os.getpgid", return_value=1234, create=True), mock.patch.object(
            os, "killpg", lambda *_: None, create=True
        ), self.assertLogs("Django_xm.apps.tools.langchain.shell", level="ERROR") as logs:
            _reap_after_kill(proc, is_windows=False)
        self.assertTrue(any("未退出" in line for line in logs.output))


# ────────────────────────────────────────────────────────────────────────
# 1.11 attachment_rag_search 外部内容信封
# ────────────────────────────────────────────────────────────────────────


class AttachmentRagWrapTests(SimpleTestCase):
    """R3 1.11：检索结果进模型上下文前包裹 wrap_external_content。"""

    def _invoke_with_stubbed_retrieval(self, pipeline_result: str) -> str:
        from Django_xm.apps.attachments import models as attachments_models
        from Django_xm.apps.tools.langchain.attachment_rag import attachment_rag_search

        fake_att = SimpleNamespace(id=7, message_id=11, created_at=None)
        fake_attachment_model = mock.MagicMock()
        fake_attachment_model.objects.filter.return_value.select_related.return_value.order_by.return_value = [
            fake_att
        ]

        fake_pipeline = mock.MagicMock()
        fake_pipeline.return_value.search.return_value = pipeline_result

        with (
            mock.patch(
                "Django_xm.apps.tools.langchain.attachment_rag.get_chat_session_exists_provider",
                return_value=lambda _sid: True,
            ),
            mock.patch.object(attachments_models, "ChatAttachment", fake_attachment_model),
            mock.patch(
                "Django_xm.apps.tools.langchain.file_reader.get_attachment_info",
                return_value={"is_image": False},
            ),
            mock.patch(
                "Django_xm.apps.knowledge.services.rag_retrieval.load_attachment_documents",
                return_value=([], 0),
            ),
            mock.patch(
                "Django_xm.apps.knowledge.services.rag_retrieval.UnifiedRagPipeline",
                fake_pipeline,
            ),
        ):
            return attachment_rag_search.invoke("检索词", config={"configurable": {"thread_id": "sess-wrap"}})

    def test_result_wrapped_in_external_content_envelope(self):
        from Django_xm.apps.tools.langchain.external_content import (
            EXTERNAL_CONTENT_END,
            EXTERNAL_CONTENT_START,
        )

        result = self._invoke_with_stubbed_retrieval("片段1 ... 片段2")
        self.assertIn("会话附件检索结果", result)
        self.assertIn(EXTERNAL_CONTENT_START.format(source="会话附件检索结果 (query=检索词)"), result)
        self.assertIn("片段1", result)
        self.assertTrue(result.strip().endswith(EXTERNAL_CONTENT_END))

    def test_error_branch_not_wrapped(self):
        """错误分支为系统生成文案，不包裹信封。"""
        from Django_xm.apps.attachments import models as attachments_models
        from Django_xm.apps.tools.langchain.attachment_rag import attachment_rag_search
        from Django_xm.apps.tools.langchain.external_content import EXTERNAL_CONTENT_START

        fake_attachment_model = mock.MagicMock()
        fake_attachment_model.objects.filter.side_effect = RuntimeError("db down")

        with (
            mock.patch(
                "Django_xm.apps.tools.langchain.attachment_rag.get_chat_session_exists_provider",
                return_value=lambda _sid: True,
            ),
            mock.patch.object(attachments_models, "ChatAttachment", fake_attachment_model),
        ):
            result = attachment_rag_search.invoke("q", config={"configurable": {"thread_id": "sess-err"}})
        self.assertIn("错误：查询会话附件列表失败", result)
        self.assertNotIn(EXTERNAL_CONTENT_START[:12], result)


# ────────────────────────────────────────────────────────────────────────
# 1.12 注册一致性校验
# ────────────────────────────────────────────────────────────────────────


class ToolRegistryConsistencyTests(SimpleTestCase):
    """R3 1.12：五处注册集合一致性——漏注册即测试失败。

    集合：langchain/__init__ 工具导出 / __all__ / get_all_advanced_tools 装配 /
    审批策略注册 / READ_ONLY_TOOL_WHITELIST。
    显式豁免清单见模块常量（deepagents 框架工具、动态创建工具、系统编排工具）。
    """

    # deepagents 框架工具（ builders 动态装配，policy 已注册但不在 tools 装配集）
    DEEPAGENTS_EXEMPT: frozenset = frozenset(
        {"ls", "glob", "grep", "read_file", "write_file", "edit_file", "execute", "write_todos"}
    )
    # 动态创建工具（agent 构建期按用户/知识库实例化，无模块级单例）
    DYNAMIC_TOOLS: frozenset = frozenset({"activate_skill", "read_skill_resource", "knowledge_base"})
    # 系统编排工具（非审批类 interrupt，显式豁免：SYSTEM_ORCHESTRATION_TOOLS）
    ORCHESTRATION_EXEMPT: frozenset = frozenset({"wait_for_subagent"})

    @classmethod
    def _collect(cls):
        from langchain_core.tools import BaseTool

        from Django_xm.apps.agent_hub.approval.middleware import ApprovalMiddleware
        from Django_xm.apps.tools import get_all_advanced_tools
        from Django_xm.apps.tools import langchain as langchain_module
        from Django_xm.apps.tools.registry import get_extension_tools

        exported_singles = {
            name
            for name in dir(langchain_module)
            if isinstance(getattr(langchain_module, name, None), BaseTool)
        }
        assembled = {t.name for t in get_all_advanced_tools()}
        extension = {t.name for t in get_extension_tools()}
        policy_names = {
            p.tool_name for p in ApprovalMiddleware._default_policies() if getattr(p, "tool_name", "")
        }
        return set(langchain_module.__all__), exported_singles, assembled, extension, policy_names

    def test_all_names_resolvable(self):
        """__all__ 无陈旧条目：每个名字都可从模块解析。"""
        from Django_xm.apps.tools import langchain as langchain_module

        for name in langchain_module.__all__:
            self.assertTrue(hasattr(langchain_module, name), f"__all__ 陈旧条目: {name}")

    def test_tool_singletons_covered_by_all(self):
        """模块级 BaseTool 单例必须出现在 __all__（导出纪律）。"""
        from langchain_core.tools import BaseTool

        from Django_xm.apps.tools import langchain as langchain_module

        singles = {
            name
            for name in dir(langchain_module)
            if isinstance(getattr(langchain_module, name, None), BaseTool)
        }
        missing = singles - set(langchain_module.__all__)
        self.assertEqual(missing, set(), f"工具单例未导出到 __all__: {sorted(missing)}")

    def test_assembled_tools_are_exported_or_extension(self):
        """get_all_advanced_tools 装配集 ⊆ 模块导出单例 ∪ 扩展注册表（无影子工具）。"""
        _, exported, assembled, extension, _ = self._collect()
        shadow = assembled - exported - extension
        self.assertEqual(shadow, set(), f"装配集包含未导出工具: {sorted(shadow)}")

    def test_every_tool_has_approval_registration(self):
        """每个真实工具（导出/装配/扩展）必须有审批策略或只读白名单注册。"""
        _, exported, assembled, extension, policy_names = self._collect()
        real_tools = exported | assembled | extension
        unregistered = real_tools - policy_names - self.DEEPAGENTS_EXEMPT - self.ORCHESTRATION_EXEMPT
        self.assertEqual(unregistered, set(), f"未注册审批策略的工具: {sorted(unregistered)}")

    def test_every_policy_targets_real_tool(self):
        """审批策略不得指向不存在的工具（死策略 = 静默放行幻觉）。"""
        _, exported, assembled, extension, policy_names = self._collect()
        real_tools = exported | assembled | extension | self.DYNAMIC_TOOLS | self.DEEPAGENTS_EXEMPT
        dead = policy_names - real_tools
        self.assertEqual(dead, set(), f"指向不存在工具的死策略: {sorted(dead)}")

    def test_whitelist_targets_real_tools(self):
        """只读白名单条目必须为真实工具（导出单例或动态创建）。"""
        _, exported, _, _, _ = self._collect()
        unknown = set(READ_ONLY_TOOL_WHITELIST) - exported - self.DYNAMIC_TOOLS
        self.assertEqual(unknown, set(), f"只读白名单含未知工具: {sorted(unknown)}")

    def test_shell_policy_resolution_parity(self):
        """step_gate.resolve_tool_policy 与 middleware 策略表同源（1.2 配套断言）。"""
        self.assertIsInstance(resolve_tool_policy("shell_exec"), ShellExecApprovalPolicy)
        self.assertIsInstance(resolve_tool_policy("no-such-tool-xyz"), UnregisteredToolApprovalPolicy)
        self.assertIsNone(resolve_tool_policy("wait_for_subagent"))


# ────────────────────────────────────────────────────────────────────────
# 1.3 自定义工具换码审核回退（views_mcp.CustomToolDetailView.put）
# ────────────────────────────────────────────────────────────────────────

TOOLS_URL = "/api/v1/tools/"
VALID_TOOL_CODE = (
    "from langchain_core.tools import tool\n"
    "@tool\ndef demo_echo(text: str) -> str:\n"
    '    """回显输入"""\n'
    "    return text\n"
)
CHANGED_TOOL_CODE = (
    "from langchain_core.tools import tool\n"
    "@tool\ndef demo_echo(text: str) -> str:\n"
    '    """回显输入（修改版）"""\n'
    "    return f'echo: {text}'\n"
)


class CustomToolCodeChangeTests(TestCase):
    """R3 1.3：代码变更强制 approval_status 回退 pending，加载链不再执行。"""

    def setUp(self):
        from rest_framework.test import APIClient

        from Django_xm.apps.tools.models import ToolCategory

        self.category, _ = ToolCategory.objects.get_or_create(
            code="general", defaults={"name": "通用"}
        )
        self.user = get_user_model().objects.create_user(username="r3user", password="pw123456")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _create_tool(self, name="r3-tool", approved=True):
        from Django_xm.apps.tools.models import CustomTool

        return CustomTool.objects.create(
            user=self.user,
            name=name,
            code=VALID_TOOL_CODE,
            tool_type="langchain",
            category=self.category,
            source="user",
            status="active",
            approval_status=(
                CustomTool.ApprovalStatus.APPROVED if approved else CustomTool.ApprovalStatus.PENDING
            ),
        )

    def test_code_change_resets_approval_to_pending(self):
        """改码 → approval_status 回退 pending（即使原为 approved）。"""
        self._create_tool()
        resp = self.client.put(
            f"{TOOLS_URL}custom/r3-tool/",
            {"code": CHANGED_TOOL_CODE},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()["data"]
        self.assertEqual(data["approval_status"], "pending")
        self.assertIn("待审核", resp.json()["message"])
        from Django_xm.apps.tools.models import CustomTool

        obj = CustomTool.objects.get(user=self.user, name="r3-tool")
        self.assertEqual(obj.approval_status, CustomTool.ApprovalStatus.PENDING)
        self.assertEqual(obj.code, CHANGED_TOOL_CODE)

    def test_approved_tool_not_executed_after_code_change(self):
        """加载链只查 approved：改码回退后 _load_custom_tools_for_user 不再加载执行。"""
        from Django_xm.apps.tools import _load_custom_tools_for_user

        self._create_tool()
        self.assertEqual(len(_load_custom_tools_for_user(self.user.id, ["r3-tool"])), 1)

        self.client.put(f"{TOOLS_URL}custom/r3-tool/", {"code": CHANGED_TOOL_CODE}, format="json")
        self.assertEqual(
            _load_custom_tools_for_user(self.user.id, ["r3-tool"]),
            [],
            "改码回退 pending 后加载链不得执行该工具",
        )

    def test_desc_only_change_keeps_approval(self):
        """防误伤：仅改 description 不回退审核状态。"""
        self._create_tool()
        resp = self.client.put(
            f"{TOOLS_URL}custom/r3-tool/",
            {"description": "仅改描述"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        from Django_xm.apps.tools.models import CustomTool

        obj = CustomTool.objects.get(user=self.user, name="r3-tool")
        self.assertEqual(obj.approval_status, CustomTool.ApprovalStatus.APPROVED)
        self.assertEqual(resp.json()["data"]["approval_status"], "approved")


if __name__ == "__main__":
    unittest.main()
