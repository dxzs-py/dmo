"""reasoning 时间窗合流节流 + 正文快照不入回放窗口测试（D2 / audit P2-8）。

审计发现（agent_audit_perf.md PF-01 / PF-02）：
- PF-01：reasoning 逐 thinking chunk 全量广播（stream_broadcast 直接广播分支），
  每个 chunk = 1 次 Redis Lua（INCR+RPUSH+LTRIM+EXPIRE）+ 1 次 group_send，
  payload 随推理长度线性增长；
- PF-02：STREAM_CONTENT_UPDATE 每 500ms 全量正文快照占用回放历史 List 席位
  （每频道 LTRIM 500 条），单条回答 >250s 生成即把 tool_call/approval/
  message_added 等关键历史事件挤出窗口，重连回放语义受损。

整改（D2）：
- reasoning 经可复用单点时间窗合流器 ``TimeWindowThrottle`` 节流（500ms，
  与正文快照节流间隔对齐）；窗口内多次 submit 仅广播最新全量快照（前端
  reasoning 消费为替换语义，丢中间帧安全）；完成态推理事件（finished=True
  或 duration>0）绕过窗口精确送达（前端 AiReasoning isStreaming 判定依赖）；
- 中间态 reasoning 快照与正文快照（STREAM_CONTENT_UPDATE）仅实时广播不入
  回放历史（publish_event persist_history=False）；完成态推理与
  message_updated（最终正文权威）正常入历史，重连回放可恢复终态。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.chat.tests.test_reasoning_throttle --noinput
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase

from Django_xm.apps.chat.services.stream_broadcast import (
    REASONING_BROADCAST_INTERVAL,
    publish_stream_event,
)
from Django_xm.async_utils import run_async

# TimeWindowThrottle 已下沉 common.stream_throttle（阶段 2：research 链复用）
from Django_xm.common.stream_throttle import TimeWindowThrottle

PUBLISH_EVENT = "Django_xm.common.realtime_events.publish_event"


def _reasoning_event(content: str, duration: float = 0, finished: bool = False) -> dict:
    """构造 reasoning 执行事件（stream_chunk_processors 产出形态）。"""
    data = {"content": content, "duration": duration, "source": "deep_thinking"}
    if finished:
        data["finished"] = True
    return {"type": "reasoning", "data": data}


class _FakeClock:
    """手动推进的单调时钟替身（TimeWindowThrottle 注入，避免 sleep 真等待）。"""

    def __init__(self, start: float = 100.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestTimeWindowThrottle(SimpleTestCase):
    """时间窗合流器单元行为（可复用单点实现的契约）。"""

    def _make_throttle(self, clock: _FakeClock, interval: float = 0.5):
        broadcaster = AsyncMock()
        throttle = TimeWindowThrottle(
            min_interval=interval, broadcaster=broadcaster, clock=clock
        )
        return throttle, broadcaster

    async def test_leading_edge_immediate_broadcast(self):
        """空闲窗口首个 submit 立即广播（leading edge）。"""
        clock = _FakeClock()
        throttle, broadcaster = self._make_throttle(clock)
        await throttle.submit({"content": "a"})
        broadcaster.assert_awaited_once_with({"content": "a"})

    async def test_window_merges_to_single_broadcast(self):
        """窗口内多次 submit 仅广播一次，payload 为最后一次的全量快照。"""
        clock = _FakeClock()
        throttle, broadcaster = self._make_throttle(clock)
        await throttle.submit({"content": "a"})  # leading edge 广播
        broadcaster.reset_mock()
        clock.advance(0.1)
        await throttle.submit({"content": "ab"})
        clock.advance(0.1)
        await throttle.submit({"content": "abc"})
        clock.advance(0.1)
        await throttle.submit({"content": "abcd"})
        broadcaster.assert_not_awaited()  # 窗口内全部合流
        await throttle.flush()
        broadcaster.assert_awaited_once_with({"content": "abcd"})

    async def test_next_window_broadcasts_newest_value(self):
        """跨窗口（推进满 min_interval）的 submit 再次广播，值为最新快照。"""
        clock = _FakeClock()
        throttle, broadcaster = self._make_throttle(clock)
        await throttle.submit({"content": "a"})
        broadcaster.reset_mock()
        clock.advance(REASONING_BROADCAST_INTERVAL + 0.01)
        await throttle.submit({"content": "ab"})
        broadcaster.assert_awaited_once_with({"content": "ab"})

    async def test_flush_broadcasts_pending_once(self):
        """flush 冲刷窗口内未广播的 pending（最终态红线）；重复 flush 幂等。"""
        clock = _FakeClock()
        throttle, broadcaster = self._make_throttle(clock)
        await throttle.submit({"content": "a"})
        broadcaster.reset_mock()
        clock.advance(0.1)
        await throttle.submit({"content": "ab"})
        await throttle.flush()
        broadcaster.assert_awaited_once_with({"content": "ab"})
        await throttle.flush()  # pending 已清空，幂等 no-op
        broadcaster.assert_awaited_once()

    async def test_flush_empty_is_noop(self):
        """空 pending 的 flush 不触发广播。"""
        clock = _FakeClock()
        throttle, broadcaster = self._make_throttle(clock)
        await throttle.flush()
        broadcaster.assert_not_awaited()


class TestReasoningThrottleWiring(SimpleTestCase):
    """publish_stream_event reasoning 分支节流接线。"""

    def _published_types(self, mock_publish) -> list[str]:
        return [call.args[0].value for call in mock_publish.await_args_list]

    def _published_payloads(self, mock_publish) -> list[dict]:
        return [call.args[1] for call in mock_publish.await_args_list]

    def test_reasoning_without_state_keeps_legacy_behavior(self):
        """未传 reasoning_state：reasoning 直接广播（旧行为，兼容既有调用方）。"""
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish:
            run_async(
                publish_stream_event(
                    _reasoning_event("思考中"), "s1", message_id=42
                )
            )
        mock_publish.assert_awaited_once()
        self.assertEqual(self._published_types(mock_publish), ["stream_reasoning"])

    def test_reasoning_window_merges_burst(self):
        """接节流器后：窗口内突发 N 个进行中 reasoning → 合流，仅 flush 后送达最新全量。"""
        clock = _FakeClock()
        reasoning_state: dict = {}
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish, patch(
            "Django_xm.apps.chat.services.stream_broadcast.time.monotonic", clock
        ):
            run_async(
                publish_stream_event(
                    _reasoning_event("第一段"), "s1", message_id=42,
                    reasoning_state=reasoning_state,
                )
            )
            run_async(
                publish_stream_event(
                    _reasoning_event("第一段第二段"), "s1", message_id=42,
                    reasoning_state=reasoning_state,
                )
            )
            run_async(
                publish_stream_event(
                    _reasoning_event("第一段第二段第三段"), "s1", message_id=42,
                    reasoning_state=reasoning_state,
                )
            )

            async def _flush_throttle():
                await reasoning_state["throttle"].flush()

            run_async(_flush_throttle())

        payloads = self._published_payloads(mock_publish)
        # leading edge 首广播 + flush 送达合流终值，窗口内中间帧全部丢弃
        self.assertEqual(len(payloads), 2)
        self.assertEqual(payloads[0]["data"]["content"], "第一段")
        self.assertEqual(payloads[-1]["data"]["content"], "第一段第二段第三段")
        self.assertEqual(payloads[-1]["message_id"], "42")
        self.assertEqual(payloads[-1]["source_id"], "s1")

    def test_reasoning_final_event_flushes_then_broadcasts_in_order(self):
        """完成态推理（duration>0）：先冲刷窗口内中间态（保序），完成态随后精确送达。"""
        clock = _FakeClock()
        reasoning_state: dict = {}
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish, patch(
            "Django_xm.apps.chat.services.stream_broadcast.time.monotonic", clock
        ):
            run_async(
                publish_stream_event(
                    _reasoning_event("中间态"), "s1", message_id=42,
                    reasoning_state=reasoning_state,
                )
            )
            clock.advance(0.1)
            run_async(
                publish_stream_event(
                    _reasoning_event("中间态尾段"), "s1", message_id=42,
                    reasoning_state=reasoning_state,
                )
            )
            clock.advance(0.1)
            run_async(
                publish_stream_event(
                    _reasoning_event("最终推理", duration=5.2), "s1", message_id=42,
                    reasoning_state=reasoning_state,
                )
            )
        types = self._published_types(mock_publish)
        self.assertEqual(types, ["stream_reasoning", "stream_reasoning", "stream_reasoning"])
        payloads = self._published_payloads(mock_publish)
        # 顺序：中间态 flush → 完成态；完成态内容为全量终值
        self.assertEqual(payloads[1]["data"]["content"], "中间态尾段")
        self.assertEqual(payloads[2]["data"]["content"], "最终推理")
        self.assertEqual(payloads[2]["data"]["duration"], 5.2)

    def test_reasoning_finished_flag_also_treated_as_final(self):
        """finished=True（Normal 策略完成态）同样绕过窗口精确送达。"""
        clock = _FakeClock()
        reasoning_state: dict = {}
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish, patch(
            "Django_xm.apps.chat.services.stream_broadcast.time.monotonic", clock
        ):
            run_async(
                publish_stream_event(
                    _reasoning_event("推理全文", finished=True), "s1", message_id=42,
                    reasoning_state=reasoning_state,
                )
            )
        mock_publish.assert_awaited_once()

    def test_boundary_event_flushes_pending_reasoning(self):
        """语义边界事件（approval 等）到达 → 冲刷窗口内未广播的推理快照。"""
        clock = _FakeClock()
        reasoning_state: dict = {}
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish, patch(
            "Django_xm.apps.chat.services.stream_broadcast.time.monotonic", clock
        ):
            run_async(
                publish_stream_event(
                    _reasoning_event("挂起前推理"), "s1", message_id=42,
                    reasoning_state=reasoning_state,
                )
            )
            clock.advance(0.1)
            run_async(
                publish_stream_event(
                    _reasoning_event("挂起前推理尾段"), "s1", message_id=42,
                    reasoning_state=reasoning_state,
                )
            )
            clock.advance(0.1)
            run_async(
                publish_stream_event(
                    {"type": "approval", "data": {"tool_name": "t"}},
                    "s1", message_id=42, reasoning_state=reasoning_state,
                )
            )
        types = self._published_types(mock_publish)
        self.assertEqual(types[0], "stream_reasoning")  # leading edge
        self.assertEqual(types[1], "stream_reasoning")  # approval 触发的 flush
        self.assertEqual(types[2], "stream_event")  # approval 本身
        self.assertEqual(
            self._published_payloads(mock_publish)[1]["data"]["content"],
            "挂起前推理尾段",
        )

    def test_chunk_event_does_not_flush_reasoning(self):
        """正文 chunk 不冲刷 reasoning（高频 chunk 会使推理节流失效）。"""
        clock = _FakeClock()
        reasoning_state: dict = {}
        content_state = {"content": "", "last_broadcast": 0.0}
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish, patch(
            "Django_xm.apps.chat.services.stream_broadcast.time.monotonic", clock
        ):
            run_async(
                publish_stream_event(
                    _reasoning_event("推理"), "s1", message_id=42,
                    reasoning_state=reasoning_state,
                )
            )
            clock.advance(0.1)
            run_async(
                publish_stream_event(
                    _reasoning_event("推理尾段"), "s1", message_id=42,
                    reasoning_state=reasoning_state,
                )
            )
            clock.advance(0.1)
            run_async(
                publish_stream_event(
                    {"type": "chunk", "content": "正文"},
                    "s1", message_id=42,
                    content_state=content_state,
                    reasoning_state=reasoning_state,
                )
            )
        reasoning_calls = [
            call for call in mock_publish.await_args_list
            if call.args[0].value == "stream_reasoning"
        ]
        self.assertEqual(len(reasoning_calls), 1)  # 仅 leading edge，尾段未冲刷

    async def test_explicit_flush_delivers_final_window(self):
        """流收尾显式 flush（chat_executor_core finally）送达窗口内推理尾部。"""
        clock = _FakeClock()
        reasoning_state: dict = {}
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish, patch(
            "Django_xm.apps.chat.services.stream_broadcast.time.monotonic", clock
        ):
            run_async(
                publish_stream_event(
                    _reasoning_event("推理"), "s1", message_id=42,
                    reasoning_state=reasoning_state,
                )
            )
            clock.advance(0.1)
            run_async(
                publish_stream_event(
                    _reasoning_event("推理全文尾段"), "s1", message_id=42,
                    reasoning_state=reasoning_state,
                )
            )

            async def _flush_throttle():
                throttle = reasoning_state.get("throttle")
                await throttle.flush()

            run_async(_flush_throttle())
        payloads = self._published_payloads(mock_publish)
        self.assertEqual(len(payloads), 2)
        self.assertEqual(payloads[-1]["data"]["content"], "推理全文尾段")


class TestPersistHistoryFlag(SimpleTestCase):
    """快照不入回放窗口：publish_event 的 persist_history 透传断言。

    PF-02 整改：中间态 reasoning / 正文快照仅实时广播（group_send），
    不占用回放历史 List 席位；完成态推理与其他关键事件保持入历史。
    """

    def _make_reasoning_state(self) -> dict:
        return {}

    def test_chunk_broadcast_skips_history(self):
        """chunk → STREAM_CONTENT_UPDATE 广播 persist_history=False。"""
        content_state = {"content": "", "last_broadcast": 0.0}
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish:
            run_async(
                publish_stream_event(
                    {"type": "chunk", "content": "增量"}, "s1", message_id=42,
                    content_state=content_state,
                )
            )
        mock_publish.assert_awaited_once()
        self.assertEqual(mock_publish.await_args.kwargs.get("persist_history"), False)

    def test_reasoning_ongoing_skips_history(self):
        """进行中 reasoning（duration=0）→ persist_history=False。"""
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish:
            run_async(
                publish_stream_event(
                    _reasoning_event("思考中"), "s1", message_id=42,
                    reasoning_state=self._make_reasoning_state(),
                )
            )
        mock_publish.assert_awaited_once()
        self.assertEqual(mock_publish.await_args.kwargs.get("persist_history"), False)

    def test_reasoning_final_keeps_history(self):
        """完成态推理 → persist_history=True（重连回放恢复最终推理文本）。"""
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish:
            run_async(
                publish_stream_event(
                    _reasoning_event("最终推理", duration=3.1), "s1", message_id=42,
                    reasoning_state=self._make_reasoning_state(),
                )
            )
        mock_publish.assert_awaited_once()
        self.assertEqual(mock_publish.await_args.kwargs.get("persist_history"), True)

    def test_reasoning_flushed_middle_frames_skip_history(self):
        """flush 冲刷出的中间态推理同样不入历史（仅完成态入历史）。"""
        clock = _FakeClock()
        reasoning_state: dict = {}
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish, patch(
            "Django_xm.apps.chat.services.stream_broadcast.time.monotonic", clock
        ):
            run_async(
                publish_stream_event(
                    _reasoning_event("推理"), "s1", message_id=42,
                    reasoning_state=reasoning_state,
                )
            )
            clock.advance(0.1)
            run_async(
                publish_stream_event(
                    _reasoning_event("推理尾段"), "s1", message_id=42,
                    reasoning_state=reasoning_state,
                )
            )
            clock.advance(0.1)
            run_async(
                publish_stream_event(
                    {"type": "approval", "data": {"tool_name": "t"}},
                    "s1", message_id=42, reasoning_state=reasoning_state,
                )
            )
        reasoning_kwargs = [
            call.kwargs.get("persist_history")
            for call in mock_publish.await_args_list
            if call.args[0].value == "stream_reasoning"
        ]
        self.assertEqual(reasoning_kwargs, [False, False])

    def test_other_events_keep_history_by_default(self):
        """其他事件（deep_research 等）不传 persist_history → 保持入历史（默认 True）。"""
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish:
            run_async(
                publish_stream_event(
                    {"type": "deep_research", "data": {"task_id": "t1"}},
                    "s1", message_id=42,
                )
            )
        mock_publish.assert_awaited_once()
        self.assertIsNone(mock_publish.await_args.kwargs.get("persist_history"))
