"""parse_approval_interrupt 批量格式解析单元测试（Task 7）。

覆盖 ApprovalMiddleware 批量 interrupt 格式：
    {"_approval": True, "requests": [...], "_meta": {"graph_interrupt_id": ...}}

关键回归点（根因 7）：
1. 批量格式按 requests 列表展开为多个 approval_data（不再塌缩为 1 个）
2. 每个 approval_data.interrupt_id = 该工具的 tool_call_id（非批次 ID）
3. 每个 approval_data.graph_interrupt_id = 批次 ID（前端分组用）
4. 单工具旧格式（含 tool_name 键）保持向后兼容，返回 1 个元素
5. 空 requests 列表 / 非 dict 输入返回空列表

mock 策略:
- 不依赖真实 Redis / Channels / Django ORM
- 仅初始化 Django 环境以导入 stream_helpers 模块

运行方式:
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    conda activate langchain_xm
    python manage.py test Django_xm.apps.chat.tests.test_parse_approval_interrupt_batch --verbosity=2
"""

from __future__ import annotations

import os
import unittest

# Django 环境初始化
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django
import django.apps

if not django.apps.apps.ready:
    django.setup()

from Django_xm.apps.chat.services.stream_helpers import (
    parse_approval_interrupt,
)

BATCH_ID = "batch-graph-interrupt-123"


def _make_request(
    tool_name: str,
    tool_call_id: str,
    *,
    operation: str = "",
    danger_level: str = "medium",
    title: str = "确认操作",
    description: str = "",
    args: dict | None = None,
) -> dict:
    """构造与 ApprovalMiddleware after_model 产生的单条审批请求一致的 dict。

    字段结构参照 middleware.py 中 request 的构建（_approval / tool_name /
    tool_call_id / graph_interrupt_id / title / description / operation /
    danger_level / risk_level / args）。
    """
    return {
        "_approval": True,
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "graph_interrupt_id": BATCH_ID,
        "session_id": "session-1",
        "title": title,
        "description": description,
        "operation": operation,
        "danger_level": danger_level,
        "risk_level": "controlled",
        "args": args or {},
    }


def _make_batch_interrupt(requests: list[dict]) -> dict:
    """构造批量 interrupt_value（含 _approval / requests / _meta）。"""
    return {
        "_approval": True,
        "requests": requests,
        "_meta": {"graph_interrupt_id": BATCH_ID},
    }


