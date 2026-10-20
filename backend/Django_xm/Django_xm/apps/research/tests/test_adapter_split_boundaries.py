"""E6 三边界拆分结构守护测试（adapter.py → result_extraction / resume_protocol / event_publishing）。

拆分为行为等价重构：等价性主要判据是既有测试零修改全绿
（test_session_budget / test_broadcast_persist_history / test_session_executor 等均未改动）。
本文件为补充的结构测试：验证三边界单元可独立实例化/调用，并锁定迁移体的
关键行为分支——其中 resume_protocol 的分组/暂停恢复分支与短回复兜底参数化
在拆分前无直接单测（既有覆盖缺口），拆分后成为可独立单测的纯函数。

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.apps.research.tests.test_adapter_split_boundaries
"""

import asyncio
import os
import tempfile
import unittest
from typing import ClassVar
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langchain_core.messages import AIMessageChunk

from Django_xm.apps.research.services.adapter import OfficialDeepAgentAdapter
from Django_xm.apps.research.services.result_extraction import (
    apply_short_report_disk_fallback,
    extract_files_info,
    scan_disk_files,
)
from Django_xm.apps.research.services.resume_protocol import handle_resume_interrupt

_EVENT_PUB_MODULE = "Django_xm.common.tool_event_publisher"
_WRITEBACK_MODULE = "Django_xm.apps.research.services.writeback"
_EVENT_PUBLISHING_MODULE = "Django_xm.apps.research.services.event_publishing"


def _approval_request(tool_call_id="tc-1", tool_name="shell_exec"):
    """单条审批请求（middleware.py 形状的最小子集）。"""
    return {"tool_name": tool_name, "tool_call_id": tool_call_id}


def _approval_value(requests, meta_graph_id="gi-1"):
    """ApprovalMiddleware 批量 interrupt 值形状。"""
    return {"_approval": True, "requests": requests, "_meta": {"graph_interrupt_id": meta_graph_id}}


class _FakeTimeoutMgr:
    """ExecutionTimeoutManager 替身：记录 pause/resume 次数。"""

    def __init__(self):
        self.paused = 0
        self.resumed = 0

    def pause(self):
        self.paused += 1

    def resume(self):
        self.resumed += 1


def _make_adapter(**kwargs) -> OfficialDeepAgentAdapter:
    return OfficialDeepAgentAdapter(graph=object(), thread_id="task-1", **kwargs)


class ShortReportFallbackTests(unittest.TestCase):
    """短回复磁盘兜底参数化（原 adapter 内 3 处逐字重复块归一后的行为锁定）。"""

    def test_replaces_short_report_with_disk_report(self):
        final = apply_short_report_disk_fallback(
            "好的",
            {"reports/final_report.md": {"content": "完整报告正文，长度超过短回复。"}},
        )
        self.assertEqual(final, "完整报告正文，长度超过短回复。")

    def test_falls_back_to_second_candidate_key(self):
        final = apply_short_report_disk_fallback("好的", {"final_report.md": {"content": "第二候选报告"}})
        self.assertEqual(final, "第二候选报告")

    def test_keeps_original_when_disk_content_not_longer(self):
        final = apply_short_report_disk_fallback("好的", {"reports/final_report.md": {"content": "短"}})
        self.assertEqual(final, "好的")

    def test_no_fallback_when_report_not_short(self):
        long_report = "x" * 200
        final = apply_short_report_disk_fallback(
            long_report, {"reports/final_report.md": {"content": "y" * 500}}
        )
        self.assertEqual(final, long_report)

    def test_breaks_after_first_present_key(self):
        # 首个存在的候选 key 命中后即停止（无论是否替换）：
        # reports/ 内容不比当前回复长 → 不替换；即使第二候选更长也不采用
        final = apply_short_report_disk_fallback(
            "好的",
            {"reports/final_report.md": {"content": "ab"}, "final_report.md": {"content": "abc"}},
        )
        self.assertEqual(final, "好的")

    def test_noop_without_disk_files(self):
        self.assertEqual(apply_short_report_disk_fallback("好的", {}), "好的")


