"""工具事件提取器单元测试（spec unify-tool-call-pipeline 任务 2）。

覆盖有状态唯一提取器 ``ToolEventExtractor`` 与无状态兼容入口
``extract_tool_events_from_message`` 的核心行为：

1. chunk args 分片聚合成完整参数后才发射 PENDING
2. 不完整 JSON 不发射（严格 json.loads）
3. 全空字符串参数占位跳过（不发射、不加入 seen）
4. Groq 名称/内联参数修复（完整 AIMessage 路径）
5. 无 id chunk 暂存 + id 到达归并（含跨轮次 index 隔离）
6. ToolMessage 阶段补发 PENDING
7. 终态判定（completed / failed / 本地化错误前缀 / 审批超时·拒绝关键字跳过）
8. 模块级兼容入口委托到同一实现

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python -m unittest Django_xm.apps.tools.tests.test_tool_event_extractor
或：
    python manage.py test Django_xm.apps.tools --settings=Django_xm.settings.test
"""

import os
import unittest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from Django_xm.apps.tools.tool_event_extractor import (
    ToolEventExtractor,
    extract_tool_events_from_message,
)
from Django_xm.common.event_schema import EventType


def _chunk(tool_call_chunks: list[dict]) -> AIMessageChunk:
    return AIMessageChunk(content="", tool_call_chunks=tool_call_chunks)


class ToolEventExtractorTests(unittest.TestCase):
    """有状态提取器核心行为。"""

    def test_chunk_args_fragments_aggregate(self):
        """① args 分片跨 chunk 聚合，参数完整才发射一次 PENDING。"""
        extractor = ToolEventExtractor()
        first = extractor.feed(
            _chunk([{"name": "shell_exec", "args": '{"path":', "id": "c1", "index": 0}])
        )
        self.assertEqual(first, [])
        second = extractor.feed(
            _chunk([{"name": None, "args": '"/tmp"}', "id": None, "index": 0}])
        )
        self.assertEqual(len(second), 1)
        evt = second[0]
        self.assertEqual(evt["event_type"], EventType.TOOL_CALL_PENDING)
        self.assertEqual(evt["tool_call_id"], "c1")
        self.assertEqual(evt["tool_name"], "shell_exec")
        self.assertEqual(evt["parameters"], {"path": "/tmp"})

    def test_incomplete_json_not_emitted(self):
        """② 不完整 JSON 不发射，也不加入 seen。"""
        extractor = ToolEventExtractor()
        events = extractor.feed(
            _chunk([{"name": "t", "args": '{"path": "/san', "id": "c2", "index": 0}])
        )
        self.assertEqual(events, [])
        self.assertNotIn("c2", extractor.seen_tool_call_ids)

    def test_all_empty_string_parameters_skipped(self):
        """③ 全部 string 值为空串的占位参数跳过（不发射、不加入 seen）。"""
        extractor = ToolEventExtractor()
        events = extractor.feed(
            _chunk(
                [
                    {
                        "name": "write_file",
                        "args": '{"relative_path":"","content":""}',
                        "id": "c3",
                        "index": 0,
                    }
                ]
            )
        )
        self.assertEqual(events, [])
        self.assertNotIn("c3", extractor.seen_tool_call_ids)

        # 存在非空 string 值时正常发射
        events = extractor.feed(
            _chunk(
                [
                    {
                        "name": "write_file",
                        "args": '{"relative_path":"a.txt","content":""}',
                        "id": "c4",
                        "index": 1,
                    }
                ]
            )
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["parameters"], {"relative_path": "a.txt", "content": ""})

    def test_groq_tool_call_name_fix(self):
        """④ 完整 AIMessage 路径修复 Groq 内联参数拼接的 name。"""
        extractor = ToolEventExtractor()
        message = AIMessage(
            content="",
            tool_calls=[{"name": 'shell_exec{"path": "/tmp"}', "args": {}, "id": "g1"}],
        )
        events = extractor.feed(message)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], EventType.TOOL_CALL_PENDING)
        self.assertEqual(events[0]["tool_name"], "shell_exec")
        self.assertEqual(events[0]["parameters"], {"path": "/tmp"})

    def test_no_id_chunk_staged_then_merged_when_id_arrives(self):
        """⑤ 无 id chunk 先暂存，id 到达后归并（同 index）。"""
        extractor = ToolEventExtractor()
        first = extractor.feed(
            _chunk([{"name": "shell_exec", "args": '{"path":', "id": None, "index": 0}])
        )
        self.assertEqual(first, [])
        # 暂存缓冲持有未归属分片
        self.assertTrue(extractor._staged)

        second = extractor.feed(
            _chunk([{"name": "shell_exec", "args": '"/tmp"}', "id": "c5", "index": 0}])
        )
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0]["tool_call_id"], "c5")
        self.assertEqual(second[0]["parameters"], {"path": "/tmp"})
        self.assertFalse(extractor._staged)

        # 同一 tool_call_id 只发射一次 PENDING
        third = extractor.feed(
            _chunk([{"name": "shell_exec", "args": "", "id": "c5", "index": 0}])
        )
        self.assertEqual(third, [])

    def test_no_id_stage_key_isolated_per_round(self):
        """⑤b 暂存键含轮次标识，跨 LLM 轮次 index 复用时不会错误拼接。"""
        extractor = ToolEventExtractor()
        key_round0 = extractor._stage_key(0, "a")
        extractor.feed(ToolMessage(content="ok", tool_call_id="ignored", name="a"))
        key_round1 = extractor._stage_key(0, "a")
        self.assertNotEqual(key_round0, key_round1)

    def test_tool_message_reemits_pending_and_completes(self):
        """⑥ chunk 阶段未发射时，ToolMessage 补发 PENDING 并发射 COMPLETED。"""
        extractor = ToolEventExtractor()
        first = extractor.feed(
            _chunk([{"name": "t", "args": '{"q":', "id": "c6", "index": 0}])
        )
        self.assertEqual(first, [])

        events = extractor.feed(ToolMessage(content="done", tool_call_id="c6", name="t"))
        types = [e["event_type"] for e in events]
        self.assertEqual(types, [EventType.TOOL_CALL_PENDING, EventType.TOOL_CALL_COMPLETED])
        self.assertIsInstance(events[0]["parameters"], dict)
        self.assertEqual(events[1]["result"], "done")
        self.assertEqual(events[1]["parameters"], events[0]["parameters"])

    def test_completed_terminal_event(self):
        """⑦a 正常执行 → COMPLETED（PENDING 只发射一次）。"""
        extractor = ToolEventExtractor()
        pending = extractor.feed(
            _chunk([{"name": "t", "args": '{"a":1}', "id": "c7", "index": 0}])
        )
        self.assertEqual([e["event_type"] for e in pending], [EventType.TOOL_CALL_PENDING])

        events = extractor.feed(ToolMessage(content="ok", tool_call_id="c7", name="t"))
        self.assertEqual([e["event_type"] for e in events], [EventType.TOOL_CALL_COMPLETED])
        self.assertEqual(events[0]["result"], "ok")
        self.assertEqual(events[0]["parameters"], {"a": 1})

    def test_failed_terminal_event_by_status_and_prefix(self):
        """⑦b status=='error' 或 content 以 'Error' 开头 → FAILED。"""
        extractor = ToolEventExtractor()
        extractor.feed(_chunk([{"name": "t", "args": '{"a":1}', "id": "c8", "index": 0}]))
        events = extractor.feed(
            ToolMessage(content="boom", tool_call_id="c8", name="t", status="error")
        )
        self.assertEqual([e["event_type"] for e in events], [EventType.TOOL_CALL_FAILED])
        self.assertEqual(events[0]["error"], "boom")

        extractor2 = ToolEventExtractor()
        extractor2.feed(_chunk([{"name": "t", "args": '{"a":1}', "id": "c8b", "index": 0}]))
        events2 = extractor2.feed(
            ToolMessage(content="Error: failed", tool_call_id="c8b", name="t")
        )
        self.assertEqual([e["event_type"] for e in events2], [EventType.TOOL_CALL_FAILED])

    def test_failed_terminal_event_by_localized_prefixes(self):
        """⑦b-2 本地化错误前缀（中文/英文，含首尾空白）→ FAILED（不误判为 COMPLETED）。"""
        prefixes = ["错误：", "Error:", "ERROR:", "FAILED", "失败:", "异常:", "Exception:"]
        for idx, prefix in enumerate(prefixes):
            extractor = ToolEventExtractor()
            tc_id = f"c8p{idx}"
            extractor.feed(_chunk([{"name": "t", "args": '{"a":1}', "id": tc_id, "index": 0}]))
            events = extractor.feed(
                ToolMessage(content=f"  {prefix}执行失败", tool_call_id=tc_id, name="t")
            )
            self.assertEqual(
                [e["event_type"] for e in events],
                [EventType.TOOL_CALL_FAILED],
                msg=f"前缀 {prefix!r} 应判定为 FAILED",
            )

        # 非错误前缀 → COMPLETED（不误伤）
        extractor = ToolEventExtractor()
        extractor.feed(_chunk([{"name": "t", "args": '{"a":1}', "id": "c8ok", "index": 0}]))
        ok_events = extractor.feed(
            ToolMessage(content="查询成功，返回 3 条结果", tool_call_id="c8ok", name="t")
        )
        self.assertEqual([e["event_type"] for e in ok_events], [EventType.TOOL_CALL_COMPLETED])

    def test_approval_final_keywords_skip_terminal(self):
        """⑦c 审批超时/拒绝关键字 → 仅补发 PENDING，不判定 FAILED/COMPLETED。"""
        for keyword in ("审批超时，请调整策略", "用户已拒绝本次操作"):
            extractor = ToolEventExtractor()
            events = extractor.feed(
                ToolMessage(content=keyword, tool_call_id="c9", name="t", status="error")
            )
            self.assertEqual([e["event_type"] for e in events], [EventType.TOOL_CALL_PENDING])


