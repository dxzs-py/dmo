"""D4/P2-10 research 链会话级 Token 硬预算熔断测试。

对齐 chat 链 COST-01（apps/chat/tests/test_session_budget.py 同构，mock 风格一致）：

- 守卫归属：chat 关联场景绑定父会话 ID（chat_session_id）；独立深度研究回退
  thread_id（research task_id，DB 聚合基线为 0，约束本次执行增量）；
- 循环接入（astream_research_with_interrupts）：
  - mock usage 超限（usage_metadata chunk 时机）→ 终止 astream + 发布
    budget_exceeded 事件（source=deep_research，task 频道路由）+ 正文追加
    系统提示 + result.budget_exceeded 标记；
  - TOOL_CALL_PENDING 时机：超限时不发布该工具事件（先预算后重复检测，
    与 chat loop.py 顺序一致）；
  - 未超限 → 全部 chunk 正常消费；预算未配置 → 守卫不启用不发布；
- 真实守卫路径：SessionTokenBudgetGuard 真类 + mock 配置/DB 基线，验证
  共享层判定与 research 挂载的全链路（工厂接线不做 mock）。

mock 说明：patch adapter 内局部 import 的守卫工厂 / 事件发布 / 正文广播，
替换实例级 _publish_tool_event / _tool_event_extractor，用假 graph.astream
驱动，不依赖 DB / Redis / LLM。

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.apps.research.tests.test_session_budget
"""

import asyncio
import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langchain_core.messages import AIMessageChunk

from Django_xm.apps.ai_engine.services.token_counter import TokenUsageCallbackHandler
from Django_xm.apps.research.services.adapter import OfficialDeepAgentAdapter
from Django_xm.common.event_schema import EventType, validate_payload

_USAGE_MODULE = "Django_xm.apps.ai_engine.services.usage_tracker"
_PUBLISH_MODULE = "Django_xm.common.realtime_events"
_WRITEBACK_MODULE = "Django_xm.apps.research.services.writeback"
_RESEARCH_MODELS_MODULE = "Django_xm.apps.research.models"


def _make_guard(enabled: bool, exceeded: bool) -> tuple[mock.MagicMock, list]:
    """构造守卫替身；返回 (guard, check 调用记录)。"""
    calls: list[int] = []
    guard = mock.MagicMock()
    guard.enabled = enabled

    def _check(current_request_tokens: int = 0):
        calls.append(current_request_tokens)
        return {
            "enabled": enabled,
            "exceeded": exceeded and enabled,
            "session_used": 100,
            "current_request": current_request_tokens,
            "total": 100 + current_request_tokens,
            "limit": 120 if enabled else 0,
        }

    guard.check.side_effect = _check
    # adapter 经 await acheck(...)（基线离环变体）——AsyncMock 使 await 可用，同一 info
    guard.acheck = mock.AsyncMock(side_effect=_check)
    return guard, calls


class _FakeGraph:
    """假 graph：astream 顺序产出 (mode, (chunk, meta)) 元组，记录消费数。"""

    def __init__(self, chunks: list[AIMessageChunk]):
        self._chunks = chunks
        self.consumed = 0

    async def astream(self, graph_input, config=None, stream_mode=None):
        for chunk in self._chunks:
            self.consumed += 1
            yield ("messages", (chunk, {"langgraph_step": self.consumed}))

    async def aget_state(self, config):  # 最终状态提取（结果字典构造）
        return None

    async def aupdate_state(self, config, values):  # 韧性消息注入兜底（本测试不应触发）
        return None


def _chunk(content: str, with_usage: bool) -> AIMessageChunk:
    if with_usage:
        return AIMessageChunk(
            content=content,
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )
    return AIMessageChunk(content=content)


def _make_adapter(
    chunks: list[AIMessageChunk],
    chat_session_id: str | None = "cs-1",
    extractor_events: list[dict] | None = None,
) -> tuple[OfficialDeepAgentAdapter, _FakeGraph, list]:
    """构造 adapter 替身；返回 (adapter, graph, 工具事件发布记录)。"""
    graph = _FakeGraph(chunks)
    adapter = OfficialDeepAgentAdapter(
        graph=graph,
        thread_id="task-1",
        chat_session_id=chat_session_id,
    )
    published_tools: list[dict] = []

    async def _fake_publish_tool_event(evt: dict):
        published_tools.append(evt)

    adapter._publish_tool_event = _fake_publish_tool_event
    if extractor_events is not None:
        fake_extractor = mock.MagicMock()
        fake_extractor.feed.return_value = extractor_events
        adapter._tool_event_extractor = fake_extractor
    return adapter, graph, published_tools


