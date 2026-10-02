"""learning resilience（工作流韧性组件）单元测试（ARCH-21）。

覆盖：
- invoke_with_resilience：happy path、可恢复错误重试后成功、
  FAIL 不重试、GraphRecursionError 不重试、TimeoutError 不重试、重试耗尽抛出
- ainvoke_with_resilience：异步 happy path + 重试
- stream_with_resilience / astream_with_resilience：happy path 与
  hard_timeout 中断（流式不重试，仅超时守卫）
- _ensure_config / _ensure_timeout_manager / _get_graph_recursion_error 辅助函数

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.learning.tests.test_resilience --noinput
"""

import asyncio
import os
from unittest import TestCase as UnitTestCase
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langgraph.errors import GraphRecursionError

from Django_xm.apps.ai_engine.services.agent_resilience import (
    ExecutionTimeoutManager,
    ResilienceConfig,
)
from Django_xm.apps.learning.services.resilience import (
    _ensure_config,
    _ensure_timeout_manager,
    _get_graph_recursion_error,
    ainvoke_with_resilience,
    astream_with_resilience,
    invoke_with_resilience,
    stream_with_resilience,
)


def _fast_config(max_retries: int = 2) -> ResilienceConfig:
    """零退避配置（测试不等待）"""
    return ResilienceConfig(
        max_retries=max_retries,
        initial_retry_interval=0.0,
        max_retry_interval=0.0,
        retry_backoff_factor=1.0,
    )


class InvokeWithResilienceTests(UnitTestCase):
    """invoke_with_resilience：同步 invoke + 重试 + 超时"""

    def test_happy_path_returns_result(self):
        """首次成功直接返回，不触发重试"""
        graph = mock.Mock()
        graph.invoke.return_value = {"current_step": "waiting_for_answers"}
        result = invoke_with_resilience(graph, {}, None, resilience_config=_fast_config())
        self.assertEqual(result, {"current_step": "waiting_for_answers"})
        graph.invoke.assert_called_once()

    def test_retryable_error_then_success(self):
        """可恢复错误（rate limit）重试一次后成功"""
        graph = mock.Mock()
        graph.invoke.side_effect = [ValueError("rate limit exceeded"), "ok"]
        result = invoke_with_resilience(graph, {}, None, resilience_config=_fast_config(max_retries=2))
        self.assertEqual(result, "ok")
        self.assertEqual(graph.invoke.call_count, 2)

    def test_non_recoverable_error_fails_immediately(self):
        """不可恢复错误（FAIL 分类）不重试，直接抛出"""
        graph = mock.Mock()
        graph.invoke.side_effect = ValueError("boom")
        with self.assertRaises(ValueError):
            invoke_with_resilience(graph, {}, None, resilience_config=_fast_config(max_retries=3))
        self.assertEqual(graph.invoke.call_count, 1)

    def test_graph_recursion_error_not_retried(self):
        """GraphRecursionError 不重试，直接抛出"""
        graph = mock.Mock()
        graph.invoke.side_effect = GraphRecursionError("recursion")
        with self.assertRaises(GraphRecursionError):
            invoke_with_resilience(graph, {}, None, resilience_config=_fast_config(max_retries=3))
        self.assertEqual(graph.invoke.call_count, 1)

    def test_timeout_error_not_retried(self):
        """TimeoutError 不重试，直接抛出"""
        graph = mock.Mock()
        graph.invoke.side_effect = TimeoutError("timeout")
        with self.assertRaises(TimeoutError):
            invoke_with_resilience(graph, {}, None, resilience_config=_fast_config(max_retries=3))
        self.assertEqual(graph.invoke.call_count, 1)

    def test_retries_exhausted_raises_last_error(self):
        """重试耗尽后抛出最后一次错误（max_retries=1 → 共 2 次调用）"""
        graph = mock.Mock()
        graph.invoke.side_effect = ValueError("rate limit exceeded")
        with self.assertRaises(ValueError):
            invoke_with_resilience(graph, {}, None, resilience_config=_fast_config(max_retries=1))
        self.assertEqual(graph.invoke.call_count, 2)

    def test_hard_timeout_raises_before_invoke(self):
        """hard_timeout 已超时 → 调用前抛 TimeoutError，不执行图"""
        graph = mock.Mock()
        tm = ExecutionTimeoutManager(soft_timeout=None, hard_timeout=0.0)
        with self.assertRaises(TimeoutError):
            invoke_with_resilience(graph, {}, None, resilience_config=_fast_config(), timeout_manager=tm)
        graph.invoke.assert_not_called()


