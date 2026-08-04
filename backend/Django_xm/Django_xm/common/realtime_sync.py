"""实时同步统一服务（三模块共享的唯一发布入口）。

设计目标：
1. 三模块（chat / deep_research / learning）共享同一套发布逻辑
2. 跨模块同步通过 cross_module_id 显式表达，不再隐式路由
3. parameters / message_id / graph_interrupt_id 由调用方显式传入，
   服务层强制 schema 校验，杜绝字段缺失

模块与频道路由映射（由 _resolve_channels 统一处理）：
    CHAT          → session:{module_id}
    LEARNING      → session:{module_id}
    DEEP_RESEARCH + cross_module_id → session:{cross_module_id} + task:{module_id}（双频道）
    DEEP_RESEARCH 无 cross_module_id → task:{module_id}

跨模块场景（chat 模块的深度研究模式）：
    模块 = DEEP_RESEARCH, module_id = task_id, cross_module_id = chat_session_id
    → 同时广播到 session:{chat_session_id} 和 task:{task_id}
    → chat 模块前端订阅 session 频道，deep_research 模块前端订阅 task 频道
    → 两个模块的前端都收到同一事件，状态自动一致

使用方式：
    from Django_xm.common.realtime_sync import publish_tool_call, publish_approval

    # 工具调用事件（三模块统一入口）
    await publish_tool_call(
        EventType.TOOL_CALL_RUNNING,
        tool_call_id='call_xxx',
        tool_name='shell_exec',
        module=EventSource.CHAT,
        module_id=session_id,
        message_id='msg_xxx',
        parameters={'command': 'ls'},
    )

    # 审批事件（三模块统一入口）
    await publish_approval(
        EventType.APPROVAL_PENDING,
        interrupt_id='call_xxx',
        tool_call_id='call_xxx',
        module=EventSource.CHAT,
        module_id=session_id,
        state='pending',
        tool_name='shell_exec',
        message_id='msg_xxx',
        parameters={'command': 'ls'},
    )

禁止直接调用 publish_event / publish_event_sync 发布工具/审批事件。
"""

import asyncio
import logging
from typing import Any

from asgiref.sync import async_to_sync

from Django_xm.common.event_schema import (
    EventSource,
    EventType,
    PayloadValidationError,
)
from Django_xm.common.realtime_events import (
    _pending_publish_tasks,
    publish_event,
)

logger = logging.getLogger(__name__)


def _resolve_channels(
    module: EventSource,
    module_id: str,
    cross_module_id: str | None,
) -> tuple[str | None, str | None]:
    """解析频道路由（三模块统一）。

    Args:
        module: 业务模块（CHAT / DEEP_RESEARCH / LEARNING）
        module_id: 模块实例 ID
            - CHAT: session_id
            - DEEP_RESEARCH: task_id
            - LEARNING: thread_id（作为 session_id）
        cross_module_id: 跨模块同步目标 ID
            - 仅 DEEP_RESEARCH 关联 chat 时为 chat_session_id
            - 其他场景为 None

    Returns:
        (session_id, task_id) 二元组：
        - CHAT: (module_id, None) → 仅 session 频道
        - LEARNING: (module_id, None) → 仅 session 频道
        - DEEP_RESEARCH + cross_module_id: (cross_module_id, module_id) → 双频道
        - DEEP_RESEARCH 无 cross_module_id: (None, module_id) → 仅 task 频道
    """
    if module in (EventSource.CHAT, EventSource.LEARNING):
        return module_id, None
    elif module == EventSource.DEEP_RESEARCH:
        if cross_module_id:
            # 关联 chat 场景：session + task 双频道
            # session_id = cross_module_id (chat_session_id)
            # task_id = module_id (task_id)
            return cross_module_id, module_id
        else:
            # 独立模式：仅 task 频道
            return None, module_id
    elif module in (EventSource.WORKFLOW, EventSource.AGENT):
        # v5 预留：新模块默认路由到 session 频道
        return module_id, None
    else:
        logger.warning(f"[RealtimeSync] 未知 module: {module}, fallback to session")
        return module_id, None