def _run_adapter(
    adapter: OfficialDeepAgentAdapter,
    guard: mock.MagicMock,
    cb_total_tokens: int = 0,
) -> tuple[dict, list, mock.MagicMock, mock.MagicMock]:
    """驱动 astream_research_with_interrupts。

    返回 (result, publish_event 调用记录, 守卫工厂 mock, 正文广播 mock)。
    """
    published: list[dict] = []

    async def _fake_publish_event(event_type, payload, session_id=None, **kwargs):
        published.append(
            {"event_type": event_type, "payload": payload, "session_id": session_id, "kwargs": kwargs}
        )

    cb = TokenUsageCallbackHandler()
    cb.total_tokens = cb_total_tokens  # 模拟本轮执行已消耗的 token 增量
    guard_factory = mock.MagicMock(return_value=guard)
    config = {"callbacks": [cb]}

    with (
        mock.patch(f"{_USAGE_MODULE}.create_session_token_budget_guard", guard_factory),
        mock.patch(f"{_PUBLISH_MODULE}.publish_event", new=_fake_publish_event),
        mock.patch(f"{_WRITEBACK_MODULE}.broadcast_stream_content") as broadcast_mock,
    ):
        result = asyncio.run(
            adapter.astream_research_with_interrupts("研究查询", config=config)
        )
    return result, published, guard_factory, broadcast_mock


class GuardSessionBindingTests(unittest.TestCase):
    """守卫 session_id 归属：chat 关联绑定父会话；独立深研回退 thread_id。"""

    def test_guard_bound_to_chat_session_when_linked(self) -> None:
        """chat 触发深研：守卫归属父 chat 会话（与父会话共享预算池）。"""
        guard, _ = _make_guard(enabled=True, exceeded=False)
        adapter, _, _ = _make_adapter([_chunk("a", with_usage=False)], chat_session_id="cs-1")

        _, _, guard_factory, _ = _run_adapter(adapter, guard)

        guard_factory.assert_called_once_with("cs-1")

    def test_guard_falls_back_to_thread_id_when_standalone(self) -> None:
        """独立深度研究：chat_session_id 为空 → 守卫回退 thread_id（task_id）。

        反查 ResearchTask.session_id 返回 None（mock，不触 DB），独立场景保持
        chat_session_id=None；thread_id 不存在于 ChatSession → DB 聚合基线为 0，
        预算约束本次执行的 cb 增量（主链 + 子代理）。
        """
        guard, _ = _make_guard(enabled=True, exceeded=False)
        adapter, _, _ = _make_adapter([_chunk("a", with_usage=False)], chat_session_id=None)
        # 反查桩：ResearchTask 查询返回 None（无关联 chat 会话）
        fake_task_model = mock.MagicMock()
        fake_task_model.objects.filter.return_value.only.return_value.first.return_value = None

        with mock.patch(f"{_RESEARCH_MODELS_MODULE}.ResearchTask", fake_task_model):
            _, _, guard_factory, _ = _run_adapter(adapter, guard)

        guard_factory.assert_called_once_with("task-1")


class ResearchLoopBudgetTests(unittest.TestCase):
    """执行循环接入：超限终止 + 事件发布 / 未超限正常 / 未配置不启用。"""

    def test_exceeded_on_usage_chunk_terminates_and_publishes(self) -> None:
        """mock usage 超限（usage_metadata 时机）：循环提前终止、发布事件、
        正文含系统提示、result 携带 budget_exceeded 标记。"""
        guard, calls = _make_guard(enabled=True, exceeded=True)
        adapter, graph, _ = _make_adapter(
            [_chunk("第一轮", with_usage=True), _chunk("第二轮", with_usage=True)]
        )

        result, published, _, _ = _run_adapter(adapter, guard, cb_total_tokens=50)

        # 第一个 chunk 消费后即熔断：第二个 chunk 未被消费
        self.assertEqual(graph.consumed, 1)
        # 守卫以当前请求增量被调用（cb.total_tokens=50）
        self.assertEqual(calls, [50])
        # budget_exceeded 事件已发布，payload 符合 schema，source=deep_research
        self.assertEqual(len(published), 1)
        self.assertIs(published[0]["event_type"], EventType.BUDGET_EXCEEDED)
        self.assertEqual(published[0]["payload"]["source"], "deep_research")
        self.assertEqual(published[0]["payload"]["source_id"], "task-1")
        self.assertEqual(published[0]["payload"]["data"]["budget_limit"], 120)
        self.assertEqual(published[0]["payload"]["data"]["used_tokens"], 150)
        self.assertEqual(published[0]["kwargs"].get("task_id"), "task-1")
        validate_payload(EventType.BUDGET_EXCEEDED, published[0]["payload"])
        # 优雅收尾：正文追加系统提示（随 result.main_content 落库）
        self.assertIn("Token 预算已耗尽", adapter._main_content)
        self.assertTrue(result.get("budget_exceeded"))
        self.assertIn("Token 预算已耗尽", result.get("main_content", ""))

    def test_exceeded_on_tool_pending_blocks_publish(self) -> None:
        """TOOL_CALL_PENDING 时机：超限时不发布该工具事件（预算先于重复检测）。"""
        guard, _ = _make_guard(enabled=True, exceeded=True)
        pending_evt = {
            "event_type": EventType.TOOL_CALL_PENDING,
            "tool_call_id": "tc-1",
            "tool_name": "web_search",
            "parameters": {"query": "x"},
        }
        adapter, graph, published_tools = _make_adapter(
            [_chunk("c1", with_usage=False)],
            extractor_events=[pending_evt],
        )

        result, published, _, _ = _run_adapter(adapter, guard)

        self.assertEqual(graph.consumed, 1)
        # 工具事件未发布（熔断先于生命周期发布）
        self.assertEqual(published_tools, [])
        # 熔断事件已发布
        self.assertEqual(len(published), 1)
        self.assertIs(published[0]["event_type"], EventType.BUDGET_EXCEEDED)
        self.assertTrue(result.get("budget_exceeded"))

    def test_not_exceeded_runs_normally(self) -> None:
        """未超限：全部 chunk 正常消费，不发布事件、正文无系统提示。"""
        guard, _ = _make_guard(enabled=True, exceeded=False)
        adapter, graph, _ = _make_adapter(
            [_chunk("a", with_usage=True), _chunk("b", with_usage=True)]
        )

        result, published, _, _ = _run_adapter(adapter, guard)

        self.assertEqual(graph.consumed, 2)
        self.assertEqual(published, [])
        self.assertNotIn("budget_exceeded", result)
        self.assertNotIn("Token 预算已耗尽", adapter._main_content)

    def test_budget_not_configured_skips_check(self) -> None:
        """预算未配置：守卫不启用，check 不被调用，行为与原循环一致。"""
        guard, calls = _make_guard(enabled=False, exceeded=False)
        adapter, graph, _ = _make_adapter(
            [_chunk("a", with_usage=True), _chunk("b", with_usage=True)]
        )

        result, published, _, _ = _run_adapter(adapter, guard)

        self.assertEqual(graph.consumed, 2)
        self.assertEqual(calls, [])  # 未启用时连 check 都不触发
        self.assertEqual(published, [])
        self.assertNotIn("budget_exceeded", result)


