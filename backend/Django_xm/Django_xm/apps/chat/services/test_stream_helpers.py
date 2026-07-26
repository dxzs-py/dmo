"""stream_helpers 单元测试（Task 14.3）。

覆盖 spec `unify-approval-and-timeout-recovery` 阶段三变更：
1. _detect_tool_timeout 识别"审批超时"关键字
2. _detect_tool_rejected 识别"用户已拒绝"关键字
3. _handle_tool_message_chunk 事件优先级：超时 > 拒绝 > 错误 > 完成

mock 策略:
- mock Django_xm.apps.chat.services.stream_helpers.service（ToolCallLifecycleService 单例）
- 事件类型从 service.transition.call_args_list 的 args[1] 提取
- 不依赖真实 Redis / Channels / Django ORM

运行方式:
    cd d:\programming\langchain\langchain_xm\backend\Django_xm
    conda activate langchain_xm
    python manage.py test Django_xm.apps.chat.services.test_stream_helpers --verbosity=2
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch, MagicMock

# Django 环境初始化
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django  # noqa: E402
import django.apps  # noqa: E402,F401

if not django.apps.apps.ready:
    django.setup()

from langchain_core.messages import ToolMessage  # noqa: E402

from Django_xm.apps.chat.services.stream_helpers import (  # noqa: E402
    _detect_tool_timeout,
    _detect_tool_rejected,
    _handle_tool_message_chunk,
)
from Django_xm.common.event_schema import EventType  # noqa: E402


def _make_tool_message(content, tool_call_id="tc-1", name="shell_exec", status=None):
    """构造 ToolMessage，status 默认不设（None 表示无 status 字段）。"""
    kwargs = {
        "content": content,
        "tool_call_id": tool_call_id,
        "name": name,
    }
    if status is not None:
        kwargs["status"] = status
    return ToolMessage(**kwargs)


def _make_tool_info(**overrides):
    """构造 stream_helpers 内部使用的 tool_info dict。"""
    defaults = {
        "id": "tc-1",
        "name": "shell_exec",
        "type": "tool-call-shell_exec",
        "state": "input-available",
        "status": "running",
        "parameters": {"command": "ls"},
        "result": None,
        "error": None,
    }
    defaults.update(overrides)
    return defaults


class DetectToolTimeoutTests(unittest.TestCase):
    """_detect_tool_timeout 关键字识别测试。"""

    def test_detect_tool_timeout_with_timeout_keyword(self):
        """content 包含'审批超时'关键字 → 返回 True。"""
        msg = _make_tool_message(
            content="工具运行失败：审批超时（超过5分钟未审批），请尝试其他方法。",
            status="error",
        )
        self.assertTrue(_detect_tool_timeout(msg))

    def test_detect_tool_timeout_without_keyword(self):
        """普通 error ToolMessage（无'审批超时'关键字）→ 返回 False。"""
        msg = _make_tool_message(
            content="命令退出码: 1\nstderr: permission denied",
            status="error",
        )
        self.assertFalse(_detect_tool_timeout(msg))

    def test_detect_tool_timeout_with_non_string_content(self):
        """content 非 str → 返回 False（健壮性）。"""
        msg = MagicMock()
        msg.content = None
        self.assertFalse(_detect_tool_timeout(msg))

    def test_detect_tool_timeout_with_success_message(self):
        """成功 ToolMessage → 返回 False。"""
        msg = _make_tool_message(
            content="命令执行成功",
        )
        self.assertFalse(_detect_tool_timeout(msg))


class DetectToolRejectedTests(unittest.TestCase):
    """_detect_tool_rejected 关键字识别测试。"""

    def test_detect_tool_rejected_with_keyword(self):
        """content 包含'用户已拒绝'关键字 → 返回 True。"""
        msg = _make_tool_message(
            content="用户已拒绝执行工具 shell_exec（操作内容: rm -rf /）。",
            status="error",
        )
        self.assertTrue(_detect_tool_rejected(msg))

    def test_detect_tool_rejected_without_keyword(self):
        """普通 error ToolMessage（无'用户已拒绝'关键字）→ 返回 False。"""
        msg = _make_tool_message(
            content="命令退出码: 1\nstderr: permission denied",
            status="error",
        )
        self.assertFalse(_detect_tool_rejected(msg))

    def test_detect_tool_rejected_with_non_string_content(self):
        """content 非 str → 返回 False（健壮性）。"""
        msg = MagicMock()
        msg.content = None
        self.assertFalse(_detect_tool_rejected(msg))


class HandleToolMessageChunkEventTests(unittest.TestCase):
    """_handle_tool_message_chunk 事件发布测试（优先级：超时 > 拒绝 > 错误 > 完成）。"""

    @patch("Django_xm.apps.chat.services.stream_helpers.service")
    def test_handle_tool_message_chunk_timeout_event(self, mock_service):
        """超时 ToolMessage 触发 TOOL_CALL_TIMEOUT 事件。"""
        tool_calls_map = {"tc-1": _make_tool_info()}
        msg = _make_tool_message(
            content="工具运行失败：审批超时（超过5分钟未审批）",
            tool_call_id="tc-1",
            status="error",
        )

        _handle_tool_message_chunk(
            msg, tool_calls_map,
            session_id="session-1", message_id="msg-1",
        )

        # 验证事件序列：RUNNING → TIMEOUT
        event_types = [call.args[1] for call in mock_service.transition.call_args_list]
        self.assertIn(EventType.TOOL_CALL_TIMEOUT, event_types)
        # 不应触发 FAILED 或 REJECTED
        self.assertNotIn(EventType.TOOL_CALL_FAILED, event_types)
        self.assertNotIn(EventType.TOOL_CALL_REJECTED, event_types)
        # tool_info 状态应被更新为 output-error
        self.assertEqual(tool_calls_map["tc-1"]["state"], "output-error")
        self.assertEqual(tool_calls_map["tc-1"]["status"], "failed")
        self.assertEqual(tool_calls_map["tc-1"]["error"], msg.content)

    @patch("Django_xm.apps.chat.services.stream_helpers.service")
    def test_handle_tool_message_chunk_rejected_event(self, mock_service):
        """拒绝 ToolMessage 触发 TOOL_CALL_REJECTED 事件。"""
        tool_calls_map = {"tc-1": _make_tool_info()}
        msg = _make_tool_message(
            content="用户已拒绝执行工具 shell_exec（操作内容: rm -rf /）。",
            tool_call_id="tc-1",
            status="error",
        )

        _handle_tool_message_chunk(
            msg, tool_calls_map,
            session_id="session-1", message_id="msg-1",
        )

        event_types = [call.args[1] for call in mock_service.transition.call_args_list]
        self.assertIn(EventType.TOOL_CALL_REJECTED, event_types)
        # 不应触发 FAILED 或 TIMEOUT
        self.assertNotIn(EventType.TOOL_CALL_FAILED, event_types)
        self.assertNotIn(EventType.TOOL_CALL_TIMEOUT, event_types)
        self.assertEqual(tool_calls_map["tc-1"]["state"], "output-error")

    @patch("Django_xm.apps.chat.services.stream_helpers.service")
    def test_handle_tool_message_chunk_completed_event(self, mock_service):
        """正常 ToolMessage 触发 TOOL_CALL_COMPLETED 事件。"""
        tool_calls_map = {"tc-1": _make_tool_info()}
        msg = _make_tool_message(
            content="命令执行结果: ok",
            tool_call_id="tc-1",
        )

        _handle_tool_message_chunk(
            msg, tool_calls_map,
            session_id="session-1", message_id="msg-1",
        )

        event_types = [call.args[1] for call in mock_service.transition.call_args_list]
        # RUNNING 已由 approval_service 统一发布，此处仅发布 COMPLETED
        self.assertIn(EventType.TOOL_CALL_COMPLETED, event_types)
        # 不应触发 FAILED/TIMEOUT/REJECTED
        self.assertNotIn(EventType.TOOL_CALL_FAILED, event_types)
        self.assertNotIn(EventType.TOOL_CALL_TIMEOUT, event_types)
        self.assertNotIn(EventType.TOOL_CALL_REJECTED, event_types)
        # tool_info 状态为 output-available
        self.assertEqual(tool_calls_map["tc-1"]["state"], "output-available")
        self.assertEqual(tool_calls_map["tc-1"]["status"], "completed")
        self.assertEqual(tool_calls_map["tc-1"]["result"], "命令执行结果: ok")

    @patch("Django_xm.apps.chat.services.stream_helpers.service")
    def test_handle_tool_message_chunk_failed_event(self, mock_service):
        """普通 error ToolMessage（无关键字）触发 TOOL_CALL_FAILED 事件。"""
        tool_calls_map = {"tc-1": _make_tool_info()}
        msg = _make_tool_message(
            content="Traceback (most recent call last): some error",
            tool_call_id="tc-1",
            status="error",
        )

        _handle_tool_message_chunk(
            msg, tool_calls_map,
            session_id="session-1", message_id="msg-1",
        )

        event_types = [call.args[1] for call in mock_service.transition.call_args_list]
        self.assertIn(EventType.TOOL_CALL_FAILED, event_types)
        # 不应触发 TIMEOUT/REJECTED/COMPLETED
        self.assertNotIn(EventType.TOOL_CALL_TIMEOUT, event_types)
        self.assertNotIn(EventType.TOOL_CALL_REJECTED, event_types)
        self.assertNotIn(EventType.TOOL_CALL_COMPLETED, event_types)
        self.assertEqual(tool_calls_map["tc-1"]["state"], "output-error")

    @patch("Django_xm.apps.chat.services.stream_helpers.service")
    def test_handle_tool_message_chunk_timeout_priority_over_rejected(self, mock_service):
        """超时优先级高于拒绝：content 同时含'审批超时'和'用户已拒绝'时，触发 TIMEOUT。"""
        tool_calls_map = {"tc-1": _make_tool_info()}
        msg = _make_tool_message(
            content="工具运行失败：审批超时；用户已拒绝执行工具",
            tool_call_id="tc-1",
            status="error",
        )

        _handle_tool_message_chunk(
            msg, tool_calls_map,
            session_id="session-1", message_id="msg-1",
        )

        event_types = [call.args[1] for call in mock_service.transition.call_args_list]
        # 优先级：超时 > 拒绝，触发 TIMEOUT 而非 REJECTED
        self.assertIn(EventType.TOOL_CALL_TIMEOUT, event_types)
        self.assertNotIn(EventType.TOOL_CALL_REJECTED, event_types)


if __name__ == "__main__":
    unittest.main()