async def publish_tool_call(
    event_type: EventType,
    *,
    tool_call_id: str,
    tool_name: str,
    module: EventSource,
    module_id: str,
    message_id: str = "",
    parameters: dict | None = None,
    cross_module_id: str | None = None,
    graph_interrupt_id: str | None = None,
    result: Any = None,
    error: str | None = None,
    auto_approved: bool = False,
    parent_tool_call_id: str | None = None,
    depth: int | None = None,
    agent_name: str | None = None,
    agent_path: list | None = None,
    risk_ceiling: str | None = None,
    _index: int | None = None,
) -> None:
    """工具调用生命周期事件发布（三模块统一入口）。

    所有 TOOL_CALL_* 事件必须通过此函数发布，禁止直接调用 publish_event。

    Args:
        event_type: 工具调用事件类型（TOOL_CALL_PENDING /
                    TOOL_CALL_RUNNING / TOOL_CALL_COMPLETED / 等）
        tool_call_id: 工具调用 ID（= LLM tool_call.id，唯一主键）
        tool_name: 工具名称
        module: 业务模块（CHAT / DEEP_RESEARCH / LEARNING）
        module_id: 模块实例 ID（chat=session_id, deep_research=task_id, learning=thread_id）
        message_id: 关联的消息 ID（未知时传 ''，必填）
        parameters: 工具参数（必填，空参数传 {}）
        cross_module_id: 跨模块同步目标 ID（仅 DEEP_RESEARCH 关联 chat 时传 chat_session_id）
        graph_interrupt_id: 批量审批批次 ID（同批次审批共享）
        result: 工具执行结果（仅 COMPLETED 事件）
        error: 错误信息（仅 FAILED 事件）
        auto_approved: SAFE 级自动通过标记（True=无需用户审批，仅审计，前端可显示徽章）
        parent_tool_call_id: 父工具调用 ID（主 agent 调用 task 工具的 tool_call_id）
        depth: 嵌套层级（0=主 agent，1=一级子 agent）
        agent_name: 子 agent 名称（如 web-researcher）
        agent_path: 完整调用链路（如 ["main", "web-researcher"]）
        risk_ceiling: 子 agent 角色风险上限（safe/controlled/high）

    Raises:
        PayloadValidationError: payload 校验失败时抛出
    """
    # 构造符合 schema 的 payload（parameters/message_id 必填）
    payload = {
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "source": module,
        "source_id": module_id,
        "message_id": message_id or "",
        "parameters": parameters if isinstance(parameters, dict) else {},
    }
    if graph_interrupt_id:
        payload["graph_interrupt_id"] = graph_interrupt_id
    if cross_module_id:
        payload["cross_module_id"] = cross_module_id
    if result is not None:
        payload["result"] = result
    if error is not None:
        payload["error"] = error
    if auto_approved:
        payload["auto_approved"] = True
    # 子 agent 嵌套层级字段（Phase E3）：仅非空时加入 payload，
    # 主 agent 不传这些字段，payload 保持简洁
    if parent_tool_call_id:
        payload["parent_tool_call_id"] = parent_tool_call_id
    if depth is not None and depth > 0:
        payload["depth"] = depth
    if agent_name:
        payload["agent_name"] = agent_name
    if agent_path:
        payload["agent_path"] = agent_path
    if risk_ceiling:
        payload["risk_ceiling"] = risk_ceiling
    if _index is not None:
        payload["_index"] = _index

    # 解析频道路由（三模块统一）
    session_id, task_id = _resolve_channels(module, module_id, cross_module_id)

    try:
        await publish_event(
            event_type,
            payload,
            session_id=session_id,
            task_id=task_id,
        )
    except PayloadValidationError:
        logger.exception(
            f"[RealtimeSync] publish_tool_call payload 校验失败: "
            f"event_type={event_type.value}, module={module.value}, "
            f"module_id={module_id}, cross_module_id={cross_module_id}, "
            f"tool_call_id={tool_call_id}",
        )
        raise


def publish_tool_call_sync(**kwargs) -> None:
    """publish_tool_call 的同步版本。智能适配 async/sync 上下文。

    - async 上下文：使用 fire-and-forget 模式调度 ``publish_tool_call`` 协程。
      串行化与 seq 顺序保证由 ``publish_event`` 内部的 ``_ordered_ensure_future``
      按 channel 统一负责，本函数不再外层串行化（避免与内层共用 channel_key
      导致 task 链死锁）。任务引用由 ``_pending_publish_tasks`` 持有，完成后自动清理。
    - sync 上下文：使用 async_to_sync 同步调用

    seq 顺序保证：同一 channel 的事件按调用顺序发布，seq 顺序与调用顺序一致，
    避免前端按 seq 处理事件时 running 覆盖 completed、工具状态永久"执行中"。
    """
    try:
        asyncio.get_running_loop()
        # 在 async 事件循环中，fire-and-forget publish_tool_call
        # 串行化由 publish_event 内部的 _ordered_ensure_future 按 channel 保证
        task = asyncio.ensure_future(publish_tool_call(**kwargs))
        _pending_publish_tasks.add(task)
        task.add_done_callback(_pending_publish_tasks.discard)
    except RuntimeError:
        # 不在 async 事件循环中，使用 async_to_sync 同步调用
        try:
            async_to_sync(publish_tool_call)(**kwargs)
        except PayloadValidationError:
            # payload 校验失败：已由 publish_tool_call 内部记录 ERROR，这里仅重抛
            raise
        except Exception:
            logger.exception(
                f"[RealtimeSync] publish_tool_call_sync 失败: "
                f"event_type={kwargs.get('event_type')}, "
                f"tool_call_id={kwargs.get('tool_call_id')}"
            )


