r"""工具事件提取器单元测试（Task 6.8）。

验证 ``Django_xm.apps.tools.tool_event_extractor.extract_tool_events_from_message``
对 deepagents 原生工具（read_file / write_file / edit_file / bash 等）的 I/O 完整性。

覆盖场景：
1. 完整 AIMessage（含 tool_calls）：INPUT_READY 携带完整 parameters
2. 流式 AIMessageChunk（含 tool_call_chunks，args 分片）：
   - INPUT_READY 在 args 不完整时不发射
   - ToolMessage 阶段从累积 chunk 聚合完整 parameters 并补发 INPUT_READY
3. ToolMessage 成功结果：COMPLETED 携带 result
4. ToolMessage 失败结果（status='error'）：FAILED 携带 error
5. ToolMessage 失败结果（content 以 'Error' 开头）：FAILED 携带 error
6. 多工具并行调用：多个 tool_call_id 都被正确提取
7. 去重：同一 tool_call_id 在多次 chunk 中只发射一次 INPUT_READY
8. deepagents 原生工具（read_file/write_file/edit_file/bash）的 I/O

运行方式：
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    conda activate langchain_xm
    python -m pytest Django_xm/apps/tools/tests/test_tool_event_extractor.py -v
"""

from __future__ import annotations

import json
import os
import unittest

# Django 环境初始化
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django
import django.apps

if not django.apps.apps.ready:
    django.setup()

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from Django_xm.apps.tools.tool_event_extractor import (
    extract_tool_events_from_message,
)
from Django_xm.common.event_schema import EventType

# ============================================================================
# 测试 1: 完整 AIMessage（含 tool_calls）
# ============================================================================


class CompleteAIMessageTests(unittest.TestCase):
    """完整 AIMessage 含 tool_calls 的事件提取。"""

    def test_aimessage_with_read_file_tool_calls(self):
        """AIMessage 含 read_file tool_calls → INPUT_READY 携带完整 parameters。"""
        msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"file_path": "/sandbox/notes/test.md"},
                    "id": "tc-read-1",
                }
            ],
        )
        seen = set()
        accumulated = []
        events = extract_tool_events_from_message(msg, seen, accumulated)

        self.assertEqual(len(events), 1)
        evt = events[0]
        self.assertEqual(evt["event_type"], EventType.TOOL_CALL_INPUT_READY)
        self.assertEqual(evt["tool_call_id"], "tc-read-1")
        self.assertEqual(evt["tool_name"], "read_file")
        self.assertEqual(evt["parameters"], {"file_path": "/sandbox/notes/test.md"})
        # seen_tool_call_ids 被更新
        self.assertIn("tc-read-1", seen)
        # message 被追加到 accumulated_messages
        self.assertEqual(len(accumulated), 1)

    def test_aimessage_with_write_file_tool_calls(self):
        """AIMessage 含 write_file tool_calls → INPUT_READY 携带 content 参数。"""
        msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write_file",
                    "args": {
                        "file_path": "/sandbox/reports/output.md",
                        "content": "# Report\n\nThis is a test report.",
                    },
                    "id": "tc-write-1",
                }
            ],
        )
        events = extract_tool_events_from_message(msg, set(), [])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["parameters"]["file_path"], "/sandbox/reports/output.md")
        self.assertEqual(events[0]["parameters"]["content"], "# Report\n\nThis is a test report.")

    def test_aimessage_with_bash_tool_call(self):
        """AIMessage 含 bash tool_calls → INPUT_READY 携带 command 参数。"""
        msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "bash",
                    "args": {"command": "ls -la /sandbox/"},
                    "id": "tc-bash-1",
                }
            ],
        )
        events = extract_tool_events_from_message(msg, set(), [])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["tool_name"], "bash")
        self.assertEqual(events[0]["parameters"]["command"], "ls -la /sandbox/")

    def test_aimessage_with_empty_args_skipped(self):
        """AIMessage 含 tool_calls 但 args 为空 → 不发射 INPUT_READY（等后续补发）。

        流式 AIMessageChunk 的 tool_calls args 可能不完整
        （parse_partial_json 返回 {}），args 为空时不发射、不去重。
        """
        msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {},
                    "id": "tc-empty-1",
                }
            ],
        )
        seen = set()
        events = extract_tool_events_from_message(msg, seen, [])
        # args 为空时不发射、不去重
        self.assertEqual(len(events), 0)
        self.assertNotIn("tc-empty-1", seen)

    def test_aimessage_without_tool_calls(self):
        """AIMessage 不含 tool_calls → 返回空列表。"""
        msg = AIMessage(content="thinking about next step")
        events = extract_tool_events_from_message(msg, set(), [])
        self.assertEqual(len(events), 0)

    def test_dedup_same_tool_call_id_only_emits_once(self):
        """同一 tool_call_id 在多次 AIMessage 中只发射一次 INPUT_READY。"""
        msg1 = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"file_path": "/a.txt"},
                    "id": "tc-dedup-1",
                }
            ],
        )
        msg2 = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"file_path": "/a.txt"},
                    "id": "tc-dedup-1",  # 同一 ID
                }
            ],
        )
        seen = set()
        accumulated = []
        events1 = extract_tool_events_from_message(msg1, seen, accumulated)
        events2 = extract_tool_events_from_message(msg2, seen, accumulated)

        self.assertEqual(len(events1), 1)
        self.assertEqual(len(events2), 0)  # 第二次因去重不发射


