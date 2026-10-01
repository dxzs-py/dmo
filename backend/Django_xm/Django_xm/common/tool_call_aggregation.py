"""工具调用条目聚合与子代理正文累计（chat 主/子代理 + research 三链路唯一实现）。

将原 chat 子代理（``chat_service._handle_subagent_tool_event``，经 configurable
回调 ``_on_subagent_tool_event``）、chat 主链路（``stream/loop.py``）与 research
（``adapter._publish_tool_event``）各自内联的「事件 → 落库条目聚合」逻辑收敛为
本模块唯一实现；当前唯一调用入口为 ``common.tool_event_publisher``
``publish_tool_lifecycle_event`` 第 4 步。子代理正文累计 + msg_id 幂等去重同样收敛于此。

设计边界：
- ``aggregate_tool_entry`` 只负责单条 entry 的字段演进（默认结构、终态保护、
  图层字段、seq/position 经 lifecycle enrich_entry_seq/position 从 ToolCallContext
  权威源补齐），不关心模块/source 差异。
- ``merge_subagent_content`` 只负责正文累计 + 幂等去重，返回是否重复；
  广播（publish_event）由各自入口处理（source/task_id 路由差异保留在入口）。
- 状态映射常量、终态集合为全项目唯一权威，chat / research / 持久化层统一引用。
"""

from __future__ import annotations

from typing import Any

from Django_xm.common.event_schema import TERMINAL_TOOL_CALL_EVENTS

# 工具事件类型 → 落库条目 status（与前端 toolCallTransition 语义一致）。
# 消费方为 aggregate_tool_entry（唯一发布编排 publish_tool_lifecycle_event 第 4 步
# 调用，覆盖 chat 主/子代理 + research 全部工具事件），事件集
# 与状态机（lifecycle._VALID_TRANSITIONS）对齐；终态事件以
# event_schema.TERMINAL_TOOL_CALL_EVENTS 为唯一声明源。
EVENT_STATUS_MAP: dict[str, str] = {
    "tool_call_pending": "pending",
    "tool_call_waiting": "waiting",
    "tool_call_running": "running",
    "tool_call_completed": "completed",
    "tool_call_failed": "failed",
    "tool_call_timeout": "timeout",
    "tool_call_rejected": "rejected",
}

# 审批状态 / 工具内部状态 → 展示 status（唯一权威）。
# 收敛历史多套本地映射（approval_service.status_mapping / approval_helpers 的
# 审批状态提升逻辑 / 已移除的 stream_chunk_processors._STATE_TO_STATUS——该文件
# 已随流式工具状态收敛至 ToolEventExtractor 而不再处理工具状态）。
# 现存消费方：approval_service（快照状态注入）/ approval_helpers（审批重建与
# status 提升）。各引用方统一 import 本常量，禁止再定义本地映射。
# 值域与前端 toolCallTransition 语义一致，展示状态字符串为前端契约，不可变更。
STATE_TO_STATUS: dict[str, str] = {
    # 审批状态（Approval.state）
    "pending": "waiting",
    "waiting": "waiting",
    "processing": "running",
    "approved": "completed",
    "rejected": "rejected",
    "timeout": "timeout",
    # 工具内部状态（tool_info.state）
    "input-available": "pending",
    "output-available": "completed",
    "output-error": "failed",
}

# 终态集合：状态只前进不回退（审批 resume 重放 PENDING 不降级终态）。
# 由终态事件唯一声明源（event_schema.TERMINAL_TOOL_CALL_EVENTS）经
# EVENT_STATUS_MAP 派生，禁止再硬编码第二份终态列表。
TERMINAL_STATUSES: frozenset[str] = frozenset(
    EVENT_STATUS_MAP[e.value] for e in TERMINAL_TOOL_CALL_EVENTS
)


def _build_default_entry(tool_call_id: str, tool_name: str) -> dict:
    """构造工具调用落库条目的默认结构。"""
    return {
        "id": tool_call_id,
        "name": tool_name,
        "type": f"tool-call-{tool_name}",
        "state": "input-available",
        "status": "pending",
        "parameters": {},
        "result": None,
        "error": None,
    }