async def publish_approval(
    event_type: EventType,
    *,
    interrupt_id: str,
    tool_call_id: str,
    module: EventSource,
    module_id: str,
    state: str,
    tool_name: str = "",
    message_id: str = "",
    parameters: dict | None = None,
    cross_module_id: str | None = None,
    graph_interrupt_id: str | None = None,
    extra_fields: dict | None = None,
) -> None:
    """审批事件发布（三模块统一入口）。

    所有 APPROVAL_* 事件必须通过此函数发布，禁止直接调用 publish_event。

    Args:
        event_type: 审批事件类型（APPROVAL_PENDING / APPROVAL_PROCESSING /
                    APPROVAL_APPROVED / APPROVAL_REJECTED / APPROVAL_TIMEOUT / 等）
        interrupt_id: 中断 ID（= tool_call_id）
        tool_call_id: 工具调用 ID（= LLM tool_call.id，唯一主键）
        module: 业务模块（CHAT / DEEP_RESEARCH / LEARNING）
        module_id: 模块实例 ID
        state: 审批状态（pending/processing/waiting/approved/rejected/timeout）
        tool_name: 工具名称
        message_id: 关联的消息 ID（未知时传 ''，必填）
        parameters: 工具参数（必填，审批面板展示用，空参数传 {}）
        cross_module_id: 跨模块同步目标 ID
        graph_interrupt_id: 批量审批批次 ID
        extra_fields: 追加字段（title/description/operation/danger_level 等）

    Raises:
        PayloadValidationError: payload 校验失败时抛出
    """
    payload = {
        "interrupt_id": interrupt_id,
        "tool_call_id": tool_call_id,
        "source": module,
        "source_id": module_id,
        "state": state,
        "message_id": message_id or "",
        "parameters": parameters if isinstance(parameters, dict) else {},
    }
    if tool_name:
        payload["tool_name"] = tool_name
    if graph_interrupt_id:
        payload["graph_interrupt_id"] = graph_interrupt_id
    if cross_module_id:
        payload["cross_module_id"] = cross_module_id
    if extra_fields:
        payload.update(extra_fields)

    # 解析频道路由（三模块统一）
    session_id, task_id = _resolve_channels(module, module_id, cross_module_id)

    try:
        await publish_event(
            event_type,
            payload,
            session_id=session_id,
            task_id=task_id,
        )
    except PayloadValidationError:
        logger.exception(
            f"[RealtimeSync] publish_approval payload 校验失败: "
            f"event_type={event_type.value}, module={module.value}, "
            f"module_id={module_id}, interrupt_id={interrupt_id}",
        )
        raise


def publish_approval_sync(**kwargs) -> None:
    """publish_approval 的同步版本。智能适配 async/sync 上下文。

    - async 上下文：使用 fire-and-forget 模式调度 ``publish_approval`` 协程。
      串行化与 seq 顺序保证由 ``publish_event`` 内部的 ``_ordered_ensure_future``
      按 channel 统一负责，本函数不再外层串行化（避免与内层共用 channel_key
      导致 task 链死锁）。任务引用由 ``_pending_publish_tasks`` 持有，完成后自动清理。
    - sync 上下文：使用 async_to_sync 同步调用

    seq 顺序保证：与 publish_tool_call_sync 同理，审批事件按 channel 串行化，
    避免 approval_approved 与 approval_pending 等事件 seq 顺序错乱导致前端状态不一致。
    """
    try:
        asyncio.get_running_loop()
        # 在 async 事件循环中，fire-and-forget publish_approval
        # 串行化由 publish_event 内部的 _ordered_ensure_future 按 channel 保证
        task = asyncio.ensure_future(publish_approval(**kwargs))
        _pending_publish_tasks.add(task)
        task.add_done_callback(_pending_publish_tasks.discard)
    except RuntimeError:
        try:
            async_to_sync(publish_approval)(**kwargs)
        except PayloadValidationError:
            raise
        except Exception:
            logger.exception(
                f"[RealtimeSync] publish_approval_sync 失败: "
                f"event_type={kwargs.get('event_type')}, "
                f"interrupt_id={kwargs.get('interrupt_id')}"
            )
