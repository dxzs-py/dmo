"""工具审批白名单语义单元测试（spec: fix-tool-approval-and-cross-browser-sync-integrity Task 2）。

验证 ``Django_xm.common.approval_gateway.is_auto_approve`` 的白名单语义：
    - 仅 get_current_time / get_current_date / calculator 可自动通过（纯只读无副作用）
    - shell_exec / fs_write_file / fs_read_file / web_fetch / 未知工具 / None / 空字符串
      一律返回 False（需人工审批）

安全第一原则：未知工具默认需审批，杜绝危险工具绕过审批流程。

运行方式：
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    conda activate langchain_xm
    python -m pytest tests/test_approval_whitelist.py -v
"""

from __future__ import annotations

import os
import unittest

# Django 环境初始化（approval_gateway.py 顶层导入 Django 模型，需 setup）
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django
import django.apps

if not django.apps.apps.ready:
    django.setup()

from Django_xm.common.approval_gateway import (
    AUTO_APPROVE_WHITELIST,
    is_auto_approve,
)


class TestAutoApproveWhitelist(unittest.TestCase):
    """is_auto_approve 白名单语义测试。"""

    # ── 危险工具：必须返回 False（需人工审批） ──────────────────────

    def test_shell_exec_requires_approval(self):
        """shell_exec（命令执行）必须人工审批。"""
        self.assertFalse(is_auto_approve("shell_exec"))

    def test_fs_write_file_requires_approval(self):
        """fs_write_file（文件写入）必须人工审批。"""
        self.assertFalse(is_auto_approve("fs_write_file"))

    def test_fs_read_file_requires_approval(self):
        """fs_read_file（文件读取）必须人工审批。"""
        self.assertFalse(is_auto_approve("fs_read_file"))

    def test_web_fetch_requires_approval(self):
        """web_fetch（网络请求）必须人工审批。"""
        self.assertFalse(is_auto_approve("web_fetch"))

    # ── 白名单工具：返回 True（自动通过） ──────────────────────────

    def test_get_current_time_auto_approved(self):
        """get_current_time（纯只读）可自动通过。"""
        self.assertTrue(is_auto_approve("get_current_time"))

    def test_get_current_date_auto_approved(self):
        """get_current_date（纯只读）可自动通过。"""
        self.assertTrue(is_auto_approve("get_current_date"))

    def test_calculator_auto_approved(self):
        """calculator（纯只读）可自动通过。"""
        self.assertTrue(is_auto_approve("calculator"))

    # ── 未知 / 边界值：必须返回 False（安全第一） ──────────────────

    def test_unknown_tool_requires_approval(self):
        """未知工具必须人工审批（安全第一）。"""
        self.assertFalse(is_auto_approve("unknown_tool"))

    def test_empty_string_requires_approval(self):
        """空字符串工具名必须人工审批。"""
        self.assertFalse(is_auto_approve(""))

    def test_none_requires_approval(self):
        """None 工具名必须人工审批。"""
        self.assertFalse(is_auto_approve(None))

    # ── 白名单常量完整性 ───────────────────────────────────────────

    def test_whitelist_contains_exactly_three_tools(self):
        """白名单仅包含 3 个纯只读工具，不多不少。"""
        self.assertEqual(len(AUTO_APPROVE_WHITELIST), 3)
        self.assertEqual(
            AUTO_APPROVE_WHITELIST,
            frozenset({"get_current_time", "get_current_date", "calculator"}),
        )

    def test_dangerous_tools_not_in_whitelist(self):
        """危险工具不在白名单中。"""
        dangerous_tools = [
            "shell_exec",
            "fs_write_file",
            "fs_read_file",
            "web_fetch",
            "execute",
            "write_file",
            "edit_file",
            "read_file",
            "file_reader",
        ]
        for tool in dangerous_tools:
            self.assertNotIn(tool, AUTO_APPROVE_WHITELIST, f"{tool} 不应在白名单中")
            self.assertFalse(is_auto_approve(tool), f"{tool} 应需人工审批")

    def test_non_string_input_returns_false(self):
        """非字符串输入（int/dict/list）返回 False。"""
        self.assertFalse(is_auto_approve(123))
        self.assertFalse(is_auto_approve({"name": "shell_exec"}))
        self.assertFalse(is_auto_approve(["shell_exec"]))


if __name__ == "__main__":
    unittest.main()
