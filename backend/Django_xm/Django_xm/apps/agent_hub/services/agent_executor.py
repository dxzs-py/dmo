"""AgentExecutor — 公共 Agent 执行器

从 chat/services/stream/resilience.py 的 ResilienceRunner 提升而来，
作为全应用统一的 Agent 执行器，提供重试/降级/超时/回退能力。

通过回调注入核心循环逻辑和 agent 重建逻辑，支持多种执行模式：
- 普通 agent 模式（chat_service.py，Task 18.3 适配）
- 深度思考模式（chat_service.py，Task 18.3 适配）
- 深度研究模式（adapter.py，Task 18.4 适配）

相对原 ResilienceRunner 的改进：
- 集成 DuplicateToolCallDetector：提供 record_tool_call / inject_warning 方法，
  由 loop_fn 在检测到 TOOL_CALL_PENDING 事件时调用，避免重复调用循环。
- 集成审批 pause/resume：yield approval 事件时暂停执行计时，
  避免用户思考时间惩罚 agent（RC17 修复）。
- 修复 hard timeout 检查顺序：先检查 hard timeout 再检查 soft timeout，
  避免 hard timeout 已触发却先 yield soft timeout 事件的逻辑倒置。

行为：
- GraphRecursionError → 优雅降级（输出"未完成"结构化说明 + 已执行步骤摘要，不伪装成答案），不重试
- 可恢复异常 → RETRY + 退避
- 不可恢复但可降级 → DEGRADE（用减少的工具重试）
- 不可恢复 → FALLBACK（无工具纯对话）
- soft timeout → 警告一次
- hard timeout → FALLBACK

层次契约（统一降级链路）：
- 模型级韧性（SDK 重试、候选切换、熔断）唯一归属 LazyFallbackChatModel
  （ai_engine.services.llm_fallback）；本执行器仅负责 agent 级语义
  （执行循环重试、工具降级、无工具纯对话回退），不持有任何模型切换逻辑、
  不创建裸模型。model_instance 必须是经 get_chat_model(enable_fallback=True)
  产出的包装实例，_run_fallback 复用该实例即自动继承降级能力
  （主模型已熔断时直接使用候选模型）。
"""

import asyncio
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from Django_xm.apps.ai_engine.services.agent_resilience import (
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
from Django_xm.common.execution_loop import HardTimeoutSignaled as _HardTimeoutSignaled
from Django_xm.common.execution_loop import iter_stream_events_with_timeout

logger = logging.getLogger(__name__)


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
                    # 通用基础能力（root cause）：不可恢复错误不直接终止任务，
                    # 回退到无工具纯对话让 agent 换方法继续（与 RETRY 耗尽后
                    # FALLBACK 同路径）。仅明确的基础设施错误（认证/密钥配置，
                    # LLM 无法修复，重试必然再次失败）才真正终止。
                    _fail_details = getattr(classified, "details", {}) or {}
                    if _fail_details.get("auth_error") or classified.error_code in (
                        "AUTH_ERROR",
                        "INVALID_API_KEY",
                        "INVALID_REQUEST",
                    ):
                        logger.exception(
                            f"[AgentExecutor] 基础设施错误，无法由 agent 调整恢复: "
                            f"{classified.error_code}: {classified.message}"
                        )
                        raise
                    logger.warning(
                        f"[AgentExecutor] 不可恢复错误转回退（不终止任务，agent 换方法继续）: "
                        f"{classified.error_code}: {classified.message}"
                    )
                    async for fb_event in self._run_fallback():
                        yield fb_event
                    return

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
        """带超时检查和审批 pause/resume 的事件迭代（公共骨架薄委托）。

        实现下沉 ``common.execution_loop.iter_stream_events_with_timeout``
        （spec 任务 4.3 环 3：research 执行器与 chat 执行器共用同一骨架实现）；
        方法保留在类上维持签名与测试 patch 锚点稳定。
        """
        async for event in iter_stream_events_with_timeout(
            loop_fn,
            agent,
            graph_input,
            config,
            ctx,
            strategy,
            data,
            timeout_mgr=self.timeout_mgr,
        ):
            yield event

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
        """GraphRecursionError 优雅降级：输出结构化"未完成"说明（round3 2.2）。

        不再将历史 AIMessage.content 反向取用伪装成最终答案（中间轮的正文
        可能是步骤性文本，冒充答案会误导用户与下游落库）；改为输出明确的
        "任务达到递归上限未完成"结构化信息，附已执行步骤摘要。
        """
        logger.warning(
            f"[AgentExecutor] Agent达到递归上限，优雅降级: 已收集 {len(ctx.all_messages)} 条消息, "
            f"内容长度={len(ctx.current_message_content)}"
        )
        if ctx.current_message_content:
            # 本轮正文已流式输出：不重复输出正文，仅追加未完成说明（不伪装成答案）
            yield {
                "type": "chunk",
                "content": "\n\n[系统提示] 任务执行步骤较多，已达到单次执行上限，任务尚未完成。"
                "以上为已生成的部分内容。\n",
            }
            return

        steps_summary = self._summarize_executed_steps(ctx.all_messages)
        content = (
            "[系统提示] 任务执行步骤较多，已达到单次执行上限，任务未完成。\n"
            "以下是本次执行已完成的步骤概要，可直接据此继续（拆分任务重试）或向用户说明当前进展与剩余工作。\n"
        )
        if steps_summary:
            content += f"\n{steps_summary}\n"
        yield {"type": "chunk", "content": content}

    @staticmethod
    def _summarize_executed_steps(all_messages: list) -> str:
        """提取已执行步骤摘要：工具调用序列（按工具名聚合次数）+ 消息轮数。

        Args:
            all_messages: StreamContext 累积的消息列表（LangChain 消息对象）。

        Returns:
            多行文本摘要；无工具调用时返回空串。
        """
        tool_call_counts: dict[str, int] = {}
        tool_result_count = 0
        ai_rounds = 0
        for msg in all_messages or []:
            if isinstance(msg, AIMessage):
                ai_rounds += 1
                for tc in getattr(msg, "tool_calls", None) or []:
                    name = tc.get("name", "") if isinstance(tc, dict) else ""
                    if name:
                        tool_call_counts[name] = tool_call_counts.get(name, 0) + 1
            elif isinstance(msg, ToolMessage):
                tool_result_count += 1

        if not tool_call_counts:
            return ""

        lines = [f"- 已执行 {ai_rounds} 轮模型调用、{tool_result_count} 次工具结果返回："]
        for name, count in tool_call_counts.items():
            lines.append(f"  · {name}（{count} 次）")
        return "\n".join(lines)

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
            # A1.4：固定可行动文案（异常类名/原文仅进上方日志），F1-1 修复后
            # 该 error 事件经 WS 广播桥直达用户
            yield {
                "type": "error",
                "content": "模型服务暂时不可用，请稍后重试或切换模型",
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
