"""
resilient_invoker - 降级链与重试管理

本模块原位于 ai_engine/services/resilient_invoker.py，已迁移至当前位置。
管理降级链中的多个模型，对每个模型执行重试逻辑，
重试耗尽后切换下一个模型，所有模型都失败时抛出 RuntimeError。
"""

import asyncio
import logging
import threading
import time
from collections.abc import AsyncIterator, Iterator
from enum import Enum
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel, ChatResult
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from pydantic import ConfigDict, Field, PrivateAttr

from Django_xm.apps.ai_engine.services.exceptions import (
    LCAgentException,
    classify_exception,
)

from .agent_resilience import (
    ResilienceConfig,
    get_resilience_config,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Circuit Breaker
# ============================================================================


class CircuitState(Enum):
    """Circuit Breaker 状态"""

    CLOSED = "closed"  # 正常调用
    OPEN = "open"  # 熔断，拒绝调用
    HALF_OPEN = "half_open"  # 半开，允许试探一次


class CircuitBreaker:
    """线程安全的 Circuit Breaker

    状态转换：
        CLOSED --(连续失败达 threshold)--> OPEN
        OPEN --(冷却时间到)--> HALF_OPEN
        HALF_OPEN --(试探成功)--> CLOSED
        HALF_OPEN --(试探失败)--> OPEN

    Attributes:
        threshold: 连续失败触发 OPEN 的阈值
        cooldown: OPEN 状态冷却时间（秒）
    """

    def __init__(self, threshold: int = 3, cooldown: float = 30.0) -> None:
        self.threshold = threshold
        self.cooldown = cooldown
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        """当前状态（线程安全读取，可能触发 OPEN -> HALF_OPEN 转换）"""
        with self._lock:
            self._maybe_half_open_locked()
            return self._state

    @property
    def consecutive_failures(self) -> int:
        """当前连续失败次数"""
        with self._lock:
            return self._consecutive_failures

    def is_available(self) -> bool:
        """检查是否可调用

        CLOSED/HALF_OPEN 可调用，OPEN 不可用。

        Returns:
            True 表示可以发起调用
        """
        with self._lock:
            self._maybe_half_open_locked()
            return self._state in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def record_success(self) -> None:
        """记录成功：重置失败计数，状态转 CLOSED"""
        with self._lock:
            prev = self._state
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._opened_at = 0.0
            if prev != CircuitState.CLOSED:
                logger.info(f"CircuitBreaker: {prev.value} -> CLOSED（成功恢复）")

    def record_failure(self) -> None:
        """记录失败：失败计数+1，达到阈值转 OPEN"""
        with self._lock:
            self._consecutive_failures += 1
            if self._state == CircuitState.HALF_OPEN:
                # HALF_OPEN 状态下任何失败都立即转 OPEN
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                logger.info(
                    f"CircuitBreaker: HALF_OPEN -> OPEN（试探失败，冷却 {self.cooldown}s）"
                )
                return
            if self._consecutive_failures >= self.threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                logger.info(
                    f"CircuitBreaker: CLOSED -> OPEN（连续失败 {self._consecutive_failures} 次，"
                    f"冷却 {self.cooldown}s）"
                )

    def reset(self) -> None:
        """重置状态为 CLOSED"""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._opened_at = 0.0

    def _maybe_half_open_locked(self) -> None:
        """在持有锁的情况下检查 OPEN 是否需要转为 HALF_OPEN"""
        if self._state == CircuitState.OPEN and self._opened_at > 0.0:
            if time.monotonic() - self._opened_at >= self.cooldown:
                self._state = CircuitState.HALF_OPEN
                logger.info("CircuitBreaker: OPEN -> HALF_OPEN（冷却完成，允许试探）")


# ============================================================================
# 错误分类
# ============================================================================


# 错误动作枚举
_ERROR_ACTION_PERMANENT = "permanent"  # 永久性错误：不重试，切换模型
_ERROR_ACTION_INPUT = "input"  # 输入错误：不降级，直接抛出
_ERROR_ACTION_TEMPORARY = "temporary"  # 临时性错误：正常重试


def _classify_error(error: Exception) -> tuple[str, LCAgentException]:
    """分类错误，返回 (action, classified_exception)

    Args:
        error: 原始异常

    Returns:
        (action, classified) 元组：
        - action: "permanent" / "input" / "temporary"
        - classified: 分类后的 LCAgentException
    """
    try:
        classified = classify_exception(error)
    except Exception:
        # classify_exception 自身异常时，保守当作临时性错误重试
        classified = LCAgentException(
            message=f"分类失败: {error}",
            error_code="CLASSIFY_ERROR",
            recoverable=True,
        )

    # ModelCallError.__init__ 的签名 (message, model_name="", **kwargs) 会将
    # 调用方传入的 details={...} 作为 kwarg 嵌套进 details["details"]，
    # 因此需要同时检查外层和嵌套层的标志位。
    nested_details = classified.details.get("details", {})
    if not isinstance(nested_details, dict):
        nested_details = {}

    # 输入错误：不降级，直接抛出
    if classified.error_code == "GUARDRAILS_VALIDATION_ERROR":
        return (_ERROR_ACTION_INPUT, classified)
    if classified.details.get("bad_request") or nested_details.get("bad_request"):
        return (_ERROR_ACTION_INPUT, classified)

    # 永久性错误：不可恢复，切换模型
    # 注意：ModelCallError 的 recoverable 在 exceptions.py 中硬编码为 True，
    # 认证错误通过 details["auth_error"]=True 标识，需要额外检查（含嵌套层）
    if not classified.recoverable:
        return (_ERROR_ACTION_PERMANENT, classified)
    if classified.details.get("auth_error") or nested_details.get("auth_error"):
        return (_ERROR_ACTION_PERMANENT, classified)

    # 临时性错误：可恢复，重试
    return (_ERROR_ACTION_TEMPORARY, classified)


# ============================================================================
# ResilientInvoker
# ============================================================================


class ResilientInvoker:
    """模型调用层重试 + 降级执行器

    管理降级链中的多个模型，对每个模型执行重试逻辑，
    重试耗尽后切换下一个模型，所有模型都失败时抛出 RuntimeError。

    Attributes:
        _models: 降级链中的模型列表（按优先级排序）
        _config: 韧性配置
        _breakers: 每个模型对应的 CircuitBreaker 实例
    """

    def __init__(
        self,
        models: list[BaseChatModel],
        config: ResilienceConfig | None = None,
    ) -> None:
        if not models:
            raise ValueError("ResilientInvoker 至少需要一个模型")
        self._models: list[BaseChatModel] = list(models)
        self._config: ResilienceConfig = config or get_resilience_config()
        # 为每个模型维护独立的 CircuitBreaker
        self._breakers: dict[int, CircuitBreaker] = {
            i: CircuitBreaker(
                threshold=self._config.circuit_breaker_threshold,
                cooldown=self._config.circuit_breaker_cooldown,
            )
            for i in range(len(self._models))
        }

    @property
    def models(self) -> list[BaseChatModel]:
        """降级链中的模型列表（只读副本）"""
        return list(self._models)

    @property
    def config(self) -> ResilienceConfig:
        """韧性配置"""
        return self._config

    def get_breaker(self, index: int) -> CircuitBreaker:
        """获取指定模型的 CircuitBreaker（供调试使用）"""
        return self._breakers[index]

    # ----- 工具方法 -----

    @staticmethod
    def _get_model_label(model: BaseChatModel) -> str:
        """获取模型的可读标识（用于日志）"""
        try:
            provider = getattr(model, "_provider_id", None) or "unknown"
            llm_type = getattr(model, "_llm_type", type(model).__name__)
            return f"{provider}/{llm_type}"
        except Exception:
            return type(model).__name__

    def _get_backoff(self, attempt: int) -> float:
        """获取第 attempt 次重试的退避时间（1-based）

        Args:
            attempt: 重试次数（1-based）

        Returns:
            退避秒数
        """
        backoff = self._config.backoff_seconds
        if not backoff:
            return 0.0
        idx = min(attempt - 1, len(backoff) - 1)
        return backoff[idx]

    def _max_attempts_for(self, breaker: CircuitBreaker) -> int:
        """根据 CircuitBreaker 状态决定最大尝试次数

        HALF_OPEN 状态下只试探一次。

        Args:
            breaker: 模型对应的 CircuitBreaker

        Returns:
            最大尝试次数
        """
        if breaker.state == CircuitState.HALF_OPEN:
            return 1
        return self._config.max_retries

    # ----- 同步接口 -----

    def invoke(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        *,
        method: str = "invoke",
        **kwargs: Any,
    ) -> Any:
        """同步调用，带重试 + 降级

        Args:
            input: 输入（字符串、消息列表等）
            config: Runnable 配置
            method: 调用模型的方法名（默认 'invoke'）
            **kwargs: 透传给模型方法的参数

        Returns:
            模型返回结果

        Raises:
            RuntimeError: 所有模型都失败
            LCAgentException: 输入错误时直接抛出
        """
        errors: list[str] = []

        for idx, model in enumerate(self._models):
            breaker = self._breakers[idx]
            if not breaker.is_available():
                logger.debug(
                    f"模型 {self._get_model_label(model)} CircuitBreaker OPEN，跳过"
                )
                errors.append(f"{self._get_model_label(model)}: CB OPEN")
                continue

            max_attempts = self._max_attempts_for(breaker)
            last_error: Exception | None = None

            for attempt in range(1, max_attempts + 1):
                try:
                    method_fn = getattr(model, method)
                    result = method_fn(input, config=config, **kwargs)
                    breaker.record_success()
                    if attempt > 1:
                        logger.info(
                            f"模型 {self._get_model_label(model)} 第 {attempt} 次重试成功"
                        )
                    return result
                except Exception as e:
                    last_error = e
                    action, classified = _classify_error(e)

                    if action == _ERROR_ACTION_INPUT:
                        # 输入错误：不降级，直接抛出
                        logger.warning(
                            f"模型 {self._get_model_label(model)} 遇到输入错误，不降级: "
                            f"{classified.error_code}"
                        )
                        raise

                    if action == _ERROR_ACTION_PERMANENT:
                        # 永久性错误：不重试，记录失败，切换
                        breaker.record_failure()
                        logger.warning(
                            f"模型 {self._get_model_label(model)} 遇到永久性错误，切换: "
                            f"{classified.error_code}"
                        )
                        break  # 退出重试循环，切换下一个模型

                    # 临时性错误：记录失败，重试
                    breaker.record_failure()
                    if attempt < max_attempts:
                        backoff = self._get_backoff(attempt)
                        logger.warning(
                            f"模型 {self._get_model_label(model)} 第 {attempt}/{max_attempts} "
                            f"次失败，退避 {backoff}s: {classified.error_code}"
                        )
                        time.sleep(backoff)
                    else:
                        logger.warning(
                            f"模型 {self._get_model_label(model)} 重试 {max_attempts} "
                            f"次后仍失败: {classified.error_code}"
                        )

            if last_error is not None:
                errors.append(
                    f"{self._get_model_label(model)}: "
                    f"{type(last_error).__name__}: {last_error}"
                )

        raise RuntimeError(
            "所有模型调用均失败。已尝试: " + "; ".join(errors)
        )

    def stream(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> Iterator[Any]:
        """同步流式调用，带重试 + 降级

        重试仅在获取第一个 chunk 之前进行；一旦开始 yield，不再重试。

        Args:
            input: 输入
            config: Runnable 配置
            **kwargs: 透传参数

        Yields:
            流式 chunk

        Raises:
            RuntimeError: 所有模型都失败
            LCAgentException: 输入错误时直接抛出
        """
        errors: list[str] = []

        for idx, model in enumerate(self._models):
            breaker = self._breakers[idx]
            if not breaker.is_available():
                logger.debug(
                    f"模型 {self._get_model_label(model)} CircuitBreaker OPEN，跳过"
                )
                errors.append(f"{self._get_model_label(model)}: CB OPEN")
                continue

            max_attempts = self._max_attempts_for(breaker)
            last_error: Exception | None = None

            for attempt in range(1, max_attempts + 1):
                try:
                    # 获取迭代器并尝试取第一个 chunk（触发实际 API 调用）
                    iterator = iter(model.stream(input, config=config, **kwargs))
                    try:
                        first_chunk = next(iterator)
                    except StopIteration:
                        # 空迭代器，视为成功
                        breaker.record_success()
                        return

                    # 成功获取第一个 chunk，后续不再重试
                    breaker.record_success()
                    if attempt > 1:
                        logger.info(
                            f"模型 {self._get_model_label(model)} 第 {attempt} 次重试成功"
                        )
                    yield first_chunk
                    yield from iterator
                    return
                except Exception as e:
                    last_error = e
                    action, classified = _classify_error(e)

                    if action == _ERROR_ACTION_INPUT:
                        logger.warning(
                            f"模型 {self._get_model_label(model)} 流式遇到输入错误，不降级: "
                            f"{classified.error_code}"
                        )
                        raise

                    if action == _ERROR_ACTION_PERMANENT:
                        breaker.record_failure()
                        logger.warning(
                            f"模型 {self._get_model_label(model)} 流式遇到永久性错误，切换: "
                            f"{classified.error_code}"
                        )
                        break

                    breaker.record_failure()
                    if attempt < max_attempts:
                        backoff = self._get_backoff(attempt)
                        logger.warning(
                            f"模型 {self._get_model_label(model)} 流式第 {attempt}/{max_attempts} "
                            f"次失败，退避 {backoff}s: {classified.error_code}"
                        )
                        time.sleep(backoff)
                    else:
                        logger.warning(
                            f"模型 {self._get_model_label(model)} 流式重试 {max_attempts} "
                            f"次后仍失败: {classified.error_code}"
                        )

            if last_error is not None:
                errors.append(
                    f"{self._get_model_label(model)}: "
                    f"{type(last_error).__name__}: {last_error}"
                )

        raise RuntimeError(
            "所有模型流式调用均失败。已尝试: " + "; ".join(errors)
        )

    def generate(
        self,
        messages: list[BaseMessage],
        config: RunnableConfig | None = None,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """同步生成，带重试 + 降级

        Args:
            messages: 消息列表
            config: Runnable 配置
            stop: 停止序列
            **kwargs: 透传给 _generate 的参数

        Returns:
            ChatResult

        Raises:
            RuntimeError: 所有模型都失败
            LCAgentException: 输入错误时直接抛出
        """
        errors: list[str] = []

        for idx, model in enumerate(self._models):
            breaker = self._breakers[idx]
            if not breaker.is_available():
                logger.debug(
                    f"模型 {self._get_model_label(model)} CircuitBreaker OPEN，跳过"
                )
                errors.append(f"{self._get_model_label(model)}: CB OPEN")
                continue

            max_attempts = self._max_attempts_for(breaker)
            last_error: Exception | None = None

            for attempt in range(1, max_attempts + 1):
                try:
                    result = model._generate(messages, stop=stop, **kwargs)
                    breaker.record_success()
                    if attempt > 1:
                        logger.info(
                            f"模型 {self._get_model_label(model)} 第 {attempt} 次重试成功"
                        )
                    return result
                except Exception as e:
                    last_error = e
                    action, classified = _classify_error(e)

                    if action == _ERROR_ACTION_INPUT:
                        logger.warning(
                            f"模型 {self._get_model_label(model)} generate 遇到输入错误，不降级: "
                            f"{classified.error_code}"
                        )
                        raise

                    if action == _ERROR_ACTION_PERMANENT:
                        breaker.record_failure()
                        logger.warning(
                            f"模型 {self._get_model_label(model)} generate 遇到永久性错误，切换: "
                            f"{classified.error_code}"
                        )
                        break

                    breaker.record_failure()
                    if attempt < max_attempts:
                        backoff = self._get_backoff(attempt)
                        logger.warning(
                            f"模型 {self._get_model_label(model)} generate 第 {attempt}/{max_attempts} "
                            f"次失败，退避 {backoff}s: {classified.error_code}"
                        )
                        time.sleep(backoff)
                    else:
                        logger.warning(
                            f"模型 {self._get_model_label(model)} generate 重试 {max_attempts} "
                            f"次后仍失败: {classified.error_code}"
                        )

            if last_error is not None:
                errors.append(
                    f"{self._get_model_label(model)}: "
                    f"{type(last_error).__name__}: {last_error}"
                )

        raise RuntimeError(
            "所有模型 generate 调用均失败。已尝试: " + "; ".join(errors)
        )

    # ----- 异步接口 -----

    async def ainvoke(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        *,
        method: str = "ainvoke",
        **kwargs: Any,
    ) -> Any:
        """异步调用，带重试 + 降级

        Args:
            input: 输入
            config: Runnable 配置
            method: 调用模型的方法名（默认 'ainvoke'）
            **kwargs: 透传参数

        Returns:
            模型返回结果

        Raises:
            RuntimeError: 所有模型都失败
            LCAgentException: 输入错误时直接抛出
        """
        errors: list[str] = []

        for idx, model in enumerate(self._models):
            breaker = self._breakers[idx]
            if not breaker.is_available():
                logger.debug(
                    f"模型 {self._get_model_label(model)} CircuitBreaker OPEN，跳过"
                )
                errors.append(f"{self._get_model_label(model)}: CB OPEN")
                continue

            max_attempts = self._max_attempts_for(breaker)
            last_error: Exception | None = None

            for attempt in range(1, max_attempts + 1):
                try:
                    method_fn = getattr(model, method)
                    result = await method_fn(input, config=config, **kwargs)
                    breaker.record_success()
                    if attempt > 1:
                        logger.info(
                            f"模型 {self._get_model_label(model)} 第 {attempt} 次异步重试成功"
                        )
                    return result
                except Exception as e:
                    last_error = e
                    action, classified = _classify_error(e)

                    if action == _ERROR_ACTION_INPUT:
                        logger.warning(
                            f"模型 {self._get_model_label(model)} 异步遇到输入错误，不降级: "
                            f"{classified.error_code}"
                        )
                        raise

                    if action == _ERROR_ACTION_PERMANENT:
                        breaker.record_failure()
                        logger.warning(
                            f"模型 {self._get_model_label(model)} 异步遇到永久性错误，切换: "
                            f"{classified.error_code}"
                        )
                        break

                    breaker.record_failure()
                    if attempt < max_attempts:
                        backoff = self._get_backoff(attempt)
                        logger.warning(
                            f"模型 {self._get_model_label(model)} 异步第 {attempt}/{max_attempts} "
                            f"次失败，退避 {backoff}s: {classified.error_code}"
                        )
                        await asyncio.sleep(backoff)
                    else:
                        logger.warning(
                            f"模型 {self._get_model_label(model)} 异步重试 {max_attempts} "
                            f"次后仍失败: {classified.error_code}"
                        )

            if last_error is not None:
                errors.append(
                    f"{self._get_model_label(model)}: "
                    f"{type(last_error).__name__}: {last_error}"
                )

        raise RuntimeError(
            "所有模型异步调用均失败。已尝试: " + "; ".join(errors)
        )

    async def astream(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """异步流式调用，带重试 + 降级

        重试仅在获取第一个 chunk 之前进行；一旦开始 yield，不再重试。

        Args:
            input: 输入
            config: Runnable 配置
            **kwargs: 透传参数

        Yields:
            流式 chunk

        Raises:
            RuntimeError: 所有模型都失败
            LCAgentException: 输入错误时直接抛出
        """
        errors: list[str] = []

        for idx, model in enumerate(self._models):
            breaker = self._breakers[idx]
            if not breaker.is_available():
                logger.debug(
                    f"模型 {self._get_model_label(model)} CircuitBreaker OPEN，跳过"
                )
                errors.append(f"{self._get_model_label(model)}: CB OPEN")
                continue

            max_attempts = self._max_attempts_for(breaker)
            last_error: Exception | None = None

            for attempt in range(1, max_attempts + 1):
                try:
                    iterator = model.astream(input, config=config, **kwargs)
                    try:
                        first_chunk = await iterator.__anext__()
                    except StopAsyncIteration:
                        breaker.record_success()
                        return

                    breaker.record_success()
                    if attempt > 1:
                        logger.info(
                            f"模型 {self._get_model_label(model)} 第 {attempt} 次异步重试成功"
                        )
                    yield first_chunk
                    async for chunk in iterator:
                        yield chunk
                    return
                except Exception as e:
                    last_error = e
                    action, classified = _classify_error(e)

                    if action == _ERROR_ACTION_INPUT:
                        logger.warning(
                            f"模型 {self._get_model_label(model)} 异步流式遇到输入错误，不降级: "
                            f"{classified.error_code}"
                        )
                        raise

                    if action == _ERROR_ACTION_PERMANENT:
                        breaker.record_failure()
                        logger.warning(
                            f"模型 {self._get_model_label(model)} 异步流式遇到永久性错误，切换: "
                            f"{classified.error_code}"
                        )
                        break

                    breaker.record_failure()
                    if attempt < max_attempts:
                        backoff = self._get_backoff(attempt)
                        logger.warning(
                            f"模型 {self._get_model_label(model)} 异步流式第 {attempt}/{max_attempts} "
                            f"次失败，退避 {backoff}s: {classified.error_code}"
                        )
                        await asyncio.sleep(backoff)
                    else:
                        logger.warning(
                            f"模型 {self._get_model_label(model)} 异步流式重试 {max_attempts} "
                            f"次后仍失败: {classified.error_code}"
                        )

            if last_error is not None:
                errors.append(
                    f"{self._get_model_label(model)}: "
                    f"{type(last_error).__name__}: {last_error}"
                )

        raise RuntimeError(
            "所有模型异步流式调用均失败。已尝试: " + "; ".join(errors)
        )

    async def agenerate(
        self,
        messages: list[BaseMessage],
        config: RunnableConfig | None = None,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """异步生成，带重试 + 降级

        Args:
            messages: 消息列表
            config: Runnable 配置
            stop: 停止序列
            **kwargs: 透传给 _agenerate 的参数

        Returns:
            ChatResult

        Raises:
            RuntimeError: 所有模型都失败
            LCAgentException: 输入错误时直接抛出
        """
        errors: list[str] = []

        for idx, model in enumerate(self._models):
            breaker = self._breakers[idx]
            if not breaker.is_available():
                logger.debug(
                    f"模型 {self._get_model_label(model)} CircuitBreaker OPEN，跳过"
                )
                errors.append(f"{self._get_model_label(model)}: CB OPEN")
                continue

            max_attempts = self._max_attempts_for(breaker)
            last_error: Exception | None = None

            for attempt in range(1, max_attempts + 1):
                try:
                    result = await model._agenerate(messages, stop=stop, **kwargs)
                    breaker.record_success()
                    if attempt > 1:
                        logger.info(
                            f"模型 {self._get_model_label(model)} 第 {attempt} 次异步重试成功"
                        )
                    return result
                except Exception as e:
                    last_error = e
                    action, classified = _classify_error(e)

                    if action == _ERROR_ACTION_INPUT:
                        logger.warning(
                            f"模型 {self._get_model_label(model)} agenerate 遇到输入错误，不降级: "
                            f"{classified.error_code}"
                        )
                        raise

                    if action == _ERROR_ACTION_PERMANENT:
                        breaker.record_failure()
                        logger.warning(
                            f"模型 {self._get_model_label(model)} agenerate 遇到永久性错误，切换: "
                            f"{classified.error_code}"
                        )
                        break

                    breaker.record_failure()
                    if attempt < max_attempts:
                        backoff = self._get_backoff(attempt)
                        logger.warning(
                            f"模型 {self._get_model_label(model)} agenerate 第 {attempt}/{max_attempts} "
                            f"次失败，退避 {backoff}s: {classified.error_code}"
                        )
                        await asyncio.sleep(backoff)
                    else:
                        logger.warning(
                            f"模型 {self._get_model_label(model)} agenerate 重试 {max_attempts} "
                            f"次后仍失败: {classified.error_code}"
                        )

            if last_error is not None:
                errors.append(
                    f"{self._get_model_label(model)}: "
                    f"{type(last_error).__name__}: {last_error}"
                )

        raise RuntimeError(
            "所有模型 agenerate 调用均失败。已尝试: " + "; ".join(errors)
        )


# ============================================================================
# ResilientModel（BaseChatModel 子类）
# ============================================================================


class ResilientModel(BaseChatModel):
    """韧性模型包装类

    将降级链封装为单一模型接口，对外提供统一的 BaseChatModel API。
    内部委托给 ResilientInvoker 执行重试和降级。

    Attributes:
        models: 降级链中的模型列表（按优先级排序）
        config: 韧性配置
    """

    # Pydantic 字段声明
    # 注：使用 List[Any] 而非 List[BaseChatModel] 以兼容 MagicMock 等测试替身，
    # 实际运行时模型应为 BaseChatModel 实例
    models: list[Any] = Field(default_factory=list)
    config: Any | None = None

    # Pydantic v2 配置：允许任意类型（BaseChatModel、ResilienceConfig 等）
    model_config = ConfigDict(arbitrary_types_allowed=True)

    # 私有属性（不参与 Pydantic 验证）
    _invoker: ResilientInvoker = PrivateAttr()

    def __init__(
        self,
        models: list[BaseChatModel],
        config: ResilienceConfig | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(models=models, config=config, **kwargs)
        self._invoker = ResilientInvoker(models=models, config=config)

    # ----- BaseChatModel 抽象方法实现 -----

    @property
    def _llm_type(self) -> str:
        """模型类型标识"""
        return "resilient"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        """用于缓存键的标识参数"""
        return {
            "models": [
                getattr(m, "_identifying_params", {"_llm_type": m._llm_type})
                for m in self._invoker.models
            ],
            "resilience_config": {
                "max_retries": self._invoker.config.max_retries,
                "circuit_breaker_threshold": self._invoker.config.circuit_breaker_threshold,
                "circuit_breaker_cooldown": self._invoker.config.circuit_breaker_cooldown,
            },
        }

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """同步生成（委托给 ResilientInvoker）"""
        return self._invoker.generate(messages, stop=stop, **kwargs)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """异步生成（委托给 ResilientInvoker）"""
        return await self._invoker.agenerate(messages, stop=stop, **kwargs)

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> Iterator[Any]:
        """同步流式（委托给 ResilientInvoker）"""
        yield from self._invoker.stream(messages, stop=stop, **kwargs)

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """异步流式（委托给 ResilientInvoker）"""
        async for chunk in self._invoker.astream(messages, stop=stop, **kwargs):
            yield chunk

    # ----- Runnable 接口覆盖（委托给 ResilientInvoker） -----

    def invoke(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> Any:
        """同步调用（委托给 ResilientInvoker）"""
        return self._invoker.invoke(input, config=config, **kwargs)

    async def ainvoke(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> Any:
        """异步调用（委托给 ResilientInvoker）"""
        return await self._invoker.ainvoke(input, config=config, **kwargs)

    def stream(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> Iterator[Any]:
        """同步流式（委托给 ResilientInvoker）"""
        yield from self._invoker.stream(input, config=config, **kwargs)

    async def astream(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """异步流式（委托给 ResilientInvoker）"""
        async for chunk in self._invoker.astream(input, config=config, **kwargs):
            yield chunk

    # ----- 代理方法 -----

    def bind_tools(
        self,
        tools: Any,
        *,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> Any:
        """绑定工具，保留降级能力

        使用 self.bind() 包装自身，使 RunnableBinding 委托回
        ResilientModel._generate，从而保留降级链的重试与切换逻辑。

        Args:
            tools: 工具列表
            tool_choice: 工具选择策略
            **kwargs: 其他参数

        Returns:
            RunnableBinding 实例
        """
        from langchain_core.utils.function_calling import convert_to_openai_tool

        formatted_tools = [convert_to_openai_tool(tool) for tool in tools]
        kwargs["tools"] = formatted_tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        return self.bind(**kwargs)

    def with_structured_output(
        self,
        schema: Any,
        **kwargs: Any,
    ) -> Any:
        """结构化输出，保留降级能力

        使用 self.bind() 包装 response_format，保留降级链。
        注意：此方式不设置输出解析器，仅传递 schema 给底层模型。

        Args:
            schema: Pydantic BaseModel 类或 JSON Schema
            **kwargs: 其他参数

        Returns:
            RunnableBinding 实例
        """
        return self.bind(response_format=schema, **kwargs)

    # ----- 调试辅助 -----

    @property
    def fallback_models(self) -> list[BaseChatModel]:
        """降级链中除第一个模型外的所有模型（供调试使用）"""
        return list(self._invoker.models[1:])

    def get_breaker_state(self, index: int) -> CircuitState:
        """获取指定位置模型的 CircuitBreaker 状态（供调试使用）"""
        return self._invoker.get_breaker(index).state
