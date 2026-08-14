"""工具调用事件 payload 时间戳与 description 字段测试（Task 2.1 / Task 2.4）。

覆盖 publish_tool_call（三模块统一发布入口）的 payload 注入规则：
1. TOOL_CALL_PENDING → created_at（起始时间戳，ISO 8601）
2. TOOL_CALL_COMPLETED / TOOL_CALL_FAILED / TOOL_CALL_TIMEOUT / TOOL_CALL_REJECTED
   → completed_at（终态时间戳，ISO 8601）
3. TOOL_CALL_RUNNING / TOOL_CALL_WAITING → 无时间戳
4. description（子 agent 任务目标描述）非空时注入 payload，缺省不注入

通过 mock publish_event 捕获 payload，不触碰 Redis/WebSocket。

运行：
    python manage.py test research
或（不依赖 Django settings/DB）：
    python -m unittest Django_xm.apps.research.tests.test_tool_call_event_payload
"""

import asyncio
import unittest
from datetime import datetime
from unittest import mock

from Django_xm.common.event_schema import EventSource, EventType
from Django_xm.common.realtime_sync import publish_tool_call


class ToolCallEventPayloadTests(unittest.TestCase):
    """publish_tool_call payload 时间戳与 description 注入规则。"""

    def _capture_publish(self, event_type: EventType, **kwargs) -> dict:
        """调用 publish_tool_call（mock publish_event）并返回捕获的 payload。"""
        captured: dict = {}

        async def fake_publish(_event_type, payload, **_kw):
            captured["event_type"] = _event_type
            captured["payload"] = payload

        with mock.patch(
            "Django_xm.common.realtime_sync.publish_event",
            side_effect=fake_publish,
        ):
            asyncio.run(
                publish_tool_call(
                    event_type,
                    tool_call_id="tc_1",
                    tool_name="search",
                    module=EventSource.DEEP_RESEARCH,
                    module_id="task_1",
                    message_id="",
                    parameters={},
                    **kwargs,
                )
            )
        return captured

    def test_pending_has_created_at(self):
        """PENDING 事件携带 created_at（ISO 8601），不携带 completed_at。"""
        captured = self._capture_publish(EventType.TOOL_CALL_PENDING)
        payload = captured["payload"]
        self.assertIn("created_at", payload)
        # 格式合法：datetime.fromisoformat 可解析
        datetime.fromisoformat(payload["created_at"])
        self.assertNotIn("completed_at", payload)

    def test_terminal_events_have_completed_at(self):
        """COMPLETED/FAILED/TIMEOUT/REJECTED 终态事件携带 completed_at，不携带 created_at。"""
        for event_type in (
            EventType.TOOL_CALL_COMPLETED,
            EventType.TOOL_CALL_FAILED,
            EventType.TOOL_CALL_TIMEOUT,
            EventType.TOOL_CALL_REJECTED,
        ):
            kwargs = {}
            if event_type == EventType.TOOL_CALL_FAILED:
                kwargs["error"] = "boom"
            captured = self._capture_publish(event_type, **kwargs)
            payload = captured["payload"]
            self.assertIn("completed_at", payload, f"{event_type.value} 应携带 completed_at")
            datetime.fromisoformat(payload["completed_at"])
            self.assertNotIn("created_at", payload)

    def test_intermediate_events_have_no_timestamps(self):
        """RUNNING/WAITING 中间态事件不携带任何时间戳。"""
        for event_type in (EventType.TOOL_CALL_RUNNING, EventType.TOOL_CALL_WAITING):
            captured = self._capture_publish(event_type)
            payload = captured["payload"]
            self.assertNotIn("created_at", payload)
            self.assertNotIn("completed_at", payload)

    def test_description_injected_when_provided(self):
        """description 非空时注入 payload（协议键 snake_case）。"""
        captured = self._capture_publish(
            EventType.TOOL_CALL_PENDING,
            description="网络搜索和信息整理专家，负责从互联网搜索和整理研究信息",
        )
        self.assertEqual(captured["payload"]["description"], "网络搜索和信息整理专家，负责从互联网搜索和整理研究信息")

    def test_description_omitted_when_missing(self):
        """description 缺省（主 agent 场景）时不注入 payload。"""
        captured = self._capture_publish(EventType.TOOL_CALL_PENDING)
        self.assertNotIn("description", captured["payload"])
