"""工具调用生命周期事件的唯一发布编排函数（chat / research 共享）。

背景：研究链路（``research/services/adapter._publish_tool_event``）、chat 主链路
（``chat/services/stream_broadcast.publish_stream_event`` 的 tool 分支）、chat 子代理
（``chat/services/chat_service._handle_subagent_tool_event``）三处曾各自内联**同一**编排序列：

    service.register(ToolCallContext(...))
        → service.bind_position(...)
        → await service.transition_async(...)
        → （子代理 / research）aggregate_tool_entry(entries, ...)

三份内联易出现字段遗漏与顺序漂移。本模块将其收敛为唯一实现
``publish_tool_lifecycle_event``，调用方只需按语义传参。

职责边界：
- 本函数只负责「编排顺序 + 容错降级」，不实现状态机（``tool_call_lifecycle``）、
  不实现条目聚合（``tool_call_aggregation``）。
- 编排各步失败互不阻断：register/bind_position/transition 失败仅 warning，
  聚合失败仅 debug（非关键路径），与既有三处内联的容错策略一致。
"""

from __future__ import annotations

import logging
from typing import Any

from Django_xm.common.event_schema import EventSource, EventType
from Django_xm.common.tool_call_aggregation import aggregate_tool_entry
from Django_xm.common.tool_call_lifecycle import ToolCallContext, service

logger = logging.getLogger(__name__)


