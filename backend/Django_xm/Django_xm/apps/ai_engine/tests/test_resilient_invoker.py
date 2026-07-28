"""ResilientInvoker 单元测试

验证:
1. 单个模型重试 3 次后成功
2. 单个模型重试 3 次后失败，切换到下一个模型
3. 永久性错误（模拟 401）不重试，直接切换
4. 输入错误（模拟 GuardrailsValidationError）不降级，直接抛出
5. 临时性错误（模拟 429）正常重试
6. Circuit Breaker 状态转换：CLOSED → OPEN → HALF_OPEN → CLOSED
7. 所有模型都失败时抛出 RuntimeError
8. 同步和异步接口都能正常工作

运行方式:
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    conda activate langchain_xm
    python -m pytest Django_xm/apps/ai_engine/tests/test_resilient_invoker.py -v

    # 或使用 unittest
    set DJANGO_SETTINGS_MODULE=Django_xm.settings.dev
    python -m unittest Django_xm.apps.ai_engine.tests.test_resilient_invoker -v
"""

from __future__ import annotations

import asyncio
import os
import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

# Django 环境初始化（兼容 pytest 和 unittest 直接运行）
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django
import django.apps

if not django.apps.apps.ready:
    django.setup()

from Django_xm.apps.agent_hub.services.agent_resilience import (
    ResilienceConfig,
)
from Django_xm.apps.agent_hub.services.resilient_invoker import (
    CircuitBreaker,
    CircuitState,
    ResilientInvoker,
    ResilientModel,
)
from Django_xm.apps.ai_engine.services.exceptions import (
    GuardrailsValidationError,
)

# ============================================================================
# 测试用异常类
# ============================================================================


class MockAuthError(Exception):
    """模拟 401 认证错误

    消息包含 "unauthorized"，classify_exception 会分类为
    ModelCallError(auth_error=True, recoverable=False) → 永久性错误
    """

    def __init__(self, message: str = "Unauthorized: invalid API key"):
        super().__init__(message)


class MockRateLimitError(Exception):
    """模拟 429 速率限制错误

    消息包含 "rate limit"，classify_exception 会分类为
    RateLimitExceededError(recoverable=True) → 临时性错误
    """

    def __init__(self, message: str = "rate limit exceeded"):
        super().__init__(message)


class MockTemporaryError(Exception):
    """模拟临时性错误（连接超时）

    消息包含 "timeout"，classify_exception 会分类为
    ModelCallError(timeout=True, recoverable=True) → 临时性错误
    """

    def __init__(self, message: str = "connection timeout"):
        super().__init__(message)


# ============================================================================
# Mock 模型工厂
# ============================================================================