# ============================================================================
# 测试 2: 流式 AIMessageChunk（args 分片聚合）
# ============================================================================


class StreamedAIMessageChunkTests(unittest.TestCase):
    """流式 AIMessageChunk 的 args 分片聚合测试。

    deepagents astream(stream_mode=["messages"]) 只产出 AIMessageChunk，
    每个 chunk 的 tool_call_chunks.args 是参数 JSON 的分片。
    需要在 ToolMessage 阶段从累积 chunk 聚合完整参数。
    """

    def test_chunk_args_aggregated_at_tool_message_stage(self):
        """流式 chunk 的 args 分片在 ToolMessage 阶段被聚合为完整 parameters。

        模拟 deepagents LLM 流式输出：
        - chunk1: ``{"file_path":`` （parse_partial_json 返回 {}，INPUT_READY 不发射）
        - chunk2: `` "/san`` （args 仍不完整，不发射）
        - chunk3: ``dbox/notes/test.md"}`` （args 完整，但需 ToolMessage 触发聚合）

        ToolMessage 阶段从累积 chunk 聚合得到完整参数 ``{"file_path": "/sandbox/notes/test.md"}``，
        并补发 INPUT_READY + COMPLETED。
        """
        # 注意：每个 chunk 的 args 必须真正不完整，确保 parse_partial_json
        # 返回 {} 或空 dict，否则 extract_tool_params 会解析出部分参数，
        # 导致 INPUT_READY 在 chunk 阶段就发射（虽然参数不完整）。
        chunks = [
            AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                        "name": "read_file",
                        "args": '{"file_path":',
                        "id": "tc-chunk-1",
                        "index": 0,
                    }
                ],
            ),
            AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                        "name": "read_file",
                        "args": ' "/san',
                        "id": "tc-chunk-1",
                        "index": 0,
                    }
                ],
            ),
            AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                        "name": "read_file",
                        "args": 'dbox/notes/test.md"}',
                        "id": "tc-chunk-1",
                        "index": 0,
                    }
                ],
            ),
        ]
        seen = set()
        accumulated = []
        # 每个 chunk 都尝试提取，但因 args 不完整（parse_partial_json 返回 {}）不发射
        for chunk in chunks:
            extract_tool_events_from_message(chunk, seen, accumulated)
        # 阶段 1：仅 chunk 阶段，INPUT_READY 应未发射（args 不完整）
        self.assertEqual(len(seen), 0)
        self.assertEqual(len(accumulated), 3)

        # 模拟工具执行完成后的 ToolMessage
        tool_msg = ToolMessage(
            content="file content here",
            tool_call_id="tc-chunk-1",
            name="read_file",
        )
        events = extract_tool_events_from_message(tool_msg, seen, accumulated)

        # 应有 2 个事件：补发 INPUT_READY + COMPLETED
        self.assertEqual(len(events), 2)
        input_event = events[0]
        completed_event = events[1]
        self.assertEqual(input_event["event_type"], EventType.TOOL_CALL_INPUT_READY)
        # 参数应被聚合为完整 JSON
        self.assertEqual(
            input_event["parameters"],
            {"file_path": "/sandbox/notes/test.md"},
        )
        self.assertEqual(completed_event["event_type"], EventType.TOOL_CALL_COMPLETED)
        self.assertEqual(completed_event["result"], "file content here")

    def test_chunk_with_complete_args_emits_input_ready_immediately(self):
        """单个 chunk 含完整 args → 立即发射 INPUT_READY。"""
        chunk = AIMessageChunk(
            content="",
            tool_call_chunks=[
                {
                    "name": "bash",
                    "args": '{"command": "ls -la"}',
                    "id": "tc-complete-1",
                    "index": 0,
                }
            ],
        )
        seen = set()
        events = extract_tool_events_from_message(chunk, seen, [])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], EventType.TOOL_CALL_INPUT_READY)
        self.assertEqual(events[0]["parameters"], {"command": "ls -la"})

    def test_chunk_with_partial_json_args_does_not_emit_input_ready(self):
        r"""chunk args 为可被 parse_partial_json 解析的部分 JSON → 不发射 INPUT_READY。

        关键 bug 复现场景：
        - chunk args = ``{"file_path": "/san``
        - parse_partial_json 会将其解析为 ``{"file_path": "/san"}``（非空 dict）
        - 但参数不完整（value 被截断），不应发射 INPUT_READY

        修复前：extract_tool_events_from_message 通过 AIMessageChunk.tool_calls
        读取 args（已被 parse_partial_json 预解析为非空 dict），导致
        INPUT_READY 携带不完整参数过早发射。
        修复后：对 AIMessageChunk 直接从 tool_call_chunks 用严格 json.loads
        解析 args，仅完整 JSON 才发射 INPUT_READY。
        """
        chunk = AIMessageChunk(
            content="",
            tool_call_chunks=[
                {
                    "name": "read_file",
                    "args": '{"file_path": "/san',
                    "id": "tc-partial-1",
                    "index": 0,
                }
            ],
        )
        seen = set()
        accumulated = []
        events = extract_tool_events_from_message(chunk, seen, accumulated)
        # args 不完整（虽可被 parse_partial_json 解析为部分 dict），不应发射
        self.assertEqual(len(events), 0)
        self.assertNotIn("tc-partial-1", seen)
        # chunk 仍应被累积，供 ToolMessage 阶段聚合
        self.assertEqual(len(accumulated), 1)

    def test_chunk_partial_then_complete_aggregates_at_tool_message(self):
        r"""部分 chunk + 完整 chunk → ToolMessage 阶段聚合完整参数。

        - chunk1: ``{"file_path": "/san`` （parse_partial_json → {"file_path": "/san"}，
          不完整，不发射）
        - chunk2: ``dbox/notes/test.md"}`` （补全 args）
        - ToolMessage: 聚合得到 {"file_path": "/sandbox/notes/test.md"}，补发 INPUT_READY
        """
        chunks = [
            AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                        "name": "read_file",
                        "args": '{"file_path": "/san',
                        "id": "tc-partial-2",
                        "index": 0,
                    }
                ],
            ),
            AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                        "name": "read_file",
                        "args": 'dbox/notes/test.md"}',
                        "id": "tc-partial-2",
                        "index": 0,
                    }
                ],
            ),
        ]
        seen = set()
        accumulated = []
        for chunk in chunks:
            events = extract_tool_events_from_message(chunk, seen, accumulated)
            self.assertEqual(len(events), 0)  # 两个 chunk 都不应发射
        self.assertNotIn("tc-partial-2", seen)

        tool_msg = ToolMessage(
            content="file content",
            tool_call_id="tc-partial-2",
            name="read_file",
        )
        events = extract_tool_events_from_message(tool_msg, seen, accumulated)
        self.assertEqual(len(events), 2)  # 补发 INPUT_READY + COMPLETED
        self.assertEqual(events[0]["event_type"], EventType.TOOL_CALL_INPUT_READY)
        self.assertEqual(
            events[0]["parameters"],
            {"file_path": "/sandbox/notes/test.md"},
        )