def aggregate_tool_entry(
    entries: dict,
    tool_call_id: str,
    tool_name: str,
    event_type: Any,
    parameters: dict | None = None,
    result: Any = None,
    error: Any = None,
    subagent_thread_id: str = "",
    agent_name: str = "",
    depth: int = 0,
) -> dict:
    """聚合单个工具事件到落库条目（原地写入 ``entries`` 并返回该 entry）。

    status/parameters/result/error 随事件演进，终态不可回退（审批 resume 重放
    PENDING 不降级）；subagent_thread_id/agent_name 非空才写，depth 恒写入
    （主代理为 0）；seq/position 经 lifecycle enrich_entry_seq/position 从
    ToolCallContext 权威源补全（守卫 seq>0 / position>=0，缺失时保持原值）。

    Args:
        entries: 会话/任务级聚合容器（tool_call_id → entry），原地更新。
        tool_call_id: 工具调用 ID。
        tool_name: 工具名。
        event_type: 事件类型（EventType 枚举或字符串）。
        parameters: 工具入参（非空 dict 才写）。
        result: 工具结果（is not None 才写，空串/空 dict 视为有效值）。
        error: 工具错误（is not None 才写）。
        subagent_thread_id: 子代理 thread_id（非空才写）。
        agent_name: 子代理名（非空才写）。
        depth: 嵌套层级（0=主代理，恒写入）。

    Returns:
        更新后的 entry dict。
    """
    if not tool_call_id:
        return {}

    # 惰性导入防循环依赖（seq/position 权威补全收口到 lifecycle enrich_*）
    from Django_xm.common.tool_call_lifecycle import service as _tc_service

    _ev = event_type.value if hasattr(event_type, "value") else str(event_type)
    _new_status = EVENT_STATUS_MAP.get(_ev, "")

    entry = entries.get(tool_call_id) or _build_default_entry(tool_call_id, tool_name)
    entry["name"] = entry.get("name") or tool_name

    if isinstance(parameters, dict) and parameters:
        entry["parameters"] = parameters

    _old_status = str(entry.get("status") or "").lower()
    if _new_status and (
        _old_status not in TERMINAL_STATUSES or _new_status in TERMINAL_STATUSES
    ):
        entry["status"] = _new_status

    if result is not None:
        entry["result"] = result
    if error is not None:
        entry["error"] = error

    if subagent_thread_id:
        entry["subagent_thread_id"] = subagent_thread_id
    if agent_name:
        entry["agent_name"] = agent_name
    if isinstance(depth, int):
        entry["depth"] = depth

    # seq/position 权威补全收口到 lifecycle enrich_*（守卫 seq>0 / position>=0，
    # 与持久化/审批侧出口一致；ctx 缺失时保持 entry 原值，由数组顺序兜底）。
    _tc_service.enrich_entry_seq(entry, tool_call_id)
    _tc_service.enrich_entry_position(entry, tool_call_id)

    entries[tool_call_id] = entry
    return entry


def merge_subagent_content(
    contents_map: dict,
    subagent_thread_id: str,
    content: str,
    reasoning_content: str,
    msg_id: str,
    seen_keys: dict,
) -> bool:
    """累计子代理正文并按 msg_id 幂等去重。

    审批 interrupt 恢复时 LangGraph 重放节点，中间件会对同一条 AIMessage 再次
    触发回调；跳过已转发消息避免正文重复累计（否则 tool_call position 错位）。

    Args:
        contents_map: 子代理正文累计容器（subagent_thread_id → {content, reasoning_content}）。
        subagent_thread_id: 子代理 thread_id（空则跳过，不累计）。
        content: 正文增量。
        reasoning_content: 思考增量。
        msg_id: 消息 ID（AIMessage.id，缺失时调用方应传确定性哈希）。
        seen_keys: 幂等键集合容器（subagent_thread_id → set[msg_id]）。

    Returns:
        True 表示该消息重复（调用方应跳过广播）；False 表示已累计（调用方应广播）。
    """
    if not subagent_thread_id:
        return True

    seen = seen_keys.setdefault(subagent_thread_id, set())
    # `seen` 和 `seen_keys[subagent_thread_id]` 指向**同一个集合对象**，后续对 `seen.add(xxx)` 的修改，会直接体现在字典里。
    if msg_id and msg_id in seen:
        return True
    if msg_id:
        seen.add(msg_id)

    entry = contents_map.setdefault(
        subagent_thread_id, {"content": "", "reasoning_content": ""}
    )
    if content:
        entry["content"] = (entry.get("content") or "") + content
    if reasoning_content:
        entry["reasoning_content"] = (entry.get("reasoning_content") or "") + reasoning_content
    return False