class AinvokeWithResilienceTests(UnitTestCase):
    """ainvoke_with_resilience：异步 ainvoke + 重试 + 超时"""

    def test_happy_path(self):
        """异步首次成功直接返回"""
        graph = mock.Mock()
        graph.ainvoke = mock.AsyncMock(return_value={"ok": True})
        result = asyncio.run(ainvoke_with_resilience(graph, {}, None, resilience_config=_fast_config()))
        self.assertEqual(result, {"ok": True})
        graph.ainvoke.assert_awaited_once()

    def test_retryable_error_then_success(self):
        """异步可恢复错误重试后成功"""
        graph = mock.Mock()
        graph.ainvoke = mock.AsyncMock(side_effect=[ValueError("rate limit exceeded"), "ok"])
        result = asyncio.run(ainvoke_with_resilience(graph, {}, None, resilience_config=_fast_config(max_retries=2)))
        self.assertEqual(result, "ok")
        self.assertEqual(graph.ainvoke.await_count, 2)

    def test_non_recoverable_error_fails_immediately(self):
        """异步不可恢复错误不重试"""
        graph = mock.Mock()
        graph.ainvoke = mock.AsyncMock(side_effect=ValueError("boom"))
        with self.assertRaises(ValueError):
            asyncio.run(ainvoke_with_resilience(graph, {}, None, resilience_config=_fast_config(max_retries=3)))
        self.assertEqual(graph.ainvoke.await_count, 1)

    def test_hard_timeout_raises_before_invoke(self):
        """异步 hard_timeout 已超时 → 调用前抛 TimeoutError"""
        graph = mock.Mock()
        graph.ainvoke = mock.AsyncMock()
        tm = ExecutionTimeoutManager(soft_timeout=None, hard_timeout=0.0)
        with self.assertRaises(TimeoutError):
            asyncio.run(
                ainvoke_with_resilience(graph, {}, None, resilience_config=_fast_config(), timeout_manager=tm)
            )
        graph.ainvoke.assert_not_awaited()


class StreamWithResilienceTests(UnitTestCase):
    """stream_with_resilience：同步 stream + 超时（不重试）"""

    def test_yields_all_chunks(self):
        """依次产出全部 chunk"""
        graph = mock.Mock()
        graph.stream.return_value = iter([{"a": 1}, {"a": 2}, {"a": 3}])
        chunks = list(stream_with_resilience(graph, {}, None, stream_mode="values", resilience_config=_fast_config()))
        self.assertEqual(chunks, [{"a": 1}, {"a": 2}, {"a": 3}])

    def test_hard_timeout_mid_stream(self):
        """hard_timeout 超时 → 迭代时抛 TimeoutError（不重试已产出 chunk）"""
        graph = mock.Mock()
        graph.stream.return_value = iter([{"a": 1}])
        tm = ExecutionTimeoutManager(soft_timeout=None, hard_timeout=0.0)
        gen = stream_with_resilience(graph, {}, None, resilience_config=_fast_config(), timeout_manager=tm)
        with self.assertRaises(TimeoutError):
            list(gen)


class AstreamWithResilienceTests(UnitTestCase):
    """astream_with_resilience：异步 astream + 超时（不重试）"""

    def test_yields_all_chunks(self):
        """异步依次产出全部 chunk"""

        async def _astream(*args, **kwargs):
            for chunk in [{"a": 1}, {"a": 2}]:
                yield chunk

        graph = mock.Mock()
        graph.astream = _astream
        chunks = asyncio.run(_collect(graph))
        self.assertEqual(chunks, [{"a": 1}, {"a": 2}])

    def test_hard_timeout_before_yield(self):
        """hard_timeout 超时 → 首个 chunk 前抛 TimeoutError"""

        async def _astream(*args, **kwargs):
            yield {"a": 1}

        graph = mock.Mock()
        graph.astream = _astream
        tm = ExecutionTimeoutManager(soft_timeout=None, hard_timeout=0.0)
        with self.assertRaises(TimeoutError):
            asyncio.run(_collect(graph, timeout_manager=tm))


async def _collect(graph, timeout_manager=None):
    """聚合 astream_with_resilience 输出"""
    chunks = []
    async for chunk in astream_with_resilience(
        graph, {}, None, resilience_config=_fast_config(), timeout_manager=timeout_manager
    ):
        chunks.append(chunk)
    return chunks


class HelperFunctionTests(UnitTestCase):
    """辅助函数：配置 / 超时管理器 / GraphRecursionError 获取"""

    def test_ensure_config_passes_through(self):
        """显式配置原样返回"""
        cfg = _fast_config()
        self.assertIs(_ensure_config(cfg), cfg)

    def test_ensure_config_loads_default_when_none(self):
        """None 时从 settings 加载默认配置"""
        cfg = _ensure_config(None)
        self.assertIsInstance(cfg, ResilienceConfig)

    def test_ensure_timeout_manager_passes_through(self):
        """显式超时管理器原样返回"""
        tm = ExecutionTimeoutManager(soft_timeout=None, hard_timeout=5.0)
        self.assertIs(_ensure_timeout_manager(tm, _fast_config()), tm)

    def test_ensure_timeout_manager_builds_from_config(self):
        """None 时基于配置新建（继承软/硬超时）"""
        cfg = ResilienceConfig(soft_timeout=10.0, hard_timeout=20.0)
        tm = _ensure_timeout_manager(None, cfg)
        self.assertEqual(tm.soft_timeout, 10.0)
        self.assertEqual(tm.hard_timeout, 20.0)

    def test_get_graph_recursion_error_available(self):
        """langgraph 可用时返回 GraphRecursionError 类"""
        self.assertIs(_get_graph_recursion_error(), GraphRecursionError)