class ModuleFunctionDelegationTests(unittest.TestCase):
    """无状态兼容入口委托到同一实现。"""

    def test_pending_emitted_and_state_shared(self):
        seen: set = set()
        accumulated: list = []
        events = extract_tool_events_from_message(
            _chunk([{"name": "t", "args": '{"a":1}', "id": "m1", "index": 0}]),
            seen,
            accumulated,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["tool_call_id"], "m1")
        self.assertIn("m1", seen)
        self.assertEqual(len(accumulated), 1)

    def test_tool_message_appends_and_emits_terminal(self):
        seen: set = set()
        accumulated: list = []
        extract_tool_events_from_message(
            _chunk([{"name": "t", "args": '{"a":1}', "id": "m2", "index": 0}]),
            seen,
            accumulated,
        )
        events = extract_tool_events_from_message(
            ToolMessage(content="ok", tool_call_id="m2", name="t"),
            seen,
            accumulated,
        )
        self.assertEqual([e["event_type"] for e in events], [EventType.TOOL_CALL_COMPLETED])
        self.assertEqual(len(accumulated), 2)

    def test_no_id_chunk_without_later_id_yields_nothing(self):
        """无状态入口单条调用不跨消息保留暂存：无 id 分片仍不发射（行为保持）。"""
        events = extract_tool_events_from_message(
            _chunk([{"name": "t", "args": '{"a":', "id": None, "index": 0}]),
            set(),
            [],
        )
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