class ResultExtractionTests(unittest.TestCase):
    """结果提取纯函数（原 adapter 私有方法迁移后可独立调用）。"""

    def test_extract_files_info_from_write_file_tool_calls(self):
        class _Msg:
            tool_calls: ClassVar[list] = [
                {"name": "write_file", "args": {"path": "reports/final.md", "content": "12345"}},
                {"name": "web_search", "args": {"query": "x"}},
            ]

        files = extract_files_info({"messages": [_Msg()]})
        self.assertEqual(
            files,
            [{"path": "reports/final.md", "name": "final.md", "type": "file", "size": 5}],
        )

    def test_scan_disk_files_reads_notes_and_reports_with_forward_slash(self):
        with tempfile.TemporaryDirectory() as work_dir:
            os.makedirs(os.path.join(work_dir, "notes"))
            os.makedirs(os.path.join(work_dir, "reports", "2026"))
            with open(os.path.join(work_dir, "notes", "a.md"), "w", encoding="utf-8") as f:
                f.write("笔记")
            with open(
                os.path.join(work_dir, "reports", "2026", "final_report.md"), "w", encoding="utf-8"
            ) as f:
                f.write("报告正文")
            files = scan_disk_files(work_dir)
        self.assertEqual(files["notes/a.md"], {"content": "笔记", "encoding": "utf-8"})
        self.assertEqual(
            files["reports/2026/final_report.md"], {"content": "报告正文", "encoding": "utf-8"}
        )

    def test_scan_disk_files_empty_inputs(self):
        self.assertEqual(scan_disk_files(None), {})
        self.assertEqual(scan_disk_files("Z:/definitely/not/exist"), {})


