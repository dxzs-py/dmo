"""AgentExecutor — 公共 Agent 执行器

从 chat/services/stream/resilience.py 的 ResilienceRunner 提升而来，
作为全应用统一的 Agent 执行器，提供重试/降级/超时/回退能力。

通过回调注入核心循环逻辑和 agent 重建逻辑，支持多种执行模式：
- 普通 agent 模式（chat_service.py，Task 18.3 适配）
- 深度思考模式（chat_service.py，Task 18.3 适配）
- 深度研究模式（official_deep_agent.py，Task 18.4 适配）

相对原 ResilienceRunner 的改进：
- 集成 DuplicateToolCallDetector：提供 record_tool_call / inject_warning 方法，
  由 loop_fn 在检测到 TOOL_CALL_PENDING 事件时调用，避免重复调用循环。
- 集成审批 pause/resume：yield approval 事件时暂停执行计时，
  避免用户思考时间惩罚 agent（RC17 修复）。
- 修复 hard timeout 检查顺序：先检查 hard timeout 再检查 soft timeout，
  避免 hard timeout 已触发却先 yield soft timeout 事件的逻辑倒置。

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
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.errors import GraphRecursionError

from Django_xm.apps.agent_hub.services.agent_resilience import (
    DegradationLevel,
    DuplicateToolCallDetector,
    DuplicateToolCallWarning,
    ErrorAction,
    ExecutionTimeoutManager,
    ResilienceConfig,
    calculate_backoff,
    classify_and_decide,
    get_degraded_tools,
    get_resilience_config,
)

logger = logging.getLogger(__name__)


class _HardTimeoutSignaled(Exception):
    """内部信号：流式循环中触发 hard timeout"""


class AgentExecutor:
    """公共 Agent 执行器：包装 loop_fn 提供重试/降级/超时/回退

    通过 loop_fn 回调注入核心循环逻辑（如 run_stream_loop），
    通过 rebuild_agent_fn 回调注入 agent 重建逻辑（降级时使用），
    通过 strategy 注入模式差异（普通 / 深度思考）。

    集成 DuplicateToolCallDetector 和审批 pause/resume，
    保证所有执行模式共享同一套韧性行为。
    """

    def __init__(
        self,
        fallback_service: Any,
        rebuild_agent_fn: Callable[[list], tuple[Any, dict]],
        tools: list,
        model_instance: Any,
        data: dict,
        usage_tracker: Any,
        token_detail_tracker: Any,
        resilience_config: ResilienceConfig | None = None,
        timeout_manager: ExecutionTimeoutManager | None = None,
        duplicate_detector: DuplicateToolCallDetector | None = None,
        inject_warning_fn: Callable[[str], Awaitable[None]] | None = None,
    ):
        """
        Args:
            fallback_service: 无工具回退服务（须提供 stream_without_tools 方法）
            rebuild_agent_fn: 接收 degraded_tools，返回 (agent, config) 的回调
            tools: 当前工具列表（用于 get_degraded_tools）
            model_instance: LLM 模型实例（用于 fallback）
            data: 请求数据
            usage_tracker: Token 用量追踪器
            token_detail_tracker: Token 详情追踪器
            resilience_config: 韧性配置（None 时使用默认配置）
            timeout_manager: 超时管理器（None 时使用默认配置）
            duplicate_detector: 重复工具调用检测器（None 时按默认配置创建）
            inject_warning_fn: 注入警告到 agent state 的异步回调，
                签名 async fn(warning_prompt: str) -> None。
                当 duplicate_detector 触发时由 loop_fn 调用 inject_warning 注入。
                若为 None，则仅记录日志不注入。
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
        self.duplicate_detector = duplicate_detector or DuplicateToolCallDetector(
            window_seconds=self.config.duplicate_tool_call_window,
            threshold=self.config.duplicate_tool_call_threshold,
        )
        self.inject_warning_fn = inject_warning_fn
        # 内部状态：保存 ctx 引用供 _run_fallback 标记 fallback_triggered
        self._ctx: Any = None

    async def run(
        self,
        loop_fn: Callable,
        agent: Any,
        graph_input: dict,
        config: dict,
        ctx: Any,
        strategy: Any,
        data: dict,
    ) -> AsyncGenerator[dict, None]:
        """带韧性的流式执行

        Args:
            loop_fn: 核心循环函数，签名
                async fn(agent, graph_input, config, ctx, strategy, data) -> AsyncGenerator[Dict]
            agent: 已创建的 agent（含 graph 属性）
            graph_input: graph 输入
            config: graph 配置（含 callbacks、recursion_limit 等）
            ctx: 流式可变状态（StreamContext 或兼容对象）
            strategy: 模式策略（Normal / DeepThinking）
            data: 请求数据
        """
        self._ctx = ctx
        while ctx.retry_count <= self.config.max_retries:
            try:
                async for event in self._iter_with_timeout(
                    loop_fn,
                    agent,
                    graph_input,
                    config,
                    ctx,
                    strategy,
                    data,
                ):
                    yield event
                return  # 成功

            except _HardTimeoutSignaled:
                # Hard timeout → fallback
                logger.warning(f"[AgentExecutor] 执行超时 (hard): {self.timeout_mgr.elapsed:.1f}s")
                yield {
                    "type": "chunk",
                    "content": "\n\n[系统提示] 执行时间过长，正在切换到简化模式...\n",
                }
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
                    stream_err,
                    ctx.retry_count,
                    self.config.max_retries,
                )

                if action == ErrorAction.RETRY:
                    ctx.retry_count += 1
                    backoff = calculate_backoff(ctx.retry_count, self.config)
                    logger.warning(
                        f"[AgentExecutor] 重试 {ctx.retry_count}/{self.config.max_retries}, "
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
                    logger.warning(f"[AgentExecutor] 降级: {classified.error_code}")
                    async for event in self._run_degrade(
                        loop_fn,
                        graph_input,
                        ctx,
                        strategy,
                        data,
                    ):
                        yield event
                    return

                elif action == ErrorAction.FAIL:
                    logger.exception(f"[AgentExecutor] 不可恢复错误: {classified.error_code}: {classified.message}")
                    raise

                else:  # FALLBACK
                    logger.warning(f"[AgentExecutor] 回退: {classified.error_code}")
                    async for fb_event in self._run_fallback():
                        yield fb_event
                    return

    async def _iter_with_timeout(
        self,
        loop_fn: Callable,
        agent: Any,
        graph_input: dict,
        config: dict,
        ctx: Any,
        strategy: Any,
        data: dict,
    ) -> AsyncGenerator[dict, None]:
        """带超时检查和审批 pause/resume 的事件迭代

        - hard timeout → 抛出 _HardTimeoutSignaled
        - soft timeout → 警告一次
        - approval 事件 → yield 前 pause()，yield 后 resume()
          （避免用户审批等待时间计入 elapsed，RC17 修复）

        注意：hard timeout 检查在 soft timeout 之前，避免 hard timeout 已触发
        却先 yield soft timeout 事件的逻辑倒置。
        """
        async for event in loop_fn(agent, graph_input, config, ctx, strategy, data):
            # Hard timeout 检查（优先）
            if self.timeout_mgr.hard_timeout and self.timeout_mgr.elapsed >= self.timeout_mgr.hard_timeout:
                raise _HardTimeoutSignaled()

            # Soft timeout（仅警告一次）
            if self.timeout_mgr.check_soft_timeout():
                logger.warning(f"[AgentExecutor] 执行超时 (soft): {self.timeout_mgr.elapsed:.1f}s")
                yield {
                    "type": "timeout_warning",
                    "data": {
                        "elapsed": round(self.timeout_mgr.elapsed),
                        "limit": self.timeout_mgr.soft_timeout,
                    },
                }

            # 审批事件：暂停计时，避免用户思考时间惩罚 agent
            is_approval = isinstance(event, dict) and event.get("type") == "approval"
            if is_approval:
                self.timeout_mgr.pause()

            yield event

            # 审批处理完毕（外部继续迭代），恢复计时
            if is_approval:
                self.timeout_mgr.resume()

    async def _run_degrade(
        self,
        loop_fn: Callable,
        graph_input: dict,
        ctx: Any,
        strategy: Any,
        data: dict,
    ) -> AsyncGenerator[dict, None]:
        """降级执行：用减少的工具重建 agent 重试

        降级成功 → yield 事件后 return
        降级失败 → 落入 FALLBACK
        """
        degraded_tools = get_degraded_tools(self.tools, DegradationLevel.REDUCED_TOOLS)
        if not degraded_tools:
            logger.warning("[AgentExecutor] 降级后无可用工具，回退到无工具纯对话模式")
            async for fb_event in self._run_fallback():
                yield fb_event
            return

        logger.info(
            f"[AgentExecutor] 工具降级: {len(self.tools)} → {len(degraded_tools)} 个工具，尝试用降级工具重建 Agent 重试"
        )
        yield {
            "type": "chunk",
            "content": "\n\n[系统提示] 部分工具暂时不可用，已切换到简化模式继续执行...\n",
        }

        try:
            degraded_agent, degraded_config = self.rebuild_agent_fn(degraded_tools)
            # 重置重复调用检测器，避免降级后的新 agent 受历史记录影响
            self.duplicate_detector.reset()
            async for event in self._iter_with_timeout(
                loop_fn,
                degraded_agent,
                graph_input,
                degraded_config,
                ctx,
                strategy,
                data,
            ):
                yield event
            # 降级成功
            return
        except (_HardTimeoutSignaled, GraphRecursionError) as e:
            logger.warning(f"[AgentExecutor] 降级工具重试也失败（{type(e).__name__}），回退到无工具模式")
        except Exception as degrade_err:
            logger.warning(f"[AgentExecutor] 降级工具重试也失败: {degrade_err}，回退到无工具模式")

        # 降级失败，落入 FALLBACK
        async for fb_event in self._run_fallback():
            yield fb_event

    async def _handle_graph_recursion(self, ctx: Any) -> AsyncGenerator[dict, None]:
        """GraphRecursionError 优雅降级：用已收集的内容生成回复"""
        logger.warning(
            f"[AgentExecutor] Agent达到递归上限，优雅降级: 已收集 {len(ctx.all_messages)} 条消息, "
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
                yield {
                    "type": "chunk",
                    "content": "任务执行步骤较多，已达到单次执行上限。以上是已收集的部分结果。",
                }

    async def _run_fallback(self) -> AsyncGenerator[dict, None]:
        """无工具纯对话回退"""
        self._ctx.fallback_triggered = True
        if not (self.model_instance and self.tools):
            logger.error("无法回退：缺少模型实例或工具配置")
            raise RuntimeError("Agent 模式执行失败且无法回退到无工具模式")

        logger.warning("回退到无工具纯对话模式")
        yield {"type": "chunk", "content": ""}
        try:
            async for fb_event in self.fallback_service.stream_without_tools(
                self.model_instance,
                self.data,
                self.usage_tracker,
                self.token_detail_tracker,
            ):
                yield fb_event
        except Exception as fallback_err:
            logger.exception(f"无工具回退模式也失败: {type(fallback_err).__name__}")
            yield {
                "type": "error",
                "content": f"模型服务暂时不可用，请稍后重试（{type(fallback_err).__name__}）",
            }

    # ========================================================================
    # DuplicateToolCallDetector 集成
    # ========================================================================

    def record_tool_call(
        self,
        tool_name: str,
        parameters: Any,
    ) -> DuplicateToolCallWarning | None:
        """记录工具调用，返回警告对象（若触发阈值）或 None

        供 loop_fn 在检测到 TOOL_CALL_PENDING 事件时调用。
        检测到重复调用时，应调用 inject_warning 注入提示到 agent state，
        并中断当前 astream 以让注入生效。

        Args:
            tool_name: 工具名称
            parameters: 工具调用参数（通常为 dict）

        Returns:
            DuplicateToolCallWarning 当窗口内相同调用次数首次超过阈值时返回；
            否则返回 None（包括已警告过的 key，直到窗口过期后重置）。
        """
        return self.duplicate_detector.record(tool_name, parameters)

    async def inject_warning(self, prompt: str) -> None:
        """注入重复工具调用警告到 agent state

        供 loop_fn 在检测到 DuplicateToolCallWarning 时调用。
        通过 inject_warning_fn 回调实现具体注入逻辑
        （如 await graph.aupdate_state(config, {"messages": [SystemMessage(content=prompt)]})）。

        若未提供 inject_warning_fn，则仅记录日志不注入。

        Args:
            prompt: 警告提示文本（通常是 DuplicateToolCallWarning.to_prompt() 的返回值）
        """
        if self.inject_warning_fn is not None:
            try:
                await self.inject_warning_fn(prompt)
            except Exception as e:
                logger.warning(f"[AgentExecutor] 注入重复调用警告失败: {e}")
        else:
            logger.warning(f"[AgentExecutor] 检测到重复工具调用，但未提供 inject_warning_fn: {prompt[:100]}")

    def reset_duplicate_detector(self) -> None:
        """重置重复工具调用检测器

        在 agent 重试或降级重建后调用，避免历史记录影响新 agent 的检测。
        """
        self.duplicate_detector.reset()