def make_mock_model(
    name: str = "mock-model",
    invoke_side_effects: Any = None,
    ainvoke_side_effects: Any = None,
    stream_chunks: Any = None,
    astream_chunks: Any = None,
    generate_side_effects: Any = None,
    agenerate_side_effects: Any = None,
) -> MagicMock:
    """创建模拟的 BaseChatModel

    Args:
        name: 模型名称（用于日志）
        invoke_side_effects: invoke 的 side_effect（异常列表或单个值）
        ainvoke_side_effects: ainvoke 的 side_effect
        stream_chunks: stream 返回的 chunk 列表
        astream_chunks: astream 返回的 chunk 列表
        generate_side_effects: _generate 的 side_effect
        agenerate_side_effects: _agenerate 的 side_effect

    Returns:
        MagicMock 实例，模拟 BaseChatModel 接口
    """
    model = MagicMock(name=name)
    model._llm_type = name
    model._provider_id = "mock"

    # invoke
    if invoke_side_effects is not None:
        model.invoke.side_effect = invoke_side_effects
    else:
        model.invoke.return_value = MagicMock(content=f"response from {name}")

    # ainvoke（必须使用 AsyncMock 才能被 await）
    if ainvoke_side_effects is not None:
        model.ainvoke = AsyncMock(side_effect=ainvoke_side_effects)
    else:
        model.ainvoke = AsyncMock(
            return_value=MagicMock(content=f"async response from {name}")
        )

    # stream
    if stream_chunks is not None:
        if isinstance(stream_chunks, Exception):
            model.stream.side_effect = stream_chunks
        else:
            model.stream.return_value = iter(list(stream_chunks))
    else:
        model.stream.return_value = iter([f"chunk-{name}-1", f"chunk-{name}-2"])

    # astream（异步迭代器：使用 async generator 函数包装）
    if astream_chunks is not None:
        if isinstance(astream_chunks, Exception):

            async def _astream_fail(*args, **kwargs):
                raise astream_chunks
                yield

            model.astream = MagicMock(side_effect=_astream_fail)
        else:

            async def _astream(*args, **kwargs):
                for chunk in list(astream_chunks):
                    yield chunk

            model.astream = MagicMock(side_effect=_astream)
    else:

        async def _default_astream(*args, **kwargs):
            yield f"chunk-{name}-1"
            yield f"chunk-{name}-2"

        model.astream = MagicMock(side_effect=_default_astream)

    # _generate / _agenerate
    if generate_side_effects is not None:
        model._generate.side_effect = generate_side_effects
    else:
        model._generate.return_value = MagicMock(generations=[MagicMock()])

    if agenerate_side_effects is not None:
        model._agenerate = AsyncMock(side_effect=agenerate_side_effects)
    else:
        model._agenerate = AsyncMock(
            return_value=MagicMock(generations=[MagicMock()])
        )

    return model


def make_fast_config() -> ResilienceConfig:
    """创建测试用配置（无退避等待，快速冷却）"""
    return ResilienceConfig(
        max_retries=3,
        backoff_seconds=(0.0, 0.0, 0.0),
        circuit_breaker_threshold=3,
        circuit_breaker_cooldown=0.1,
    )


# ============================================================================
# CircuitBreaker 单元测试
# ============================================================================


class CircuitBreakerTestCase(unittest.TestCase):
    """CircuitBreaker 状态转换测试"""

    def test_initial_state_is_closed(self):
        """初始状态为 CLOSED"""
        cb = CircuitBreaker(threshold=3, cooldown=30.0)
        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertTrue(cb.is_available())

    def test_closed_to_open_after_threshold(self):
        """连续失败达阈值后 CLOSED -> OPEN"""
        cb = CircuitBreaker(threshold=3, cooldown=30.0)

        cb.record_failure()
        self.assertEqual(cb.state, CircuitState.CLOSED)

        cb.record_failure()
        self.assertEqual(cb.state, CircuitState.CLOSED)

        cb.record_failure()
        self.assertEqual(cb.state, CircuitState.OPEN)
        self.assertFalse(cb.is_available())

    def test_success_resets_failure_count(self):
        """成功调用重置失败计数"""
        cb = CircuitBreaker(threshold=3, cooldown=30.0)

        cb.record_failure()
        cb.record_failure()
        self.assertEqual(cb.consecutive_failures, 2)

        cb.record_success()
        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertEqual(cb.consecutive_failures, 0)

    def test_open_to_half_open_after_cooldown(self):
        """OPEN 冷却后转为 HALF_OPEN"""
        cb = CircuitBreaker(threshold=3, cooldown=0.1)

        # 触发 OPEN
        for _ in range(3):
            cb.record_failure()
        self.assertEqual(cb.state, CircuitState.OPEN)

        # 等待冷却
        import time

        time.sleep(0.15)

        # is_available 触发 OPEN -> HALF_OPEN
        self.assertTrue(cb.is_available())
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)

    def test_half_open_to_closed_on_success(self):
        """HALF_OPEN 试探成功转 CLOSED"""
        cb = CircuitBreaker(threshold=3, cooldown=0.1)

        for _ in range(3):
            cb.record_failure()
        self.assertEqual(cb.state, CircuitState.OPEN)

        import time

        time.sleep(0.15)
        cb.is_available()  # 触发 HALF_OPEN
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)

        cb.record_success()
        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertEqual(cb.consecutive_failures, 0)

    def test_half_open_to_open_on_failure(self):
        """HALF_OPEN 试探失败立即转 OPEN"""
        cb = CircuitBreaker(threshold=3, cooldown=0.1)

        for _ in range(3):
            cb.record_failure()
        self.assertEqual(cb.state, CircuitState.OPEN)

        import time

        time.sleep(0.15)
        cb.is_available()  # 触发 HALF_OPEN
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)

        cb.record_failure()
        self.assertEqual(cb.state, CircuitState.OPEN)

    def test_reset(self):
        """reset 恢复到 CLOSED"""
        cb = CircuitBreaker(threshold=3, cooldown=30.0)
        for _ in range(3):
            cb.record_failure()
        self.assertEqual(cb.state, CircuitState.OPEN)

        cb.reset()
        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertEqual(cb.consecutive_failures, 0)