# ============================================================================
# 测试 3: ToolMessage 成功结果
# ============================================================================


class ToolMessageCompletedTests(unittest.TestCase):
    """ToolMessage 成功结果 → COMPLETED 事件。"""

    def test_completed_event_carries_result(self):
        """COMPLETED 事件携带 result 字段。"""
        # 先发 INPUT_READY
        ai_msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"file_path": "/a.txt"},
                    "id": "tc-comp-1",
                }
            ],
        )
        seen = set()
        accumulated = []
        extract_tool_events_from_message(ai_msg, seen, accumulated)

        # 工具结果
        tool_msg = ToolMessage(
            content="hello world",
            tool_call_id="tc-comp-1",
            name="read_file",
        )
        events = extract_tool_events_from_message(tool_msg, seen, accumulated)
        self.assertEqual(len(events), 1)
        evt = events[0]
        self.assertEqual(evt["event_type"], EventType.TOOL_CALL_COMPLETED)
        self.assertEqual(evt["result"], "hello world")
        self.assertEqual(evt["tool_call_id"], "tc-comp-1")
        # parameters 也应被携带（聚合自累积 chunk 或 AIMessage.tool_calls）
        self.assertEqual(evt["parameters"], {"file_path": "/a.txt"})

    def test_completed_with_list_content(self):
        """ToolMessage content 为 list → 序列化为 JSON 字符串。"""
        ai_msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "ls",
                    "args": {"path": "/sandbox/"},
                    "id": "tc-list-1",
                }
            ],
        )
        seen = set()
        accumulated = []
        extract_tool_events_from_message(ai_msg, seen, accumulated)

        tool_msg = ToolMessage(
            content=[{"name": "file1.txt", "size": 100}, {"name": "file2.txt", "size": 200}],
            tool_call_id="tc-list-1",
            name="ls",
        )
        events = extract_tool_events_from_message(tool_msg, seen, accumulated)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], EventType.TOOL_CALL_COMPLETED)
        # list content 应被 JSON 序列化
        result = events[0]["result"]
        self.assertIsInstance(result, str)
        parsed = json.loads(result)
        self.assertEqual(len(parsed), 2)


