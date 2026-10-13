"""STREAM_SUBAGENT_CONTENT 全量快照语义 + 节流 + 不入回放历史测试（阶段 2）。

背景（eliminate-priority-debts 阶段 2 / spec REMOVED「增量追加语义」）：
- 旧语义：data.content 为本轮增量，前端逐事件追加累计；且 persist_history=True，
  高频中间帧挤占每频道 LTRIM 500 条回放窗口（E9 遗留缺口）。
- 新契约（对齐 chat 链 reasoning/content_update 覆盖语义，D2）：
  1. payload.data.content / reasoning_content 为该子代理**累积全量正文快照**
     （前端覆盖渲染，丢中间帧不影响最终态）；
  2. 经 ``TimeWindowThrottle``（下沉 common，500ms）按 subagent_thread_id
     独立合流节流（多子代理并发互不覆盖）；
  3. ``persist_history=False`` 仅实时广播，不占用回放历史席位；
  4. msg_id 幂等去重保持（审批 resume 重放同一条 AIMessage 不重复累计/广播）；
  5. TOOL_CALL 类边界事件冲刷同线程 pending（保序：正文快照先于工具事件送达）；
  6. 节流广播同时触发运行期落库（persist_chat_tool_calls，与广播同频合流）。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.chat.tests.test_subagent_content_snapshot --noinput
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase

from Django_xm.apps.chat.services.chat_service import ChatService, _SubagentStreamState
from Django_xm.async_utils import run_async

PUBLISH_EVENT = "Django_xm.common.realtime_events.publish_event"
PERSIST_TOOL_CALLS = "Django_xm.apps.chat.services.stream_persistence.persist_chat_tool_calls"
PUBLISH_TOOL_LIFECYCLE = "Django_xm.common.tool_event_publisher.publish_tool_lifecycle_event"
THROTTLE_CLOCK = "Django_xm.common.stream_throttle.time.monotonic"


class _FakeClock:
    """手动推进的单调时钟替身（TimeWindowThrottle 注入，避免 sleep 真等待）。"""

    def __init__(self, start: float = 100.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _make_service() -> ChatService:
    """构造绕过子服务初始化的 ChatService（本组用例仅覆盖子代理转发回调）。"""
    return object.__new__(ChatService)


def _make_state() -> _SubagentStreamState:
    return _SubagentStreamState(
        session_id="s1",
        message_id="42",
        contents={},
        tool_entries={},
        sent_msg_keys={},
    )


def _make_data(**extra) -> dict:
    data = {"session_id": "s1", "_assistant_message_id": "42"}
    data.update(extra)
    return data


class _PublishedRecorder:
    """跨多个 patched 发布点记录调用顺序（保序断言用）。"""

    def __init__(self):
        self.order: list[str] = []
        self.publish_calls: list = []

    async def record_publish(self, event_type, payload, **kwargs):
        self.order.append("subagent_content")
        self.publish_calls.append((event_type, payload, kwargs))

    async def record_tool_lifecycle(self, *args, **kwargs):
        self.order.append("tool_lifecycle")


class TestChatSubagentContentFullSnapshot(SimpleTestCase):
    """chat 侧 _handle_subagent_content 全量快照契约。"""

    def _published_payloads(self, mock_publish) -> list[dict]:
        return [call.args[1] for call in mock_publish.await_args_list]

    def test_payload_is_accumulated_full_snapshot(self):
        """两次模型轮次 → 第二次 payload.data.content 为累计全量（非增量）。"""
        svc = _make_service()
        data = _make_data()
        state = _make_state()
        clock = _FakeClock()
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish, patch(
            PERSIST_TOOL_CALLS, new_callable=AsyncMock
        ), patch(THROTTLE_CLOCK, clock):
            run_async(
                svc._handle_subagent_content(
                    data, state, ["main"], "第一轮", "思考一", "researcher", 1,
                    subagent_thread_id="sa1", msg_id="m1",
                )
            )
            clock.advance(0.51)  # 跨过节流窗口，第二帧独立广播
            run_async(
                svc._handle_subagent_content(
                    data, state, ["main"], "第二轮", "思考二", "researcher", 1,
                    subagent_thread_id="sa1", msg_id="m2",
                )
            )
        payloads = self._published_payloads(mock_publish)
        self.assertEqual(len(payloads), 2)
        # 全量快照：第二帧 content 为累计全文，而非 "第二轮" 增量
        self.assertEqual(payloads[0]["data"]["content"], "第一轮")
        self.assertEqual(payloads[1]["data"]["content"], "第一轮第二轮")
        self.assertEqual(payloads[1]["data"]["reasoning_content"], "思考一思考二")
        self.assertEqual(payloads[1]["data"]["agent_name"], "researcher")
        self.assertEqual(payloads[1]["data"]["depth"], 1)
        self.assertEqual(payloads[1]["source_id"], "s1")
        self.assertEqual(payloads[1]["message_id"], "42")

    def test_duplicate_msg_id_not_republished(self):
        """审批 resume 重放同一条 AIMessage（同 msg_id）→ 不重复累计、不重复广播。"""
        svc = _make_service()
        data = _make_data()
        state = _make_state()
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish, patch(
            PERSIST_TOOL_CALLS, new_callable=AsyncMock
        ):
            run_async(
                svc._handle_subagent_content(
                    data, state, ["main"], "第一轮", "", "researcher", 1,
                    subagent_thread_id="sa1", msg_id="m1",
                )
            )
            run_async(
                svc._handle_subagent_content(
                    data, state, ["main"], "第一轮", "", "researcher", 1,
                    subagent_thread_id="sa1", msg_id="m1",
                )
            )
        self.assertEqual(mock_publish.await_count, 1)
        self.assertEqual(
            state.contents["sa1"]["content"], "第一轮"
        )  # 幂等：无重复累计

    def test_publish_skips_history(self):
        """快照仅实时广播：publish_event 收到 persist_history=False（回放窗口保护）。"""
        svc = _make_service()
        data = _make_data()
        state = _make_state()
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish, patch(
            PERSIST_TOOL_CALLS, new_callable=AsyncMock
        ):
            run_async(
                svc._handle_subagent_content(
                    data, state, ["main"], "内容", "", "researcher", 1,
                    subagent_thread_id="sa1", msg_id="m1",
                )
            )
        mock_publish.assert_awaited_once()
        self.assertIs(mock_publish.await_args.kwargs.get("persist_history"), False)

    def test_throttle_state_isolated_per_thread(self):
        """并发多子代理：各线程节流器独立，快照互不串线。"""
        svc = _make_service()
        data = _make_data()
        state = _make_state()
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish, patch(
            PERSIST_TOOL_CALLS, new_callable=AsyncMock
        ):
            run_async(
                svc._handle_subagent_content(
                    data, state, ["main"], "A1", "", "agentA", 1,
                    subagent_thread_id="sa-a", msg_id="ma1",
                )
            )
            run_async(
                svc._handle_subagent_content(
                    data, state, ["main"], "B1", "", "agentB", 1,
                    subagent_thread_id="sa-b", msg_id="mb1",
                )
            )
        payloads = self._published_payloads(mock_publish)
        self.assertEqual(payloads[0]["data"]["content"], "A1")
        self.assertEqual(payloads[1]["data"]["content"], "B1")

    def test_throttled_broadcast_triggers_runtime_persist(self):
        """节流广播时同步触发运行期落库（挂起/刷新恢复基线，同频合流防 DB 写放大）。"""
        svc = _make_service()
        data = _make_data()
        state = _make_state()
        with patch(PUBLISH_EVENT, new_callable=AsyncMock), patch(
            PERSIST_TOOL_CALLS, new_callable=AsyncMock
        ) as mock_persist:
            run_async(
                svc._handle_subagent_content(
                    data, state, ["main"], "内容", "", "researcher", 1,
                    subagent_thread_id="sa1", msg_id="m1",
                )
            )
        mock_persist.assert_awaited_once()
        self.assertEqual(mock_persist.await_args.args[2], "s1")


class TestChatSubagentContentThrottle(SimpleTestCase):
    """chat 侧节流合流 / 边界冲刷保序。"""

    def _published_payloads(self, mock_publish) -> list[dict]:
        return [call.args[1] for call in mock_publish.await_args_list]

    def test_window_merges_burst_to_latest_full_snapshot(self):
        """窗口内突发多帧 → 仅 leading edge + flush 各广播一次，终值为最新全量。"""
        svc = _make_service()
        data = _make_data()
        state = _make_state()
        clock = _FakeClock()
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish, patch(
            PERSIST_TOOL_CALLS, new_callable=AsyncMock
        ), patch(THROTTLE_CLOCK, clock):
            run_async(
                svc._handle_subagent_content(
                    data, state, ["main"], "第一帧", "", "r", 1,
                    subagent_thread_id="sa1", msg_id="m1",
                )
            )
            clock.advance(0.1)
            run_async(
                svc._handle_subagent_content(
                    data, state, ["main"], "第二帧", "", "r", 1,
                    subagent_thread_id="sa1", msg_id="m2",
                )
            )
            clock.advance(0.1)
            run_async(
                svc._handle_subagent_content(
                    data, state, ["main"], "第三帧", "", "r", 1,
                    subagent_thread_id="sa1", msg_id="m3",
                )
            )
        # 窗口内仅 leading edge 广播，后续帧合流进 pending
        self.assertEqual(mock_publish.await_count, 1)
        # 终态冲刷（collect 红线）：flush 送达最新全量快照
        run_async(svc._flush_subagent_content_throttles(data))
        payloads = self._published_payloads(mock_publish)
        self.assertEqual(len(payloads), 2)
        self.assertEqual(payloads[-1]["data"]["content"], "第一帧第二帧第三帧")

    def test_tool_event_flushes_pending_before_tool_publish(self):
        """TOOL_CALL 边界冲刷（D2 白名单规则对齐）：正文快照先于工具事件送达（保序）。"""
        svc = _make_service()
        data = _make_data()
        state = _make_state()
        clock = _FakeClock()
        recorder = _PublishedRecorder()
        with patch(
            PUBLISH_EVENT, new=AsyncMock(side_effect=recorder.record_publish)
        ), patch(
            PERSIST_TOOL_CALLS, new_callable=AsyncMock
        ), patch(
            THROTTLE_CLOCK, clock
        ), patch(
            PUBLISH_TOOL_LIFECYCLE,
            new=AsyncMock(side_effect=recorder.record_tool_lifecycle),
        ):
            run_async(
                svc._handle_subagent_content(
                    data, state, ["main"], "挂起前正文", "", "r", 1,
                    subagent_thread_id="sa1", msg_id="m1",
                )
            )
            clock.advance(0.1)
            run_async(
                svc._handle_subagent_content(
                    data, state, ["main"], "挂起前正文尾段", "", "r", 1,
                    subagent_thread_id="sa1", msg_id="m2",
                )
            )
            run_async(
                svc._handle_subagent_tool_event(
                    data, state, "tool_call_pending", "tc1", "web_search",
                    subagent_thread_id="sa1", depth=1, parameters={},
                )
            )
        self.assertEqual(recorder.order, ["subagent_content", "subagent_content", "tool_lifecycle"])
        flushed = recorder.publish_calls[-1][1]
        self.assertEqual(flushed["data"]["content"], "挂起前正文挂起前正文尾段")

    def test_flush_is_idempotent(self):
        """重复冲刷幂等：pending 清空后不再广播。"""
        svc = _make_service()
        data = _make_data()
        state = _make_state()
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish, patch(
            PERSIST_TOOL_CALLS, new_callable=AsyncMock
        ):
            run_async(
                svc._handle_subagent_content(
                    data, state, ["main"], "内容", "", "r", 1,
                    subagent_thread_id="sa1", msg_id="m1",
                )
            )
            run_async(svc._flush_subagent_content_throttles(data))
            run_async(svc._flush_subagent_content_throttles(data))
        self.assertEqual(mock_publish.await_count, 1)

    def test_flush_no_throttle_is_noop(self):
        """无任何节流器时冲刷不抛错（空容器幂等）。"""
        svc = _make_service()
        data = _make_data()
        run_async(svc._flush_subagent_content_throttles(data))
