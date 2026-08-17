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
        # md5 仅用于参数内容去重指纹（非安全场景），无防碰撞要求
        return hashlib.md5(payload.encode("utf-8")).hexdigest()[:12]  # noqa: S324
    except Exception:
        # 极端不可序列化场景退化为 repr（仍内容相关、稳定），避免指纹计算中断发布
        try:
            # 同上，非安全去重指纹用途
            return hashlib.md5(repr(parameters).encode("utf-8")).hexdigest()[:12]  # noqa: S324
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

    子 agent 嵌套层级字段（Phase E3，由 subagent_support.py 注入到 configurable，
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
    # 子 agent 角色描述（任务目标，Task 2.4，取自 deep_builder SubAgent 静态
    # description，仅子 agent 工具事件携带；主 agent 为空）。透传到 tool_call_*
    # 事件 payload，前端 ToolCallGroup 组头展示任务目标描述。
    description: str = ""
    # 子代理 thread_id（spec D10 SSE 定向推送路由标识符）。子代理工具事件携带，
    # 主 agent 为空；透传到事件顶层字段（非 payload），前端据此路由到子代理卡片。
    subagent_thread_id: str = ""
    # 全局递增序号（register 唯一分配点，按 (module, module_id) 独立计数）。
    # 同一会话/任务内跨 LLM 轮次全局唯一，作为前端跨浏览器工具调用统一排序的
    # 唯一权威依据（替代跨轮重复的 _index：_index 是 LLM 单轮输出内序号，跨轮
    # 会重复，排序 key 相等时退化为各浏览器本地 Map 插入顺序，导致顺序不一致）。
    seq: int = 0
    # position：工具调用在该图层自身正文中的字符偏移（该图层已输出 content 长度）。
    # 按图层局部化（Agent 图层嵌套规范 D3）：每个 agent 图层独立累计自身正文，
    # 子层正文不计入父层累计。首次 PENDING 注册时由执行层采集写入，之后永久不变
    # （后续 content 增长、工具回调返回均禁止改写）——merge 策略 keep_existing 保证。
    position: int | None = None


def _is_empty(value: Any) -> bool:
    """fill_empty 合并策略的空值判定。

    空值定义：None、空串、空 list/dict、False、int 0。
    注意 int 0 被判定为空（如 depth=0 表示主 agent，允许被子 agent 的非零 depth 覆盖）。
    """
    if value is None:
        return True
    if isinstance(value, str) and value == "":
        return True
    if isinstance(value, (list, dict)) and len(value) == 0:
        return True
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value == 0
    return False


# register() 全字段合并策略
# 每个 ToolCallContext 字段的合并语义：
#   skip:         不合并（主键字段 tool_call_id）
#   fill_empty:   已存在为空时用新值补全（默认策略）
#   keep_existing: 始终保留已存在值（如 last_event_type，由状态机管理）
#   allow_update:  允许新值覆盖已有值（如 graph_interrupt_id 批次更新）
#   once_true:     一旦为 True/非空永不回退（如 auto_approved）
_FIELD_MERGE_POLICY: dict[str, str] = {
    "tool_call_id":      "skip",
    "tool_name":         "fill_empty",
    "module":            "fill_empty",
    "module_id":         "fill_empty",
    "cross_module_id":   "fill_empty",
    "message_id":        "fill_empty",
    "parameters":        "fill_empty",
    "graph_interrupt_id":"allow_update",
    "last_event_type":   "keep_existing",
    "auto_approved":     "once_true",
    "parent_tool_call_id":"fill_empty",
    "depth":             "fill_empty",
    "agent_name":        "fill_empty",
    "agent_path":        "fill_empty",
    "risk_ceiling":      "fill_empty",
    "risk_level":        "fill_empty",
    "description":       "fill_empty",
    "subagent_thread_id":"fill_empty",
    "seq":               "fill_empty",
    # position 首次 PENDING 写入后永久不变（后续状态事件、回调返回均不覆盖）
    "position":          "keep_existing",
}


