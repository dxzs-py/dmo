"""COST-01 会话级 Token 硬预算熔断单元测试。

覆盖：
- SessionTokenBudgetGuard：预算未配置不启用 / 超限判定 / 基线缓存（DB 只查一次）；
- 执行循环接入（run_stream_loop）：mock usage 超限 → 终止本轮循环且发布
  budget_exceeded 事件；未超限 → 正常走完；预算未配置 → 不启用不发布。

mock 说明：循环集成测试 patch loop 模块的守卫工厂 / 事件发布 /
chunk 处理器 / 工具事件提取器，用假 graph.astream 驱动，不依赖 DB / Redis / LLM。

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.apps.chat.tests.test_session_budget
"""

import asyncio
import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langchain_core.messages import AIMessageChunk

from Django_xm.apps.chat.services.stream.context import StreamContext
from Django_xm.apps.chat.services.stream.loop import run_stream_loop
from Django_xm.apps.chat.services.stream.strategy import NormalStreamStrategy
from Django_xm.apps.ai_engine.services.token_counter import TokenUsageCallbackHandler
from Django_xm.apps.ai_engine.services.usage_tracker import SessionTokenBudgetGuard
from Django_xm.common.event_schema import EventType, validate_payload

_LOOP_MODULE = "Django_xm.apps.chat.services.stream.loop"
_USAGE_MODULE = "Django_xm.apps.ai_engine.services.usage_tracker"


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

    async def aupdate_state(self, config, values):  # 韧性消息注入兜底（本测试不应触发）
        return None


def _chunk(content: str, with_usage: bool) -> AIMessageChunk:
    if with_usage:
        return AIMessageChunk(
            content=content,
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )
    return AIMessageChunk(content=content)


def _run_loop(graph, guard, data: dict) -> tuple[list[dict], list[dict]]:
    """驱动 run_stream_loop，返回 (产出事件, publish_event 调用记录)。"""
    published: list[dict] = []

    async def _fake_publish(event_type, payload, session_id=None, **kwargs):
        published.append({"event_type": event_type, "payload": payload, "session_id": session_id})

    fake_extractor = mock.MagicMock()
    fake_extractor.feed.return_value = []

    def _fake_process_chunk(mode_data, content, **kwargs):
        yield {"type": "chunk", "content": "正文片段"}

    async def _collect():
        events = []
        async for event in run_stream_loop(
            mock.MagicMock(graph=graph),
            {"messages": []},
            {"callbacks": [TokenUsageCallbackHandler()]},
            StreamContext(),
            NormalStreamStrategy(),
            data,
        ):
            events.append(event)
        return events

    with (
        mock.patch(f"{_LOOP_MODULE}.create_session_token_budget_guard", return_value=guard),
        mock.patch(f"{_LOOP_MODULE}.publish_event", new=_fake_publish),
        mock.patch(f"{_LOOP_MODULE}.process_stream_chunk", _fake_process_chunk),
        mock.patch(f"{_LOOP_MODULE}.ToolEventExtractor", return_value=fake_extractor),
    ):
        events = asyncio.run(_collect())
    return events, published