# ============================================================================
# 测试 4: ToolMessage 失败结果
# ============================================================================


class ToolMessageFailedTests(unittest.TestCase):
    """ToolMessage 失败结果 → FAILED 事件。"""

    def test_failed_event_with_error_status(self):
        """ToolMessage status='error' → FAILED 事件携带 error。"""
        ai_msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write_file",
                    "args": {"file_path": "/forbidden/path", "content": "x"},
                    "id": "tc-fail-1",
                }
            ],
        )
        seen = set()
        accumulated = []
        extract_tool_events_from_message(ai_msg, seen, accumulated)

        tool_msg = ToolMessage(
            content="Permission denied: /forbidden/path",
            tool_call_id="tc-fail-1",
            name="write_file",
            status="error",
        )
        events = extract_tool_events_from_message(tool_msg, seen, accumulated)
        self.assertEqual(len(events), 1)
        evt = events[0]
        self.assertEqual(evt["event_type"], EventType.TOOL_CALL_FAILED)
        self.assertEqual(evt["error"], "Permission denied: /forbidden/path")
        self.assertEqual(evt["tool_call_id"], "tc-fail-1")

    def test_failed_event_with_error_prefix_content(self):
        """ToolMessage content 以 'Error' 开头 → FAILED 事件。"""
        ai_msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "bash",
                    "args": {"command": "invalid_cmd"},
                    "id": "tc-fail-2",
                }
            ],
        )
        seen = set()
        accumulated = []
        extract_tool_events_from_message(ai_msg, seen, accumulated)

        tool_msg = ToolMessage(
            content="Error: command not found: invalid_cmd",
            tool_call_id="tc-fail-2",
            name="bash",
        )
        events = extract_tool_events_from_message(tool_msg, seen, accumulated)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], EventType.TOOL_CALL_FAILED)
        self.assertIn("command not found", events[0]["error"])


# ============================================================================
# 测试 5: 多工具并行调用
# ============================================================================


