"""run_stream_loop — 核心流式循环（单一底层模块）

chat_service 共用的唯一 astream 循环实现。
保证以下行为在普通模式和深度思考模式下完全一致：

- astream stream_mode=["messages", "updates"]
- messages 模式：ToolEventExtractor 归一化工具事件 + publish_tool_lifecycle_event 发布
  （唯一管线：与 research 共用），另调用 process_stream_chunk 处理正文/思考
- updates 模式：审批中断检测（extract_interrupt_ids + parse_approval_interrupt）
- chunk 累积到 ctx.current_message_content
- asyncio.sleep(0.01) 让出事件循环

不包含：韧性重试、超时（由 AgentExecutor 包装，位于 agent_hub/services/agent_executor.py）
不包含：审批中断收尾、finalize、建议生成（由 interrupt.py / finalizer.py 处理）
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Any

from langchain_core.messages import SystemMessage

from Django_xm.apps.approvals.services.approval_batch import assert_non_empty_decision
from Django_xm.apps.ai_engine.services.token_counter import TokenUsageCallbackHandler
from Django_xm.apps.ai_engine.services.usage_tracker import create_session_token_budget_guard
from Django_xm.apps.chat.services.stream_helpers import (
    extract_interrupt_ids,
    process_stream_chunk,
)
from Django_xm.apps.chat.utils import _lcp_len
from Django_xm.apps.tools.base import is_approval_interrupt, is_subagent_wait_interrupt
from Django_xm.apps.tools.tool_event_extractor import ToolEventExtractor
from Django_xm.common.event_schema import EventSource, EventType
from Django_xm.common.execution_loop import run_astream_loop
from Django_xm.common.realtime_events import publish_event
from Django_xm.common.tool_event_publisher import publish_tool_lifecycle_event

from .context import StreamContext
from .strategy import BaseStreamStrategy

logger = logging.getLogger(__name__)


def _record_duplicate_tool_call(
    tool_name: str,
    parameters: Any,
    duplicate_detector,
    warnings: list | None,
) -> None:
    """对新检测到的工具调用执行重复调用检测（chat 补齐韧性，spec D8）。

    与 research 侧重复检测同构：窗口内相同 tool_name + 相同 parameters 超过阈值时，
    将警告 SystemMessage 追加到 warnings，由公共循环注入 graph state。
    """
    if duplicate_detector is None or warnings is None:
        return
    try:
        warning = duplicate_detector.record(tool_name, parameters)
        if warning is not None:
            warnings.append(SystemMessage(content=warning.to_prompt()))
    except Exception as e:
        logger.warning(f"重复工具调用检测失败: tool={tool_name}, err={e}")


def _count_tool_call(tool_name: str, tool_call_count: dict[str, int] | None) -> None:
    """统计工具调用次数；单工具超过 8 次记录日志（诊断用）。"""
    if not isinstance(tool_call_count, dict):
        return
    tool_call_count[tool_name] = tool_call_count.get(tool_name, 0) + 1
    if tool_call_count[tool_name] > 8:
        logger.info(f"工具 {tool_name} 被调用了 {tool_call_count[tool_name]} 次")


# 知识库检索工具前缀（返回大量原始文档）：结果超过阈值时统一截断为摘要。
# 该策略为 chat 侧唯一实现（发布编排共享，禁止下沉共享 extractor）；
# 命中前缀与 apps/tools/base.py 的 _RAW_CONTENT_TOOL_PREFIXES 语义一致。
_KB_TOOL_PREFIX = "knowledge_base_"
# 超过该长度才截断（保留重构前 stream_chunk_processors._handle_tool_message_chunk 阈值）
_KB_RESULT_TRUNCATE_THRESHOLD = 500
# 截断后保留的预览长度（前 N 字符 + "..."）
_KB_RESULT_PREVIEW_LEN = 200


def _summarize_kb_result(tool_name: str, result: Any) -> Any:
    """知识库工具结果摘要（chat 侧唯一实现）。

    ``knowledge_base_*`` 工具返回大量原始文档，结果超过阈值时截断为
    ``前 200 字符 + "..."``，避免海量原文进入 WS 事件 payload 与落库条目
    （恢复重构前 ``_handle_tool_message_chunk`` 的摘要行为）。非知识库工具
    或短结果原样返回。
    """
    if (
        isinstance(tool_name, str)
        and tool_name.startswith(_KB_TOOL_PREFIX)
        and isinstance(result, str)
        and len(result) > _KB_RESULT_TRUNCATE_THRESHOLD
    ):
        return result[:_KB_RESULT_PREVIEW_LEN].rstrip() + "..."
    return result


def _evaluate_tool_usage_guard(
    tool_name: str,
    tool_id: str,
    parameters: dict | None,
) -> tuple[list[dict], bool]:
    """ToolUsageGuard 检查（唯一实现，从 stream_chunk_processors 迁移）。

    返回 ``(events, skip_publish)``：
    - events: 需产出的事件列表（tool_usage_dedup / tool_usage_blocked / 软警告）
    - skip_publish: True 表示命中 dedup/block 短路，应跳过该工具的生命周期发布
    """
    events: list[dict] = []
    try:
        from Django_xm.apps.ai_engine.services.tool_usage_guard import (
            ToolUsageDecision,
            ToolUsageStatus,
            get_tool_usage_guard,
        )

        if parameters:
            decision = get_tool_usage_guard().check(
                tool_name=tool_name,
                args=parameters,
                thread_id="default",  # 流式上下文不直接拿 thread_id，使用 default
            )
        else:
            decision = ToolUsageDecision(
                status=ToolUsageStatus.ALLOW, reason="params not yet available"
            )
        # 抽取通用事件字段（所有工具都有 resource_key / payload_preview）
        sse_data = decision.sse_event.get("data", {}) if decision.sse_event else {}
        common_data = {
            "tool_name": tool_name,
            "tool_id": tool_id,
            "resource_key": sse_data.get("resource_key", ""),
            "payload_preview": sse_data.get("payload_preview", ""),
            "reason": sse_data.get("reason", ""),
            "message": sse_data.get("message", ""),
        }
        if decision.status.value == "dedup" and decision.short_circuit_response:
            # 短时相同内容：直接以 DEDUP 状态传递，由 chat_service 注入 ToolMessage
            common_data["short_circuit_response"] = decision.short_circuit_response
            events.append({"type": "tool_usage_dedup", "data": common_data})
            if decision.sse_event:
                events.append(decision.sse_event)
            return events, True
        if decision.status.value == "block" and decision.short_circuit_response:
            # 渐进式阻断：返回阻断原因，仍由模型决定是否继续
            common_data["short_circuit_response"] = decision.short_circuit_response
            events.append({"type": "tool_usage_blocked", "data": common_data})
            if decision.sse_event:
                events.append(decision.sse_event)
            return events, True
        if decision.sse_event:
            # 软警告
            events.append(decision.sse_event)
    except Exception as guard_err:
        logger.debug(f"ToolUsageGuard 检查失败（不影响工具执行）: {guard_err}")
    return events, False


async def run_stream_loop(
    agent,
    graph_input: dict,
    config: dict,
    ctx: StreamContext,
    strategy: BaseStreamStrategy,
    data: dict,
    interrupt_handler=None,
    *,
    timeout_mgr=None,
    duplicate_detector=None,
) -> AsyncGenerator[dict, None]:
    """核心流式循环（SSE 模式 / 单协程挂起模式）

    基于公共执行循环骨架 ``common.execution_loop.run_astream_loop``（spec D8），
    以回调注入 chat 执行内核差异（chunk 处理 / 审批创建）。

    单协程挂起模式（interrupt_handler 非 None，执行服务使用）：
    - 外层 while 驱动 astream；
    - updates 检测到审批中断 → 创建审批 + 产出 approval 事件 → 调用
      interrupt_handler 挂起等待批次决策 → Command(resume=...) 重入 astream；
    - 审批挂起期间暂停超时计时（timeout_mgr.pause/resume），用户思考时间
      不惩罚 agent（chat 补齐韧性，spec D8）。

    SSE 模式（interrupt_handler 为 None，旧路径）：
    - 中断时产出 approval 事件后 astream 自然结束（保持原行为）。

    Args:
        agent: 已创建的 agent（含 graph 属性）
        graph_input: graph 输入（messages 列表）
        config: graph 配置（含 callbacks、recursion_limit 等）
        ctx: 流式可变状态
        strategy: 模式策略（Normal / DeepThinking）
        data: 请求数据
        interrupt_handler: 审批中断挂起回调，签名
            async fn(graph_interrupt_id, langgraph_resume_id) -> decisions dict；
            返回 {langgraph_resume_id: {tool_call_id: bool}}。为 None 时走 SSE 模式。
        timeout_mgr: 执行超时管理器（None 表示不启用超时暂停）。
        duplicate_detector: 重复工具调用检测器（None 表示不检测）。
    """
    session_id = data.get("session_id", "")
    message_id = str(data.get("_assistant_message_id") or data.get("message_id", ""))
    # 统一工具条目容器（主/子代理共享，data["_tool_entries"] 由
    # chat_service._init_subagent_stream_state 创建）；发布编排聚合落库此容器。
    tool_entries = data.get("_tool_entries")
    # 主代理 position 采集依据：content_state["content"]（已输出正文长度，
    # 与收敛前 stream_broadcast 的 position 语义一致）；缺失时回退 ctx 累积正文。
    content_state = data.get("_content_state_ref")

    # ── COST-01：会话级 Token 硬预算守卫 ──
    # 预算未配置（session_token_budget<=0）或无 session_id 时自动不启用。
    # 当前请求 token 增量来源：graph callbacks 上的 TokenUsageCallbackHandler
    # （每轮 LLM 调用结束累加）；会话累计基线由守卫首次检查时从 DB 聚合并缓存。
    budget_guard = create_session_token_budget_guard(session_id)
    usage_cb = next(
        (c for c in (config.get("callbacks") or []) if isinstance(c, TokenUsageCallbackHandler)),
        None,
    )

    # 归一化工具事件的唯一提取器（长生命周期，跨 chunk / resume 复用）
    extractor = ToolEventExtractor()

    # 策略钩子：循环开始前的事件（深度思考发送"正在深度思考中..."）
    async for event in strategy.on_loop_start(ctx, data):
        yield event

    def _main_position() -> int:
        if isinstance(content_state, dict):
            return len(content_state.get("content") or "")
        return len(ctx.current_message_content or "")

    # ── COST-01：会话级 Token 硬预算熔断 ──
    # 检查点（"每轮 LLM 调用后"的两个可观测标记）：
    #   1. 消息 chunk 携带 usage_metadata（流式下每轮 LLM 调用结束的标记）；
    #   2. TOOL_CALL_PENDING 事件（每轮工具调用前，agent 工具循环自我膨胀的刹车）。
    # 超限 → 发布 budget_exceeded 事件 + 正文追加系统提示（随 finalizer 落库）
    # → should_break 终止本轮循环（优雅结束，不抛异常，走现有 finalizer 收尾）。
    _BUDGET_EXCEEDED_NOTICE = "\n\n[系统提示] 本会话 Token 预算已耗尽，已自动停止本次生成。"

    async def _check_session_budget() -> dict[str, Any] | None:
        """检查会话 Token 硬预算，超限时完成事件发布与正文收尾。

        Returns:
            超限时的预算 info dict；未超限/未启用/检查异常返回 None（不阻塞主流程）。
        """
        if not budget_guard.enabled:
            return None
        try:
            info = budget_guard.check(usage_cb.total_tokens if usage_cb is not None else 0)
        except Exception as e:
            logger.warning(f"[Loop] 会话预算检查失败（忽略）: {e}")
            return None
        if not info.get("exceeded"):
            return None

        logger.warning(
            f"[Loop] 会话 Token 预算耗尽，终止本轮生成: session={session_id}, "
            f"total={info.get('total')}/{info.get('limit')}"
        )
        # 发布 budget_exceeded 事件（前端未监听不影响熔断；发布失败不阻断终止）
        try:
            await publish_event(
                EventType.BUDGET_EXCEEDED,
                {
                    "source": EventSource.CHAT,
                    "source_id": session_id,
                    "session_id": session_id,
                    "data": {
                        "session_id": session_id,
                        "used_tokens": info.get("total"),
                        "budget_limit": info.get("limit"),
                    },
                },
                session_id=session_id,
            )
        except Exception:
            logger.exception("[Loop] budget_exceeded 事件发布失败（熔断继续）")
        # 正文追加系统提示（finalizer 持久化该内容）
        ctx.current_message_content += _BUDGET_EXCEEDED_NOTICE
        data["_budget_exceeded"] = True
        return info

    async def _budget_stop_events(control) -> AsyncGenerator[dict, None]:
        """预算超限时产出收尾事件并置位终止信号（未超限不产出任何事件）。"""
        if await _check_session_budget() is not None:
            yield {"type": "chunk", "content": _BUDGET_EXCEEDED_NOTICE}
            control.should_break = True

    # messages 模式 chunk 处理器（公共循环注入）
    async def _process_chunk(mode_data, control, resume_values):
        message = mode_data[0] if isinstance(mode_data, tuple) and len(mode_data) == 2 else mode_data
        ctx.all_messages.append(message)

        # 1. 归一化工具事件（唯一提取器：严格 JSON / 无 id 暂存 / ToolMessage 补发）
        try:
            tool_events = extractor.feed(message)
        except Exception as e:
            logger.warning(f"工具事件提取失败: {e}")
            tool_events = []

        # 2. 事件分发：PENDING 做重复检测 / 计数 / Guard；全类型统一发布编排
        for evt in tool_events:
            event_type = evt.get("event_type")
            tool_call_id = evt.get("tool_call_id", "") or ""
            if not tool_call_id:
                continue
            tool_name = evt.get("tool_name") or "unknown"
            parameters = evt.get("parameters") or {}

            if event_type == EventType.TOOL_CALL_PENDING:
                # COST-01：每轮工具调用前检查会话 Token 硬预算（工具循环自我膨胀的服务端刹车）
                async for stop_event in _budget_stop_events(control):
                    yield stop_event
                if control.should_break:
                    return
                # 重复调用检测（注入警告到 graph state）
                _record_duplicate_tool_call(tool_name, parameters, duplicate_detector, control.warnings)
                # 调用计数（诊断）
                _count_tool_call(tool_name, ctx.tool_call_count)
                # ToolUsageGuard：命中 dedup/block 时产出事件并跳过发布
                guard_events, skip_publish = _evaluate_tool_usage_guard(tool_name, tool_call_id, parameters)
                for guard_event in guard_events:
                    replacement = strategy.handle_special_event(guard_event, ctx)
                    if replacement is None:
                        yield guard_event
                    else:
                        for replacement_event in replacement:
                            yield replacement_event
                if skip_publish:
                    continue

            # 统一发布编排：register → bind_position → transition_async → 聚合条目
            # 知识库工具结果摘要：在发布入口统一截断，同时作用于 WS 事件 payload 与
            # 落库条目（聚合容器 entries），避免海量 KB 原文进入实时通道与 DB。
            await publish_tool_lifecycle_event(
                module=EventSource.CHAT,
                module_id=session_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                event_type=event_type,
                parameters=parameters,
                result=_summarize_kb_result(tool_name, evt.get("result")),
                error=evt.get("error"),
                message_id=message_id,
                entries=tool_entries,
                position=_main_position(),
            )

        # 3. 正文流式 / 思考内容（已瘦身，不含工具事件）
        try:
            for event in process_stream_chunk(
                mode_data,
                ctx.current_message_content,
                lcp_func=_lcp_len,
                accumulated_reasoning=ctx.accumulated_reasoning,
                mode=data.get("mode", "agent"),
                enable_deep_thinking=strategy.enable_deep_thinking,
            ):
                # chunk 事件：累积内容
                if event.get("type") == "chunk":
                    ctx.current_message_content += event.get("content", "")

                # 策略钩子：处理 reasoning 等特殊事件
                replacement = strategy.handle_special_event(event, ctx)
                if replacement is not None:
                    for replacement_event in replacement:
                        yield replacement_event
                    continue

                yield event
        except Exception as chunk_err:
            logger.warning(f"处理流式 chunk 失败: {chunk_err}")
            return

        # COST-01：本轮 LLM 调用结束（流式末尾 chunk 携带 usage_metadata）后检查会话预算
        if getattr(message, "usage_metadata", None) is not None:
            async for stop_event in _budget_stop_events(control):
                yield stop_event
            if control.should_break:
                return

        await asyncio.sleep(0.01)

    # updates 模式处理器（公共循环注入）
    async def _handle_updates(mode_data, resume_values, suspend_box, control):
        if interrupt_handler is not None:
            groups = await _collect_chat_approvals(mode_data, ctx, data)
            if groups:
                # 产出 approval 事件 + 挂起等待批次决策
                for group in groups:
                    for event in group["approval_events"]:
                        yield event
                    # 审批挂起期间暂停超时计时（用户思考时间不惩罚 agent，spec D8）
                    if timeout_mgr is not None:
                        timeout_mgr.pause()
                    try:
                        decisions = await interrupt_handler(
                            group["graph_interrupt_id"],
                            group["langgraph_resume_id"],
                        )
                    finally:
                        if timeout_mgr is not None:
                            timeout_mgr.resume()
                    # 空决策（审批创建失败）对齐 research 侧语义：直接抛错终止，
                    # 避免静默 break 导致 graph 卡在中断、后续消息处理异常。
                    assert_non_empty_decision(decisions)
                    if isinstance(decisions, dict):
                        resume_values.update(decisions)
                control.should_break = True
                return
            # 业务等待挂起（wait_for_subagent，非审批 interrupt）：记录挂起
            # 信息后退出流，不产出审批 UI、不 Command(resume)——由执行器
            # （SessionExecutor）注册父 awaiter 并在子代理终态时唤醒续跑
            # （与 deep_research adapter 的 subagent_wait_suspend 同构，spec D4）。
            wait_info = _detect_subagent_wait_suspend(mode_data)
            if wait_info:
                suspend_box["suspend"] = wait_info
                logger.info(
                    f"[Loop] 业务等待挂起: subagents={wait_info['subagent_thread_ids']}, "
                    f"interrupt_id={wait_info['interrupt_id']}"
                )
            return
        # SSE 模式：产出 approval 事件后 astream 自然结束（保持原行为）
        async for event in _handle_updates_chunk(mode_data, ctx, data):
            yield event

    # 注入韧性消息到 graph state（重复调用警告；chat 当前无子代理重试指令队列）
    async def _inject_state_messages(messages):
        await agent.graph.aupdate_state(config, {"messages": messages})

    current_input = graph_input
    while True:
        resume_values: dict = {}
        suspend_box: dict = {}

        logger.debug(
            f"[Loop] astream: input keys="
            f"{list(current_input.keys()) if isinstance(current_input, dict) else type(current_input).__name__}"
        )
        async for event in run_astream_loop(
            graph=agent.graph,
            graph_input=current_input,
            config=config,
            process_chunk=_process_chunk,
            handle_updates=_handle_updates,
            resume_values=resume_values,
            suspend_box=suspend_box,
            timeout_mgr=timeout_mgr,
            check_soft_timeout=False,
            inject_state_messages=_inject_state_messages,
        ):
            yield event

        # 业务等待挂起（wait_for_subagent）：不 Command(resume)，直接退出循环——
        # 由执行器接管（注册父 awaiter，子代理终态唤醒后以 resume_command 重入）
        if suspend_box.get("suspend"):
            data["_subagent_wait_suspend"] = suspend_box["suspend"]
            break

        # 无审批中断：astream 正常结束
        if not resume_values:
            break

        # 构造 Command(resume=...) 重入 astream（key = LangGraph Interrupt.id）
        from langgraph.types import Command

        current_input = Command(resume=resume_values)
        logger.info(f"[Loop] 审批恢复重入: resume_keys={list(resume_values.keys())}")


def _detect_subagent_wait_suspend(mode_data: Any) -> dict | None:
    """检测 updates chunk 中的子代理业务等待中断（wait_for_subagent，spec D4）。

    非审批 interrupt：不产出审批 UI，仅返回挂起信息（subagent_thread_id +
    interrupt_id），供执行器注册父 awaiter 并在子代理终态时 Command(resume) 续跑。
    与 deep_research adapter 的 is_subagent_wait_interrupt 分支同构。
    """
    if not (isinstance(mode_data, dict) and "__interrupt__" in mode_data):
        return None
    interrupts = mode_data["__interrupt__"]
    if not interrupts:
        return None
    for intr in interrupts:
        interrupt_value, _graph_id, langgraph_resume_id = extract_interrupt_ids(intr)
        if is_subagent_wait_interrupt(interrupt_value):
            return {
                "subagent_thread_ids": interrupt_value.get("subagent_thread_ids", []) or [],
                "interrupt_id": langgraph_resume_id,
            }
    return None


async def _collect_chat_approvals(
    mode_data: Any,
    ctx: StreamContext,
    data: dict,
) -> list[dict]:
    """解析 updates chunk 中的审批中断，创建 Approval DB 记录，返回分组数据。

    与 deep_research 的 create_approvals_for_interrupts 同构（仅审批创建方式不同，
    chat 复用 request_approval_async）。返回按 interrupt 分组的列表，供
    _handle_updates_chunk（SSE 模式）与 run_stream_loop 挂起模式共用。

    审批解析直接委托 ``common.approval_parser.parse_approval_interrupt``
    （中断 request 已携带 LLM tool_call_id，前端按其精确匹配，不再依赖流式
    工具映射匹配 llm_tool_call_id）。

    返回元素结构：
        {
            "graph_interrupt_id": str,
            "langgraph_resume_id": str,
            "approval_events": [{"type": "approval", "data": {...}}, ...],
        }
    """
    if not (isinstance(mode_data, dict) and "__interrupt__" in mode_data):
        return []

    interrupts = mode_data["__interrupt__"]
    if not interrupts:
        return []

    from Django_xm.common.approval_parser import parse_approval_interrupt

    groups: list[dict] = []
    for intr in interrupts:
        interrupt_value, graph_interrupt_id, langgraph_resume_id = extract_interrupt_ids(intr)

        if not is_approval_interrupt(interrupt_value):
            continue

        approval_data_list = parse_approval_interrupt(
            interrupt_value,
            graph_interrupt_id,
            langgraph_resume_id,
        )

        approval_events: list[dict] = []
        for approval_data in approval_data_list:
            tool_name = approval_data.get("tool_name", "unknown")
            action = approval_data.get("action", "confirm")
            tool_call_id = approval_data.get("tool_call_id", "")
            logger.info(
                f"approval interrupt: tool={tool_name}, "
                f"action={action}, danger={approval_data.get('danger_level', 'medium')}, "
                f"interrupt_id={approval_data.get('interrupt_id')}, "
                f"graph_interrupt_id={graph_interrupt_id}, "
                f"langgraph_resume_id={langgraph_resume_id}"
            )

            if ctx.session_id:
                approval_data["session_id"] = ctx.session_id
            if ctx.message_id:
                approval_data["message_id"] = ctx.message_id

            # 持久化工具/模型配置到 approval.extra（统一出口，chat/deep_research 共用）
            from Django_xm.apps.approvals.services.approval_service import build_approval_extra
            approval_data["extra"] = build_approval_extra(
                data,
                tool_call_id=tool_call_id,
                graph_interrupt_id=graph_interrupt_id,
                langgraph_resume_id=langgraph_resume_id,
                message_id=ctx.message_id,
                base_extra=approval_data.get("extra"),
            )

            try:
                from Django_xm.apps.approvals.services.approval_service import request_approval_async
                await request_approval_async(
                    source="chat",
                    source_id=ctx.session_id or "",
                    interrupt_id=tool_call_id,
                    approval_data=approval_data,
                )
                logger.info(f"[Approval] DB记录已创建: interrupt_id={tool_call_id}, session={ctx.session_id}")
            except Exception:
                logger.exception(
                    f"[Approval] DB记录创建失败: interrupt_id={tool_call_id}, session={ctx.session_id}"
                )

            # 标记发生了审批中断（用第一个请求的信息）
            if ctx.interrupt_info is None:
                ctx.interrupt_info = {
                    "tool_name": tool_name,
                    "interrupt_id": approval_data.get("interrupt_id"),
                    "graph_interrupt_id": graph_interrupt_id,
                    "langgraph_resume_id": langgraph_resume_id,
                }
            approval_events.append({"type": "approval", "data": approval_data})

        if approval_events:
            groups.append(
                {
                    "graph_interrupt_id": graph_interrupt_id,
                    "langgraph_resume_id": langgraph_resume_id,
                    "approval_events": approval_events,
                }
            )

    return groups


async def _handle_updates_chunk(
    mode_data: Any,
    ctx: StreamContext,
    data: dict,
) -> AsyncGenerator[dict, None]:
    """处理 updates stream mode chunk（审批中断检测，SSE 模式）。

    复用 _collect_chat_approvals 创建审批 + 产出 approval 事件。
    """
    groups = await _collect_chat_approvals(mode_data, ctx, data)
    for group in groups:
        for event in group["approval_events"]:
            yield event