class SessionBudgetGuardTests(unittest.TestCase):
    """SessionTokenBudgetGuard：会话级累计读取与超限判定。"""

    def test_disabled_when_budget_not_configured(self) -> None:
        """预算未配置（<=0）：不启用，check 恒不超限。"""
        with mock.patch(f"{_USAGE_MODULE}.get_session_token_budget_limit", return_value=0):
            guard = SessionTokenBudgetGuard("s-1")
            self.assertFalse(guard.enabled)
            info = guard.check(999999)
        self.assertFalse(info["exceeded"])
        self.assertFalse(info["enabled"])

    def test_disabled_when_no_session_id(self) -> None:
        """无 session_id：不启用（且不触发 DB 查询）。"""
        with (
            mock.patch(f"{_USAGE_MODULE}.get_session_token_budget_limit", return_value=2_000_000),
            mock.patch(f"{_USAGE_MODULE}.get_session_total_tokens") as total_mock,
        ):
            guard = SessionTokenBudgetGuard("")
            self.assertFalse(guard.enabled)
            guard.check(10)
            total_mock.assert_not_called()

    def test_exceeded_when_total_over_limit(self) -> None:
        """会话累计 + 当前请求增量 >= 上限 → 超限。"""
        with (
            mock.patch(f"{_USAGE_MODULE}.get_session_token_budget_limit", return_value=120),
            mock.patch(f"{_USAGE_MODULE}.get_session_total_tokens", return_value=100),
        ):
            guard = SessionTokenBudgetGuard("s-1")
            info = guard.check(current_request_tokens=50)
        self.assertTrue(info["exceeded"])
        self.assertEqual(info["session_used"], 100)
        self.assertEqual(info["current_request"], 50)
        self.assertEqual(info["total"], 150)
        self.assertEqual(info["limit"], 120)

    def test_not_exceeded_when_below_limit(self) -> None:
        """累计低于上限：不超限。"""
        with (
            mock.patch(f"{_USAGE_MODULE}.get_session_token_budget_limit", return_value=2_000_000),
            mock.patch(f"{_USAGE_MODULE}.get_session_total_tokens", return_value=10),
        ):
            guard = SessionTokenBudgetGuard("s-1")
            info = guard.check(current_request_tokens=5)
        self.assertFalse(info["exceeded"])

    def test_baseline_loaded_once(self) -> None:
        """基线缓存：多次 check 只聚合查询一次 DB。"""
        with (
            mock.patch(f"{_USAGE_MODULE}.get_session_token_budget_limit", return_value=2_000_000),
            mock.patch(f"{_USAGE_MODULE}.get_session_total_tokens", return_value=7) as total_mock,
        ):
            guard = SessionTokenBudgetGuard("s-1")
            guard.check(1)
            guard.check(2)
            guard.check(3)
        total_mock.assert_called_once_with("s-1")


class RunStreamLoopBudgetTests(unittest.TestCase):
    """执行循环接入：超限终止 + 事件发布 / 未超限正常 / 未配置不启用。"""

    def test_exceeded_terminates_loop_and_publishes_event(self) -> None:
        """mock usage 超限：循环提前终止、发布 budget_exceeded、正文含系统提示。"""
        guard, calls = _make_guard(enabled=True, exceeded=True)
        graph = _FakeGraph([_chunk("第一轮", with_usage=True), _chunk("第二轮", with_usage=True)])
        data = {"session_id": "s-1"}

        events, published = _run_loop(graph, guard, data)

        # 第一轮 chunk 消费后即熔断：第二个 chunk 未被消费
        self.assertEqual(graph.consumed, 1)
        # 守卫以当前请求增量被调用（fake cb total_tokens=0）
        self.assertEqual(calls, [0])
        # budget_exceeded 事件已发布，payload 符合 schema
        self.assertEqual(len(published), 1)
        self.assertIs(published[0]["event_type"], EventType.BUDGET_EXCEEDED)
        self.assertEqual(published[0]["session_id"], "s-1")
        self.assertEqual(published[0]["payload"]["source"], "chat")
        self.assertEqual(published[0]["payload"]["data"]["budget_limit"], 120)
        validate_payload(EventType.BUDGET_EXCEEDED, published[0]["payload"])
        # 优雅收尾：系统提示已产出为 chunk 事件（finalizer 持久化该内容）
        notice_chunks = [e for e in events if e.get("type") == "chunk" and "Token 预算已耗尽" in e.get("content", "")]
        self.assertEqual(len(notice_chunks), 1)
        self.assertTrue(data.get("_budget_exceeded"))

    def test_not_exceeded_runs_normally(self) -> None:
        """未超限：全部 chunk 正常消费，不发布事件、无系统提示。"""
        guard, _ = _make_guard(enabled=True, exceeded=False)
        graph = _FakeGraph([_chunk("a", with_usage=True), _chunk("b", with_usage=True)])
        data = {"session_id": "s-1"}

        events, published = _run_loop(graph, guard, data)

        self.assertEqual(graph.consumed, 2)
        self.assertEqual(published, [])
        self.assertNotIn("_budget_exceeded", data)
        notice_chunks = [e for e in events if e.get("type") == "chunk" and "Token 预算已耗尽" in e.get("content", "")]
        self.assertEqual(notice_chunks, [])

    def test_budget_not_configured_skips_check(self) -> None:
        """预算未配置：守卫不启用，check 不被调用，行为与原循环一致。"""
        guard, calls = _make_guard(enabled=False, exceeded=False)
        graph = _FakeGraph([_chunk("a", with_usage=True), _chunk("b", with_usage=True)])
        data = {"session_id": "s-1"}

        events, published = _run_loop(graph, guard, data)

        self.assertEqual(graph.consumed, 2)
        self.assertEqual(calls, [])  # 未启用时连 check 都不触发
        self.assertEqual(published, [])


if __name__ == "__main__":
    unittest.main()
