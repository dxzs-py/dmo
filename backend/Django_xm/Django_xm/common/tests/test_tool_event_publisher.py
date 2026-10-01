"""tool_event_publisher.publish_tool_lifecycle_event 编排单元测试。

锁定唯一发布编排函数的契约（不触 Redis/WebSocket/DB，全部 mock）：
1. 四步顺序：register → bind_position → transition_async → aggregate_tool_entry
2. PENDING 补发防护：状态已推进时跳过 transition，但仍 register/position/聚合
3. transition 抛错不阻断聚合（聚合独立 try）
4. position 非 None 才绑定（None 不绑定，0 视为有效值绑定）
5. entries=None 不聚合；非 None 才聚合
6. 参数兜底：调用方参数为空时从 context 补齐；空 dict {} 传给 transition（保留占位）

运行：
    python manage.py test Django_xm.common.tests.test_tool_event_publisher
"""

import asyncio
import unittest
from unittest import mock

from Django_xm.common.event_schema import EventSource, EventType
from Django_xm.common.tool_event_publisher import publish_tool_lifecycle_event


class PublishToolLifecycleEventTest(unittest.TestCase):
    def setUp(self):
        self.order: list[str] = []

        self.service_patcher = mock.patch(
            "Django_xm.common.tool_event_publisher.service"
        )
        self.service = self.service_patcher.start()
        self.service.get_context.return_value = None
        self.service.register.side_effect = lambda *a, **k: self.order.append("register")
        self.service.bind_position.side_effect = lambda *a, **k: self.order.append(
            "bind_position"
        )
        self.service.transition_async = mock.AsyncMock(
            side_effect=lambda *a, **k: self.order.append("transition")
        )

        self.agg_patcher = mock.patch(
            "Django_xm.common.tool_event_publisher.aggregate_tool_entry"
        )
        self.aggregate = self.agg_patcher.start()
        self.aggregate.side_effect = lambda *a, **k: self.order.append("aggregate")

    def tearDown(self):
        self.agg_patcher.stop()
        self.service_patcher.stop()

    def _publish(self, **overrides):
        kwargs = {
            "module": EventSource.CHAT,
            "module_id": "session_1",
            "tool_call_id": "tc_1",
            "tool_name": "search",
            "event_type": EventType.TOOL_CALL_PENDING,
            "entries": {},
            "position": 0,
        }
        kwargs.update(overrides)
        asyncio.run(publish_tool_lifecycle_event(**kwargs))

    # ── 1. 四步顺序 ──
    def test_four_steps_ordering(self):
        self._publish(position=5, entries={})
        self.assertEqual(
            self.order, ["register", "bind_position", "transition", "aggregate"]
        )

    # ── 2. PENDING 补发防护 ──
    def test_pending_reissue_skips_transition_but_aggregates(self):
        self.service.get_context.return_value = {"last_event_type": "tool_call_running"}
        self._publish(
            event_type=EventType.TOOL_CALL_PENDING, position=3, entries={}
        )
        self.assertNotIn("transition", self.order)
        self.assertIn("register", self.order)
        self.assertIn("bind_position", self.order)
        self.assertIn("aggregate", self.order)
        self.service.transition_async.assert_not_called()
        self.aggregate.assert_called_once()

    def test_pending_when_last_event_is_pending_still_transitions(self):
        self.service.get_context.return_value = {
            "last_event_type": EventType.TOOL_CALL_PENDING.value
        }
        self._publish(event_type=EventType.TOOL_CALL_PENDING, entries=None)
        self.assertIn("transition", self.order)

    # ── 3. transition 抛错不阻断聚合 ──
    def test_transition_error_does_not_block_aggregate(self):
        self.service.transition_async = mock.AsyncMock(
            side_effect=RuntimeError("boom")
        )
        self._publish(event_type=EventType.TOOL_CALL_RUNNING, entries={})
        self.aggregate.assert_called_once()
        self.assertIn("aggregate", self.order)

    # ── 4. position 绑定条件 ──
    def test_position_none_not_bound(self):
        self._publish(position=None, entries=None)
        self.service.bind_position.assert_not_called()

    def test_position_zero_is_bound(self):
        self._publish(position=0, entries=None)
        self.service.bind_position.assert_called_once_with("tc_1", 0)

    # ── 5. entries 聚合条件 ──
    def test_entries_none_skips_aggregation(self):
        self._publish(entries=None)
        self.aggregate.assert_not_called()

    def test_entries_provided_triggers_aggregation(self):
        entries: dict = {}
        self._publish(entries=entries)
        self.aggregate.assert_called_once()
        self.assertIs(self.aggregate.call_args.args[0], entries)

    # ── 6. 参数兜底与 transition 参数语义 ──
    def test_parameters_fallback_from_context(self):
        self.service.get_context.return_value = {"parameters": {"q": "x"}}
        self._publish(parameters=None, entries=None)
        ctx = self.service.register.call_args.args[0]
        self.assertEqual(ctx.parameters, {"q": "x"})
        self.assertEqual(
            self.service.transition_async.call_args.kwargs["parameters"], {"q": "x"}
        )

    def test_non_empty_parameters_not_overridden(self):
        self.service.get_context.return_value = {"parameters": {"q": "ctx"}}
        self._publish(parameters={"q": "caller"}, entries=None)
        ctx = self.service.register.call_args.args[0]
        self.assertEqual(ctx.parameters, {"q": "caller"})

    def test_empty_parameters_passed_as_empty_dict(self):
        self.service.get_context.return_value = None
        self._publish(
            event_type=EventType.TOOL_CALL_RUNNING, parameters={}, entries=None
        )
        self.assertEqual(
            self.service.transition_async.call_args.kwargs["parameters"], {}
        )

    def test_register_receives_context_fields(self):
        self._publish(
            module=EventSource.DEEP_RESEARCH,
            module_id="task_1",
            message_id="msg_1",
            parameters={"q": "x"},
            cross_module_id="session_1",
            parent_tool_call_id="parent_1",
            depth=1,
            agent_name="web-researcher",
            risk_ceiling="controlled",
            description="desc",
            subagent_thread_id="sub_thread_1",
            entries=None,
        )
        ctx = self.service.register.call_args.args[0]
        self.assertEqual(ctx.module, EventSource.DEEP_RESEARCH)
        self.assertEqual(ctx.module_id, "task_1")
        self.assertEqual(ctx.message_id, "msg_1")
        self.assertEqual(ctx.cross_module_id, "session_1")
        self.assertEqual(ctx.parent_tool_call_id, "parent_1")
        self.assertEqual(ctx.depth, 1)
        self.assertEqual(ctx.agent_name, "web-researcher")
        self.assertEqual(ctx.risk_ceiling, "controlled")
        self.assertEqual(ctx.description, "desc")
        self.assertEqual(ctx.subagent_thread_id, "sub_thread_1")