class ToolCallLifecycleService:
    """工具调用生命周期服务（模块级单例，三模块共享）。

    通过 Redis 持久化上下文，确保：
    - 跨请求/跨 worker 状态一致（Celery worker 重启后仍可恢复）
    - 跨模块共享同一份状态机代码
    - parameters/message_id 从 context 透传，杜绝字段缺失
    """

    def _assign_seq(self, module: EventSource, module_id: str) -> int:
        """为同一 (module, module_id) 内的工具调用分配全局递增序号（跨轮唯一）。

        seq 计数维度按模块实例隔离：chat=session_id、deep_research=task_id，
        同一会话/任务内工具调用序号全局递增（跨 LLM 轮次不重复）；不同会话/任务
        互不影响。seq 是前端跨浏览器工具调用统一排序的唯一权威依据。

        Args:
            module: 业务模块（EventSource）
            module_id: 模块实例 ID

        Returns:
            递增序号（从 1 开始）
        """
        counter_key = f"tool_call:seq:{module.value}:{module_id}"
        try:
            seq = cache.incr(counter_key)
            if seq == 1:
                cache.expire(counter_key, _TC_CTX_TTL)
            return seq
        except ValueError:
            # key 不存在：首次分配（INCR 在 Redis key 不存在时由 django cache 抛 ValueError）
            cache.set(counter_key, 1, _TC_CTX_TTL)
            return 1

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
        注：agent_path 仅由审批链路（ApprovalMiddleware 审计注册）写入，供 retry
        归属匹配与展示恢复读取；不再透传到事件 payload（spec REMOVED）。
        graph_interrupt_id 是例外：作为审批批次标识允许更新为新批次（M16 复用
        interrupt_id 重新发起审批时携带新批次 id，保留旧值会导致后续 WAITING/RUNNING
        事件沿用旧批次指纹，被旧批次 dedup key 误拦截）。

        seq 分配（register 是唯一权威分配点）：existing 与新 ctx 均保证在写回前
        分配非空 seq。fill_empty 合并策略 + _is_empty 将 int 0 判为空，保证：
        - 已分配 seq（>0）的条目不会被后续 register 的默认 seq=0 覆盖；
        - 首次 register 时分配 seq 并持久化，所有下游事件（工具/审批）从 ctx 透传。

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
            # seq 分配：existing 缺失 seq 时补全（register 唯一权威分配点）
            if _is_empty(existing.get("seq")):
                existing["seq"] = self._assign_seq(ctx.module, ctx.module_id)
            # 全字段合并：由 _FIELD_MERGE_POLICY 驱动，替代手选字段列表。
            # 每个 ToolCallContext 字段的策略定义在模块级 _FIELD_MERGE_POLICY 中，
            # 新增字段只需在 dataclass 和 policy 各加一行，无需修改合并逻辑。
            ctx_dict = ctx.__dict__
            for field_name, new_val in ctx_dict.items():
                if field_name == "tool_call_id":
                    continue  # 主键跳过

                policy = _FIELD_MERGE_POLICY.get(field_name, "fill_empty")
                if policy in ("keep_existing", "skip"):
                    continue
                if policy == "fill_empty":
                    if _is_empty(existing.get(field_name)) and not _is_empty(new_val):
                        existing[field_name] = new_val
                elif policy == "allow_update":
                    if new_val is not None and new_val != existing.get(field_name):
                        existing[field_name] = new_val
                elif policy == "once_true":
                    if new_val and not existing.get(field_name):
                        existing[field_name] = new_val

            cache.set(key, existing, _TC_CTX_TTL)
        else:
            ctx_dict = dict(ctx.__dict__)
            # seq 分配：新建上下文首次分配（register 唯一权威分配点）
            if _is_empty(ctx_dict.get("seq")):
                ctx_dict["seq"] = self._assign_seq(ctx.module, ctx.module_id)
            cache.set(key, ctx_dict, _TC_CTX_TTL)

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

    def bind_position(self, tool_call_id: str, position: int | None) -> None:
        """补全工具调用的图层内 position（执行层在首次 PENDING 事件前调用）。

        Agent 图层嵌套规范 D3（position 单一权威来源，按图层局部化）：
        position 取值必须取触发该工具调用瞬间该图层自身已输出 content 长度；
        一旦写入永久不变（后续 content 增长、工具回调返回均禁止改写）。
        仅补全空字段（None 不覆盖已有值），配合 _FIELD_MERGE_POLICY 的
        keep_existing 策略保证 position 不可变。
        """
        if position is None or not isinstance(position, int) or position < 0:
            return
        key = f"{_TC_CTX_PREFIX}:{tool_call_id}"
        ctx = cache.get(key) or {}
        if ctx.get("position") is None:
            ctx["position"] = position
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
                risk_ceiling=_normalize_risk_ceiling(ctx_dict.get("risk_ceiling")),
                # risk_level 透传：从 context 读取，注入到 tool_call_* 事件 payload
                # 根因修复：让前端从工具事件直接获取风险等级，不再单一依赖 approval_pending 事件
                risk_level=_normalize_risk_level(ctx_dict.get("risk_level")),
                # description 透传：子 agent 角色描述（任务目标），仅子 agent 事件携带
                description=ctx_dict.get("description") or None,
                _index=_index if _index is not None else ctx_dict.get("_index"),
                # seq 透传：从 context 读取，注入到 tool_call_* 事件 payload
                # 根因修复：seq 为 (module, module_id) 内跨 LLM 轮次全局递增序号，
                # 前端据此跨浏览器统一排序（替代跨轮重复的 _index）
                seq=ctx_dict.get("seq") or None,
                # position 透传：工具调用在该图层自身正文中的字符偏移（按图层局部化），
                # 首次 PENDING 写入后永久不变（keep_existing + bind_position 保证）
                position=ctx_dict.get("position"),
                # subagent_thread_id 透传：子代理 SSE 定向推送路由标识符（spec D10）
                subagent_thread_id=ctx_dict.get("subagent_thread_id") or None,
            )
        except Exception:
            logger.exception(
                f"[ToolCallLifecycle] 发布事件失败: tool_call_id={tool_call_id}, event_type={event_type.value}, err="
            )
            return

        self._finalize_transition(tool_call_id, event_type, ctx_dict, fingerprint)

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
                risk_ceiling=_normalize_risk_ceiling(ctx_dict.get("risk_ceiling")),
                # risk_level 透传：从 context 读取，注入到 tool_call_* 事件 payload
                # 根因修复：让前端从工具事件直接获取风险等级，不再单一依赖 approval_pending 事件
                risk_level=_normalize_risk_level(ctx_dict.get("risk_level")),
                # description 透传：子 agent 角色描述（任务目标），仅子 agent 事件携带
                description=ctx_dict.get("description") or None,
                _index=_index if _index is not None else ctx_dict.get("_index"),
                # seq 透传：从 context 读取，注入到 tool_call_* 事件 payload
                # 根因修复：seq 为 (module, module_id) 内跨 LLM 轮次全局递增序号，
                # 前端据此跨浏览器统一排序（替代跨轮重复的 _index）
                seq=ctx_dict.get("seq") or None,
                # position 透传：工具调用在该图层自身正文中的字符偏移（按图层局部化），
                # 首次 PENDING 写入后永久不变（keep_existing + bind_position 保证）
                position=ctx_dict.get("position"),
                # subagent_thread_id 透传：子代理 SSE 定向推送路由标识符（spec D10）
                subagent_thread_id=ctx_dict.get("subagent_thread_id") or None,
            )
        except Exception:
            logger.exception(
                f"[ToolCallLifecycle] 发布事件失败(async): "
                f"tool_call_id={tool_call_id}, event_type={event_type.value}, err="
            )
            return

        self._finalize_transition(tool_call_id, event_type, ctx_dict, fingerprint)

    def get_context(self, tool_call_id: str) -> dict | None:
        """获取工具调用上下文（调试/测试用）。"""
        return cache.get(f"{_TC_CTX_PREFIX}:{tool_call_id}")

    def enrich_entry_seq(self, entry: dict, tool_call_id: str) -> dict:
        """为工具调用条目补全 seq（所有持久化/重建出口的统一权威补全点）。

        从 ToolCallContext 读取 register 分配的全局递增序号（与 WebSocket
        tool_call_* 事件透传同一来源），写入 ``entry["seq"]``。所有构建 tool_call
        条目的出口（sse_generator tool_calls_map、stream_persistence 持久化、
        approval_service 审批重建与 Approval.extra）统一调用本方法，杜绝各消费点
        重复内联逻辑导致漏补。context 缺失或 seq 非正整数时保持 entry 不变
        （由调用方数组顺序兜底）。

        Args:
            entry: 工具调用条目 dict（原地修改）
            tool_call_id: 工具调用 ID

        Returns:
            原 entry（支持链式调用）
        """
        if not isinstance(entry, dict):
            return entry
        ctx = self.get_context(tool_call_id)
        seq = (ctx or {}).get("seq")
        if isinstance(seq, int) and seq > 0:
            entry["seq"] = seq
        return entry

    def enrich_entry_parameters(self, entry: dict, tool_call_id: str) -> dict:
        """为工具调用条目补全参数（流式 extractor 参数缺失时从 ctx 权威源兜底）。

        流式 extractor（process_stream_chunk）依赖 tool_call_chunks 携带完整 args
        才能将参数写入 tool_calls_map；部分模型（如 DeepSeek）流式返回工具调用时
        args 为空串（实证 args_str=''），map 条目 parameters 恒为 {}。而
        ApprovalMiddleware（aafter_model）注册 ToolCallContext 时已持有完整参数
        （实证 "参数完整"），此处从 context 读取补全——仅当 entry 参数为空时补全，
        避免覆盖 extractor 已解析的参数。

        Args:
            entry: 工具调用条目 dict（原地修改）
            tool_call_id: 工具调用 ID

        Returns:
            原 entry（支持链式调用）
        """
        if not isinstance(entry, dict):
            return entry
        existing = entry.get("parameters")
        if isinstance(existing, dict) and existing:
            return entry
        ctx = self.get_context(tool_call_id)
        ctx_params = (ctx or {}).get("parameters")
        if isinstance(ctx_params, dict) and ctx_params:
            entry["parameters"] = ctx_params
        return entry

    def enrich_entry_position(self, entry: dict, tool_call_id: str) -> dict:
        """为工具调用条目补全图层内 position（所有持久化/重建出口的统一权威补全点）。

        Agent 图层嵌套规范 D3：position 由执行层在 tool 事件出口经 bind_position
        写入 ToolCallContext（keep_existing 策略保证不可变），此处从 context 读取
        写入 ``entry["position"]``，与 WS tool_call_* 事件透传同一来源。
        所有构建 tool_call 条目的出口（stream_persistence 持久化、approval_service
        审批重建与 Approval.extra）统一调用本方法，保证刷新后前端可依据 position
        恢复图层内联布局（缺 position 的工具卡会确定性排在图层末尾，视觉上表现为
        "工具乱序/不内联"）。

        Args:
            entry: 工具调用条目 dict（原地修改）
            tool_call_id: 工具调用 ID

        Returns:
            原 entry（支持链式调用）
        """
        if not isinstance(entry, dict):
            return entry
        ctx = self.get_context(tool_call_id)
        position = (ctx or {}).get("position")
        if isinstance(position, int) and position >= 0:
            entry["position"] = position
        return entry

    def clear_context(self, tool_call_id: str) -> None:
        """清除工具调用上下文（工具进入终态后可调用，释放 Redis 空间）。"""
        cache.delete(f"{_TC_CTX_PREFIX}:{tool_call_id}")
        # 清除 legacy（无指纹）去重 key；指纹去重 key（tool_call:published:{id}:{type}:{fp}）
        # 无法枚举全部指纹，依赖 24h TTL 自动过期（_TC_CTX_TTL）
        for event_type in EventType:
            if event_type.value.startswith("tool_call_"):
                cache.delete(f"{_PUBLISHED_KEY_PREFIX}:{tool_call_id}:{event_type.value}")


# 模块级单例
service = ToolCallLifecycleService()