class ResumeProtocolTests(unittest.TestCase):
    """审批中断 / 业务等待挂起协议（原 _handle_updates 内联逻辑迁移后的分支锁定）。"""

    def test_approval_decision_grouped_by_langgraph_resume_id(self):
        intr = {"value": _approval_value([_approval_request()]), "id": "int-1"}
        tm = _FakeTimeoutMgr()
        seen = []

        async def on_interrupt(interrupts):
            seen.append(interrupts)
            return {"tc-1": True}

        resume_values: dict = {}
        info = asyncio.run(
            handle_resume_interrupt(intr, on_interrupt=on_interrupt, timeout_mgr=tm, resume_values=resume_values)
        )

        self.assertIsNone(info)
        self.assertEqual(resume_values, {"int-1": {"tc-1": True}})
        # 回调期间暂停超时计时（pause → resume 各一次）
        self.assertEqual((tm.paused, tm.resumed), (1, 1))
        # 回调收到的是解析后的 interrupt_list（tool_name 透传）
        self.assertEqual(seen[0][0]["tool_name"], "shell_exec")

    def test_approval_sync_callback_and_empty_langgraph_id_branch(self):
        # intr 直接为审批 dict（无 value/id 包装）→ interrupt_id 为空 →
        # resume_values 以 interrupt_id（=tool_call_id）为平铺 key
        intr = _approval_value([_approval_request()])
        tm = _FakeTimeoutMgr()

        def on_interrupt(interrupts):
            return {"tc-1": False}

        resume_values: dict = {}
        info = asyncio.run(
            handle_resume_interrupt(intr, on_interrupt=on_interrupt, timeout_mgr=tm, resume_values=resume_values)
        )
        self.assertIsNone(info)
        self.assertEqual(resume_values, {"tc-1": False})

    def test_langgraph_interrupt_instance_normalized(self):
        from langgraph.types import Interrupt

        intr = Interrupt(value=_approval_value([_approval_request()]), id="int-9")
        tm = _FakeTimeoutMgr()

        async def on_interrupt(interrupts):
            return {"tc-1": True}

        resume_values: dict = {}
        info = asyncio.run(
            handle_resume_interrupt(intr, on_interrupt=on_interrupt, timeout_mgr=tm, resume_values=resume_values)
        )
        self.assertIsNone(info)
        self.assertEqual(resume_values, {"int-9": {"tc-1": True}})

    def test_no_on_interrupt_fills_false_decisions(self):
        intr = {"value": _approval_value([_approval_request()]), "id": "int-1"}
        resume_values: dict = {}
        info = asyncio.run(
            handle_resume_interrupt(intr, on_interrupt=None, timeout_mgr=_FakeTimeoutMgr(), resume_values=resume_values)
        )
        self.assertIsNone(info)
        self.assertEqual(resume_values, {"int-1": {"tc-1": False}})

    def test_subagent_wait_interrupt_returns_suspend_info(self):
        intr = {
            "value": {"_subagent_wait": True, "subagent_thread_ids": ["sub-1", "sub-2"]},
            "id": "w-1",
        }
        resume_values: dict = {}
        info = asyncio.run(
            handle_resume_interrupt(intr, on_interrupt=None, timeout_mgr=_FakeTimeoutMgr(), resume_values=resume_values)
        )
        self.assertEqual(info, {"subagent_thread_ids": ["sub-1", "sub-2"], "interrupt_id": "w-1"})
        self.assertEqual(resume_values, {})

    def test_unparseable_approval_interrupt_skipped(self):
        intr = {"value": {"_approval": True, "requests": "not-a-list"}, "id": "int-1"}
        resume_values: dict = {}
        info = asyncio.run(
            handle_resume_interrupt(intr, on_interrupt=None, timeout_mgr=_FakeTimeoutMgr(), resume_values=resume_values)
        )
        self.assertIsNone(info)
        self.assertEqual(resume_values, {})

    def test_non_approval_non_wait_interrupt_ignored(self):
        intr = {"value": {"other": 1}, "id": "x"}
        resume_values: dict = {}
        info = asyncio.run(
            handle_resume_interrupt(intr, on_interrupt=None, timeout_mgr=_FakeTimeoutMgr(), resume_values=resume_values)
        )
        self.assertIsNone(info)
        self.assertEqual(resume_values, {})

    def test_empty_decision_raises_and_resumes_timeout(self):
        intr = {"value": _approval_value([_approval_request()]), "id": "int-1"}
        tm = _FakeTimeoutMgr()

        async def on_interrupt(interrupts):
            return {}

        with self.assertRaises(RuntimeError):
            asyncio.run(
                handle_resume_interrupt(
                    intr, on_interrupt=on_interrupt, timeout_mgr=tm, resume_values={}
                )
            )
        # finally 保证 resume（计时器不因审批失败而泄漏暂停态）
        self.assertEqual((tm.paused, tm.resumed), (1, 1))


