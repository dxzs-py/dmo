"""工具调用生命周期服务（三模块共享的状态机）。

管理工具调用从创建到终态的完整上下文：
  pending → input_ready → waiting/running → completed/failed/timeout/rejected

所有 TOOL_CALL_* 事件必须通过本服务发布，确保：
1. parameters/message_id/graph_interrupt_id/cross_module_id 不缺失（从 context 透传）
2. 状态机转换合法（非法转换仅 warning，不抛异常，容错优先）
3. 事件不重复发布（同 tool_call_id + 同 event_type 仅发布一次，INPUT_READY 除外）
4. 三模块（chat/deep_research/learning）共享同一份代码

使用方式：
    from Django_xm.common.tool_call_lifecycle import service, ToolCallContext

    # 1. 首次发现 tool_call 时注册上下文
    service.register(ToolCallContext(
        tool_call_id='call_xxx',
        tool_name='shell_exec',
        module=EventSource.CHAT,
        module_id=session_id,
        message_id='msg_xxx',  # 未知时传 ''
        parameters={'command': 'ls'},  # 未知时传 {}
    ))

    # 2. 参数恢复后补全
    service.bind_parameters('call_xxx', {'command': 'ls -la'})

    # 3. 消息持久化后补全 message_id
    service.bind_message_id('call_xxx', 'msg_xxx')

    # 4. 状态机转换并发布事件
    service.transition('call_xxx', EventType.TOOL_CALL_RUNNING)
    service.transition('call_xxx', EventType.TOOL_CALL_COMPLETED, result={'output': '...'})

模块级单例：service = ToolCallLifecycleService()
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from django.core.cache import cache

from Django_xm.common.event_schema import EventSource, EventType
from Django_xm.common.realtime_sync import publish_tool_call, publish_tool_call_sync

logger = logging.getLogger(__name__)

# Redis key 前缀（24h TTL，与事件历史对齐）
_TC_CTX_PREFIX = "tool_call:ctx"
_TC_CTX_TTL = 24 * 60 * 60

# 已发布事件去重 key 前缀（tool_call_id:event_type → 1）
_PUBLISHED_KEY_PREFIX = "tool_call:published"

# 状态机：合法的前驱状态集合（None 表示初始状态，允许从无到有）
# INPUT_READY 允许自循环（参数补全场景下重发）
# INPUT_READY 允许从 None 转换：chat/learning 模块直接发布 INPUT_READY，不发布 PENDING
# WAITING 允许从 None 转换：ApprovalMiddleware 创建审批时直接发布 WAITING（无需先 INPUT_READY）
# RUNNING 允许自循环：审批通过（PROCESSING）时由 approval_service 发布 RUNNING，stream_helpers 可能因并发再次发布
# FAILED 允许从 WAITING 转换：审批通过前工具可能因前置依赖失败
_VALID_TRANSITIONS: dict[EventType, set[EventType | None]] = {
    EventType.TOOL_CALL_PENDING:     {None},
    EventType.TOOL_CALL_INPUT_READY: {None, EventType.TOOL_CALL_PENDING, EventType.TOOL_CALL_INPUT_READY},
    EventType.TOOL_CALL_WAITING:     {None, EventType.TOOL_CALL_INPUT_READY, EventType.TOOL_CALL_WAITING},
    EventType.TOOL_CALL_RUNNING:     {
        EventType.TOOL_CALL_INPUT_READY,
        EventType.TOOL_CALL_WAITING,
        EventType.TOOL_CALL_RUNNING,
    },
    EventType.TOOL_CALL_COMPLETED:   {
        EventType.TOOL_CALL_RUNNING,
        EventType.TOOL_CALL_INPUT_READY,
        EventType.TOOL_CALL_WAITING,
        None,
    },
    EventType.TOOL_CALL_FAILED:      {
        EventType.TOOL_CALL_RUNNING,
        EventType.TOOL_CALL_INPUT_READY,
        EventType.TOOL_CALL_WAITING,
        None,
    },
    EventType.TOOL_CALL_TIMEOUT:     {
        EventType.TOOL_CALL_INPUT_READY,
        EventType.TOOL_CALL_WAITING,
        None,
    },
    EventType.TOOL_CALL_REJECTED:    {
        EventType.TOOL_CALL_INPUT_READY,
        EventType.TOOL_CALL_WAITING,
        None,
    },
}


@dataclass
class ToolCallContext:
    """工具调用上下文（Redis 持久化，跨模块共享）。

    核心字段：
        module: 业务模块（CHAT / DEEP_RESEARCH / LEARNING）
        module_id: 模块实例 ID（chat=session_id, deep_research=task_id, learning=thread_id）
        cross_module_id: 跨模块同步目标（仅 DEEP_RESEARCH 关联 chat 时为 chat_session_id）

    所有字段在 register 时确定，后续可通过 bind_parameters / bind_message_id 补全。
    """
    tool_call_id: str
    tool_name: str
    module: EventSource
    module_id: str
    cross_module_id: str | None = None
    message_id: str = ''
    parameters: dict = field(default_factory=dict)
    graph_interrupt_id: str | None = None
    last_event_type: str | None = None  # 最后一次发布的 event_type.value


class ToolCallLifecycleService:
    """工具调用生命周期服务（模块级单例，三模块共享）。

    通过 Redis 持久化上下文，确保：
    - 跨请求/跨 worker 状态一致（Celery worker 重启后仍可恢复）
    - 跨模块共享同一份状态机代码
    - parameters/message_id 从 context 透传，杜绝字段缺失
    """

    def register(self, ctx: ToolCallContext) -> None:
        """注册工具调用上下文（首次发现 tool_call 时调用）。幂等。

        已存在时不覆盖非空字段（保留先注册的值），仅补全空字段。
        """
        key = f"{_TC_CTX_PREFIX}:{ctx.tool_call_id}"
        existing = cache.get(key)
        if existing:
            # 合并：仅补全空字段，不覆盖已有非空值
            if not existing.get('parameters') and ctx.parameters:
                existing['parameters'] = ctx.parameters
            if not existing.get('message_id') and ctx.message_id:
                existing['message_id'] = ctx.message_id
            if not existing.get('graph_interrupt_id') and ctx.graph_interrupt_id:
                existing['graph_interrupt_id'] = ctx.graph_interrupt_id
            if not existing.get('cross_module_id') and ctx.cross_module_id:
                existing['cross_module_id'] = ctx.cross_module_id
            cache.set(key, existing, _TC_CTX_TTL)
        else:
            cache.set(key, ctx.__dict__, _TC_CTX_TTL)

    def bind_parameters(self, tool_call_id: str, parameters: dict) -> None:
        """补全工具参数（finalize_tool_calls 参数恢复后调用）。"""
        if not isinstance(parameters, dict) or not parameters:
            return
        key = f"{_TC_CTX_PREFIX}:{tool_call_id}"
        ctx = cache.get(key) or {}
        ctx['parameters'] = parameters
        cache.set(key, ctx, _TC_CTX_TTL)

    def bind_message_id(self, tool_call_id: str, message_id: str) -> None:
        """补全消息 ID（消息持久化后调用）。仅补全空字段，不覆盖。"""
        if not message_id:
            return
        key = f"{_TC_CTX_PREFIX}:{tool_call_id}"
        ctx = cache.get(key) or {}
        if not ctx.get('message_id'):
            ctx['message_id'] = str(message_id)
            cache.set(key, ctx, _TC_CTX_TTL)

    def bind_graph_interrupt_id(self, tool_call_id: str, graph_interrupt_id: str) -> None:
        """补全批量审批批次 ID（ApprovalMiddleware 创建审批时调用）。"""
        if not graph_interrupt_id:
            return
        key = f"{_TC_CTX_PREFIX}:{tool_call_id}"
        ctx = cache.get(key) or {}
        if not ctx.get('graph_interrupt_id'):
            ctx['graph_interrupt_id'] = graph_interrupt_id
            cache.set(key, ctx, _TC_CTX_TTL)

    def _prepare_transition(
        self,
        tool_call_id: str,
        event_type: EventType,
        parameters: dict | None,
    ) -> dict[str, Any] | None:
        """状态机转换的公共预处理逻辑（sync/async 共享）。

        流程：
        1. 读取 context（不存在则 warning 并跳过）
        2. 校验状态转换合法性（仅 warning，不抛异常）
        3. 去重检查（同 tool_call_id + event_type 已发布则跳过，INPUT_READY 除外）
        4. 补全 parameters（如果调用方提供了非空 parameters）

        Returns:
            context_dict（如果应该继续发布事件），None 表示跳过
        """
        key = f"{_TC_CTX_PREFIX}:{tool_call_id}"
        ctx_dict = cache.get(key)
        if not ctx_dict:
            logger.warning(
                f"[ToolCallLifecycle] 上下文不存在，跳过事件发布: "
                f"tool_call_id={tool_call_id}, event_type={event_type.value}"
            )
            return None

        # 状态机校验（仅 warning，容错优先）
        last_event = ctx_dict.get('last_event_type')
        last_event_enum = EventType.from_value(last_event) if last_event else None
        valid_prev = _VALID_TRANSITIONS.get(event_type, set())
        if last_event_enum not in valid_prev:
            logger.warning(
                f"[ToolCallLifecycle] 非法状态转换: "
                f"tool_call_id={tool_call_id}, {last_event} → {event_type.value}, "
                f"allowed_prev={[e.value if e else None for e in valid_prev]}"
            )

        # 去重：非 INPUT_READY 事件已发布则跳过
        if event_type != EventType.TOOL_CALL_INPUT_READY:
            dedup_key = f"{_PUBLISHED_KEY_PREFIX}:{tool_call_id}:{event_type.value}"
            if cache.get(dedup_key):
                logger.info(
                    f"[ToolCallLifecycle] 事件已发布，跳过: "
                    f"tool_call_id={tool_call_id}, event_type={event_type.value}"
                )
                return None

        # 补全 parameters（如果调用方提供了非空 parameters）
        if parameters and isinstance(parameters, dict):
            self.bind_parameters(tool_call_id, parameters)
            ctx_dict = cache.get(key) or ctx_dict

        return ctx_dict

    def _finalize_transition(
        self,
        tool_call_id: str,
        event_type: EventType,
        ctx_dict: dict[str, Any],
    ) -> None:
        """状态机转换的公共后处理逻辑（sync/async 共享）。

        标记已发布，更新 last_event_type。
        """
        key = f"{_TC_CTX_PREFIX}:{tool_call_id}"
        ctx_dict['last_event_type'] = event_type.value
        cache.set(key, ctx_dict, _TC_CTX_TTL)
        if event_type != EventType.TOOL_CALL_INPUT_READY:
            dedup_key = f"{_PUBLISHED_KEY_PREFIX}:{tool_call_id}:{event_type.value}"
            cache.set(dedup_key, 1, _TC_CTX_TTL)

    def transition(
        self,
        tool_call_id: str,
        event_type: EventType,
        *,
        result: Any = None,
        error: str | None = None,
        parameters: dict | None = None,
    ) -> None:
        """状态机转换并发布事件（同步版，适配 sync 上下文：chat 模块 stream_helpers）。

        内部调用 publish_tool_call_sync：
        - 不在 async 事件循环中：async_to_sync 同步调用
        - 在 async 事件循环中：asyncio.ensure_future fire-and-forget

        Args:
            tool_call_id: 工具调用 ID
            event_type: 目标事件类型
            result: 工具执行结果（仅 COMPLETED 事件）
            error: 错误信息（仅 FAILED 事件）
            parameters: 补全的参数（可选，用于参数恢复场景）
        """
        ctx_dict = self._prepare_transition(tool_call_id, event_type, parameters)
        if ctx_dict is None:
            return

        # 从 context 透传所有字段（三模块共享逻辑）
        module_value = ctx_dict.get('module', 'chat')
        module_enum = EventSource.from_value(module_value) or EventSource.CHAT

        try:
            publish_tool_call_sync(
                event_type=event_type,
                tool_call_id=tool_call_id,
                tool_name=ctx_dict.get('tool_name', 'unknown'),
                module=module_enum,
                module_id=ctx_dict.get('module_id', ''),
                message_id=ctx_dict.get('message_id', ''),
                parameters=ctx_dict.get('parameters', {}),
                cross_module_id=ctx_dict.get('cross_module_id'),
                graph_interrupt_id=ctx_dict.get('graph_interrupt_id'),
                result=result,
                error=error,
            )
        except Exception as e:
            logger.error(
                f"[ToolCallLifecycle] 发布事件失败: "
                f"tool_call_id={tool_call_id}, event_type={event_type.value}, err={e}"
            )
            return

        self._finalize_transition(tool_call_id, event_type, ctx_dict)

    async def transition_async(
        self,
        tool_call_id: str,
        event_type: EventType,
        *,
        result: Any = None,
        error: str | None = None,
        parameters: dict | None = None,
    ) -> None:
        """状态机转换并发布事件（异步版，适配 async 上下文：research/learning 模块）。

        内部 await publish_tool_call，确保事件按调用顺序发布（避免 fire-and-forget 顺序错乱）。

        Args:
            tool_call_id: 工具调用 ID
            event_type: 目标事件类型
            result: 工具执行结果（仅 COMPLETED 事件）
            error: 错误信息（仅 FAILED 事件）
            parameters: 补全的参数（可选，用于参数恢复场景）
        """
        ctx_dict = self._prepare_transition(tool_call_id, event_type, parameters)
        if ctx_dict is None:
            return

        module_value = ctx_dict.get('module', 'chat')
        module_enum = EventSource.from_value(module_value) or EventSource.CHAT

        try:
            await publish_tool_call(
                event_type,
                tool_call_id=tool_call_id,
                tool_name=ctx_dict.get('tool_name', 'unknown'),
                module=module_enum,
                module_id=ctx_dict.get('module_id', ''),
                message_id=ctx_dict.get('message_id', ''),
                parameters=ctx_dict.get('parameters', {}),
                cross_module_id=ctx_dict.get('cross_module_id'),
                graph_interrupt_id=ctx_dict.get('graph_interrupt_id'),
                result=result,
                error=error,
            )
        except Exception as e:
            logger.error(
                f"[ToolCallLifecycle] 发布事件失败(async): "
                f"tool_call_id={tool_call_id}, event_type={event_type.value}, err={e}"
            )
            return

        self._finalize_transition(tool_call_id, event_type, ctx_dict)

    def get_context(self, tool_call_id: str) -> dict | None:
        """获取工具调用上下文（调试/测试用）。"""
        return cache.get(f"{_TC_CTX_PREFIX}:{tool_call_id}")

    def clear_context(self, tool_call_id: str) -> None:
        """清除工具调用上下文（工具进入终态后可调用，释放 Redis 空间）。"""
        cache.delete(f"{_TC_CTX_PREFIX}:{tool_call_id}")
        # 清除所有 event_type 的去重 key
        for event_type in EventType:
            if event_type.value.startswith('tool_call_'):
                cache.delete(f"{_PUBLISHED_KEY_PREFIX}:{tool_call_id}:{event_type.value}")


# 模块级单例
service = ToolCallLifecycleService()
