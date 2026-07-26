"""ApprovalMiddleware 单元测试（Task 14.2）。

覆盖 spec `unify-approval-and-timeout-recovery` 阶段二变更：
1. middleware 不再注入 approved_by_middleware 字段到 tool_call args
2. approved 的 tool_call 保留原始 args（不被修改）
3. rejected 的 tool_call 注入 error ToolMessage

运行方式:
    cd d:\programming\langchain\langchain_xm\backend\Django_xm
    conda activate langchain_xm
    python manage.py test Django_xm.apps.agent_hub.approval.tests.test_middleware --verbosity=2
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

# Django 环境初始化
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django  # noqa: E402
import django.apps  # noqa: E402,F401

if not django.apps.apps.ready:
    django.setup()

from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402

from Django_xm.apps.agent_hub.approval.middleware import ApprovalMiddleware  # noqa: E402
from Django_xm.apps.agent_hub.approval.timeout_handler import TIMEOUT_DECISION  # noqa: E402


def _make_ai_message(tool_calls):
    """构造带 tool_calls 的 AIMessage。"""
    return AIMessage(content="", tool_calls=list(tool_calls))


def _make_state(messages):
    """构造 middleware state dict。"""
    return {"messages": list(messages)}


def _make_runtime():
    """构造最小化 runtime（middleware 未使用 runtime 字段）。"""
    return MagicMock()


class ApprovalMiddlewareApprovedTests(unittest.IsolatedAsyncioTestCase):
    """approved 场景：保留原参数，不注入 approved_by_middleware 字段。"""

    async def test_middleware_does_not_inject_approved_by_middleware(self):
        """approved 的 tool_call args 中不应包含 approved_by_middleware 字段。

        阶段二变更：middleware 不再注入 approved_by_middleware=True，
        approved 的 tool_call 直接 revised_tool_calls.append(tc) 保留原参数。
        """
        middleware = ApprovalMiddleware()
        tc = {
            "name": "shell_exec",
            "id": "tc-001",
            "args": {"command": "ls -la"},
        }
        ai_msg = _make_ai_message([tc])

        # mock interrupt 返回 approved 决策
        with patch(
            "Django_xm.apps.agent_hub.approval.middleware.interrupt",
            return_value={"tc-001": True},
        ):
            result = await middleware.aafter_model(
                _make_state([ai_msg]), _make_runtime()
            )

        self.assertIsNotNone(result)
        self.assertIn("messages", result)
        # 第一条是 last_ai_msg（已被修改 tool_calls）
        returned_ai_msg = result["messages"][0]
        self.assertIsInstance(returned_ai_msg, AIMessage)
        # 验证 tool_call args 不含 approved_by_middleware
        for tc in returned_ai_msg.tool_calls:
            args = tc.get("args", {})
            self.assertNotIn(
                "approved_by_middleware",
                args,
                f"tool_call {tc.get('id')} args 不应包含 approved_by_middleware: {args}",
            )

    async def test_middleware_approved_tool_call_preserves_original_args(self):
        """approved 的 tool_call 保留原始 args（参数不被修改）。"""
        middleware = ApprovalMiddleware()
        original_args = {"command": "rm -rf /tmp/x", "timeout": 30}
        tc = {
            "name": "shell_exec",
            "id": "tc-002",
            "args": dict(original_args),
        }
        ai_msg = _make_ai_message([tc])

        with patch(
            "Django_xm.apps.agent_hub.approval.middleware.interrupt",
            return_value={"tc-002": True},
        ):
            result = await middleware.aafter_model(
                _make_state([ai_msg]), _make_runtime()
            )

        returned_ai_msg = result["messages"][0]
        self.assertEqual(len(returned_ai_msg.tool_calls), 1)
        # 验证原始参数完全保留（未被添加/删除/修改任何字段）
        self.assertEqual(returned_ai_msg.tool_calls[0]["args"], original_args)
        self.assertEqual(
            returned_ai_msg.tool_calls[0]["args"]["command"], "rm -rf /tmp/x"
        )
        self.assertEqual(returned_ai_msg.tool_calls[0]["args"]["timeout"], 30)

    async def test_middleware_approved_preserves_multiple_tool_calls(self):
        """多个 approved tool_call 都保留原参数，不注入任何字段。"""
        middleware = ApprovalMiddleware()
        tcs = [
            {"name": "shell_exec", "id": "tc-a", "args": {"command": "ls"}},
            {"name": "shell_exec", "id": "tc-b", "args": {"command": "pwd"}},
        ]
        ai_msg = _make_ai_message(tcs)

        with patch(
            "Django_xm.apps.agent_hub.approval.middleware.interrupt",
            return_value={"tc-a": True, "tc-b": True},
        ):
            result = await middleware.aafter_model(
                _make_state([ai_msg]), _make_runtime()
            )

        returned_ai_msg = result["messages"][0]
        self.assertEqual(len(returned_ai_msg.tool_calls), 2)
        for tc in returned_ai_msg.tool_calls:
            self.assertNotIn("approved_by_middleware", tc["args"])


class ApprovalMiddlewareRejectedTests(unittest.IsolatedAsyncioTestCase):
    """rejected 场景：注入 error ToolMessage。"""

    async def test_middleware_rejected_tool_call_injects_error_toolmessage(self):
        """rejected tool_call 注入 error ToolMessage，content 包含'用户已拒绝'。"""
        middleware = ApprovalMiddleware()
        tc = {
            "name": "shell_exec",
            "id": "tc-rej-1",
            "args": {"command": "rm -rf /"},
        }
        ai_msg = _make_ai_message([tc])

        with patch(
            "Django_xm.apps.agent_hub.approval.middleware.interrupt",
            return_value={"tc-rej-1": False},
        ):
            result = await middleware.aafter_model(
                _make_state([ai_msg]), _make_runtime()
            )

        self.assertIsNotNone(result)
        messages = result["messages"]
        # 第一条是 last_ai_msg，第二条是注入的 error ToolMessage
        self.assertGreaterEqual(len(messages), 2)
        tool_msg = messages[1]
        self.assertIsInstance(tool_msg, ToolMessage)
        self.assertEqual(tool_msg.tool_call_id, "tc-rej-1")
        self.assertEqual(tool_msg.status, "error")
        # content 包含"用户已拒绝"关键字（stream_helpers._detect_tool_rejected 依赖此关键字）
        self.assertIn("用户已拒绝", tool_msg.content)

    async def test_middleware_rejected_preserves_tool_call_in_ai_message(self):
        """rejected tool_call 仍保留在 AIMessage.tool_calls 中（让 ToolNode 路由跳过执行）。"""
        middleware = ApprovalMiddleware()
        tc = {
            "name": "shell_exec",
            "id": "tc-rej-2",
            "args": {"command": "rm -rf /"},
        }
        ai_msg = _make_ai_message([tc])

        with patch(
            "Django_xm.apps.agent_hub.approval.middleware.interrupt",
            return_value={"tc-rej-2": False},
        ):
            result = await middleware.aafter_model(
                _make_state([ai_msg]), _make_runtime()
            )

        returned_ai_msg = result["messages"][0]
        # tool_call 仍保留（让 ToolNode 检测到已有 ToolMessage 跳过执行）
        self.assertEqual(len(returned_ai_msg.tool_calls), 1)
        self.assertEqual(returned_ai_msg.tool_calls[0]["id"], "tc-rej-2")
        # args 不应被修改
        self.assertEqual(
            returned_ai_msg.tool_calls[0]["args"], {"command": "rm -rf /"}
        )


class ApprovalMiddlewareTimeoutTests(unittest.IsolatedAsyncioTestCase):
    """timeout 场景：注入超时 ToolMessage（content 包含'审批超时'）。"""

    async def test_middleware_timeout_injects_timeout_toolmessage(self):
        """timeout 决策注入 build_timeout_tool_message 构造的 ToolMessage。"""
        middleware = ApprovalMiddleware()
        tc = {
            "name": "shell_exec",
            "id": "tc-timeout-1",
            "args": {"command": "rm -rf /"},
        }
        ai_msg = _make_ai_message([tc])

        with patch(
            "Django_xm.apps.agent_hub.approval.middleware.interrupt",
            return_value={"tc-timeout-1": TIMEOUT_DECISION},
        ):
            result = await middleware.aafter_model(
                _make_state([ai_msg]), _make_runtime()
            )

        self.assertIsNotNone(result)
        messages = result["messages"]
        self.assertGreaterEqual(len(messages), 2)
        tool_msg = messages[1]
        self.assertIsInstance(tool_msg, ToolMessage)
        self.assertEqual(tool_msg.tool_call_id, "tc-timeout-1")
        self.assertEqual(tool_msg.status, "error")
        # content 包含"审批超时"关键字（stream_helpers._detect_tool_timeout 依赖此关键字）
        self.assertIn("审批超时", tool_msg.content)


if __name__ == "__main__":
    unittest.main()