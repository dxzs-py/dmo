"""工具调用持久化与实时广播单元测试。

覆盖 spec ``fix-tool-approval-and-cross-browser-sync-integrity`` Task 3/4：

1. ``persist_stream_result``：3 个工具调用（含 shell_exec "ollama list" +
   shell_exec "ollama ps" + fs_write_file）完整持久化到 ``Message.tool_calls``
2. ``_merge_tool_calls_incremental``：增量合并，已存在的不覆盖
3. ``process_stream_chunk``：分块 args（shell_exec 长 command）累积完整后
   发布 ``TOOL_CALL_INPUT_READY`` 到 WebSocket（修复跨浏览器工具调用不同步）

mock 策略:
- ``persist_stream_result`` 测试用 Django TestCase + 真实 SQLite DB
- ``publish_event`` 被 patch 为 AsyncMock，隔离 Redis/Channels 依赖
- ``service``（ToolCallLifecycleService）被 patch，验证 transition 调用

运行方式:
    cd backend/Django_xm
    conda activate langchain_xm
    python -m pytest Django_xm/apps/chat/tests/test_tool_calls_persistence.py -v
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

# Django 环境初始化（兼容 pytest 和 unittest 直接运行）
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django
import django.apps

if not django.apps.apps.ready:
    django.setup()

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TestCase
from langchain_core.messages import AIMessageChunk

from Django_xm.apps.chat.models import ChatMessage, ChatSession
from Django_xm.apps.chat.services.stream_helpers import (
    _build_persisted_tool_calls,
    _merge_tool_calls_incremental,
    persist_stream_result,
    process_stream_chunk,
)
from Django_xm.common.event_schema import EventType

User = get_user_model()


def _make_tool_calls_map() -> dict:
    """构造 3 个工具调用的 tool_calls_map（模拟代理模式场景）。

    含 shell_exec "ollama list" + shell_exec "ollama ps" + fs_write_file，
    复现今晨 4 浏览器测试发现的工具调用集。
    """
    return {
        "call_ollama_list": {
            "id": "call_ollama_list",
            "name": "shell_exec",
            "type": "tool-call-shell_exec",
            "state": "output-available",
            "status": "completed",
            "parameters": {"command": "ollama list"},
            "result": "NAME       SIZE    MODIFIED\nqwen3:8b  4.9GB  2026-07-01",
            "error": None,
        },
        "call_ollama_ps": {
            "id": "call_ollama_ps",
            "name": "shell_exec",
            "type": "tool-call-shell_exec",
            "state": "output-available",
            "status": "completed",
            "parameters": {"command": "ollama ps"},
            "result": "NAME       SIZE    PROCESSOR\nqwen3:8b  4.9GB  100% CPU",
            "error": None,
        },
        "call_fs_write": {
            "id": "call_fs_write",
            "name": "fs_write_file",
            "type": "tool-call-fs_write_file",
            "state": "output-available",
            "status": "completed",
            "parameters": {"file_path": "/tmp/report.md", "content": "..."},  # noqa: S108
            "result": "已写入 /tmp/report.md",
            "error": None,
        },
    }


class BuildPersistedToolCallsTests(unittest.TestCase):
    """_build_persisted_tool_calls：清理 _ 前缀字段。"""

    def test_strips_underscore_prefix_fields(self):
        """_index / _summarized 等内部字段应被清理。"""
        tool_calls_map = {
            "tc-1": {
                "id": "tc-1",
                "name": "shell_exec",
                "parameters": {"command": "ls"},
                "_index": 0,
                "_summarized": True,
            }
        }
        result = _build_persisted_tool_calls(tool_calls_map)
        self.assertEqual(len(result), 1)
        self.assertNotIn("_index", result[0])
        self.assertNotIn("_summarized", result[0])
        self.assertEqual(result[0]["name"], "shell_exec")


class MergeToolCallsIncrementalTests(unittest.TestCase):
    """_merge_tool_calls_incremental：按 id 去重，已存在不覆盖。"""

    def test_appends_new_tool_calls(self):
        """新 tool_call 追加到 existing 列表。"""
        existing = [{"id": "tc-1", "name": "shell_exec"}]
        new = [{"id": "tc-2", "name": "fs_write_file"}]
        merged = _merge_tool_calls_incremental(existing, new)
        self.assertEqual(len(merged), 2)
        ids = {tc["id"] for tc in merged}
        self.assertEqual(ids, {"tc-1", "tc-2"})

    def test_does_not_overwrite_existing(self):
        """已存在的 tool_call 不被覆盖（保留 approval/result 字段）。"""
        existing = [{"id": "tc-1", "name": "shell_exec", "approval": {"state": "approved"}}]
        new = [{"id": "tc-1", "name": "shell_exec", "parameters": {"command": "ls"}}]
        merged = _merge_tool_calls_incremental(existing, new)
        self.assertEqual(len(merged), 1)
        # 保留 existing 的 approval 字段
        self.assertEqual(merged[0]["approval"]["state"], "approved")
        # 不写入 new 的 parameters（不覆盖）
        self.assertNotIn("parameters", merged[0])


class PersistStreamResultToolCallsTests(TestCase):
    """persist_stream_result：3 个工具调用完整持久化到 Message.tool_calls。"""

    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pass")
        self.session = ChatSession.objects.create(
            session_id="test-session-tool-calls",
            user=self.user,
            title="测试工具调用持久化",
            mode="agent",
        )
        # 创建空的 assistant Message（模拟前端占位，stream 期间未同步 tool_calls）
        self.assistant_msg = ChatMessage.objects.create(
            session=self.session,
            role="assistant",
            content="",
            tool_calls=[],
        )

    @patch("Django_xm.common.realtime_events.publish_event", new_callable=AsyncMock)
    def test_persists_all_three_tool_calls(self, _mock_publish):
        """3 个工具调用（shell_exec x2 + fs_write_file）全部持久化。"""
        tool_calls_map = _make_tool_calls_map()

        saved_id = async_to_sync(persist_stream_result)(
            session_id=self.session.session_id,
            user_id=self.user.id,
            content="✅ 全部完成！",
            tool_calls_map=tool_calls_map,
            message_id=str(self.assistant_msg.id),
        )

        self.assertEqual(saved_id, str(self.assistant_msg.id))

        # 重新查询，验证持久化
        self.assistant_msg.refresh_from_db()
        self.assertEqual(len(self.assistant_msg.tool_calls), 3)

        tool_names = [tc["name"] for tc in self.assistant_msg.tool_calls]
        self.assertEqual(sorted(tool_names), ["fs_write_file", "shell_exec", "shell_exec"])

        # 验证 shell_exec "ollama list" 参数完整
        ollama_list_tc = next(
            tc for tc in self.assistant_msg.tool_calls if tc.get("parameters", {}).get("command") == "ollama list"
        )
        self.assertEqual(ollama_list_tc["name"], "shell_exec")
        self.assertEqual(ollama_list_tc["state"], "output-available")

        # 验证 shell_exec "ollama ps" 参数完整
        ollama_ps_tc = next(
            tc for tc in self.assistant_msg.tool_calls if tc.get("parameters", {}).get("command") == "ollama ps"
        )
        self.assertEqual(ollama_ps_tc["parameters"]["command"], "ollama ps")

    @patch("Django_xm.common.realtime_events.publish_event", new_callable=AsyncMock)
    def test_incremental_merge_preserves_existing_approval(self, _mock_publish):
        """增量合并：已有 tool_call 不被覆盖，保留 approval 字段。"""
        # 预置 1 个已持久化的 tool_call（模拟前端已同步 fs_write_file + 审批状态）
        self.assistant_msg.tool_calls = [
            {
                "id": "call_fs_write",
                "name": "fs_write_file",
                "parameters": {"file_path": "/tmp/report.md"},  # noqa: S108
                "approval": {"state": "approved"},
            }
        ]
        self.assistant_msg.save(update_fields=["tool_calls"])

        tool_calls_map = _make_tool_calls_map()

        async_to_sync(persist_stream_result)(
            session_id=self.session.session_id,
            user_id=self.user.id,
            content="完成",
            tool_calls_map=tool_calls_map,
            message_id=str(self.assistant_msg.id),
        )

        self.assistant_msg.refresh_from_db()
        # existing 1 个（fs_write_file）+ 新增 2 个 shell_exec = 3 个
        # tool_calls_map 中的 fs_write_file 因 id 已存在被跳过（不覆盖）
        self.assertEqual(len(self.assistant_msg.tool_calls), 3)
        # 保留 existing 的 approval 字段
        fs_tc = next(tc for tc in self.assistant_msg.tool_calls if tc.get("name") == "fs_write_file")
        self.assertEqual(fs_tc["approval"]["state"], "approved")

    @patch("Django_xm.common.realtime_events.publish_event", new_callable=AsyncMock)
    def test_message_id_none_falls_back_to_last_assistant(self, _mock_publish):
        """message_id=None 时，降级找最后一条 assistant Message。"""
        tool_calls_map = _make_tool_calls_map()

        saved_id = async_to_sync(persist_stream_result)(
            session_id=self.session.session_id,
            user_id=self.user.id,
            content="完成",
            tool_calls_map=tool_calls_map,
            message_id=None,  # 不指定 message_id
        )

        self.assertEqual(saved_id, str(self.assistant_msg.id))
        self.assistant_msg.refresh_from_db()
        self.assertEqual(len(self.assistant_msg.tool_calls), 3)


class ProcessStreamChunkInputReadyBroadcastTests(unittest.TestCase):
    """process_stream_chunk：分块 args 累积完整后发布 INPUT_READY 到 WebSocket。

    修复背景：原 loop.py._publish_input_ready_events 对 AIMessageChunk 用单 chunk
    严格 json.loads，args 分多块到达时永远解析失败 → 不发布 INPUT_READY。
    现 process_stream_chunk 内部 _broadcast_tool_input_ready 用 accumulator
    累积，与 SSE tool 事件同源。
    """

    @patch("Django_xm.apps.chat.services.stream_tool_lifecycle.service")
    def test_split_args_triggers_input_ready_on_completion(self, mock_service):
        """shell_exec args 分 2 块到达，累积完整后发布 INPUT_READY。"""
        tool_calls_map: dict = {}
        tool_call_count: dict = {}
        accumulated_reasoning: dict = {"content": "", "_stream_state": None}
        tool_args_accumulator: dict = {}

        # chunk 1: args 不完整 '{"command": "ollama'
        chunk1 = AIMessageChunk(
            content="",
            tool_call_chunks=[{"id": "call_split", "name": "shell_exec", "args": '{"command": "ollama', "index": 0}],
        )
        # chunk 2: args 补全 ' list"}'  → 累积 '{"command": "ollama list"}'
        chunk2 = AIMessageChunk(
            content="",
            tool_call_chunks=[{"id": "call_split", "name": "shell_exec", "args": ' list"}', "index": 0}],
        )

        events: list = []
        # chunk 1: args 不完整，累积路径 json.loads 失败，不发布 INPUT_READY
        for event in process_stream_chunk(
            chunk1,
            tool_calls_map,
            "",
            tool_call_count=tool_call_count,
            lcp_func=lambda a, b: 0,
            accumulated_reasoning=accumulated_reasoning,
            tool_args_accumulator=tool_args_accumulator,
            mode="agent",
            session_id="test-session",
            message_id="msg-1",
        ):
            events.append(event)

        # chunk 2: args 补全，json.loads 成功，发布 INPUT_READY
        for event in process_stream_chunk(
            chunk2,
            tool_calls_map,
            "",
            tool_call_count=tool_call_count,
            lcp_func=lambda a, b: 0,
            accumulated_reasoning=accumulated_reasoning,
            tool_args_accumulator=tool_args_accumulator,
            mode="agent",
            session_id="test-session",
            message_id="msg-1",
        ):
            events.append(event)

        # 断言：chunk2 后 service.transition 被调用，且含 TOOL_CALL_INPUT_READY
        self.assertGreater(mock_service.transition.call_count, 0)
        transition_events = [call.args[1] for call in mock_service.transition.call_args_list]
        self.assertIn(EventType.TOOL_CALL_INPUT_READY, transition_events)

        # 断言：tool_calls_map 含完整参数
        self.assertIn("call_split", tool_calls_map)
        self.assertEqual(
            tool_calls_map["call_split"]["parameters"],
            {"command": "ollama list"},
        )

        # 断言：SSE 也 yield 了 tool 事件（与 WebSocket 同源）
        tool_events = [e for e in events if e.get("type") == "tool"]
        self.assertGreater(len(tool_events), 0)

    @patch("Django_xm.apps.chat.services.stream_tool_lifecycle.service")
    def test_no_input_ready_when_session_id_empty(self, mock_service):
        """session_id 为空（如 deep_chat_service 未传）时跳过 INPUT_READY 发布。"""
        tool_calls_map: dict = {}
        tool_call_count: dict = {}
        accumulated_reasoning: dict = {"content": "", "_stream_state": None}
        tool_args_accumulator: dict = {}

        # 单 chunk 完整 args
        chunk = AIMessageChunk(
            content="",
            tool_call_chunks=[
                {"id": "call_single", "name": "fs_write_file", "args": '{"file_path": "/tmp/x"}', "index": 0}
            ],
        )

        for _event in process_stream_chunk(
            chunk,
            tool_calls_map,
            "",
            tool_call_count=tool_call_count,
            lcp_func=lambda a, b: 0,
            accumulated_reasoning=accumulated_reasoning,
            tool_args_accumulator=tool_args_accumulator,
            mode="agent",
            session_id="",  # 空 session_id
            message_id="",
        ):
            pass

        # session_id 为空，service.transition 不应被调用
        mock_service.transition.assert_not_called()
        # 但 tool_calls_map 仍更新（SSE 逻辑不受影响）
        self.assertIn("call_single", tool_calls_map)


if __name__ == "__main__":
    unittest.main()
