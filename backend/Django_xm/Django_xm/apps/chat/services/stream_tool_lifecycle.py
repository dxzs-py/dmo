"""工具调用生命周期事件发布（Task 15.2 从 stream_helpers.py 拆分）。

集中封装工具调用生命周期事件的发布逻辑：
- ``_publish_tool_lifecycle_event``：register + transition 统一入口
- ``_broadcast_tool_input_ready``：流式期间实时广播 INPUT_READY 事件到同会话其他浏览器

依赖方向：本模块仅依赖 ``Django_xm.common.*``，不依赖其他 stream_* 子模块，
位于依赖链最底层，可被 ``stream_chunk_processors`` / ``stream_tool_state`` 安全导入。
"""

import logging
from typing import Any

from Django_xm.common.event_schema import EventSource, EventType
from Django_xm.common.tool_call_lifecycle import ToolCallContext, service

logger = logging.getLogger(__name__)


def _publish_tool_lifecycle_event(
    event_type: EventType,
    tool_info: dict[str, Any],
    session_id: str | None,
    message_id: str | None,
    *,
    module: EventSource = EventSource.CHAT,
    module_id: str | None = None,
) -> None:
    """发布工具调用生命周期事件到统一 tool_call_lifecycle.service。

    封装 ``service.register`` + ``service.transition`` 调用，提供单一入口
    供 chat 模块（stream 流式 / regenerate 重新生成）发布工具调用事件。

    跳过逻辑（任一缺失即跳过，不调用 register/transition）：
        - ``session_id`` 为 None / 空串
        - ``tool_info['id']`` 为 None / 空串（tool_call_id）
        - ``tool_info['name']`` 为 None / 空串（tool_name）

    register 行为（始终调用，幂等）：
        - ``ToolCallContext.tool_call_id`` = ``tool_info['id']``
        - ``ToolCallContext.tool_name`` = ``tool_info['name']``
        - ``ToolCallContext.module`` = ``module`` 参数（默认 EventSource.CHAT）
        - ``ToolCallContext.module_id`` = ``module_id`` or ``session_id``
        - ``ToolCallContext.message_id`` = ``str(message_id) if message_id is not None else ''``
        - ``ToolCallContext.parameters`` = ``tool_info.get('parameters') or {}``（始终为 dict）

    bind_message_id 行为：
        - 仅当 ``message_id`` 非 None 时调用（补全 message_id）
        - ``message_id`` 为 None 时跳过（register 已设为空串，无需补全）

    transition 行为：
        - ``parameters``：空 dict / None → None（falsy 判断）；非空 dict → 原样透传
        - ``result``：仅 ``EventType.TOOL_CALL_COMPLETED`` 传 ``tool_info.get('result')``
        - ``error``：仅 ``EventType.TOOL_CALL_FAILED`` 传 ``tool_info.get('error')``

    Args:
        event_type: ``EventType`` 枚举成员（如 ``TOOL_CALL_PENDING``）
        tool_info: 工具调用信息 dict，必须包含 ``id`` / ``name``，
            可选包含 ``parameters`` / ``result`` / ``error``
        session_id: 会话 ID（用于 ``ToolCallContext.module_id`` 兜底）
        message_id: 消息 ID（用于 ``ToolCallContext.message_id`` 与 ``bind_message_id``）
        module: 事件来源模块（EventSource 枚举），三模块通用
        module_id: 模块级 ID（session_id / task_id / thread_id 等）

    契约对齐：``apps/chat/tests/test_stream_helpers_tool_events.py``
    """
    # 跳过逻辑：session_id / tool_call_id / tool_name 任一缺失即跳过
    if not session_id:
        return
    tool_call_id = tool_info.get("id") if isinstance(tool_info, dict) else None
    tool_name = tool_info.get("name") if isinstance(tool_info, dict) else None
    if not tool_call_id or not tool_name:
        return

    # message_id 处理：None → 空串（register），不调用 bind_message_id
    # 非 None → str 化后传给 register，并调用 bind_message_id 补全
    resolved_message_id = "" if message_id is None else str(message_id)

    # module_id 处理：显式传入优先，否则回退到 session_id
    resolved_module_id = module_id if module_id else session_id

    # register：始终调用，parameters 始终为 dict（空时为 {}）
    # 传入 event_type 以启用 register 的 last_event_type 防护：
    # PENDING 在状态已推进（waiting/running 等）时重复注册会被拒绝并告警。
    parameters = tool_info.get("parameters") or {}
    ctx = ToolCallContext(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        module=module,
        module_id=resolved_module_id,
        message_id=resolved_message_id,
        parameters=parameters,
    )
    service.register(ctx, event_type=event_type)

    # bind_message_id：仅当 message_id 非 None 时调用（补全 message_id）
    if message_id is not None:
        service.bind_message_id(tool_call_id, resolved_message_id)

    # transition：parameters 始终作为 kwarg 传递
    # 空 dict / None → None（falsy 判断）；非空 dict → 原样透传
    # 契约：测试断言 kwargs['parameters'] is None（需显式传 None，不能省略）
    transition_parameters = parameters if parameters else None

    # result 仅 COMPLETED 事件透传（None 时也省略，与 _handle_tool_message_chunk 一致）
    result = tool_info.get("result") if event_type == EventType.TOOL_CALL_COMPLETED else None

    # error 仅 FAILED 事件透传
    error = tool_info.get("error") if event_type == EventType.TOOL_CALL_FAILED else None

    # _index：LLM 生成的工具调用原始序号（用于跨浏览器工具顺序稳定排序）
    _index = tool_info.get("_index") if isinstance(tool_info, dict) else None

    service.transition(
        tool_call_id,
        event_type,
        parameters=transition_parameters,
        result=result,
        error=error,
        _index=_index,
    )