class RealGuardWiringTests(unittest.TestCase):
    """真实守卫路径：SessionTokenBudgetGuard 真类 + mock 配置/DB 基线，
    验证共享层判定与 research 挂载的全链路（工厂不做 mock）。"""

    def test_real_guard_exceeded_with_db_baseline(self) -> None:
        """真实守卫：DB 基线 100 + 本次增量 50 >= 上限 120 → 熔断行为触发。"""
        adapter, graph, _ = _make_adapter(
            [_chunk("第一轮", with_usage=True), _chunk("第二轮", with_usage=True)]
        )
        published: list[dict] = []

        async def _fake_publish_event(event_type, payload, session_id=None, **kwargs):
            published.append({"event_type": event_type, "payload": payload, "kwargs": kwargs})

        cb = TokenUsageCallbackHandler()
        cb.total_tokens = 50
        config = {"callbacks": [cb]}

        with (
            mock.patch(f"{_USAGE_MODULE}.get_session_token_budget_limit", return_value=120),
            mock.patch(f"{_USAGE_MODULE}.get_session_total_tokens", return_value=100),
            mock.patch(f"{_PUBLISH_MODULE}.publish_event", new=_fake_publish_event),
            mock.patch(f"{_WRITEBACK_MODULE}.broadcast_stream_content"),
        ):
            result = asyncio.run(
                adapter.astream_research_with_interrupts("研究查询", config=config)
            )

        self.assertEqual(graph.consumed, 1)
        self.assertEqual(len(published), 1)
        self.assertIs(published[0]["event_type"], EventType.BUDGET_EXCEEDED)
        info = published[0]["payload"]["data"]
        self.assertEqual(info["used_tokens"], 150)  # 基线 100 + 增量 50
        self.assertEqual(info["budget_limit"], 120)
        self.assertTrue(result.get("budget_exceeded"))

    def test_real_guard_not_exceeded(self) -> None:
        """真实守卫：基线 + 增量低于上限 → 不熔断，全量消费。"""
        adapter, graph, _ = _make_adapter(
            [_chunk("a", with_usage=True), _chunk("b", with_usage=True)]
        )
        published: list[dict] = []

        async def _fake_publish_event(event_type, payload, session_id=None, **kwargs):
            published.append({"event_type": event_type, "payload": payload, "kwargs": kwargs})

        cb = TokenUsageCallbackHandler()
        cb.total_tokens = 5
        config = {"callbacks": [cb]}

        with (
            mock.patch(f"{_USAGE_MODULE}.get_session_token_budget_limit", return_value=2_000_000),
            mock.patch(f"{_USAGE_MODULE}.get_session_total_tokens", return_value=100),
            mock.patch(f"{_PUBLISH_MODULE}.publish_event", new=_fake_publish_event),
            mock.patch(f"{_WRITEBACK_MODULE}.broadcast_stream_content"),
        ):
            result = asyncio.run(
                adapter.astream_research_with_interrupts("研究查询", config=config)
            )

        self.assertEqual(graph.consumed, 2)
        self.assertEqual(published, [])
        self.assertNotIn("budget_exceeded", result)


if __name__ == "__main__":
    unittest.main()
