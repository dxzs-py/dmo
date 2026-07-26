"""ResilienceRunner — 韧性包装器

包装 run_stream_loop，提供重试/降级/超时/回退能力。
两种模式（普通 / 深度思考）共用此包装器，保证韧性行为完全一致。

修复的 bug：
- 深度思考模式原无韧性重试（API 抖动直接失败）
- 深度思考模式原无超时保护（可能无限等待）
- 深度思考模式原无降级能力（无法用减少的工具重试）

行为：
- GraphRecursionError → 优雅降级（用已收集内容），不重试
- 可恢复异常 → RETRY + 退避
- 不可恢复但可降级 → DEGRADE（用减少的工具重试）
- 不可恢复 → FALLBACK（无工具纯对话）
- soft timeout → 警告一次
- hard timeout → FALLBACK
"""

import asyncio
import logging
from typing import Any, AsyncGenerator, Callable, Dict, List, Tuple

from langchain_core.messages import AIMessage
from langgraph.errors import GraphRecursionError

from Django_xm.apps.agent_hub.services.agent_resilience import (
    DegradationLevel,
    ErrorAction,
    ExecutionTimeoutManager,
    ResilienceConfig,
    calculate_backoff,
    classify_and_decide,
    get_degraded_tools,
    get_resilience_config,
)

from .context import StreamContext
from .fallback import FallbackStreamService
from .strategy import BaseStreamStrategy

logger = logging.getLogger(__name__)


class _HardTimeoutSignaled(Exception):
    """内部信号：流式循环中触发 hard timeout"""
    pass