# ============================================================================
# ResilientInvoker 同步接口测试
# ============================================================================


class ResilientInvokerSyncTestCase(unittest.TestCase):
    """ResilientInvoker 同步接口测试"""

    @patch("Django_xm.apps.agent_hub.services.resilient_invoker.time.sleep")
    def test_retry_3_times_then_success(self, mock_sleep):
        """单个模型重试 3 次后成功（第 1、2 次失败，第 3 次成功）"""
        model = make_mock_model(
            name="model-a",
            invoke_side_effects=[
                MockRateLimitError(),  # 第 1 次失败
                MockRateLimitError(),  # 第 2 次失败
                MagicMock(content="success"),  # 第 3 次成功
            ],
        )

        invoker = ResilientInvoker(models=[model], config=make_fast_config())
        result = invoker.invoke("test input")

        self.assertEqual(result.content, "success")
        self.assertEqual(model.invoke.call_count, 3)
        # 重试 2 次，sleep 2 次
        self.assertEqual(mock_sleep.call_count, 2)
        # CircuitBreaker 应该是 CLOSED（成功后重置）
        self.assertEqual(invoker.get_breaker(0).state, CircuitState.CLOSED)

    @patch("Django_xm.apps.agent_hub.services.resilient_invoker.time.sleep")
    def test_retry_3_times_then_switch_to_next_model(self, mock_sleep):
        """单个模型重试 3 次后失败，切换到下一个模型"""
        model_a = make_mock_model(
            name="model-a",
            invoke_side_effects=[
                MockRateLimitError(),
                MockRateLimitError(),
                MockRateLimitError(),
            ],
        )
        model_b = make_mock_model(
            name="model-b",
            invoke_side_effects=[MagicMock(content="success from b")],
        )

        invoker = ResilientInvoker(
            models=[model_a, model_b], config=make_fast_config()
        )
        result = invoker.invoke("test input")

        self.assertEqual(result.content, "success from b")
        # model_a 被调用 3 次（重试耗尽）
        self.assertEqual(model_a.invoke.call_count, 3)
        # model_b 被调用 1 次
        self.assertEqual(model_b.invoke.call_count, 1)
        # model_a 的 CircuitBreaker 应该是 OPEN（3 次失败）
        self.assertEqual(invoker.get_breaker(0).state, CircuitState.OPEN)
        # model_b 的 CircuitBreaker 应该是 CLOSED（成功）
        self.assertEqual(invoker.get_breaker(1).state, CircuitState.CLOSED)

    @patch("Django_xm.apps.agent_hub.services.resilient_invoker.time.sleep")
    def test_permanent_error_no_retry_switch_immediately(self, mock_sleep):
        """永久性错误（模拟 401）不重试，直接切换"""
        model_a = make_mock_model(
            name="model-a",
            invoke_side_effects=[MockAuthError()],
        )
        model_b = make_mock_model(
            name="model-b",
            invoke_side_effects=[MagicMock(content="success from b")],
        )

        invoker = ResilientInvoker(
            models=[model_a, model_b], config=make_fast_config()
        )
        result = invoker.invoke("test input")

        self.assertEqual(result.content, "success from b")
        # model_a 只被调用 1 次（永久性错误不重试）
        self.assertEqual(model_a.invoke.call_count, 1)
        # model_b 被调用 1 次
        self.assertEqual(model_b.invoke.call_count, 1)
        # 不应该有 sleep（没重试）
        self.assertEqual(mock_sleep.call_count, 0)

    @patch("Django_xm.apps.agent_hub.services.resilient_invoker.time.sleep")
    def test_input_error_no_fallback_raise_directly(self, mock_sleep):
        """输入错误（GuardrailsValidationError）不降级，直接抛出"""
        model_a = make_mock_model(
            name="model-a",
            invoke_side_effects=[
                GuardrailsValidationError("content validation failed")
            ],
        )
        model_b = make_mock_model(name="model-b")

        invoker = ResilientInvoker(
            models=[model_a, model_b], config=make_fast_config()
        )

        with self.assertRaises(GuardrailsValidationError):
            invoker.invoke("test input")

        # model_a 只被调用 1 次
        self.assertEqual(model_a.invoke.call_count, 1)
        # model_b 不应该被调用（不降级）
        self.assertEqual(model_b.invoke.call_count, 0)
        self.assertEqual(mock_sleep.call_count, 0)

    @patch("Django_xm.apps.agent_hub.services.resilient_invoker.time.sleep")
    def test_temporary_error_normal_retry(self, mock_sleep):
        """临时性错误（模拟 429）正常重试"""
        model = make_mock_model(
            name="model-a",
            invoke_side_effects=[
                MockRateLimitError(),
                MagicMock(content="success after retry"),
            ],
        )

        invoker = ResilientInvoker(models=[model], config=make_fast_config())
        result = invoker.invoke("test input")

        self.assertEqual(result.content, "success after retry")
        self.assertEqual(model.invoke.call_count, 2)
        self.assertEqual(mock_sleep.call_count, 1)

    @patch("Django_xm.apps.agent_hub.services.resilient_invoker.time.sleep")
    def test_all_models_fail_raises_runtime_error(self, mock_sleep):
        """所有模型都失败时抛出 RuntimeError"""
        model_a = make_mock_model(
            name="model-a",
            invoke_side_effects=[
                MockRateLimitError(),
                MockRateLimitError(),
                MockRateLimitError(),
            ],
        )
        model_b = make_mock_model(
            name="model-b",
            invoke_side_effects=[
                MockTemporaryError(),
                MockTemporaryError(),
                MockTemporaryError(),
            ],
        )

        invoker = ResilientInvoker(
            models=[model_a, model_b], config=make_fast_config()
        )

        with self.assertRaises(RuntimeError) as ctx:
            invoker.invoke("test input")

        self.assertIn("所有模型调用均失败", str(ctx.exception))
        self.assertEqual(model_a.invoke.call_count, 3)
        self.assertEqual(model_b.invoke.call_count, 3)

    @patch("Django_xm.apps.agent_hub.services.resilient_invoker.time.sleep")
    def test_circuit_breaker_skips_open_model(self, mock_sleep):
        """CircuitBreaker OPEN 时跳过模型"""
        model_a = make_mock_model(
            name="model-a",
            invoke_side_effects=[
                MockRateLimitError(),
                MockRateLimitError(),
                MockRateLimitError(),
            ],
        )
        model_b = make_mock_model(
            name="model-b",
            invoke_side_effects=[MagicMock(content="success from b")],
        )

        invoker = ResilientInvoker(
            models=[model_a, model_b], config=make_fast_config()
        )

        # 第一次调用：model_a 重试 3 次失败，切换到 model_b 成功
        result1 = invoker.invoke("test input")
        self.assertEqual(result1.content, "success from b")
        self.assertEqual(invoker.get_breaker(0).state, CircuitState.OPEN)

        # 第二次调用：model_a CB OPEN 应该被跳过，直接用 model_b
        model_b.invoke.reset_mock(side_effect=True)
        model_b.invoke.return_value = MagicMock(content="success from b again")
        result2 = invoker.invoke("test input")
        self.assertEqual(result2.content, "success from b again")

        # model_a 不应该被再次调用（CB OPEN 跳过）
        # 注意：call_count 仍然是 3（来自第一次调用）
        self.assertEqual(model_a.invoke.call_count, 3)
        # model_b 第二次被调用
        self.assertEqual(model_b.invoke.call_count, 1)

    @patch("Django_xm.apps.agent_hub.services.resilient_invoker.time.sleep")
    def test_generate_with_retry_and_fallback(self, mock_sleep):
        """generate 接口的重试与降级"""
        model_a = make_mock_model(
            name="model-a",
            generate_side_effects=[
                MockRateLimitError(),
                MockRateLimitError(),
                MockRateLimitError(),
            ],
        )
        model_b = make_mock_model(
            name="model-b",
            generate_side_effects=[MagicMock(generations=[MagicMock(text="b")])],
        )

        invoker = ResilientInvoker(
            models=[model_a, model_b], config=make_fast_config()
        )
        result = invoker.generate([])

        self.assertEqual(model_a._generate.call_count, 3)
        self.assertEqual(model_b._generate.call_count, 1)
        self.assertIsNotNone(result)

    @patch("Django_xm.apps.agent_hub.services.resilient_invoker.time.sleep")
    def test_stream_with_retry_and_fallback(self, mock_sleep):
        """stream 接口的重试与降级"""
        model_a = make_mock_model(
            name="model-a",
            stream_chunks=MockRateLimitError(),
        )
        model_b = make_mock_model(
            name="model-b",
            stream_chunks=["chunk-b-1", "chunk-b-2"],
        )

        invoker = ResilientInvoker(
            models=[model_a, model_b], config=make_fast_config()
        )
        chunks = list(invoker.stream("test input"))

        self.assertEqual(chunks, ["chunk-b-1", "chunk-b-2"])
        # model_a 重试 3 次都失败
        self.assertEqual(model_a.stream.call_count, 3)
        # model_b 调用 1 次成功
        self.assertEqual(model_b.stream.call_count, 1)

    def test_circuit_breaker_full_state_transition(self):
        """Circuit Breaker 完整状态转换：CLOSED → OPEN → HALF_OPEN → CLOSED

        注意：此测试不 mock time.sleep，因为 CB 冷却需要真实时间流逝。
        backoff_seconds 配置为 (0.0, 0.0, 0.0)，重试退避是即时的；
        CB 冷却时间 0.1s 由真实 time.sleep(0.15) 等待。
        """
        config = ResilienceConfig(
            max_retries=3,
            backoff_seconds=(0.0, 0.0, 0.0),
            circuit_breaker_threshold=3,
            circuit_breaker_cooldown=0.1,
        )

        # 第一阶段：让 model_a 的 CB 进入 OPEN
        model_a = make_mock_model(
            name="model-a",
            invoke_side_effects=[
                MockRateLimitError(),
                MockRateLimitError(),
                MockRateLimitError(),
            ],
        )
        model_b = make_mock_model(
            name="model-b",
            invoke_side_effects=[MagicMock(content="from b")],
        )
        invoker = ResilientInvoker(
            models=[model_a, model_b], config=config
        )

        # CLOSED -> OPEN（3 次失败）
        invoker.invoke("test")
        self.assertEqual(invoker.get_breaker(0).state, CircuitState.OPEN)

        # 等待冷却（真实时间流逝，使 CB 从 OPEN 转为 HALF_OPEN）
        import time

        time.sleep(0.15)

        # OPEN -> HALF_OPEN（试探成功后 -> CLOSED）
        # 重新配置 model_a 使其成功
        model_a.invoke.reset_mock(side_effect=True)
        model_a.invoke.return_value = MagicMock(content="recovered")

        # 这次调用应该试探 model_a（HALF_OPEN），成功后 CB 转 CLOSED
        result = invoker.invoke("test")
        self.assertEqual(result.content, "recovered")
        self.assertEqual(invoker.get_breaker(0).state, CircuitState.CLOSED)


