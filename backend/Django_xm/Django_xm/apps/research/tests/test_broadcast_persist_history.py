"""research 链流式广播不入回放历史测试（E9 / audit P2-8，对齐 chat 链 D2）。

背景（D2 遗留）：chat 链已在 stream_broadcast.py 落地"中间态快照不入回放历史"
（publish_event persist_history=False，Lua ARGV[5]），research 链经 task 频道的
推理/正文快照广播同源挤占 500 条回放窗口——reasoning 节流 0.2s（长推理 10min
最多 ~3000 条）、content_update 节流 0.5s（~1200 条），远超每频道 LTRIM 500
上限，会把 tool_call/approval/status_change 等关键历史事件挤出重连回放窗口。

整改（E9）：writeback.py 两个中间态发布点（_publish_stream_reasoning /
_publish_stream_content）传 persist_history=False——仅实时广播（group_send 语义
不变），不占用回放历史席位。终态权威恢复源保持默认入历史：
- broadcast_stream_completed（stream_completed，payload 含最终全量 content）
- writeback_to_chat_message / persist_research_progress 的 message_updated
  （落库快照广播，不传 persist_history → 默认 True）

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.research.tests.test_broadcast_persist_history --noinput
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

import time as _time_module
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase

from Django_xm.async_utils import run_async
from Django_xm.apps.research.services import writeback
from Django_xm.common.event_schema import EventType
from Django_xm.common.realtime_events import _publish_to_task_async

PUBLISH_SYNC = "Django_xm.apps.research.services.writeback.publish_event_sync"
REDIS_CLIENT = "Django_xm.common.realtime_events.get_redis_client"
LOOP_LAYER = "Django_xm.common.realtime_events._get_loop_channel_layer"
ATOMIC_PUBLISH = "Django_xm.common.realtime_events._atomic_publish_event"


class _ThrottleStateCleanup(SimpleTestCase):
    """节流状态隔离：模块级 throttle dict 为跨用例全局态，逐用例清空。"""

    def setUp(self):
        super().setUp()
        writeback._reasoning_throttle_state.clear()
        writeback._content_throttle_state.clear()
        self.addCleanup(writeback._reasoning_throttle_state.clear)
        self.addCleanup(writeback._content_throttle_state.clear)


class TestStreamReasoningNotPersisted(_ThrottleStateCleanup):
    """中间态推理快照：广播但 persist_history=False（E9 核心断言）。"""

    def test_reasoning_broadcast_skips_history(self):
        with patch(PUBLISH_SYNC) as publish_mock:
            writeback.broadcast_stream_reasoning(
                session_id="s1", task_id="t1", message_id="m1", content="思考内容"
            )
        publish_mock.assert_called_once()
        args, kwargs = publish_mock.call_args
        self.assertEqual(args[0], EventType.STREAM_REASONING)
        self.assertEqual(kwargs.get("session_id"), "s1")
        self.assertEqual(kwargs.get("task_id"), "t1")
        # E9 核心：中间态推理快照不入回放历史
        self.assertIs(kwargs.get("persist_history"), False)

    def test_reasoning_payload_is_accumulated_full_text(self):
        """payload.data.content 为累积全量（前端覆盖语义），丢中间帧不丢最终态。"""
        with patch(PUBLISH_SYNC) as publish_mock:
            writeback.broadcast_stream_reasoning(
                session_id="s1", task_id="t1", message_id="m1", content="累积全文"
            )
        payload = publish_mock.call_args.args[1]
        self.assertEqual(payload["data"]["content"], "累积全文")

    def test_reasoning_window_merges_to_single_broadcast(self):
        """节流窗口内（<0.2s）多次调用仅广播一次（既有节流语义保持）。"""
        with patch(PUBLISH_SYNC) as publish_mock:
            writeback.broadcast_stream_reasoning(
                session_id="s1", task_id="t1", message_id="m1", content="第一帧"
            )
            writeback.broadcast_stream_reasoning(
                session_id="s1", task_id="t1", message_id="m1", content="第二帧"
            )
        publish_mock.assert_called_once()  # 窗口内合流，第二帧仅记录待下窗口补发

    def test_reasoning_next_window_broadcasts_latest(self):
        """跨窗口（>=0.2s）的调用广播最新累积快照。"""
        clock_now = 1000.0

        def fake_monotonic():
            return clock_now

        with patch(PUBLISH_SYNC) as publish_mock, patch.object(
            _time_module, "monotonic", side_effect=fake_monotonic
        ):
            writeback.broadcast_stream_reasoning(
                session_id="s1", task_id="t1", message_id="m1", content="第一帧"
            )
            clock_now += writeback.REASONING_BROADCAST_INTERVAL_SECONDS + 0.01
            writeback.broadcast_stream_reasoning(
                session_id="s1", task_id="t1", message_id="m1", content="最新帧"
            )
        self.assertEqual(publish_mock.call_count, 2)
        self.assertEqual(
            publish_mock.call_args_list[-1].args[1]["data"]["content"], "最新帧"
        )
        # 跨窗口补发同样不入历史（E9：所有中间态广播一致）
        self.assertIs(publish_mock.call_args_list[-1].kwargs.get("persist_history"), False)


class TestStreamContentNotPersisted(_ThrottleStateCleanup):
    """中间态正文快照：广播但 persist_history=False（E9 核心断言）。"""

    def test_content_broadcast_skips_history(self):
        with patch(PUBLISH_SYNC) as publish_mock:
            writeback.broadcast_stream_content(
                session_id="s1", task_id="t1", message_id="m1", content="过程正文"
            )
        publish_mock.assert_called_once()
        args, kwargs = publish_mock.call_args
        self.assertEqual(args[0], EventType.STREAM_CONTENT_UPDATE)
        self.assertEqual(kwargs.get("session_id"), "s1")
        self.assertEqual(kwargs.get("task_id"), "t1")
        # E9 核心：正文快照不入回放历史
        self.assertIs(kwargs.get("persist_history"), False)

    def test_content_independent_research_empty_session(self):
        """独立深度研究（session_id=None）：仍广播到 task 频道且不入历史。"""
        with patch(PUBLISH_SYNC) as publish_mock:
            writeback.broadcast_stream_content(
                session_id=None, task_id="t1", message_id="", content="正文"
            )
        args, kwargs = publish_mock.call_args
        self.assertEqual(args[0], EventType.STREAM_CONTENT_UPDATE)
        self.assertIsNone(kwargs.get("session_id"))
        self.assertEqual(kwargs.get("task_id"), "t1")
        self.assertIs(kwargs.get("persist_history"), False)

    def test_content_window_merges_to_single_broadcast(self):
        """节流窗口内（<0.5s）多次调用仅广播一次（既有节流语义保持）。"""
        with patch(PUBLISH_SYNC) as publish_mock:
            writeback.broadcast_stream_content(
                session_id="s1", task_id="t1", message_id="m1", content="第一帧"
            )
            writeback.broadcast_stream_content(
                session_id="s1", task_id="t1", message_id="m1", content="第二帧"
            )
        publish_mock.assert_called_once()


class TestTerminalEventsKeepHistory(SimpleTestCase):
    """终态/权威恢复源事件：保持默认入历史（回放恢复红线）。"""

    def test_stream_completed_defaults_to_persist(self):
        """broadcast_stream_completed 不传 persist_history → 默认 True 入历史。

        stream_completed 是 research 终态权威事件（payload 含最终全量 content），
        重连回放据此恢复任务终态正文——红线断言。
        """
        with patch(PUBLISH_SYNC) as publish_mock:
            writeback.broadcast_stream_completed(
                chat_session_id="s1",
                task_id="t1",
                success=True,
                final_report="最终报告",
                message_id="m1",
                content="最终全量正文",
            )
        publish_mock.assert_called_once()  # 不传 user_id → 仅 stream_completed 单次发布
        args, kwargs = publish_mock.call_args
        self.assertEqual(args[0], EventType.STREAM_COMPLETED)
        # 终态事件未覆盖 persist_history → publish_event_sync 默认 True → 入历史
        self.assertTrue(kwargs.get("persist_history", True))
        payload = args[1]
        self.assertEqual(payload["content"], "最终全量正文")
        self.assertTrue(payload["finalized"])

    def test_task_status_changed_defaults_to_persist(self):
        """终态伴随的 task_status_changed（user 频道）同样默认入历史。"""
        with patch(PUBLISH_SYNC) as publish_mock:
            writeback.broadcast_stream_completed(
                chat_session_id=None,
                task_id="t1",
                success=False,
                error="执行失败",
                user_id=1,
            )
        calls = publish_mock.call_args_list
        self.assertEqual(len(calls), 2)  # stream_completed + task_status_changed
        for call in calls:
            self.assertTrue(call.kwargs.get("persist_history", True))


class TestTaskChannelPersistFlagForwarding(SimpleTestCase):
    """publish_event → _publish_to_task_async 透传 persist_history（research 主频道）。

    D2 的 common 测试仅覆盖 session 频道；research 链 reasoning/content 广播
    以 task 频道为主通道（独立深度研究唯一通道），此处锁定 task 频道透传契约。
    """

    def _publish_to_task(self, **kwargs):
        with patch(REDIS_CLIENT, return_value=MagicMock()), patch(
            ATOMIC_PUBLISH, return_value=1
        ) as mock_atomic, patch(LOOP_LAYER) as mock_layer:
            mock_layer.return_value.group_send = AsyncMock()
            run_async(
                _publish_to_task_async("t1", "stream_event", {"k": "v"}, **kwargs)
            )
        return mock_atomic

    def test_task_channel_flag_forwarded(self):
        for persist, expected in ((True, True), (False, False)):
            with self.subTest(persist=persist):
                mock_atomic = self._publish_to_task(persist_history=persist)
                mock_atomic.assert_called_once()
                self.assertIs(
                    mock_atomic.call_args.kwargs.get("persist_history"), expected
                )

    def test_task_channel_default_keeps_history(self):
        """不传参默认 True：工具/审批/终态事件（无显式 persist 参数）行为不变。"""
        mock_atomic = self._publish_to_task()
        self.assertIs(mock_atomic.call_args.kwargs.get("persist_history", True), True)