class ParallelToolCallsTests(unittest.TestCase):
    """多工具并行调用的事件提取。"""

    def test_multiple_tool_calls_in_single_aimessage(self):
        """单个 AIMessage 含多个 tool_calls → 多个 INPUT_READY 事件。"""
        msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"file_path": "/a.txt"},
                    "id": "tc-parallel-1",
                },
                {
                    "name": "bash",
                    "args": {"command": "ls -la"},
                    "id": "tc-parallel-2",
                },
                {
                    "name": "write_file",
                    "args": {"file_path": "/b.txt", "content": "hi"},
                    "id": "tc-parallel-3",
                },
            ],
        )
        events = extract_tool_events_from_message(msg, set(), [])
        self.assertEqual(len(events), 3)
        tool_names = [e["tool_name"] for e in events]
        self.assertIn("read_file", tool_names)
        self.assertIn("bash", tool_names)
        self.assertIn("write_file", tool_names)
        # 每个事件都应有独立的 tool_call_id
        ids = [e["tool_call_id"] for e in events]
        self.assertEqual(len(set(ids)), 3)

    def test_multiple_tool_results_independently_tracked(self):
        """多个工具结果按各自 tool_call_id 独立发射 COMPLETED。"""
        ai_msg = AIMessage(
            content="",
            tool_calls=[
                {"name": "read_file", "args": {"file_path": "/a"}, "id": "tc-r-1"},
                {"name": "read_file", "args": {"file_path": "/b"}, "id": "tc-r-2"},
            ],
        )
        seen = set()
        accumulated = []
        extract_tool_events_from_message(ai_msg, seen, accumulated)

        # 第一个工具结果
        tool_msg_1 = ToolMessage(
            content="content-a",
            tool_call_id="tc-r-1",
            name="read_file",
        )
        events_1 = extract_tool_events_from_message(tool_msg_1, seen, accumulated)
        self.assertEqual(len(events_1), 1)
        self.assertEqual(events_1[0]["tool_call_id"], "tc-r-1")
        self.assertEqual(events_1[0]["result"], "content-a")

        # 第二个工具结果
        tool_msg_2 = ToolMessage(
            content="content-b",
            tool_call_id="tc-r-2",
            name="read_file",
        )
        events_2 = extract_tool_events_from_message(tool_msg_2, seen, accumulated)
        self.assertEqual(len(events_2), 1)
        self.assertEqual(events_2[0]["tool_call_id"], "tc-r-2")
        self.assertEqual(events_2[0]["result"], "content-b")


# ============================================================================
# 测试 6: 非工具消息类型
# ============================================================================


class NonToolMessageTests(unittest.TestCase):
    """非工具消息类型应被忽略。"""

    def test_human_message_ignored(self):
        """HumanMessage 不应触发任何工具事件。"""
        from langchain_core.messages import HumanMessage

        msg = HumanMessage(content="hello")
        events = extract_tool_events_from_message(msg, set(), [])
        self.assertEqual(len(events), 0)

    def test_system_message_ignored(self):
        """SystemMessage 不应触发任何工具事件。"""
        from langchain_core.messages import SystemMessage

        msg = SystemMessage(content="system prompt")
        events = extract_tool_events_from_message(msg, set(), [])
        self.assertEqual(len(events), 0)

    def test_string_message_ignored(self):
        """纯字符串消息不应触发任何工具事件。"""
        events = extract_tool_events_from_message("just a string", set(), [])
        self.assertEqual(len(events), 0)


# ============================================================================
# 测试 7: deepagents 原生工具 I/O 完整性（端到端验证）
# ============================================================================


