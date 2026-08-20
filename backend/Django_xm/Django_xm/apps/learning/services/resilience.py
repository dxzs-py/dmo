"""学习工作流韧性组件

提供轻量级重试 + 超时保护，复用 ai_engine 韧性基础设施（agent_resilience）的原语。
不接入 AgentExecutor，因为学习工作流无 tools 概念，
降级（DEGRADE）和回退（FALLBACK）机制不适用——
学习工作流的"降级"等价于"重试耗尽后抛出"。

提供：
- invoke_with_resilience: 同步 invoke + 重试 + 超时
- ainvoke_with_resilience: 异步 ainvoke + 重试 + 超时
- astream_with_resilience: 异步 astream + 超时（流式不重试，避免 chunk 重复）
- stream_with_resilience: 同步 stream + 超时（流式不重试，避免 chunk 重复）
"""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Generator
from typing import Any

from Django_xm.apps.ai_engine.services.agent_resilience import (
    ErrorAction,
    ExecutionTimeoutManager,
    ResilienceConfig,
    calculate_backoff,
    classify_and_decide,
    get_resilience_config,
)

logger = logging.getLogger(__name__)


def _ensure_config(
    resilience_config: ResilienceConfig | None,
) -> ResilienceConfig:
    """获取韧性配置，None 时从 Django settings 加载默认值"""
    if resilience_config is not None:
        return resilience_config
    return get_resilience_config()


def _ensure_timeout_manager(
    timeout_manager: ExecutionTimeoutManager | None,
    cfg: ResilienceConfig,
) -> ExecutionTimeoutManager:
    """获取超时管理器，None 时基于配置新建"""
    if timeout_manager is not None:
        return timeout_manager
    return ExecutionTimeoutManager(
        soft_timeout=cfg.soft_timeout,
        hard_timeout=cfg.hard_timeout,
    )


def _check_timeouts(tm: ExecutionTimeoutManager) -> None:
    """统一超时检查

    - hard_timeout 触发：抛 TimeoutError
    - soft_timeout 触发：仅记录日志警告一次
    """
    if tm.hard_timeout is not None and tm.elapsed >= tm.hard_timeout:
        logger.error(f"[Learning Resilience] hard_timeout 触发: elapsed={tm.elapsed:.1f}s, hard={tm.hard_timeout}s")
        raise TimeoutError(f"学习工作流执行超过 hard_timeout: elapsed={tm.elapsed:.1f}s, hard={tm.hard_timeout}s")

    if tm.check_soft_timeout():
        logger.warning(
            f"[Learning Resilience] soft_timeout 触发（仅警告，不中断）: "
            f"elapsed={tm.elapsed:.1f}s, soft={tm.soft_timeout}s"
        )


def _get_graph_recursion_error():
    """安全导入 GraphRecursionError，不可用时返回 None"""
    try:
        from langgraph.errors import GraphRecursionError

        return GraphRecursionError
    except ImportError:
        return None


def invoke_with_resilience(
    graph,
    inputs: dict[str, Any],
    config: dict[str, Any] | None = None,
    resilience_config: ResilienceConfig | None = None,
    timeout_manager: ExecutionTimeoutManager | None = None,
) -> dict[str, Any]:
    """同步 invoke + 重试 + 超时

    Args:
        graph: 编译后的 LangGraph 图
        inputs: 输入状态
        config: LangGraph 调用配置
        resilience_config: 韧性配置，None 时从 settings 加载
        timeout_manager: 超时管理器，None 时基于配置新建

    Returns:
        图执行结果

    Raises:
        GraphRecursionError: 递归上限触发，不重试
        TimeoutError: hard_timeout 触发，不重试
        其他异常: 重试耗尽后抛出
    """
    cfg = _ensure_config(resilience_config)
    tm = _ensure_timeout_manager(timeout_manager, cfg)
    GraphRecursionError = _get_graph_recursion_error()

    last_error: Exception | None = None

    for attempt in range(cfg.max_retries + 1):
        # 每次尝试前检查超时
        _check_timeouts(tm)

        try:
            return graph.invoke(inputs, config)
        except Exception as error:
            # GraphRecursionError：不重试，直接 raise
            if GraphRecursionError is not None and isinstance(error, GraphRecursionError):
                logger.exception("[Learning Resilience] GraphRecursionError 触发，不重试")
                raise

            # TimeoutError：不重试，直接 raise
            if isinstance(error, TimeoutError):
                logger.exception("[Learning Resilience] TimeoutError 触发，不重试")
                raise

            last_error = error
            action, classified = classify_and_decide(error, attempt, cfg.max_retries)

            # FAIL：直接 raise
            if action == ErrorAction.FAIL:
                logger.exception(
                    f"[Learning Resilience] 不可恢复错误（FAIL）: {classified.error_code}: {classified.message}"
                )
                raise

            # RETRY / DEGRADE / FALLBACK：学习工作流无降级/回退概念，统一重试
            if attempt < cfg.max_retries:
                backoff = calculate_backoff(attempt + 1, cfg)
                logger.warning(
                    f"[Learning Resilience] 重试 {attempt + 1}/{cfg.max_retries}, "
                    f"退避 {backoff:.1f}s, 错误: {classified.error_code}: {classified.message}"
                )
                time.sleep(backoff)
                continue
            else:
                logger.exception(
                    f"[Learning Resilience] 重试耗尽（attempt={attempt}, "
                    f"max_retries={cfg.max_retries}）: {classified.error_code}: {classified.message}"
                )
                raise

    # 理论不可达，保险兜底
    if last_error is not None:
        raise last_error
    raise RuntimeError("invoke_with_resilience 未知异常：重试循环退出但无错误")