class ParseApprovalInterruptBatchTests(unittest.TestCase):
    """批量格式解析测试。"""

    def test_batch_three_requests_returns_three_items(self):
        """用例 1：3 个 requests（shell_exec × 2 + fs_write_file × 1）→ 返回 3 个。"""
        requests = [
            _make_request("shell_exec", "tc-shell-1", operation="ls -la", danger_level="high"),
            _make_request("shell_exec", "tc-shell-2", operation="rm -rf /tmp/x", danger_level="high"),
            _make_request("fs_write_file", "tc-fs-1", operation="/app/main.py", danger_level="medium"),
        ]
        interrupt_value = _make_batch_interrupt(requests)

        result = parse_approval_interrupt(interrupt_value, graph_interrupt_id=BATCH_ID)

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 3, "批量 3 个 requests 必须返回 3 个 approval_data")

    def test_batch_tool_names_are_actual_not_unknown(self):
        """用例 2：每个 approval_data 的 tool_name 为实际工具名（非 unknown）。"""
        requests = [
            _make_request("shell_exec", "tc-shell-1"),
            _make_request("shell_exec", "tc-shell-2"),
            _make_request("fs_write_file", "tc-fs-1"),
        ]
        result = parse_approval_interrupt(_make_batch_interrupt(requests), graph_interrupt_id=BATCH_ID)

        self.assertEqual([r["tool_name"] for r in result], ["shell_exec", "shell_exec", "fs_write_file"])
        for r in result:
            self.assertNotEqual(r["tool_name"], "unknown", "tool_name 不应为 unknown")

    def test_batch_interrupt_id_is_tool_call_id_not_batch_id(self):
        """用例 3：每个 approval_data 的 interrupt_id 为该工具的 tool_call_id（非批次 ID）。"""
        requests = [
            _make_request("shell_exec", "tc-shell-1"),
            _make_request("shell_exec", "tc-shell-2"),
            _make_request("fs_write_file", "tc-fs-1"),
        ]
        result = parse_approval_interrupt(_make_batch_interrupt(requests), graph_interrupt_id=BATCH_ID)

        self.assertEqual([r["interrupt_id"] for r in result], ["tc-shell-1", "tc-shell-2", "tc-fs-1"])
        for r in result:
            self.assertNotEqual(r["interrupt_id"], BATCH_ID, "interrupt_id 不应为批次 ID")
            # tool_call_id 字段与 interrupt_id 一致（前端 resume 端点使用）
            self.assertEqual(r["tool_call_id"], r["interrupt_id"])

    def test_batch_graph_interrupt_id_is_batch_id(self):
        """用例 4：每个 approval_data 的 graph_interrupt_id 为批次 ID（前端分组用）。"""
        requests = [
            _make_request("shell_exec", "tc-shell-1"),
            _make_request("fs_write_file", "tc-fs-1"),
        ]
        result = parse_approval_interrupt(_make_batch_interrupt(requests), graph_interrupt_id=BATCH_ID)

        for r in result:
            self.assertEqual(r["graph_interrupt_id"], BATCH_ID, "graph_interrupt_id 必须为批次 ID")

    def test_single_legacy_format_returns_one_item(self):
        """用例 5：单工具旧格式（含 tool_name 键）→ 返回 1 个（向后兼容）。"""
        # 旧格式：interrupt_value 本身即单个请求 dict，无 requests 列表
        legacy = {
            "tool_name": "shell_exec",
            "tool_call_id": "tc-single-1",
            "title": "执行命令",
            "description": "即将执行 shell 命令",
            "operation": "ls -la",
            "danger_level": "high",
        }

        result = parse_approval_interrupt(legacy, graph_interrupt_id="graph-single-1")

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1, "单工具旧格式必须返回 1 个元素")
        item = result[0]
        self.assertEqual(item["tool_name"], "shell_exec")
        # 旧格式携带 tool_call_id 时优先使用它
        self.assertEqual(item["interrupt_id"], "tc-single-1")
        self.assertEqual(item["tool_call_id"], "tc-single-1")
        self.assertEqual(item["graph_interrupt_id"], "graph-single-1")
        self.assertEqual(item["title"], "执行命令")
        self.assertEqual(item["operation"], "ls -la")
        self.assertEqual(item["danger_level"], "high")
        self.assertEqual(item["state"], "pending")

    def test_single_legacy_format_without_tool_call_id_falls_back_to_graph_id(self):
        """补充用例：旧格式无 tool_call_id 时回退到 graph_interrupt_id（向后兼容）。"""
        legacy = {
            "tool_name": "shell_exec",
            "interrupt_id": "legacy-intr-1",
            "title": "确认操作",
        }
        result = parse_approval_interrupt(legacy, graph_interrupt_id="graph-fallback")

        self.assertEqual(len(result), 1)
        # 无 tool_call_id → 回退到 graph_interrupt_id（而非 request.interrupt_id）
        self.assertEqual(result[0]["interrupt_id"], "graph-fallback")

    def test_empty_requests_returns_empty_list(self):
        """用例 6：空列表 requests → 返回空列表。"""
        interrupt_value = _make_batch_interrupt([])
        result = parse_approval_interrupt(interrupt_value, graph_interrupt_id=BATCH_ID)

        self.assertIsInstance(result, list)
        self.assertEqual(result, [], "空 requests 列表必须返回空列表")

    def test_non_dict_input_returns_empty_list(self):
        """用例 7：非 dict 输入 → 返回空列表。"""
        for invalid in [None, "string", 123, [], 3.14]:
            with self.subTest(value=invalid):
                result = parse_approval_interrupt(invalid, graph_interrupt_id=BATCH_ID)
                self.assertEqual(result, [], f"非 dict 输入 {invalid!r} 必须返回空列表")

    def test_batch_operation_and_extra_passthrough(self):
        """补充用例：批量格式下 operation / extra / input_placeholder 透传正确。"""
        requests = [
            _make_request(
                "shell_exec",
                "tc-shell-1",
                operation="ls -la",
                danger_level="high",
                title="执行 ls",
                description="列出目录",
            ),
        ]
        requests[0]["extra"] = {"subagent": "research"}
        requests[0]["input_placeholder"] = "请输入确认码"
        result = parse_approval_interrupt(_make_batch_interrupt(requests), graph_interrupt_id=BATCH_ID)

        self.assertEqual(len(result), 1)
        item = result[0]
        self.assertEqual(item["operation"], "ls -la")
        self.assertEqual(item["title"], "执行 ls")
        self.assertEqual(item["description"], "列出目录")
        self.assertEqual(item["danger_level"], "high")
        self.assertEqual(item["extra"], {"subagent": "research"})
        self.assertEqual(item["input_placeholder"], "请输入确认码")

    def test_batch_skips_non_dict_request_entries(self):
        """补充用例：requests 中混入非 dict 元素时被跳过。"""
        requests = [
            _make_request("shell_exec", "tc-shell-1"),
            "not-a-dict",  # 应被跳过
            None,  # 应被跳过
            _make_request("fs_write_file", "tc-fs-1"),
        ]
        result = parse_approval_interrupt(_make_batch_interrupt(requests), graph_interrupt_id=BATCH_ID)

        self.assertEqual(len(result), 2)
        self.assertEqual([r["tool_call_id"] for r in result], ["tc-shell-1", "tc-fs-1"])


if __name__ == "__main__":
    unittest.main()