class ResilienceRunner:
    """韧性包装器：包装 run_stream_loop 提供重试/降级/超时/回退"""

    def __init__(
        self,
        fallback_service: FallbackStreamService,
        rebuild_agent_fn: Callable[[List], Tuple[Any, Dict]],
        tools: List,
        model_instance: Any,
        data: Dict,
        usage_tracker,
        token_detail_tracker,
        resilience_config: ResilienceConfig = None,
        timeout_manager: ExecutionTimeoutManager = None,
    ):
        """
        Args:
            fallback_service: 无工具回退服务
            rebuild_agent_fn: 接收 degraded_tools，返回 (agent, config) 的回调
            tools: 当前工具列表（用于 get_degraded_tools）
            model_instance: LLM 模型实例（用于 fallback）
            data: 请求数据
            usage_tracker: Token 用量追踪器
            token_detail_tracker: Token 详情追踪器
            resilience_config: 韧性配置（None 时使用默认配置）
            timeout_manager: 超时管理器（None 时使用默认配置）
        """
        self.fallback_service = fallback_service
        self.rebuild_agent_fn = rebuild_agent_fn
        self.tools = tools
        self.model_instance = model_instance
        self.data = data
        self.usage_tracker = usage_tracker
        self.token_detail_tracker = token_detail_tracker
        self.config = resilience_config or get_resilience_config()
        self.timeout_mgr = timeout_manager or ExecutionTimeoutManager(
            soft_timeout=self.config.soft_timeout,
            hard_timeout=self.config.hard_timeout,
        )

    async def run(
        self,
        loop_fn: Callable,
        agent: Any,
        graph_input: Dict,
        config: Dict,
        ctx: StreamContext,
        strategy: BaseStreamStrategy,
        data: Dict,
    ) -> AsyncGenerator[Dict, None]:
        """带韧性的流式执行

        Args:
            loop_fn: run_stream_loop 函数
            agent: 已创建的 agent
            graph_input: graph 输入
            config: graph 配置
            ctx: 流式可变状态
            strategy: 模式策略
            data: 请求数据
        """
        self._ctx = ctx
        while ctx.retry_count <= self.config.max_retries:
            try:
                async for event in self._iter_with_timeout(
                    loop_fn, agent, graph_input, config, ctx, strategy, data,
                ):
                    yield event
                return  # 成功

            except _HardTimeoutSignaled:
                # Hard timeout → fallback
                logger.warning(
                    f"[Resilience] 执行超时 (hard): {self.timeout_mgr.elapsed:.1f}s"
                )
                yield {"type": "chunk", "content": "\n\n[系统提示] 执行时间过长，正在切换到简化模式...\n"}
                async for fb_event in self._run_fallback():
                    yield fb_event
                return

            except GraphRecursionError:
                # 优雅降级：用已收集内容，不重试
                async for event in self._handle_graph_recursion(ctx):
                    yield event
                return  # 继续后续 finalize

            except Exception as stream_err:
                action, classified = classify_and_decide(
                    stream_err, ctx.retry_count, self.config.max_retries,
                )

                if action == ErrorAction.RETRY:
                    ctx.retry_count += 1
                    backoff = calculate_backoff(ctx.retry_count, self.config)
                    logger.warning(
                        f"[Resilience] 重试 {ctx.retry_count}/{self.config.max_retries}, "
                        f"退避 {backoff:.1f}s: {classified.error_code}"
                    )
                    yield {
                        "type": "retry",
                        "data": {
                            "attempt": ctx.retry_count,
                            "max": self.config.max_retries,
                            "backoff": backoff,
                            "error_code": classified.error_code,
                        },
                    }
                    await asyncio.sleep(backoff)
                    continue

                elif action == ErrorAction.DEGRADE:
                    logger.warning(f"[Resilience] 降级: {classified.error_code}")
                    async for event in self._run_degrade(
                        loop_fn, graph_input, ctx, strategy, data,
                    ):
                        yield event
                    return

                elif action == ErrorAction.FAIL:
                    logger.error(
                        f"[Resilience] 不可恢复错误: {classified.error_code}: {classified.message}"
                    )
                    raise

                else:  # FALLBACK
                    logger.warning(f"[Resilience] 回退: {classified.error_code}")
                    async for fb_event in self._run_fallback():
                        yield fb_event
                    return

    async def _iter_with_timeout(
        self,
        loop_fn: Callable,
        agent: Any,
        graph_input: Dict,
        config: Dict,
        ctx: StreamContext,
        strategy: BaseStreamStrategy,
        data: Dict,
    ) -> AsyncGenerator[Dict, None]:
        """带超时检查的事件迭代"""
        async for event in loop_fn(agent, graph_input, config, ctx, strategy, data):
            # Hard timeout
            if self.timeout_mgr.hard_timeout and self.timeout_mgr.elapsed >= self.timeout_mgr.hard_timeout:
                raise _HardTimeoutSignaled()
            # Soft timeout（仅警告一次）
            if self.timeout_mgr.check_soft_timeout():
                logger.warning(
                    f"[Resilience] 执行超时 (soft): {self.timeout_mgr.elapsed:.1f}s"
                )
                yield {
                    "type": "timeout_warning",
                    "data": {
                        "elapsed": round(self.timeout_mgr.elapsed),
                        "limit": self.timeout_mgr.soft_timeout,
                    },
                }
            yield event

    async def _run_degrade(
        self,
        loop_fn: Callable,
        graph_input: Dict,
        ctx: StreamContext,
        strategy: BaseStreamStrategy,
        data: Dict,
    ) -> AsyncGenerator[Dict, None]:
        """降级执行：用减少的工具重建 agent 重试

        降级成功 → yield 事件后 return
        降级失败 → 落入 FALLBACK
        """
        degraded_tools = get_degraded_tools(self.tools, DegradationLevel.REDUCED_TOOLS)
        if not degraded_tools:
            logger.warning("[Resilience] 降级后无可用工具，回退到无工具纯对话模式")
            async for fb_event in self._run_fallback():
                yield fb_event
            return

        logger.info(
            f"[Resilience] 工具降级: {len(self.tools)} → {len(degraded_tools)} 个工具，"
            f"尝试用降级工具重建 Agent 重试"
        )
        yield {
            "type": "chunk",
            "content": "\n\n[系统提示] 部分工具暂时不可用，已切换到简化模式继续执行...\n",
        }

        try:
            degraded_agent, degraded_config = self.rebuild_agent_fn(degraded_tools)
            async for event in self._iter_with_timeout(
                loop_fn, degraded_agent, graph_input, degraded_config,
                ctx, strategy, data,
            ):
                yield event
            # 降级成功
            return
        except (_HardTimeoutSignaled, GraphRecursionError) as e:
            logger.warning(f"[Resilience] 降级工具重试也失败（{type(e).__name__}），回退到无工具模式")
        except Exception as degrade_err:
            logger.warning(f"[Resilience] 降级工具重试也失败: {degrade_err}，回退到无工具模式")

        # 降级失败，落入 FALLBACK
        async for fb_event in self._run_fallback():
            yield fb_event

    async def _handle_graph_recursion(self, ctx: StreamContext) -> AsyncGenerator[Dict, None]:
        """GraphRecursionError 优雅降级：用已收集的内容生成回复"""
        logger.warning(
            f"Agent达到递归上限，优雅降级: 已收集 {len(ctx.all_messages)} 条消息, "
            f"内容长度={len(ctx.current_message_content)}"
        )
        if ctx.current_message_content:
            yield {"type": "chunk", "content": ""}
        else:
            for msg in reversed(ctx.all_messages):
                if isinstance(msg, AIMessage) and msg.content:
                    ctx.current_message_content = msg.content
                    yield {"type": "chunk", "content": msg.content}
                    break
            if not ctx.current_message_content:
                yield {"type": "chunk", "content": "任务执行步骤较多，已达到单次执行上限。以上是已收集的部分结果。"}

    async def _run_fallback(self) -> AsyncGenerator[Dict, None]:
        """无工具纯对话回退"""
        self._ctx.fallback_triggered = True
        if not (self.model_instance and self.tools):
            logger.error("无法回退：缺少模型实例或工具配置")
            raise RuntimeError("Agent 模式执行失败且无法回退到无工具模式")

        logger.warning("回退到无工具纯对话模式")
        yield {"type": "chunk", "content": ""}
        try:
            async for fb_event in self.fallback_service.stream_without_tools(
                self.model_instance, self.data, self.usage_tracker, self.token_detail_tracker,
            ):
                yield fb_event
        except Exception as fallback_err:
            logger.error(
                f"无工具回退模式也失败: {type(fallback_err).__name__}: {fallback_err}"
            )
            yield {
                "type": "error",
                "content": f"模型服务暂时不可用，请稍后重试（{type(fallback_err).__name__}）",
            }