async def ainvoke_with_resilience(
    graph,
    inputs: dict[str, Any],
    config: dict[str, Any] | None = None,
    resilience_config: ResilienceConfig | None = None,
    timeout_manager: ExecutionTimeoutManager | None = None,
) -> dict[str, Any]:
    """异步 ainvoke + 重试 + 超时

    Args:
        graph: 编译后的 LangGraph 图
        inputs: 输入状态
        config: LangGraph 调用配置
        resilience_config: 韧性配置，None 时从 settings 加载
        timeout_manager: 超时管理器，None 时基于配置新建

    Returns:
        图执行结果

    Raises:
        GraphRecursionError: 递归上限触发，不重试
        TimeoutError: hard_timeout 触发，不重试
        其他异常: 重试耗尽后抛出
    """
    cfg = _ensure_config(resilience_config)
    tm = _ensure_timeout_manager(timeout_manager, cfg)
    GraphRecursionError = _get_graph_recursion_error()

    last_error: Exception | None = None

    for attempt in range(cfg.max_retries + 1):
        # 每次尝试前检查超时
        _check_timeouts(tm)

        try:
            return await graph.ainvoke(inputs, config)
        except Exception as error:
            # GraphRecursionError：不重试，直接 raise
            if GraphRecursionError is not None and isinstance(error, GraphRecursionError):
                logger.exception("[Learning Resilience] GraphRecursionError 触发，不重试")
                raise

            # TimeoutError：不重试，直接 raise
            if isinstance(error, TimeoutError):
                logger.exception("[Learning Resilience] TimeoutError 触发，不重试")
                raise

            last_error = error
            action, classified = classify_and_decide(error, attempt, cfg.max_retries)

            # FAIL：直接 raise
            if action == ErrorAction.FAIL:
                logger.exception(
                    f"[Learning Resilience] 不可恢复错误（FAIL）: {classified.error_code}: {classified.message}"
                )
                raise

            # RETRY / DEGRADE / FALLBACK：学习工作流无降级/回退概念，统一重试
            if attempt < cfg.max_retries:
                backoff = calculate_backoff(attempt + 1, cfg)
                logger.warning(
                    f"[Learning Resilience] 重试 {attempt + 1}/{cfg.max_retries}, "
                    f"退避 {backoff:.1f}s, 错误: {classified.error_code}: {classified.message}"
                )
                await asyncio.sleep(backoff)
                continue
            else:
                logger.exception(
                    f"[Learning Resilience] 重试耗尽（attempt={attempt}, "
                    f"max_retries={cfg.max_retries}）: {classified.error_code}: {classified.message}"
                )
                raise

    # 理论不可达，保险兜底
    if last_error is not None:
        raise last_error
    raise RuntimeError("ainvoke_with_resilience 未知异常：重试循环退出但无错误")


async def astream_with_resilience(
    graph,
    inputs: dict[str, Any],
    config: dict[str, Any] | None = None,
    stream_mode: str | list[str] = "values",
    resilience_config: ResilienceConfig | None = None,
    timeout_manager: ExecutionTimeoutManager | None = None,
) -> AsyncGenerator[Any, None]:
    """异步 astream + 超时（流式不重试）

    流式输出不重试，因为重试会导致 chunk 重复（已 yield 的 chunk 无法撤销）。
    仅做超时检查：
    - 每个 chunk yield 前检查 hard_timeout，超过则抛 TimeoutError
    - soft_timeout 仅记录日志警告一次

    Args:
        graph: 编译后的 LangGraph 图
        inputs: 输入状态
        config: LangGraph 调用配置
        stream_mode: 流式模式（values / updates / messages 或组合列表），默认 "values"
        resilience_config: 韧性配置，None 时从 settings 加载
        timeout_manager: 超时管理器，None 时基于配置新建

    Yields:
        图流式输出的 chunk

    Raises:
        TimeoutError: hard_timeout 触发
        其他异常: 直接 raise（不重试）
    """
    cfg = _ensure_config(resilience_config)
    tm = _ensure_timeout_manager(timeout_manager, cfg)

    async for chunk in graph.astream(inputs, config, stream_mode=stream_mode):
        # 每个 chunk yield 前检查超时
        _check_timeouts(tm)
        yield chunk


def stream_with_resilience(
    graph,
    inputs: dict[str, Any] | None,
    config: dict[str, Any] | None = None,
    stream_mode: str = "values",
    resilience_config: ResilienceConfig | None = None,
    timeout_manager: ExecutionTimeoutManager | None = None,
) -> Generator[Any, None, None]:
    """同步 stream + 超时（流式不重试）

    流式输出不重试，因为重试会导致 chunk 重复（已 yield 的 chunk 无法撤销）。
    仅做超时检查：
    - 每个 chunk yield 前检查 hard_timeout，超过则抛 TimeoutError
    - soft_timeout 仅记录日志警告一次

    Args:
        graph: 编译后的 LangGraph 图
        inputs: 输入状态；中断后恢复执行时可传 None
        config: LangGraph 调用配置
        stream_mode: 流式模式（values / updates / messages 等），默认 "values"
        resilience_config: 韧性配置，None 时从 settings 加载
        timeout_manager: 超时管理器，None 时基于配置新建

    Yields:
        图流式输出的 chunk

    Raises:
        TimeoutError: hard_timeout 触发
        其他异常: 直接 raise（不重试）
    """
    cfg = _ensure_config(resilience_config)
    tm = _ensure_timeout_manager(timeout_manager, cfg)

    for chunk in graph.stream(inputs, config, stream_mode=stream_mode):
        # 每个 chunk yield 前检查超时
        _check_timeouts(tm)
        yield chunk