class DeepAgentsNativeToolIOTests(unittest.TestCase):
    """deepagents 原生工具 I/O 完整性验证。

    覆盖 deepagents 内置工具（read_file / write_file / edit_file / bash /
    ls / glob / grep / task）的输入参数与输出结果提取。
    """

    def test_read_file_io(self):
        """read_file: 输入 file_path，输出文件内容。"""
        ai_msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"file_path": "/reports/final.md"},
                    "id": "tc-io-read",
                }
            ],
        )
        seen = set()
        accumulated = []
        events = extract_tool_events_from_message(ai_msg, seen, accumulated)
        self.assertEqual(events[0]["parameters"], {"file_path": "/reports/final.md"})

        tool_msg = ToolMessage(
            content="# Final Report\n\nDone.",
            tool_call_id="tc-io-read",
            name="read_file",
        )
        events = extract_tool_events_from_message(tool_msg, seen, accumulated)
        self.assertEqual(events[0]["result"], "# Final Report\n\nDone.")

    def test_write_file_io(self):
        """write_file: 输入 file_path + content，输出写入确认。"""
        ai_msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write_file",
                    "args": {
                        "file_path": "/sandbox/notes/note.md",
                        "content": "# Note\n\n- item 1\n- item 2",
                    },
                    "id": "tc-io-write",
                }
            ],
        )
        events = extract_tool_events_from_message(ai_msg, set(), [])
        self.assertEqual(events[0]["parameters"]["file_path"], "/sandbox/notes/note.md")
        self.assertEqual(events[0]["parameters"]["content"], "# Note\n\n- item 1\n- item 2")

    def test_edit_file_io(self):
        """edit_file: 输入 file_path + old_string + new_string。"""
        ai_msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "edit_file",
                    "args": {
                        "file_path": "/reports/draft.md",
                        "old_string": "TODO",
                        "new_string": "Done",
                        "replace_all": False,
                    },
                    "id": "tc-io-edit",
                }
            ],
        )
        events = extract_tool_events_from_message(ai_msg, set(), [])
        self.assertEqual(events[0]["parameters"]["old_string"], "TODO")
        self.assertEqual(events[0]["parameters"]["new_string"], "Done")
        self.assertEqual(events[0]["parameters"]["replace_all"], False)

    def test_bash_io(self):
        """bash: 输入 command，输出命令执行结果。"""
        ai_msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "bash",
                    "args": {"command": "echo 'hello' && ls -la"},
                    "id": "tc-io-bash",
                }
            ],
        )
        seen = set()
        accumulated = []
        extract_tool_events_from_message(ai_msg, seen, accumulated)

        tool_msg = ToolMessage(
            content="hello\ntotal 0\ndrwxr-xr-x 1 user group 0",
            tool_call_id="tc-io-bash",
            name="bash",
        )
        events = extract_tool_events_from_message(tool_msg, seen, accumulated)
        self.assertEqual(events[0]["event_type"], EventType.TOOL_CALL_COMPLETED)
        self.assertIn("hello", events[0]["result"])

    def test_ls_io(self):
        """ls: 输入 path，输出目录列表。"""
        ai_msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "ls",
                    "args": {"path": "/sandbox/"},
                    "id": "tc-io-ls",
                }
            ],
        )
        events = extract_tool_events_from_message(ai_msg, set(), [])
        self.assertEqual(events[0]["parameters"]["path"], "/sandbox/")

    def test_glob_io(self):
        """glob: 输入 pattern + path，输出匹配文件列表。"""
        ai_msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "glob",
                    "args": {"pattern": "**/*.md", "path": "/reports/"},
                    "id": "tc-io-glob",
                }
            ],
        )
        events = extract_tool_events_from_message(ai_msg, set(), [])
        self.assertEqual(events[0]["parameters"]["pattern"], "**/*.md")
        self.assertEqual(events[0]["parameters"]["path"], "/reports/")

    def test_grep_io(self):
        """grep: 输入 pattern + path + include，输出匹配行。"""
        ai_msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "grep",
                    "args": {
                        "pattern": "TODO",
                        "path": "/reports/",
                        "include": "*.md",
                    },
                    "id": "tc-io-grep",
                }
            ],
        )
        events = extract_tool_events_from_message(ai_msg, set(), [])
        self.assertEqual(events[0]["parameters"]["pattern"], "TODO")
        self.assertEqual(events[0]["parameters"]["include"], "*.md")

    def test_task_tool_io(self):
        """task: 子智能体调用工具，输入 description + subagent_type。"""
        ai_msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "task",
                    "args": {
                        "description": "search the web for latest news",
                        "subagent_type": "web-researcher",
                    },
                    "id": "tc-io-task",
                }
            ],
        )
        events = extract_tool_events_from_message(ai_msg, set(), [])
        self.assertEqual(events[0]["parameters"]["subagent_type"], "web-researcher")
        self.assertEqual(
            events[0]["parameters"]["description"],
            "search the web for latest news",
        )

    def test_write_file_with_long_content_io(self):
        """write_file 长内容参数：验证大段 content 不被截断。"""
        long_content = "# Report\n\n" + "line\n" * 1000
        ai_msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write_file",
                    "args": {
                        "file_path": "/reports/long.md",
                        "content": long_content,
                    },
                    "id": "tc-io-long",
                }
            ],
        )
        events = extract_tool_events_from_message(ai_msg, set(), [])
        self.assertEqual(events[0]["parameters"]["content"], long_content)
        self.assertEqual(len(events[0]["parameters"]["content"]), len(long_content))