# ============================================================================
# ResilientInvoker 异步接口测试
# ============================================================================


class ResilientInvokerAsyncTestCase(unittest.TestCase):
    """ResilientInvoker 异步接口测试"""

    @patch("Django_xm.apps.agent_hub.services.resilient_invoker.asyncio.sleep")
    def test_async_retry_3_times_then_success(self, mock_sleep):
        """异步接口重试 3 次后成功"""
        model = make_mock_model(
            name="model-a",
            ainvoke_side_effects=[
                MockRateLimitError(),
                MockRateLimitError(),
                MagicMock(content="async success"),
            ],
        )

        invoker = ResilientInvoker(models=[model], config=make_fast_config())
        result = asyncio.run(invoker.ainvoke("test input"))

        self.assertEqual(result.content, "async success")
        self.assertEqual(model.ainvoke.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("Django_xm.apps.agent_hub.services.resilient_invoker.asyncio.sleep")
    def test_async_retry_then_switch_model(self, mock_sleep):
        """异步接口重试失败后切换模型"""
        model_a = make_mock_model(
            name="model-a",
            ainvoke_side_effects=[
                MockRateLimitError(),
                MockRateLimitError(),
                MockRateLimitError(),
            ],
        )
        model_b = make_mock_model(
            name="model-b",
            ainvoke_side_effects=[MagicMock(content="async from b")],
        )

        invoker = ResilientInvoker(
            models=[model_a, model_b], config=make_fast_config()
        )
        result = asyncio.run(invoker.ainvoke("test input"))

        self.assertEqual(result.content, "async from b")
        self.assertEqual(model_a.ainvoke.call_count, 3)
        self.assertEqual(model_b.ainvoke.call_count, 1)

    @patch("Django_xm.apps.agent_hub.services.resilient_invoker.asyncio.sleep")
    def test_async_permanent_error_switch_immediately(self, mock_sleep):
        """异步接口永久性错误直接切换"""
        model_a = make_mock_model(
            name="model-a",
            ainvoke_side_effects=[MockAuthError()],
        )
        model_b = make_mock_model(
            name="model-b",
            ainvoke_side_effects=[MagicMock(content="async from b")],
        )

        invoker = ResilientInvoker(
            models=[model_a, model_b], config=make_fast_config()
        )
        result = asyncio.run(invoker.ainvoke("test input"))

        self.assertEqual(result.content, "async from b")
        self.assertEqual(model_a.ainvoke.call_count, 1)
        self.assertEqual(mock_sleep.call_count, 0)

    @patch("Django_xm.apps.agent_hub.services.resilient_invoker.asyncio.sleep")
    def test_async_input_error_raise_directly(self, mock_sleep):
        """异步接口输入错误直接抛出"""
        model_a = make_mock_model(
            name="model-a",
            ainvoke_side_effects=[
                GuardrailsValidationError("async validation failed")
            ],
        )
        model_b = make_mock_model(name="model-b")

        invoker = ResilientInvoker(
            models=[model_a, model_b], config=make_fast_config()
        )

        with self.assertRaises(GuardrailsValidationError):
            asyncio.run(invoker.ainvoke("test input"))

        self.assertEqual(model_a.ainvoke.call_count, 1)
        self.assertEqual(model_b.ainvoke.call_count, 0)

    @patch("Django_xm.apps.agent_hub.services.resilient_invoker.asyncio.sleep")
    def test_async_all_models_fail(self, mock_sleep):
        """异步接口所有模型都失败"""
        model_a = make_mock_model(
            name="model-a",
            ainvoke_side_effects=[
                MockRateLimitError(),
                MockRateLimitError(),
                MockRateLimitError(),
            ],
        )
        model_b = make_mock_model(
            name="model-b",
            ainvoke_side_effects=[
                MockTemporaryError(),
                MockTemporaryError(),
                MockTemporaryError(),
            ],
        )

        invoker = ResilientInvoker(
            models=[model_a, model_b], config=make_fast_config()
        )

        with self.assertRaises(RuntimeError):
            asyncio.run(invoker.ainvoke("test input"))

    @patch("Django_xm.apps.agent_hub.services.resilient_invoker.asyncio.sleep")
    def test_async_agenerate_with_fallback(self, mock_sleep):
        """异步 agenerate 接口的降级"""
        model_a = make_mock_model(
            name="model-a",
            agenerate_side_effects=[
                MockRateLimitError(),
                MockRateLimitError(),
                MockRateLimitError(),
            ],
        )

        async def _b_agenerate(*args, **kwargs):
            return MagicMock(generations=[MagicMock(text="b")])

        model_b = MagicMock(name="model-b")
        model_b._llm_type = "model-b"
        model_b._provider_id = "mock"
        model_b._agenerate = MagicMock(side_effect=_b_agenerate)

        invoker = ResilientInvoker(
            models=[model_a, model_b], config=make_fast_config()
        )
        result = asyncio.run(invoker.agenerate([]))

        self.assertEqual(model_a._agenerate.call_count, 3)
        self.assertEqual(model_b._agenerate.call_count, 1)
        self.assertIsNotNone(result)

    @patch("Django_xm.apps.agent_hub.services.resilient_invoker.asyncio.sleep")
    def test_async_astream_with_fallback(self, mock_sleep):
        """异步 astream 接口的降级"""

        async def _a_fail(*args, **kwargs):
            raise MockRateLimitError()
            yield

        model_a = MagicMock(name="model-a")
        model_a._llm_type = "model-a"
        model_a._provider_id = "mock"
        model_a.astream = MagicMock(side_effect=_a_fail)

        async def _b_stream(*args, **kwargs):
            yield "chunk-b-1"
            yield "chunk-b-2"

        model_b = MagicMock(name="model-b")
        model_b._llm_type = "model-b"
        model_b._provider_id = "mock"
        model_b.astream = MagicMock(side_effect=_b_stream)

        invoker = ResilientInvoker(
            models=[model_a, model_b], config=make_fast_config()
        )

        async def _collect():
            chunks = []
            async for chunk in invoker.astream("test"):
                chunks.append(chunk)
            return chunks

        chunks = asyncio.run(_collect())
        self.assertEqual(chunks, ["chunk-b-1", "chunk-b-2"])
        self.assertEqual(model_a.astream.call_count, 3)
        self.assertEqual(model_b.astream.call_count, 1)


# ============================================================================
# ResilientModel 测试
# ============================================================================


class ResilientModelTestCase(unittest.TestCase):
    """ResilientModel 包装类测试"""

    @patch("Django_xm.apps.agent_hub.services.resilient_invoker.time.sleep")
    def test_model_invoke_delegates_to_invoker(self, mock_sleep):
        """ResilientModel.invoke 委托给 ResilientInvoker"""
        model_a = make_mock_model(
            name="model-a",
            invoke_side_effects=[MagicMock(content="result")],
        )

        resilient = ResilientModel(
            models=[model_a], config=make_fast_config()
        )
        result = resilient.invoke("test")

        self.assertEqual(result.content, "result")
        self.assertEqual(model_a.invoke.call_count, 1)

    @patch("Django_xm.apps.agent_hub.services.resilient_invoker.time.sleep")
    def test_model_invoke_with_fallback(self, mock_sleep):
        """ResilientModel.invoke 降级链工作"""
        model_a = make_mock_model(
            name="model-a",
            invoke_side_effects=[
                MockRateLimitError(),
                MockRateLimitError(),
                MockRateLimitError(),
            ],
        )
        model_b = make_mock_model(
            name="model-b",
            invoke_side_effects=[MagicMock(content="from b")],
        )

        resilient = ResilientModel(
            models=[model_a, model_b], config=make_fast_config()
        )
        result = resilient.invoke("test")

        self.assertEqual(result.content, "from b")
        self.assertEqual(model_a.invoke.call_count, 3)
        self.assertEqual(model_b.invoke.call_count, 1)

    def test_model_llm_type(self):
        """ResilientModel._llm_type 返回 'resilient'"""
        model = make_mock_model(name="test")
        resilient = ResilientModel(models=[model], config=make_fast_config())
        self.assertEqual(resilient._llm_type, "resilient")

    def test_model_identifying_params(self):
        """ResilientModel._identifying_params 包含模型与配置信息"""
        model_a = make_mock_model(name="model-a")
        model_b = make_mock_model(name="model-b")

        resilient = ResilientModel(
            models=[model_a, model_b], config=make_fast_config()
        )
        params = resilient._identifying_params

        self.assertIn("models", params)
        self.assertEqual(len(params["models"]), 2)
        self.assertIn("resilience_config", params)
        self.assertEqual(params["resilience_config"]["max_retries"], 3)

    def test_model_models_property(self):
        """ResilientModel.models 返回降级链"""
        model_a = make_mock_model(name="model-a")
        model_b = make_mock_model(name="model-b")

        resilient = ResilientModel(
            models=[model_a, model_b], config=make_fast_config()
        )
        self.assertEqual(len(resilient.models), 2)
        self.assertIs(resilient.models[0], model_a)
        self.assertIs(resilient.models[1], model_b)

    @patch("Django_xm.apps.agent_hub.services.resilient_invoker.time.sleep")
    def test_model_generate_delegates_to_invoker(self, mock_sleep):
        """ResilientModel._generate 委托给 ResilientInvoker"""
        model_a = make_mock_model(
            name="model-a",
            generate_side_effects=[MagicMock(generations=[])],
        )

        resilient = ResilientModel(
            models=[model_a], config=make_fast_config()
        )
        result = resilient._generate([])

        self.assertEqual(model_a._generate.call_count, 1)
        self.assertIsNotNone(result)

    def test_model_get_breaker_state(self):
        """ResilientModel.get_breaker_state 返回正确状态"""
        model_a = make_mock_model(name="model-a")
        resilient = ResilientModel(models=[model_a], config=make_fast_config())

        self.assertEqual(resilient.get_breaker_state(0), CircuitState.CLOSED)


# ============================================================================
# ResilienceConfig 测试
# ============================================================================


class ResilienceConfigTestCase(unittest.TestCase):
    """ResilienceConfig 配置测试"""

    def test_default_config(self):
        """默认配置值"""
        config = ResilienceConfig()
        self.assertEqual(config.max_retries, 3)
        self.assertEqual(config.backoff_seconds, (0.5, 1.0, 2.0))
        self.assertEqual(config.circuit_breaker_threshold, 3)
        self.assertEqual(config.circuit_breaker_cooldown, 30.0)

    def test_get_resilience_config_with_overrides(self):
        """get_resilience_config 支持 overrides"""
        config = get_resilience_config_with_overrides(max_retries=5)
        self.assertEqual(config.max_retries, 5)
        self.assertEqual(config.circuit_breaker_threshold, 3)  # 默认值

    def test_get_resilience_config_from_django_settings(self):
        """get_resilience_config 从 Django settings 读取"""
        with patch("django.conf.settings") as mock_settings:
            mock_settings.AGENT_MAX_RETRIES = 10
            mock_settings.MODEL_CIRCUIT_BREAKER_THRESHOLD = 5
            mock_settings.MODEL_CIRCUIT_BREAKER_COOLDOWN = 60.0
            mock_settings.MODEL_BACKOFF_SECONDS = [1.0, 2.0, 4.0]
            # 屏蔽未显式设置的 agent 层字段，使 getattr 返回默认值
            mock_settings.AGENT_INITIAL_RETRY_INTERVAL = 2.0
            mock_settings.AGENT_MAX_RETRY_INTERVAL = 30.0
            mock_settings.AGENT_RETRY_BACKOFF_FACTOR = 2.0
            mock_settings.AGENT_SOFT_TIMEOUT = None
            mock_settings.AGENT_HARD_TIMEOUT = None

            config = get_resilience_config_with_overrides()
            self.assertEqual(config.max_retries, 10)
            self.assertEqual(config.circuit_breaker_threshold, 5)
            self.assertEqual(config.circuit_breaker_cooldown, 60.0)
            self.assertEqual(config.backoff_seconds, (1.0, 2.0, 4.0))


def get_resilience_config_with_overrides(**kwargs):
    """辅助函数：包装 get_resilience_config 以便测试"""
    from Django_xm.apps.agent_hub.services.resilient_invoker import (
        get_resilience_config,
    )

    return get_resilience_config(**kwargs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
