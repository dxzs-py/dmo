"""工具调用条目聚合与子代理正文累计（chat / research 双链路唯一实现）。

将原 chat（``chat_service._on_subagent_tool_event``）与 research
（``adapter._publish_tool_event``）各自内联的「事件 → 落库条目聚合」逻辑收敛为
本模块唯一实现；子代理正文累计 + msg_id 幂等去重同样收敛于此。

设计边界：
- ``aggregate_tool_entry`` 只负责单条 entry 的字段演进（默认结构、终态保护、
  图层字段、seq/position 从 ToolCallContext 权威源补齐），不关心模块/source 差异。
- ``merge_subagent_content`` 只负责正文累计 + 幂等去重，返回是否重复；
  广播（publish_event）由各自入口处理（source/task_id 路由差异保留在入口）。
- 状态映射常量、终态集合为全项目唯一权威，chat / research / 持久化层统一引用。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# 工具事件类型 → 落库条目 status（与前端 toolCallTransition 语义一致）。
# 覆盖主/子代理所有 tool_call_* 事件，非子代理专属。
EVENT_STATUS_MAP: dict[str, str] = {
    "tool_call_pending": "pending",
    "tool_call_waiting": "waiting",
    "tool_call_running": "running",
    "tool_call_completed": "completed",
    "tool_call_failed": "failed",
    "tool_call_timeout": "timeout",
}

# 审批状态 / 工具内部状态 → 展示 status（唯一权威）。
# 收敛历史四套本地映射（stream_chunk_processors._STATE_TO_STATUS /
# approval_service.status_mapping / approval_helpers 的审批状态提升逻辑），
# 各引用方统一 import 本常量，禁止再定义本地映射。
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
# 与持久化层 _TOOL_CALL_TERMINAL_STATUSES 语义一致，此处为唯一权威。
TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed", "timeout", "rejected"})


def _build_default_entry(tool_call_id: str, tool_name: str) -> dict:
    """构造工具调用落库条目的默认结构。"""
    return {
        "id": tool_call_id,
        "name": tool_name,
        "type": f"tool-call-{tool_name}",
        "state": "input-available",
        "status": "pending",
        "parameters": {},
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
    get_ctx: Callable[[str], dict | None] | None = None,
) -> dict:
    """聚合单个工具事件到落库条目（原地写入 ``entries`` 并返回该 entry）。

    status/parameters/result/error 随事件演进，终态不可回退（审批 resume 重放
    PENDING 不降级）；图层字段（subagent_thread_id/agent_name/depth）非空才写；
    seq/position 从 ToolCallContext 权威源读取（register/bind_position 已写入）。

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
        depth: 嵌套层级（int 才写）。
        get_ctx: 读取 ToolCallContext 的可调用对象，默认用全局 service.get_context。

    Returns:
        更新后的 entry dict。
    """
    if not tool_call_id:
        return {}

    if get_ctx is None:
        from Django_xm.common.tool_call_lifecycle import service as _tc_service

        get_ctx = _tc_service.get_context

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

    _ctx = get_ctx(tool_call_id) or {}
    if isinstance(_ctx.get("seq"), int):
        entry["seq"] = _ctx["seq"]
    if isinstance(_ctx.get("position"), int):
        entry["position"] = _ctx["position"]

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