# ============================================================================
# 测试 8: 端到端流程模拟（chunk → tool_message）
# ============================================================================


class EndToEndFlowTests(unittest.TestCase):
    """端到端流程：模拟 deepagents LLM 流式输出 + 工具执行。"""

    def test_full_flow_chunked_args_to_completed(self):
        """完整流程：分片 chunk args → ToolMessage → COMPLETED 携带完整参数。

        模拟 deepagents LLM 流式输出：
        - chunk1: ``{"file_path":`` （parse_partial_json 返回 {}，不发射）
        - chunk2: `` "/reports/final.md"}`` （args 完整）

        ToolMessage 阶段聚合 + 补发 INPUT_READY + COMPLETED。
        """
        chunks = [
            AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                        "name": "read_file",
                        "args": '{"file_path":',
                        "id": "tc-e2e-1",
                        "index": 0,
                    }
                ],
            ),
            AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                        "name": "read_file",
                        "args": ' "/reports/final.md"}',
                        "id": "tc-e2e-1",
                        "index": 0,
                    }
                ],
            ),
        ]
        seen = set()
        accumulated = []

        # 阶段 1：处理 chunks（args 不完整，INPUT_READY 不发射）
        for chunk in chunks:
            events = extract_tool_events_from_message(chunk, seen, accumulated)
            self.assertEqual(len(events), 0)  # args 不完整不发射
        self.assertEqual(len(accumulated), 2)

        # 阶段 2：工具执行完成，ToolMessage 触发参数聚合 + 补发 + COMPLETED
        tool_msg = ToolMessage(
            content="# Final Report\n\nAll done.",
            tool_call_id="tc-e2e-1",
            name="read_file",
        )
        events = extract_tool_events_from_message(tool_msg, seen, accumulated)

        # 应有 2 个事件：补发 INPUT_READY + COMPLETED
        self.assertEqual(len(events), 2)

        # 第一个：补发的 INPUT_READY
        input_evt = events[0]
        self.assertEqual(input_evt["event_type"], EventType.TOOL_CALL_INPUT_READY)
        self.assertEqual(
            input_evt["parameters"],
            {"file_path": "/reports/final.md"},
        )

        # 第二个：COMPLETED
        completed_evt = events[1]
        self.assertEqual(completed_evt["event_type"], EventType.TOOL_CALL_COMPLETED)
        self.assertEqual(completed_evt["result"], "# Final Report\n\nAll done.")

    def test_full_flow_complete_args_to_completed(self):
        """完整流程：完整 args 的 AIMessage → ToolMessage → COMPLETED。"""
        ai_msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "bash",
                    "args": {"command": "echo hello"},
                    "id": "tc-e2e-2",
                }
            ],
        )
        seen = set()
        accumulated = []

        # 阶段 1：AIMessage 立即发射 INPUT_READY
        events_1 = extract_tool_events_from_message(ai_msg, seen, accumulated)
        self.assertEqual(len(events_1), 1)
        self.assertEqual(events_1[0]["event_type"], EventType.TOOL_CALL_INPUT_READY)
        self.assertIn("tc-e2e-2", seen)

        # 阶段 2：ToolMessage 只发射 COMPLETED（INPUT_READY 已发射，不补发）
        tool_msg = ToolMessage(
            content="hello",
            tool_call_id="tc-e2e-2",
            name="bash",
        )
        events_2 = extract_tool_events_from_message(tool_msg, seen, accumulated)
        self.assertEqual(len(events_2), 1)
        self.assertEqual(events_2[0]["event_type"], EventType.TOOL_CALL_COMPLETED)
        self.assertEqual(events_2[0]["result"], "hello")


if __name__ == "__main__":
    unittest.main()
