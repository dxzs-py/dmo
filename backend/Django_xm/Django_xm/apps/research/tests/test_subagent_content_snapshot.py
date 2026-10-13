"""research 链 STREAM_SUBAGENT_CONTENT 全量快照语义 + 节流测试（阶段 2）。

背景（eliminate-priority-debts 阶段 2，与 chat 链 test_subagent_content_snapshot 同构）：
- 旧语义：data.content 为本轮增量、persist_history=True（挤占回放窗口）；
- 新契约：payload.data.content / reasoning_content 为该子代理**累积全量正文快照**；
  经 TimeWindowThrottle（下沉 common，500ms）按 subagent_thread_id 独立节流；
  persist_history=False 仅实时广播；msg_id 幂等去重保持；
  TOOL_CALL 边界冲刷同线程 pending（保序）；节流广播同时触发运行期落库
  （persist_research_subagent_contents，2.1 权威源补缺：独立深研运行中刷新恢复依据）。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.research.tests.test_subagent_content_snapshot --noinput
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase

from Django_xm.apps.research.services.event_publishing import DeepResearchEventPublisher
from Django_xm.async_utils import run_async

PUBLISH_EVENT = "Django_xm.common.realtime_events.publish_event"
PERSIST_SUB_SNAPSHOT = (
    "Django_xm.apps.research.services.writeback.persist_research_subagent_contents"
)
THROTTLE_CLOCK = "Django_xm.common.stream_throttle.time.monotonic"


class _FakeClock:
    """手动推进的单调时钟替身（TimeWindowThrottle 注入，避免 sleep 真等待）。"""

    def __init__(self, start: float = 100.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _make_owner() -> SimpleNamespace:
    """最小 owner 替身（发布边界仅读写这些会话级属性）。"""
    return SimpleNamespace(
        chat_session_id="s1",
        thread_id="t1",
        _assistant_message_id="m1",
        _last_config=None,
        subagent_contents={},
        _tool_entries={},
        _main_content="",
        _publish_tool_event=AsyncMock(),
    )


class _PublishedRecorder:
    """跨多个 patched 发布点记录调用顺序（保序断言用）。"""

    def __init__(self):
        self.order: list[str] = []
        self.publish_calls: list = []

    async def record_publish(self, event_type, payload, **kwargs):
        self.order.append("subagent_content")
        self.publish_calls.append((event_type, payload, kwargs))

    async def record_tool_event(self, evt):
        self.order.append("tool_event")


class TestResearchSubagentContentFullSnapshot(SimpleTestCase):
    """research 侧 make_subagent_content_callback 全量快照契约。"""

    def setUp(self):
        self.owner = _make_owner()
        self.publisher = DeepResearchEventPublisher(self.owner)
        self.callback = self.publisher.make_subagent_content_callback()

    def _published_payloads(self, mock_publish) -> list[dict]:
        return [call.args[1] for call in mock_publish.await_args_list]

    def test_payload_is_accumulated_full_snapshot(self):
        """两次模型轮次 → 第二次 payload.data.content 为累计全量（非增量）。"""
        clock = _FakeClock()
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish, patch(
            PERSIST_SUB_SNAPSHOT
        ), patch(THROTTLE_CLOCK, clock):
            run_async(
                self.callback(
                    agent_path=["plan"], content="第一轮", reasoning_content="思考一",
                    agent_name="researcher", depth=1,
                    subagent_thread_id="sa1", msg_id="m1",
                )
            )
            clock.advance(0.51)  # 跨过节流窗口，第二帧独立广播
            run_async(
                self.callback(
                    agent_path=["plan"], content="第二轮", reasoning_content="思考二",
                    agent_name="researcher", depth=1,
                    subagent_thread_id="sa1", msg_id="m2",
                )
            )
        payloads = self._published_payloads(mock_publish)
        self.assertEqual(len(payloads), 2)
        self.assertEqual(payloads[0]["data"]["content"], "第一轮")
        self.assertEqual(payloads[1]["data"]["content"], "第一轮第二轮")
        self.assertEqual(payloads[1]["data"]["reasoning_content"], "思考一思考二")
        self.assertEqual(payloads[1]["source"], "deep_research")
        self.assertEqual(payloads[1]["source_id"], "t1")
        self.assertEqual(payloads[1]["message_id"], "m1")

    def test_duplicate_msg_id_not_republished(self):
        """审批 resume 重放同一条 AIMessage（同 msg_id）→ 不重复累计、不重复广播。"""
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish, patch(
            PERSIST_SUB_SNAPSHOT
        ):
            run_async(
                self.callback(
                    agent_path=["plan"], content="第一轮", reasoning_content="",
                    agent_name="r", depth=1,
                    subagent_thread_id="sa1", msg_id="m1",
                )
            )
            run_async(
                self.callback(
                    agent_path=["plan"], content="第一轮", reasoning_content="",
                    agent_name="r", depth=1,
                    subagent_thread_id="sa1", msg_id="m1",
                )
            )
        self.assertEqual(mock_publish.await_count, 1)
        self.assertEqual(self.owner.subagent_contents["sa1"]["content"], "第一轮")

    def test_publish_routes_both_channels_and_skips_history(self):
        """session + task 双频道路由 + persist_history=False（回放窗口保护）。"""
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish, patch(
            PERSIST_SUB_SNAPSHOT
        ):
            run_async(
                self.callback(
                    agent_path=["plan"], content="内容", reasoning_content="",
                    agent_name="r", depth=1,
                    subagent_thread_id="sa1", msg_id="m1",
                )
            )
        mock_publish.assert_awaited_once()
        kwargs = mock_publish.await_args.kwargs
        self.assertIs(kwargs.get("persist_history"), False)
        self.assertEqual(kwargs.get("session_id"), "s1")
        self.assertEqual(kwargs.get("task_id"), "t1")
        self.assertEqual(kwargs.get("subagent_thread_id"), "sa1")

    def test_throttled_broadcast_triggers_runtime_persist(self):
        """节流广播触发运行期落库（独立深研运行中刷新的恢复依据，2.1 补缺）。

        persist_research_subagent_contents 为 sync 函数（经 sync_to_async 包装
        调用），替身用 MagicMock 断言 call 而非 await。
        """
        with patch(PUBLISH_EVENT, new_callable=AsyncMock), patch(
            PERSIST_SUB_SNAPSHOT
        ) as mock_persist:
            run_async(
                self.callback(
                    agent_path=["plan"], content="累积正文", reasoning_content="思考",
                    agent_name="r", depth=1,
                    subagent_thread_id="sa1", msg_id="m1",
                )
            )
        mock_persist.assert_called_once()
        args = mock_persist.call_args.args
        self.assertEqual(args[0], "t1")
        self.assertEqual(args[1], "sa1")
        self.assertEqual(args[2], "累积正文")
        self.assertEqual(args[3], "思考")


class TestResearchSubagentContentThrottle(SimpleTestCase):
    """research 侧节流合流 / 边界冲刷保序。"""

    def setUp(self):
        self.owner = _make_owner()
        self.publisher = DeepResearchEventPublisher(self.owner)
        self.callback = self.publisher.make_subagent_content_callback()
        self.tool_callback = self.publisher.make_tool_event_callback()

    def _published_payloads(self, mock_publish) -> list[dict]:
        return [call.args[1] for call in mock_publish.await_args_list]

    def test_window_merges_burst_to_latest_full_snapshot(self):
        """窗口内突发多帧 → 仅 leading edge 广播，flush 送达最新全量。"""
        clock = _FakeClock()
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish, patch(
            PERSIST_SUB_SNAPSHOT
        ), patch(THROTTLE_CLOCK, clock):
            run_async(
                self.callback(
                    agent_path=["plan"], content="第一帧", reasoning_content="",
                    agent_name="r", depth=1,
                    subagent_thread_id="sa1", msg_id="m1",
                )
            )
            clock.advance(0.1)
            run_async(
                self.callback(
                    agent_path=["plan"], content="第二帧", reasoning_content="",
                    agent_name="r", depth=1,
                    subagent_thread_id="sa1", msg_id="m2",
                )
            )
        self.assertEqual(mock_publish.await_count, 1)
        run_async(self.publisher.flush_all_subagent_content())
        payloads = self._published_payloads(mock_publish)
        self.assertEqual(len(payloads), 2)
        self.assertEqual(payloads[-1]["data"]["content"], "第一帧第二帧")

    def test_tool_event_flushes_pending_before_tool_publish(self):
        """TOOL_CALL 边界冲刷：正文快照先于工具事件发布（position 内联保序）。"""
        clock = _FakeClock()
        recorder = _PublishedRecorder()
        self.owner._publish_tool_event = AsyncMock(side_effect=recorder.record_tool_event)
        with patch(
            PUBLISH_EVENT, new=AsyncMock(side_effect=recorder.record_publish)
        ), patch(
            PERSIST_SUB_SNAPSHOT
        ), patch(THROTTLE_CLOCK, clock):
            run_async(
                self.callback(
                    agent_path=["plan"], content="工具前正文", reasoning_content="",
                    agent_name="r", depth=1,
                    subagent_thread_id="sa1", msg_id="m1",
                )
            )
            clock.advance(0.1)
            run_async(
                self.callback(
                    agent_path=["plan"], content="工具前正文尾段", reasoning_content="",
                    agent_name="r", depth=1,
                    subagent_thread_id="sa1", msg_id="m2",
                )
            )
            run_async(
                self.tool_callback(
                    "tool_call_pending", "tc1", "web_search",
                    subagent_thread_id="sa1", depth=1, parameters={"q": "x"},
                )
            )
        self.assertEqual(recorder.order, ["subagent_content", "subagent_content", "tool_event"])
        flushed = recorder.publish_calls[-1][1]
        self.assertEqual(flushed["data"]["content"], "工具前正文工具前正文尾段")

    def test_flush_is_idempotent(self):
        """重复冲刷幂等：pending 清空后不再广播。"""
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish, patch(
            PERSIST_SUB_SNAPSHOT
        ):
            run_async(
                self.callback(
                    agent_path=["plan"], content="内容", reasoning_content="",
                    agent_name="r", depth=1,
                    subagent_thread_id="sa1", msg_id="m1",
                )
            )
            run_async(self.publisher.flush_all_subagent_content())
            run_async(self.publisher.flush_all_subagent_content())
        self.assertEqual(mock_publish.await_count, 1)
