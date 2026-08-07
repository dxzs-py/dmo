"""工具调用生命周期服务（三模块共享的状态机）。

管理工具调用从创建到终态的完整上下文：
  pending → waiting/running → completed/failed/timeout/rejected

所有 TOOL_CALL_* 事件必须通过本服务发布，确保：
1. parameters/message_id/graph_interrupt_id/cross_module_id 不缺失（从 context 透传）
2. 状态机转换合法（非法转换仅 warning，不抛异常，容错优先）
3. 事件不重复发布（批次指纹去重：同 tool_call_id + 同 event_type + 同批次指纹仅发布一次；
   PENDING 同样参与去重，指纹以 parameters 稳定哈希为批次维度，参数变化允许重新发布）
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

import hashlib
import json
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

# 已发布事件去重 key 前缀（tool_call_id:event_type:batch_fingerprint → 1）
# batch_fingerprint 构成（见 _compute_batch_fingerprint）：
#   - g:{graph_interrupt_id}：审批批次标识（WAITING/RUNNING/终态事件优先使用）
#   - p:{parameters_md5_12}：parameters 稳定内容哈希（PENDING 固定使用；无批次标识时其他事件回退）
#   - no_batch：两者皆无（退化场景）
_PUBLISHED_KEY_PREFIX = "tool_call:published"

# 状态机：合法的前驱状态集合（None 表示初始状态，允许从无到有）
# PENDING 允许自循环（参数补全场景下重发）；允许从 None 转换（chat/learning/research 模块首个工具事件）
# WAITING 允许从 None/PENDING 转换，自循环（ApprovalMiddleware 直接发布 WAITING / 审批重试）
# RUNNING 允许从 None/PENDING/WAITING 转换，自循环（无需审批直接执行 / 审批通过后执行 / 并发重发）
# 终态（COMPLETED/FAILED/TIMEOUT/REJECTED）允许从任意非终态 + None 转换
# REJECTED 不包括 RUNNING（执行中的工具不可被拒绝，只能取消）
_VALID_TRANSITIONS: dict[EventType, set[EventType | None]] = {
    EventType.TOOL_CALL_PENDING: {None, EventType.TOOL_CALL_PENDING},
    EventType.TOOL_CALL_WAITING: {None, EventType.TOOL_CALL_PENDING, EventType.TOOL_CALL_WAITING},
    EventType.TOOL_CALL_RUNNING: {
        None,
        EventType.TOOL_CALL_PENDING,
        EventType.TOOL_CALL_WAITING,
        EventType.TOOL_CALL_RUNNING,
    },
    EventType.TOOL_CALL_COMPLETED: {
        None,
        EventType.TOOL_CALL_PENDING,
        EventType.TOOL_CALL_WAITING,
        EventType.TOOL_CALL_RUNNING,
    },
    EventType.TOOL_CALL_FAILED: {
        None,
        EventType.TOOL_CALL_PENDING,
        EventType.TOOL_CALL_WAITING,
        EventType.TOOL_CALL_RUNNING,
    },
    EventType.TOOL_CALL_TIMEOUT: {
        None,
        EventType.TOOL_CALL_PENDING,
        EventType.TOOL_CALL_WAITING,
        EventType.TOOL_CALL_RUNNING,
    },
    EventType.TOOL_CALL_REJECTED: {
        None,
        EventType.TOOL_CALL_PENDING,
        EventType.TOOL_CALL_WAITING,
    },
}


def _normalize_risk_ceiling(risk_ceiling: Any) -> str | None:
    """将 risk_ceiling 统一为字符串（供 publish_tool_call payload 使用）。

    Args:
        risk_ceiling: RiskLevel 枚举值 / 枚举字符串 / None

    Returns:
        RiskLevel 枚举的 .value 字符串（如 'safe'/'controlled'/'high'），
        或 None（None/空值/未知值，不写入 payload）
    """
    if risk_ceiling is None:
        return None
    # RiskLevel 枚举（继承 str，但 .value 显式取字符串更安全）
    if hasattr(risk_ceiling, "value"):
        return str(risk_ceiling.value)
    if isinstance(risk_ceiling, str) and risk_ceiling:
        # 已是字符串，校验是否合法 RiskLevel（非法值返回 None，避免前端误解）
        from Django_xm.common.risk_levels import RiskLevel

        try:
            RiskLevel(risk_ceiling)
            return risk_ceiling
        except ValueError:
            logger.warning(f"[ToolCallLifecycle] 非法 risk_ceiling 值: {risk_ceiling!r}, 忽略")
            return None
    return None


def _normalize_risk_level(risk_level: Any) -> str | None:
    """将 risk_level 统一为合法 RiskLevel 字符串（供 publish_tool_call payload 使用）。

    与 _normalize_risk_ceiling 逻辑一致，独立函数以便后续语义分化（risk_level
    可能扩展更多等级，risk_ceiling 仅限子 agent 角色上限场景）。

    Args:
        risk_level: RiskLevel 枚举值 / 枚举字符串 / None

    Returns:
        RiskLevel 枚举的 .value 字符串（如 'safe'/'controlled'/'high'），
        或 None（None/空值/未知值，不写入 payload）
    """
    if risk_level is None:
        return None
    if hasattr(risk_level, "value"):
        return str(risk_level.value)
    if isinstance(risk_level, str) and risk_level:
        from Django_xm.common.risk_levels import RiskLevel

        try:
            RiskLevel(risk_level)
            return risk_level
        except ValueError:
            logger.warning(f"[ToolCallLifecycle] 非法 risk_level 值: {risk_level!r}, 忽略")
            return None
    return None


def _stable_parameters_digest(parameters: Any) -> str:
    """计算 parameters 的稳定内容哈希（12 位 hex）。

    稳定性要求（PENDING 参数就绪场景）：
    - sort_keys=True：忽略 dict 键顺序差异（同一参数内容多次序列化结果一致）
    - ensure_ascii=False + default=str：非 ASCII 内容与非常规对象（datetime 等）可序列化
    - 参数为空 / 非 dict 时返回空串（调用方据此退化为 no_batch）

    Returns:
        12 位 md5 hex；无法计算时返回空串
    """
    if not isinstance(parameters, dict) or not parameters:
        return ""
    try:
        payload = json.dumps(parameters, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.md5(payload.encode("utf-8")).hexdigest()[:12]
    except Exception:
        # 极端不可序列化场景退化为 repr（仍内容相关、稳定），避免指纹计算中断发布
        try:
            return hashlib.md5(repr(parameters).encode("utf-8")).hexdigest()[:12]
        except Exception:
            return ""


def _compute_batch_fingerprint(
    event_type: EventType,
    ctx_dict: dict[str, Any],
    parameters: dict | None,
) -> str:
    """计算事件批次指纹（去重 key 的批次维度，Task 1 指纹去重核心）。

    指纹构成决定"同一 tool_call_id + 同一 event_type 的事件是否重复发布"：
    - TOOL_CALL_PENDING：固定使用 parameters 稳定哈希 → no_batch。
      graph_interrupt_id 在首次 PENDING 发布时通常尚未绑定（审批批次在 PENDING
      之后才创建），若改用 context 的 graph_interrupt_id，会在审批创建后发生
      指纹漂移（p:xxx → g:yyy），导致同批次重复 PENDING 无法被去重拦截
      （Task 1 根因：PENDING 被多个发布入口重复发布）。
    - 其他事件：优先 graph_interrupt_id（审批批次标识）——M16 复用 interrupt_id
      重审批时新批次携带新 graph_interrupt_id，可区分批次、避免被旧批次 dedup key
      误拦截；无 graph_interrupt_id 时回退 parameters 哈希；再退化为 no_batch。

    与去重检查/写入的对应：_prepare_transition 用本指纹检查 dedup key 是否存在，
    _finalize_transition 用同一指纹写入 dedup key，保证检查与写入一致。

    Returns:
        指纹字符串（含来源前缀，如 g:xxx / p:xxxxxxxxxxxx / no_batch）
    """
    if event_type == EventType.TOOL_CALL_PENDING:
        params = parameters if isinstance(parameters, dict) and parameters else ctx_dict.get("parameters")
        digest = _stable_parameters_digest(params)
        return f"p:{digest}" if digest else "no_batch"

    graph_interrupt_id = ctx_dict.get("graph_interrupt_id")
    if graph_interrupt_id:
        return f"g:{graph_interrupt_id}"
    params = parameters if isinstance(parameters, dict) and parameters else ctx_dict.get("parameters")
    digest = _stable_parameters_digest(params)
    return f"p:{digest}" if digest else "no_batch"


@dataclass
class ToolCallContext:
    """工具调用上下文（Redis 持久化，跨模块共享）。

    核心字段：
        module: 业务模块（CHAT / DEEP_RESEARCH / LEARNING）
        module_id: 模块实例 ID（chat=session_id, deep_research=task_id, learning=thread_id）
        cross_module_id: 跨模块同步目标（仅 DEEP_RESEARCH 关联 chat 时为 chat_session_id）
        auto_approved: SAFE 级自动通过标记（True=无需用户审批，仅审计）

    子 agent 嵌套层级字段（Phase E3，由 subagent_patch 注入到 configurable，
    ApprovalMiddleware._audit_auto_approved_tools / adapter._publish_tool_event
    从 configurable 提取后传入 register）：
        parent_tool_call_id: 主 agent 调用 task 工具的 tool_call_id
        depth: 嵌套层级（0=主 agent，1=一级子 agent）
        agent_name: 子 agent 名称（如 web-researcher）
        agent_path: 完整调用链路（如 ["main", "general-purpose", "web-researcher"]）
        risk_ceiling: 子 agent 角色风险上限（RiskLevel 枚举值字符串）

    所有字段在 register 时确定，后续可通过 bind_parameters / bind_message_id 补全。
    """

    tool_call_id: str
    tool_name: str
    module: EventSource
    module_id: str
    cross_module_id: str | None = None
    message_id: str = ""
    parameters: dict = field(default_factory=dict)
    graph_interrupt_id: str | None = None
    last_event_type: str | None = None  # 最后一次发布的 event_type.value
    auto_approved: bool = False  # SAFE 级自动通过标记（审计用）
    # 子 agent 嵌套层级字段（Phase E3）
    parent_tool_call_id: str = ""
    depth: int = 0
    agent_name: str = ""
    agent_path: list = field(default_factory=list)
    risk_ceiling: str = ""
    # 工具调用实际风险等级（safe/controlled/high，由 ApprovalMiddleware 计算）
    # 与 risk_ceiling 区别：
    #   - risk_ceiling: 子 agent 角色风险上限（如 web-researcher 角色上限为 safe）
    #   - risk_level: 具体工具调用的实际风险等级，驱动前端审批 UI 显示
    # 根因修复：原 tool_call_* 事件 payload 不携带 risk_level，非触发浏览器依赖
    # approval_pending 事件获取风险等级，事件丢失时 riskLevel 缺失导致跨浏览器显示不一致
    risk_level: str = ""


class ToolCallLifecycleService:
    """工具调用生命周期服务（模块级单例，三模块共享）。

    通过 Redis 持久化上下文，确保：
    - 跨请求/跨 worker 状态一致（Celery worker 重启后仍可恢复）
    - 跨模块共享同一份状态机代码
    - parameters/message_id 从 context 透传，杜绝字段缺失
    - 所有模块工具状态变更自动持久化到 ChatMessage.tool_calls（内置，无需注册）
    """

    @staticmethod
    def _persist_tool_state(
        ctx_dict: dict,
        event_type: EventType,
        result: Any = None,
        error: str | None = None,
    ) -> None:
        """工具终态持久化（所有模块自动生效，transition/transition_async 内置调用）。

        仅持久化终态（COMPLETED / FAILED）到 ChatMessage.tool_calls。
        PENDING / RUNNING 不触发持久化：
        - 瞬态通过 WS 实时广播即可，无需写库
        - 避免 fire-and-forget 乱序竞态：多个 PENDING→COMPLETED 任务交叉完成
          时，后完成的 PENDING 可能在 COMPLETED 之后 append，破坏 tool_calls 顺序
        - agent 模式依赖 persist_stream_result（finally 块）做全量持久化兜底
        - 深度研究模式依赖 Writeback 合并链弥补缺失的 PENDING 条目

        通过 asyncio.get_running_loop() 检测运行上下文：
        - async 上下文（transition_async / agent mode SSE）：fire-and-forget
          经由 sync_to_async 写库，不阻塞事件发布
        - sync 上下文（transition 同步调用）：直接写库

        异常仅 logger.warning，不阻塞 transition。
        """
        tool_call_id = ctx_dict.get("tool_call_id", "")
        if not tool_call_id:
            return
        module_str = ctx_dict.get("module", "")
        module_id = ctx_dict.get("module_id", "")
        if not module_str or not module_id:
            return

        # 仅终态需要持久化（PENDING/RUNNING 不写库，避免乱序竞态）
        TERMINAL_STATUS_MAP = {
            EventType.TOOL_CALL_COMPLETED: "completed",
            EventType.TOOL_CALL_FAILED: "failed",
        }
        status = TERMINAL_STATUS_MAP.get(event_type)
        if not status:
            return

        tool_name = ctx_dict.get("tool_name", "unknown")

        def _do_persist():
            try:
                sync_tool_result_to_chat_message(
                    tool_call_id, tool_name, status, result, error, module_str, module_id
                )
            except Exception:
                logger.warning(
                    f"[ToolCallLifecycle] 持久化工具状态失败: "
                    f"tool_call_id={tool_call_id}, status={status}, module={module_str}",
                    exc_info=True,
                )

        try:
            import asyncio as _asyncio
            _asyncio.get_running_loop()
            # async 上下文：fire-and-forget 写库，不阻塞事件发布
            from asgiref.sync import sync_to_async as _stoa
            _asyncio.ensure_future(_stoa(_do_persist, thread_sensitive=True)())
        except RuntimeError:
            # sync 上下文：直接调用
            _do_persist()

    def register(
        self,
        ctx: ToolCallContext,
        *,
        event_type: EventType | None = None,
    ) -> None:
        """注册工具调用上下文（首次发现 tool_call 时调用）。幂等。

        已存在时不覆盖非空字段（保留先注册的值），仅补全空字段。
        auto_approved 一旦为 True 就保持 True（不会被覆盖回 False）。
        子 agent 嵌套层级字段（parent_tool_call_id/depth/agent_name/agent_path/risk_ceiling）
        一旦写入非空值就保持（不被覆盖回空），确保主 agent 与子 agent 场景的字段不互斥。
        graph_interrupt_id 是例外：作为审批批次标识允许更新为新批次（M16 复用
        interrupt_id 重新发起审批时携带新批次 id，保留旧值会导致后续 WAITING/RUNNING
        事件沿用旧批次指纹，被旧批次 dedup key 误拦截）。

        Args:
            ctx: 工具调用上下文
            event_type: 本次注册对应的待发布事件类型（可选）。
                last_event_type 防护（Task 1）：当 context 已存在且 last_event_type
                已推进到 waiting/running 等非 PENDING 状态，而本次注册为
                TOOL_CALL_PENDING（同 tool_call_id 的重复发布入口）时，拒绝本次
                注册并 log warning（说明重复发布来源），不覆盖已推进状态。
                None 表示仅补全上下文（不触发防护）。
        """
        key = f"{_TC_CTX_PREFIX}:{ctx.tool_call_id}"
        existing = cache.get(key)
        if existing:
            # last_event_type 防护：状态已推进到非 PENDING 状态时拒绝重复 PENDING 注册
            # （与状态机"waiting → pending 非法"一致，提前拦截并记录重复发布入口）
            last_event = existing.get("last_event_type")
            if (
                event_type == EventType.TOOL_CALL_PENDING
                and last_event
                and last_event != EventType.TOOL_CALL_PENDING.value
            ):
                logger.warning(
                    f"[ToolCallLifecycle] 拒绝重复 PENDING 注册（状态已推进）: "
                    f"tool_call_id={ctx.tool_call_id}, tool_name={ctx.tool_name}, "
                    f"last_event_type={last_event}, 本次注册将被忽略（重复发布入口）"
                )
                return
            # 合并：仅补全空字段，不覆盖已有非空值
            if not existing.get("parameters") and ctx.parameters:
                existing["parameters"] = ctx.parameters
            if not existing.get("message_id") and ctx.message_id:
                existing["message_id"] = ctx.message_id
            # graph_interrupt_id：批次标识允许更新为新批次（M16 重审批防护，见 docstring）
            if ctx.graph_interrupt_id and existing.get("graph_interrupt_id") != ctx.graph_interrupt_id:
                existing["graph_interrupt_id"] = ctx.graph_interrupt_id
            if not existing.get("cross_module_id") and ctx.cross_module_id:
                existing["cross_module_id"] = ctx.cross_module_id
            # auto_approved：一旦为 True 就保持（不被覆盖回 False）
            if ctx.auto_approved and not existing.get("auto_approved"):
                existing["auto_approved"] = True
            # 子 agent 嵌套层级字段：仅补全空字段（主 agent 不传，子 agent 传入非空值）
            if not existing.get("parent_tool_call_id") and ctx.parent_tool_call_id:
                existing["parent_tool_call_id"] = ctx.parent_tool_call_id
            if not existing.get("depth") and ctx.depth:
                existing["depth"] = ctx.depth
            if not existing.get("agent_name") and ctx.agent_name:
                existing["agent_name"] = ctx.agent_name
            if not existing.get("agent_path") and ctx.agent_path:
                existing["agent_path"] = ctx.agent_path
            if not existing.get("risk_ceiling") and ctx.risk_ceiling:
                existing["risk_ceiling"] = ctx.risk_ceiling
            # risk_level：仅补全空字段（审批创建时由 ApprovalMiddleware 计算并传入）
            # 一旦写入非空值就保持，避免后续事件覆盖已计算的风险等级
            if not existing.get("risk_level") and ctx.risk_level:
                existing["risk_level"] = ctx.risk_level
            cache.set(key, existing, _TC_CTX_TTL)
        else:
            cache.set(key, ctx.__dict__, _TC_CTX_TTL)

    def bind_parameters(self, tool_call_id: str, parameters: dict) -> None:
        """补全工具参数（finalize_tool_calls 参数恢复后调用）。"""
        if not isinstance(parameters, dict) or not parameters:
            return
        key = f"{_TC_CTX_PREFIX}:{tool_call_id}"
        ctx = cache.get(key) or {}
        ctx["parameters"] = parameters
        cache.set(key, ctx, _TC_CTX_TTL)

    def bind_message_id(self, tool_call_id: str, message_id: str) -> None:
        """补全消息 ID（消息持久化后调用）。仅补全空字段，不覆盖。"""
        if not message_id:
            return
        key = f"{_TC_CTX_PREFIX}:{tool_call_id}"
        ctx = cache.get(key) or {}
        if not ctx.get("message_id"):
            ctx["message_id"] = str(message_id)
            cache.set(key, ctx, _TC_CTX_TTL)

    def bind_graph_interrupt_id(self, tool_call_id: str, graph_interrupt_id: str) -> None:
        """补全批量审批批次 ID（ApprovalMiddleware 创建审批时调用）。"""
        if not graph_interrupt_id:
            return
        key = f"{_TC_CTX_PREFIX}:{tool_call_id}"
        ctx = cache.get(key) or {}
        if not ctx.get("graph_interrupt_id"):
            ctx["graph_interrupt_id"] = graph_interrupt_id
            cache.set(key, ctx, _TC_CTX_TTL)

    def _prepare_transition(
        self,
        tool_call_id: str,
        event_type: EventType,
        parameters: dict | None,
        batch_fingerprint: str | None = None,
    ) -> tuple[dict[str, Any], str] | None:
        """状态机转换的公共预处理逻辑（sync/async 共享）。

        流程：
        1. 读取 context（不存在则 warning 并跳过）
        2. 计算批次指纹并去重检查（同 tool_call_id + event_type + 批次指纹 已发布则跳过，
           PENDING 同样参与去重，在状态校验之前拦截同批次重复发布）
        3. 校验状态转换合法性（非法则 warning 并阻止发布）
        4. 补全 parameters（如果调用方提供了非空 parameters）

        Args:
            batch_fingerprint: 调用方显式指定的批次指纹（可选，如审批流程直接传入
                graph_interrupt_id）；为空时按 _compute_batch_fingerprint 推导。

        Returns:
            (context_dict, fingerprint)：应该继续发布事件时返回二元组；
            None 表示跳过（上下文缺失 / 已去重 / 非法转换）
        """
        key = f"{_TC_CTX_PREFIX}:{tool_call_id}"
        ctx_dict = cache.get(key)
        if not ctx_dict:
            logger.warning(
                f"[ToolCallLifecycle] 上下文不存在，跳过事件发布: "
                f"tool_call_id={tool_call_id}, event_type={event_type.value}"
            )
            return None

        # 批次指纹：调用方显式传入优先，否则按（事件类型 + 上下文 + 本次参数）推导
        fingerprint = batch_fingerprint or _compute_batch_fingerprint(event_type, ctx_dict, parameters)
        # 去重：同 tool_call_id + event_type + 批次指纹 已发布则跳过
        # （PENDING 同样参与去重，在状态校验之前拦截同批次重复发布）
        dedup_key = f"{_PUBLISHED_KEY_PREFIX}:{tool_call_id}:{event_type.value}:{fingerprint}"
        if cache.get(dedup_key):
            logger.info(
                f"[ToolCallLifecycle] 事件已发布（同批次指纹），跳过: "
                f"tool_call_id={tool_call_id}, event_type={event_type.value}, fingerprint={fingerprint}"
            )
            return None

        # 状态机校验：非法转换阻止发布
        last_event = ctx_dict.get("last_event_type")
        last_event_enum = EventType.from_value(last_event) if last_event else None
        valid_prev = _VALID_TRANSITIONS.get(event_type, set())
        if last_event_enum not in valid_prev:
            logger.warning(
                f"[ToolCallLifecycle] 非法状态转换，阻止发布: "
                f"tool_call_id={tool_call_id}, {last_event} → {event_type.value}, "
                f"allowed_prev={[e.value if e else None for e in valid_prev]}"
            )
            return None

        # 补全 parameters（如果调用方提供了非空 parameters）
        if parameters and isinstance(parameters, dict):
            self.bind_parameters(tool_call_id, parameters)
            ctx_dict = cache.get(key) or ctx_dict

        return ctx_dict, fingerprint

    def _finalize_transition(
        self,
        tool_call_id: str,
        event_type: EventType,
        ctx_dict: dict[str, Any],
        fingerprint: str,
    ) -> None:
        """状态机转换的公共后处理逻辑（sync/async 共享）。

        标记已发布（含批次指纹），更新 last_event_type。
        """
        key = f"{_TC_CTX_PREFIX}:{tool_call_id}"
        ctx_dict["last_event_type"] = event_type.value
        cache.set(key, ctx_dict, _TC_CTX_TTL)
        dedup_key = f"{_PUBLISHED_KEY_PREFIX}:{tool_call_id}:{event_type.value}:{fingerprint}"
        cache.set(dedup_key, 1, _TC_CTX_TTL)

    def transition(
        self,
        tool_call_id: str,
        event_type: EventType,
        *,
        result: Any = None,
        error: str | None = None,
        parameters: dict | None = None,
        batch_fingerprint: str | None = None,
        _index: int | None = None,
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
            batch_fingerprint: 批次指纹（可选，显式传入时跳过自动推导，
                如审批流程直接传入 graph_interrupt_id）
            _index: LLM 生成的工具调用原始序号（用于跨浏览器工具顺序稳定排序）
        """
        prepared = self._prepare_transition(tool_call_id, event_type, parameters, batch_fingerprint)
        if prepared is None:
            return
        ctx_dict, fingerprint = prepared

        # 从 context 透传所有字段（三模块共享逻辑）
        module_value = ctx_dict.get("module", "chat")
        module_enum = EventSource.from_value(module_value) or EventSource.CHAT

        try:
            publish_tool_call_sync(
                event_type=event_type,
                tool_call_id=tool_call_id,
                tool_name=ctx_dict.get("tool_name", "unknown"),
                module=module_enum,
                module_id=ctx_dict.get("module_id", ""),
                message_id=ctx_dict.get("message_id", ""),
                parameters=ctx_dict.get("parameters", {}),
                cross_module_id=ctx_dict.get("cross_module_id"),
                graph_interrupt_id=ctx_dict.get("graph_interrupt_id"),
                result=result,
                error=error,
                auto_approved=ctx_dict.get("auto_approved", False),
                # 子 agent 嵌套层级字段（Phase E3）：从 context 透传到事件 payload
                # 主 agent 字段为空/0 时统一规范化为 None，保持 publish_tool_call
                # 签名语义一致（主 agent 不携带嵌套字段），payload 仅含非空字段
                parent_tool_call_id=ctx_dict.get("parent_tool_call_id") or None,
                depth=(
                    ctx_dict.get("depth")
                    if isinstance(ctx_dict.get("depth"), int) and ctx_dict.get("depth") > 0
                    else None
                ),
                agent_name=ctx_dict.get("agent_name") or None,
                agent_path=ctx_dict.get("agent_path") or None,
                risk_ceiling=_normalize_risk_ceiling(ctx_dict.get("risk_ceiling")),
                # risk_level 透传：从 context 读取，注入到 tool_call_* 事件 payload
                # 根因修复：让前端从工具事件直接获取风险等级，不再单一依赖 approval_pending 事件
                risk_level=_normalize_risk_level(ctx_dict.get("risk_level")),
                _index=_index if _index is not None else ctx_dict.get("_index"),
            )
        except Exception:
            logger.exception(
                f"[ToolCallLifecycle] 发布事件失败: tool_call_id={tool_call_id}, event_type={event_type.value}, err="
            )
            return

        self._finalize_transition(tool_call_id, event_type, ctx_dict, fingerprint)

        # 工具状态持久化：所有模块自动生效，无需注册
        ToolCallLifecycleService._persist_tool_state(ctx_dict, event_type, result, error)

    async def transition_async(
        self,
        tool_call_id: str,
        event_type: EventType,
        *,
        result: Any = None,
        error: str | None = None,
        parameters: dict | None = None,
        batch_fingerprint: str | None = None,
        _index: int | None = None,
    ) -> None:
        """状态机转换并发布事件（异步版，适配 async 上下文：research/learning 模块）。

        内部 await publish_tool_call，确保事件按调用顺序发布（避免 fire-and-forget 顺序错乱）。

        Args:
            tool_call_id: 工具调用 ID
            event_type: 目标事件类型
            result: 工具执行结果（仅 COMPLETED 事件）
            error: 错误信息（仅 FAILED 事件）
            parameters: 补全的参数（可选，用于参数恢复场景）
            batch_fingerprint: 批次指纹（可选，显式传入时跳过自动推导，
                如审批流程直接传入 graph_interrupt_id）
            _index: LLM 生成的工具调用原始序号（用于跨浏览器工具顺序稳定排序）
        """
        prepared = self._prepare_transition(tool_call_id, event_type, parameters, batch_fingerprint)
        if prepared is None:
            return
        ctx_dict, fingerprint = prepared

        module_value = ctx_dict.get("module", "chat")
        module_enum = EventSource.from_value(module_value) or EventSource.CHAT

        try:
            await publish_tool_call(
                event_type,
                tool_call_id=tool_call_id,
                tool_name=ctx_dict.get("tool_name", "unknown"),
                module=module_enum,
                module_id=ctx_dict.get("module_id", ""),
                message_id=ctx_dict.get("message_id", ""),
                parameters=ctx_dict.get("parameters", {}),
                cross_module_id=ctx_dict.get("cross_module_id"),
                graph_interrupt_id=ctx_dict.get("graph_interrupt_id"),
                result=result,
                error=error,
                auto_approved=ctx_dict.get("auto_approved", False),
                # 子 agent 嵌套层级字段（Phase E3）：从 context 透传到事件 payload
                # 主 agent 字段为空/0 时统一规范化为 None，保持 publish_tool_call
                # 签名语义一致（主 agent 不携带嵌套字段），payload 仅含非空字段
                parent_tool_call_id=ctx_dict.get("parent_tool_call_id") or None,
                depth=(
                    ctx_dict.get("depth")
                    if isinstance(ctx_dict.get("depth"), int) and ctx_dict.get("depth") > 0
                    else None
                ),
                agent_name=ctx_dict.get("agent_name") or None,
                agent_path=ctx_dict.get("agent_path") or None,
                risk_ceiling=_normalize_risk_ceiling(ctx_dict.get("risk_ceiling")),
                # risk_level 透传：从 context 读取，注入到 tool_call_* 事件 payload
                # 根因修复：让前端从工具事件直接获取风险等级，不再单一依赖 approval_pending 事件
                risk_level=_normalize_risk_level(ctx_dict.get("risk_level")),
                _index=_index if _index is not None else ctx_dict.get("_index"),
            )
        except Exception:
            logger.exception(
                f"[ToolCallLifecycle] 发布事件失败(async): "
                f"tool_call_id={tool_call_id}, event_type={event_type.value}, err="
            )
            return

        self._finalize_transition(tool_call_id, event_type, ctx_dict, fingerprint)

        # 工具状态持久化：所有模块自动生效，无需注册
        ToolCallLifecycleService._persist_tool_state(ctx_dict, event_type, result, error)

    def get_context(self, tool_call_id: str) -> dict | None:
        """获取工具调用上下文（调试/测试用）。"""
        return cache.get(f"{_TC_CTX_PREFIX}:{tool_call_id}")

    def clear_context(self, tool_call_id: str) -> None:
        """清除工具调用上下文（工具进入终态后可调用，释放 Redis 空间）。"""
        cache.delete(f"{_TC_CTX_PREFIX}:{tool_call_id}")
        # 清除 legacy（无指纹）去重 key；指纹去重 key（tool_call:published:{id}:{type}:{fp}）
        # 无法枚举全部指纹，依赖 24h TTL 自动过期（_TC_CTX_TTL）
        for event_type in EventType:
            if event_type.value.startswith("tool_call_"):
                cache.delete(f"{_PUBLISHED_KEY_PREFIX}:{tool_call_id}:{event_type.value}")