class EventPublishingTests(unittest.TestCase):
    """事件发布边界（DeepResearchEventPublisher 可独立实例化；注入缝保持）。"""

    def test_publish_tool_event_main_agent_position_and_entries_identity(self):
        from Django_xm.common.event_schema import EventSource, EventType

        adapter = _make_adapter()
        adapter._main_content = "abcde"
        captured = {}

        async def _fake_publish(**kwargs):
            captured.update(kwargs)

        evt = {
            "event_type": EventType.TOOL_CALL_PENDING,
            "tool_call_id": "tc-1",
            "tool_name": "web_search",
            "parameters": {"query": "x"},
        }
        with mock.patch(f"{_EVENT_PUB_MODULE}.publish_tool_lifecycle_event", new=_fake_publish):
            asyncio.run(adapter._event_publisher.publish_tool_event(evt))

        self.assertEqual(captured["module"], EventSource.DEEP_RESEARCH)
        self.assertEqual(captured["module_id"], "task-1")
        self.assertEqual(captured["position"], 5)  # len(_main_content)
        # entries 传实例容器本身（条目聚合落库权威来源）
        self.assertIs(captured["entries"], adapter._tool_entries)
        self.assertEqual(captured["tool_call_id"], "tc-1")
        self.assertEqual(captured["message_id"], "")

    def test_publish_tool_event_subagent_position_and_risk_conversion(self):
        from Django_xm.common.event_schema import EventType

        adapter = _make_adapter()
        adapter.subagent_contents["sub-1"] = {"content": "xy", "reasoning_content": ""}

        class _Risk:
            value = "high"

        captured = {}

        async def _fake_publish(**kwargs):
            captured.update(kwargs)

        evt = {
            "event_type": EventType.TOOL_CALL_COMPLETED,
            "tool_call_id": "tc-2",
            "tool_name": "spawn_sub_agent",
            "subagent_thread_id": "sub-1",
            "depth": 1,
            "risk_ceiling": _Risk(),
        }
        with mock.patch(f"{_EVENT_PUB_MODULE}.publish_tool_lifecycle_event", new=_fake_publish):
            asyncio.run(adapter._event_publisher.publish_tool_event(evt))

        # 子代理 position = 该图层已转发正文长度
        self.assertEqual(captured["position"], 2)
        self.assertEqual(captured["subagent_thread_id"], "sub-1")
        self.assertEqual(captured["depth"], 1)
        self.assertEqual(captured["risk_ceiling"], "high")

    def test_make_tool_event_callback_preserves_empty_params_and_skips_empty_fields(self):
        from Django_xm.common.event_schema import EventType

        adapter = _make_adapter()
        published = []

        async def _fake(evt):
            published.append(evt)

        adapter._publish_tool_event = _fake  # 实例级注入缝（与 test_session_budget 同模式）
        callback = adapter._event_publisher.make_tool_event_callback()

        asyncio.run(
            callback(
                EventType.TOOL_CALL_PENDING,
                "tc-3",
                "shell_exec",
                parameters={},  # 空 dict 是有效值，必须保留
                depth=0,  # 主 agent depth=0 不写入
                description="",  # 空描述不写入
                subagent_thread_id="sub-9",
            )
        )

        evt = published[0]
        self.assertEqual(evt["event_type"], EventType.TOOL_CALL_PENDING)
        self.assertEqual(evt["tool_call_id"], "tc-3")
        self.assertEqual(evt["parameters"], {})
        self.assertNotIn("depth", evt)
        self.assertNotIn("description", evt)
        self.assertEqual(evt["subagent_thread_id"], "sub-9")

    def test_reasoning_and_content_chunk_accumulate_and_broadcast(self):
        adapter = _make_adapter()
        publisher = adapter._event_publisher
        reasoning_cfg = {"configurable": {"chat_session_id": "cs-1", "assistant_message_id": "m1"}}

        with (
            mock.patch(f"{_EVENT_PUBLISHING_MODULE}.extract_thinking_content", return_value="思考"),
            mock.patch(f"{_WRITEBACK_MODULE}.broadcast_stream_reasoning") as br,
        ):
            publisher.handle_reasoning_chunk(object(), reasoning_cfg)
            # 缺 session/message_id 时不广播，但仍累计
            publisher.handle_reasoning_chunk(object(), {"configurable": {}})

        self.assertEqual(adapter.accumulated_reasoning, "思考思考")
        # 首次调用时广播的是当时点的累积内容（第二次调用缺 id 不广播）
        br.assert_called_once_with(
            session_id="cs-1", task_id="task-1", message_id="m1", content="思考"
        )

        with mock.patch(f"{_WRITEBACK_MODULE}.broadcast_stream_content") as bc:
            publisher.handle_content_chunk(AIMessageChunk(content="你好"), reasoning_cfg)
            # 非 AIMessageChunk（如 ToolMessage）不累计不广播
            publisher.handle_content_chunk(object(), reasoning_cfg)

        self.assertEqual(adapter._main_content, "你好")
        bc.assert_called_once_with(
            session_id="cs-1", task_id="task-1", message_id="m1", content="你好"
        )


if __name__ == "__main__":
    unittest.main()