def _broadcast_tool_input_ready(
    tool_info: dict[str, Any],
    session_id: str | None,
    message_id: str | None,
    *,
    module: EventSource = EventSource.CHAT,
    module_id: str | None = None,
) -> None:
    """流式期间实时广播 TOOL_CALL_PENDING 事件到同会话其他浏览器。

    在 ``_handle_ai_message_chunk`` / ``finalize_tool_calls`` 内部每次 yield ``tool``
    SSE 事件时调用，确保非触发浏览器通过 WebSocket 实时收到工具调用（含输入参数）。

    跳过条件（任一满足即跳过，不抛异常）：
        - ``session_id`` 为 None / 空串
        - ``tool_info['id']`` 为 None / 空串
        - ``tool_info['name']`` 为 None / 空串
        - ``tool_info['parameters']`` 为空 dict / None（参数不完整时不广播）

    Args:
        tool_info: 工具调用信息 dict（须含 id / name / parameters）
        session_id: 会话 ID（None 时跳过广播）
        message_id: 关联的助手消息 ID（None 时不绑定）
        module: 事件来源模块（EventSource 枚举），三模块通用
        module_id: 模块级 ID（session_id / task_id / thread_id 等）
    """
    if not session_id:
        return
    if not isinstance(tool_info, dict):
        return
    tool_call_id = tool_info.get("id") or ""
    tool_name = tool_info.get("name") or ""
    if not tool_call_id or not tool_name:
        return
    parameters = tool_info.get("parameters")
    # 参数未就绪时跳过广播（None 表示参数尚未解析完成，避免前端展示残缺参数）
    # 空 dict {} 是合法的"无参数"状态（如 get_current_time），必须正常广播，
    # 否则非触发浏览器对该工具收不到任何事件（issue_p0_cross_browser_tool_call_count_mismatch 根因 B1）
    # PENDING 重复发布由 tool_call_lifecycle 的批次指纹去重天然拦截
    # （同 tool_call_id + 同 parameters 指纹仅发布一次，Task 1）。本函数保留
    # 供恢复流（chat_resume_generator）等调用方兼容使用，不在此处额外去重。
    if parameters is None or not isinstance(parameters, dict):
        return
    try:
        _publish_tool_lifecycle_event(
            EventType.TOOL_CALL_PENDING,
            tool_info,
            session_id,
            message_id,
            module=module,
            module_id=module_id,
        )
    except Exception as e:
        logger.warning(f"发布 PENDING 事件失败: tool_call_id={tool_call_id}, err={e}")