def sync_tool_result_to_chat_message(
    tool_call_id: str,
    tool_name: str,
    status: str,
    result: Any = None,
    error: str | None = None,
    module: str = "",
    module_id: str = "",
) -> bool:
    """工具终态持久化统一入口（所有模块共用）。

    在 COMPLETED/FAILED 事件发布后调用，直接写入 ChatMessage.tool_calls。
    使用 _merge_tool_calls_incremental 做字段级演进（非终态 → 终态，终态不可回退）。

    Args:
        tool_call_id: 工具调用 ID
        tool_name: 工具名称
        status: 终态（completed / failed）
        result: 输出结果（COMPLETED）
        error: 错误信息（FAILED）
        module: 业务模块（chat / deep_research / learning）
        module_id: 模块实例 ID（session_id / task_id / thread_id）

    Returns:
        bool: 是否成功写库
    """
    from django.apps import apps
    from django.db import transaction

    ChatMessage = apps.get_model("chat", "ChatMessage")

    # 用 select_for_update + transaction.atomic 序列化同一 ChatMessage 的并发写入，
    # 消除 read-modify-write 竞态（多个 PENDING/COMPLETED 写交叉 append 破坏顺序）。
    try:
        with transaction.atomic():
            if module == "deep_research" and module_id:
                chat_msg = (
                    ChatMessage.objects.select_for_update()
                    .filter(
                        research_task_id=module_id,
                        role="assistant",
                        is_deleted=False,
                    )
                    .order_by("-created_at")
                    .first()
                )
            elif module_id:
                chat_msg = (
                    ChatMessage.objects.select_for_update()
                    .filter(
                        session__session_id=module_id,
                        role="assistant",
                        is_deleted=False,
                    )
                    .order_by("-created_at")
                    .first()
                )
            else:
                return False

            if chat_msg is None:
                logger.info(
                    f"[ToolCallLifecycle] sync_tool_result_to_chat_message 跳过（未找到关联 ChatMessage）: "
                    f"tool_call_id={tool_call_id}, module={module}, module_id={module_id}"
                )
                return False

            tool_entry: dict[str, Any] = {
                "id": tool_call_id,
                "tool_call_id": tool_call_id,
                "name": tool_name,
                "status": status,
            }
            if result is not None:
                tool_entry["result"] = result
            if error is not None:
                tool_entry["error"] = error

            from Django_xm.apps.chat.services.stream_persistence import _merge_tool_calls_incremental

            existing = chat_msg.tool_calls or []
            merged = _merge_tool_calls_incremental(existing, [tool_entry])

            if merged != existing:
                chat_msg.tool_calls = merged
                chat_msg.save(update_fields=["tool_calls", "updated_at"])
                logger.info(
                    f"[ToolCallLifecycle] sync_tool_result_to_chat_message 已持久化: "
                    f"msg={chat_msg.id}, tool_call_id={tool_call_id}, status={status}, "
                    f"module={module}"
                )
            return True
    except Exception as e:
        logger.warning(
            f"[ToolCallLifecycle] sync_tool_result_to_chat_message 持久化失败: "
            f"tool_call_id={tool_call_id}, module={module}, module_id={module_id}, err={e}"
        )
        return False


# 模块级单例
service = ToolCallLifecycleService()