async def publish_tool_lifecycle_event(
    *,
    module: EventSource,
    module_id: str,
    tool_call_id: str,
    tool_name: str,
    event_type: EventType | str,
    message_id: str = "",
    parameters: dict | None = None,
    result: Any = None,
    error: Any = None,
    sources: list | None = None,
    cross_module_id: str = "",
    parent_tool_call_id: str = "",
    depth: int = 0,
    agent_name: str = "",
    risk_ceiling: str = "",
    description: str = "",
    subagent_thread_id: str = "",
    entries: dict | None = None,
    position: int | None = None,
) -> None:
    """发布单个工具调用生命周期事件（唯一编排入口）。

    内部严格顺序：
    1. ``service.register(ToolCallContext(...))``：注册/补全上下文（幂等）。
    2. 若 ``position is not None``：``service.bind_position``（图层内偏移，不可变）。
    3. ``await service.transition_async(...)``：状态机转换并发布事件
       （PENDING 补发防护命中时跳过）。
    4. 若 ``entries is not None``：``aggregate_tool_entry`` 聚合落库条目。

    容错：各步独立 try/except，单步失败不阻断后续步骤；PENDING 补发防护仅跳过
    第 3 步，register/position/聚合仍执行（保持条目一致）。

    Args:
        module: 事件来源模块（EventSource）。
        module_id: 模块实例 ID（chat=session_id，deep_research=task_id）。
        tool_call_id: 工具调用 ID（唯一主键）。
        tool_name: 工具名。
        event_type: 目标事件类型（EventType 枚举或等价字符串）。
        message_id: 归属消息 ID（未知传 ''）。
        parameters: 工具入参（空 dict {} 为有效占位；为空时从 context 兜底）。
        result: 工具结果（仅终态事件；None 表示不提供）。
        error: 工具错误（仅失败事件；None 表示不提供）。
        cross_module_id: 跨模块同步目标（deep_research 关联 chat 时为 chat_session_id）。
        parent_tool_call_id: 父工具调用 ID（子代理嵌套层级字段）。
        depth: 嵌套层级（0=主代理）。
        agent_name: 子代理名（主代理为空）。
        risk_ceiling: 子代理角色风险上限（RiskLevel 字符串值，主代理为空）。
        description: 子代理角色描述（任务目标）。
        subagent_thread_id: 子代理 SSE 定向推送路由标识符。
        entries: 聚合容器（非 None 时才聚合落库条目）。
        position: 调用方已算好的图层内 position（None 表示不绑定）。
    """
    # 参数兜底：调用方未提供非空参数时，从 ToolCallContext 权威源补齐
    # （middleware SAFE/审批路径已写入完整 AIMessage 参数），避免前端参数为空。
    if not (isinstance(parameters, dict) and parameters):
        try:
            _ctx = service.get_context(tool_call_id)
            _ctx_params = _ctx.get("parameters") if _ctx else None
            if isinstance(_ctx_params, dict) and _ctx_params:
                parameters = _ctx_params
        except Exception as e:
            logger.warning(
                f"[ToolEventPublisher] 读取工具调用参数兜底失败: "
                f"tc_id={tool_call_id}, err={e}"
            )

    # 1. 注册工具调用上下文（幂等；失败仅 warning，不中断后续发布）
    try:
        service.register(
            ToolCallContext(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                module=module,
                module_id=module_id,
                message_id=message_id,
                parameters=parameters if isinstance(parameters, dict) else {},
                cross_module_id=cross_module_id,
                parent_tool_call_id=parent_tool_call_id,
                depth=depth,
                agent_name=agent_name,
                risk_ceiling=risk_ceiling,
                description=description,
                subagent_thread_id=subagent_thread_id,
            )
        )
    except Exception as e:
        logger.warning(
            f"[ToolEventPublisher] 注册工具调用上下文失败: "
            f"tool={tool_name}, tc_id={tool_call_id}, err={e}"
        )

    # 2. position 绑定（按图层局部化；仅调用方已算好时绑定，失败仅 warning）
    if position is not None:
        try:
            service.bind_position(tool_call_id, position)
        except Exception as e:
            logger.warning(
                f"[ToolEventPublisher] 绑定 position 失败: "
                f"tc_id={tool_call_id}, position={position}, err={e}"
            )

    # 3. 状态机转换并发布事件
    # PENDING 补发防护：ToolMessage 阶段会对 AIMessage 阶段未发射的工具补发 PENDING。
    # 若 context 已推进到非 PENDING 状态（SAFE 自动通过 / 审批路径），补发 PENDING
    # 会触发 `running → pending` 非法转换 WARNING；此时跳过 transition，但
    # register/position/聚合仍执行（保持条目一致）。
    _event_value = event_type.value if hasattr(event_type, "value") else str(event_type)
    _skip_transition = False
    if _event_value == EventType.TOOL_CALL_PENDING.value:
        try:
            _existing_ctx = service.get_context(tool_call_id)
            _last_event = _existing_ctx.get("last_event_type") if _existing_ctx else None
        except Exception:
            _last_event = None
        if _last_event and _last_event != EventType.TOOL_CALL_PENDING.value:
            _skip_transition = True
            logger.debug(
                f"[ToolEventPublisher] 跳过 PENDING 补发（状态已推进）: "
                f"tool_call_id={tool_call_id}, last_event_type={_last_event}"
            )

    if not _skip_transition:
        # must-use is-not-None：空 dict {} 是有效占位 → 保留为 {} 而非 None
        # （transition 内部按空值跳过参数绑定，非空 dict 才写入 context）。
        transition_parameters = (
            parameters
            if (isinstance(parameters, dict) and parameters)
            else ({} if isinstance(parameters, dict) else None)
        )
        try:
            await service.transition_async(
                tool_call_id,
                event_type,
                result=result,
                error=error,
                parameters=transition_parameters,
                sources=sources,
            )
        except Exception as e:
            logger.warning(
                f"[ToolEventPublisher] 发布工具事件失败: event={_event_value}, "
                f"tool={tool_name}, tc_id={tool_call_id}, err={e}"
            )

    # 4. 落库条目聚合（独立 try：transition 失败不阻断聚合；失败仅 debug）
    if entries is not None:
        try:
            aggregate_tool_entry(
                entries,
                tool_call_id,
                tool_name,
                event_type,
                parameters=parameters,
                result=result,
                error=error,
                subagent_thread_id=subagent_thread_id,
                agent_name=agent_name,
                depth=depth,
            )
        except Exception as e:
            logger.debug(
                f"[ToolEventPublisher] 工具条目聚合失败: tc_id={tool_call_id}, err={e}"
            )
