"""流式结果持久化（Task 15.2 从 stream_helpers.py 拆分）。

集中处理流结束后 AI 回复内容与工具调用到 ``ChatMessage`` 的持久化：
- ``_build_persisted_tool_calls``：从 tool_calls_map 构建可持久化列表（清理内部字段）
- ``merge_tool_calls_incremental``：增量合并工具调用列表（字段级状态演进，P3-R2）
- ``_persist_to_db_sync``：同步执行所有 DB 操作（编排，供 sync_to_async 调用）：
    - ``_locate_session_and_message``：定位会话与助手消息（含最新 assistant 回退）
    - ``_merge_message_content``：正文合并（追加保留 + 尾部重叠去重）
    - ``_merge_message_tool_calls``：工具调用增量合并与变更检测（深度比较）
    - ``_apply_reasoning_override`` / ``_apply_subagent_contents_override``：
      reasoning / 子代理图层正文按需覆盖
    - ``_save_message_and_touch_session``：变更检测保存 + 会话 updated_at 刷新
- ``persist_stream_result``：流结束时持久化并广播 MESSAGE_UPDATED 事件（编排）：
    - ``_collect_tool_calls_with_subagents``：构建 tool_calls 并合并子代理条目
    - ``_execute_db_persist``：经 sync_to_async 执行 DB 持久化
    - ``_broadcast_message_updated``：广播 MESSAGE_UPDATED 事件
- ``persist_chat_tool_calls``：挂起前/流结束时的复用持久化入口

依赖方向：本模块不依赖其他 stream_* 子模块；ORM / broadcast 均通过函数内延迟导入，
规避 Django ``SynchronousOnlyOperation`` 与循环依赖。
"""

import logging
from typing import Any

from Django_xm.common.tool_call_lifecycle import service as tool_call_service

logger = logging.getLogger(__name__)

# 工具调用状态优先级（数值越大越接近终态）。
# 与前端 ``utils/toolCallTransition.js`` 的 ``getToolCallStatusPriority`` 保持一致，
# 用于非终态间的演进方向判断（拒绝回退，如 running → pending）。
_TOOL_CALL_STATUS_PRIORITY: dict[str, int] = {
    "pending": 0,
    "waiting": 1,
    "running": 2,
    "completed": 3,
    "failed": 3,
    "timeout": 3,
    "rejected": 3,
}

# 工具调用终态集合（不可回退）。
# 与前端 ``TERMINAL_STATUSES``（completed/failed/timeout/rejected）保持一致。
_TOOL_CALL_TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed", "timeout", "rejected"})


def _build_persisted_tool_calls(
    tool_calls_map: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """从 tool_calls_map 构建可持久化到 ``Message.tool_calls`` 的列表。

    过滤内部辅助字段（``_index`` / ``_summarized``），仅保留可序列化字段：
        - id / name / type / state / status / parameters / result / error / seq

    seq（register 分配的全局递增序号）必须保留：前端跨浏览器统一排序的唯一权威
    依据。若持久化丢弃 seq，API 快照与 WebSocket 事件（透传 seq）的排序依据
    不一致，刷新后工具调用乱序（fs_write_file 等后发工具因缺 seq 被排到最前）。
    seq 由 ``stream_broadcast`` 构建 tool_calls_map 时从 ToolCallContext 补齐。

    Args:
        tool_calls_map: 流式累积的 tool_calls_map（key=dedup_key，value=tool_info）

    Returns:
        list[dict]: 可持久化的 tool_calls 列表（按 tool_call_id 去重）
    """
    persisted: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for tool_info in tool_calls_map.values():
        if not isinstance(tool_info, dict):
            continue
        tool_call_id = tool_info.get("id") or ""
        tool_name = tool_info.get("name") or ""
        if not tool_call_id and not tool_name:
            continue
        # 按 tool_call_id 去重（保留最后一条，参数最完整）
        dedup_key = tool_call_id or tool_name
        if dedup_key in seen_ids:
            continue
        seen_ids.add(dedup_key)
        persisted_entry: dict[str, Any] = {
            "id": tool_call_id,
            "name": tool_name,
            "type": tool_info.get("type", "") or f"tool-call-{tool_name}",
            "state": tool_info.get("state", "") or "input-available",
            "status": tool_info.get("status", "") or "pending",
            "parameters": tool_info.get("parameters") or {},
            "result": tool_info.get("result"),
            "error": tool_info.get("error"),
        }
        # 参数兜底（与 seq/position 同模式）：流式 extractor 参数不流式时
        # （tool_call_chunks args 为空串）map 条目 parameters 恒为 {}，而
        # ApprovalMiddleware 注册 ToolCallContext 时已持有完整参数，此处从
        # ctx 权威源补全（仅当条目参数为空时，不覆盖 extractor 已解析参数）。
        tool_call_service.enrich_entry_parameters(persisted_entry, tool_call_id)
        # 保留 seq（register 全局递增序号，前端排序权威依据）：
        # 1) 快照优先——tool_calls_map 已补过 seq（stream_broadcast）则直接透传；
        # 2) 统一兜底——map 未补过的场景（context 过期或非 SSE 构建路径）经
        #    enrich_entry_seq 从 ToolCallContext 权威源补全，所有持久化出口收敛
        #    到同一函数，杜绝漏补。
        _seq = tool_info.get("seq")
        if isinstance(_seq, int) and _seq > 0:
            persisted_entry["seq"] = _seq
        tool_call_service.enrich_entry_seq(persisted_entry, tool_call_id)
        # 保留 position（Agent 图层嵌套规范 D3，图层内联布局恢复依据）：
        # 与 seq 同策略——tool_calls_map 已含则透传，否则经 enrich_entry_position
        # 从 ToolCallContext 权威源补全。刷新后前端依据 position 恢复工具卡
        # 内联位置；缺 position 的工具卡会确定性排在图层末尾（视觉乱序）。
        _position = tool_info.get("position")
        if isinstance(_position, int) and _position >= 0:
            persisted_entry["position"] = _position
        tool_call_service.enrich_entry_position(persisted_entry, tool_call_id)
        persisted.append(persisted_entry)
    return persisted


def _apply_immutable_tool_call_fields(evolved: dict[str, Any], new_tc: dict[str, Any]) -> None:
    """补全不可变字段：existing 缺失而 new 有值时补上（就地修改 evolved）。

    覆盖字段（existing 已有则原样保留，不覆盖）：
    - approval：审批字段由审批服务单独写入，LLM 输出不含
    - position：Agent 图层内联位置（不可变）
    - 图层字段（subagent_thread_id/agent_name/depth）：审批重建路径写入的
      条目缺图层字段，经此补全，保证刷新后前端按 subagent_thread_id
      归集子代理工具卡
    """
    if "approval" not in evolved and isinstance(new_tc.get("approval"), dict):
        evolved["approval"] = new_tc["approval"]

    if "position" not in evolved and isinstance(new_tc.get("position"), int):
        evolved["position"] = new_tc["position"]

    for _layer_field in ("subagent_thread_id", "agent_name", "depth"):
        if _layer_field not in evolved and new_tc.get(_layer_field) is not None:
            evolved[_layer_field] = new_tc[_layer_field]


def _evolve_tool_call(
    existing_tc: dict[str, Any],
    new_tc: dict[str, Any],
) -> dict[str, Any]:
    """字段级状态演进：合并已匹配的 existing/new 工具调用条目。

    P3-R2 修复核心：审批中断时首次流将 tool_calls 持久化为非终态
    （如 status='pending'）；审批通过、工具执行完成后再次持久化时，
    new 已更新为 completed + result，必须演进数据库条目，否则刷新/断连后
    快照（直接读取 ``ChatMessage.tool_calls``）永远停留在 pending，
    无 status/result 可恢复。

    演进规则（与前端状态机 ``utils/toolCallTransition.js`` 一致）：
        - ``approval`` 等审批字段：existing 原样保留（审批字段由审批服务
          单独写入，LLM 输出不含）；existing 无 approval 而 new 有时以 new 为准
        - ``status``/``state``/``result``/``error``：用 new 的值演进（new 有值才演进）
        - **禁止终态回退**：existing 的 status 已是终态
          （completed/failed/timeout/rejected）时，new 提供 pending/waiting/
          running 等非终态则保持 existing 不变（终态不可逆）
        - existing 非终态而 new 更终态（如 pending→completed）：演进 status，
          并同步演进 state/result/error
        - 两者皆非终态时按状态优先级演进（pending < waiting < running），
          拒绝非终态回退（如 running→pending）

    Args:
        existing_tc: 数据库中已有的工具调用条目（只读）
        new_tc: 本次流式产出的同 id/name 工具调用条目（只读）

    Returns:
        dict: 演进后的新条目（不修改入参对象）
    """
    evolved: dict[str, Any] = dict(existing_tc)
    _apply_immutable_tool_call_fields(evolved, new_tc)

    existing_status = str(existing_tc.get("status") or "").lower()
    new_status = str(new_tc.get("status") or "").lower()

    # 终态保护：existing 已是终态 → 保持 status 不变（终态不可逆），
    # 但 state/result/error 属数据字段，仍应演进（根因修复：审批服务 B3
    # 在审批 approved 时提前将 status 映射为 completed 终态，若在此整体
    # return，工具执行完成后的 result 永远无法落库，刷新/后开浏览器
    # 快照将缺失工具输出结果）。
    if existing_status in _TOOL_CALL_TERMINAL_STATUSES:
        for field in ("state", "result", "error"):
            if field in new_tc and new_tc[field] is not None:
                evolved[field] = new_tc[field]
        return evolved

    # 非终态演进：仅当 new 状态不构成回退时演进 status
    if not new_status:
        can_evolve_status = False
    elif new_status == existing_status:
        can_evolve_status = False  # 等值幂等，避免无意义变更
    elif new_status in _TOOL_CALL_TERMINAL_STATUSES:
        can_evolve_status = True  # 非终态 → 终态
    else:
        # 两者皆非终态：按优先级演进（pending<waiting<running），拒绝回退
        existing_priority = _TOOL_CALL_STATUS_PRIORITY.get(existing_status, 0)
        new_priority = _TOOL_CALL_STATUS_PRIORITY.get(new_status, 0)
        can_evolve_status = new_priority >= existing_priority

    if can_evolve_status:
        evolved["status"] = new_tc["status"]

    # state/result/error：new 有值则演进（new 无值保留 existing）
    for field in ("state", "result", "error"):
        if field in new_tc and new_tc[field] is not None:
            evolved[field] = new_tc[field]

    return evolved


def merge_tool_calls_incremental(
    existing_tool_calls: list[dict[str, Any]] | None,
    new_tool_calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """增量合并工具调用列表（字段级状态演进，P3-R2）。

    匹配策略（按优先级）：
        1. ``tool_call_id`` 字段（即 ``id``）
        2. ``name`` 字段（降级，兼容仅含 name 的旧数据）

    合并规则（演进而非覆盖）：
        - existing 中存在但 new 中不存在的条目：保留（不丢失历史）
        - new 中存在且 existing 中也存在的条目：调用 ``_evolve_tool_call``
          做字段级状态演进——保留 existing 的 approval 等审批字段，
          用 new 演进 status/state/result/error（含终态回退保护，P3-R2 修复）
        - new 中存在但 existing 中不存在的条目：追加

    P3-R2 背景：审批中断时首次流将 tool_calls 持久化为 status='pending'；
    审批通过、工具执行完成后再次持久化时，new 已更新为 completed + result，
    旧策略「保留 existing 完整不动」导致永不覆盖，数据库永远停留在 pending。
    本函数改为字段级演进：existing 非终态且 new 更终态时演进，恢复流完成后
    刷新/断连快照即可读到 completed + result。

    Args:
        existing_tool_calls: 数据库中已有的 tool_calls（None 视为空列表）
        new_tool_calls: 本次流式产出的 tool_calls

    Returns:
        list[dict]: 合并后的 tool_calls 列表（existing 在前，新增在后；
        matched 条目为演进后的新字典，不修改入参对象）
    """
    if not existing_tool_calls:
        return list(new_tool_calls)
    if not new_tool_calls:
        return list(existing_tool_calls)

    # 建立 existing 索引（按 id 与 name 双索引，记录其在 merged 中的下标；
    # 过滤非 dict 条目，保证索引与 merged 下标一一对应）
    merged: list[dict[str, Any]] = []
    existing_by_id: dict[str, int] = {}
    existing_by_name: dict[str, int] = {}
    for tc in existing_tool_calls:
        if not isinstance(tc, dict):
            continue
        idx = len(merged)
        merged.append(dict(tc))
        tc_id = tc.get("id") or ""
        tc_name = tc.get("name") or ""
        if tc_id and tc_id not in existing_by_id:
            existing_by_id[tc_id] = idx
        if tc_name and tc_name not in existing_by_name:
            existing_by_name[tc_name] = idx

    for new_tc in new_tool_calls:
        if not isinstance(new_tc, dict):
            continue
        new_id = new_tc.get("id") or ""
        new_name = new_tc.get("name") or ""
        # 匹配已存在条目：优先按 id 精确匹配（聚合/审批条目均含唯一 id）。
        # name 降级匹配仅限 new 条目无 id 的旧数据兼容场景——若 new 有 id 仍按
        # name 匹配，会把同名工具（如多个子代理的 shell_exec）误合并进同一条
        # （演进吞掉新条目），导致子代理工具聚合条目（带图层字段）无法落库，
        # 刷新后工具卡脱离子代理卡、图层字段丢失。
        matched_idx = existing_by_id.get(new_id) if new_id else None
        if matched_idx is None and not new_id and new_name:
            matched_idx = existing_by_name.get(new_name)
        if matched_idx is None:
            merged.append(new_tc)  # 无匹配 → 追加
        else:
            merged[matched_idx] = _evolve_tool_call(merged[matched_idx], new_tc)

    return merged


def _locate_session_and_message(
    session_id: str,
    message_id: str | None,
) -> tuple[Any, Any] | None:
    """定位目标会话与助手消息（消息定位段）。

    定位策略：
        - 会话不存在 → 返回 None（上层跳过持久化）
        - 优先按 ``message_id`` 精确取该会话的 assistant 消息；id 非法
          （ValueError/TypeError）或消息不存在时回退取该会话最新一条
          assistant 消息
        - 找不到任何助手消息 → 返回 None

    Args:
        session_id: 会话 ID
        message_id: 关联的助手消息 ID（None 时直接回退最新助手消息）

    Returns:
        tuple | None: ``(session, assistant_msg)``；None 表示会话或消息不存在
    """
    # 延迟导入避免循环依赖
    from Django_xm.apps.chat.models import ChatMessage, ChatSession

    session = ChatSession.objects.filter(session_id=session_id).first()
    if session is None:
        logger.warning(f"[persist_stream_result] 会话不存在: session_id={session_id}")
        return None

    # 定位助手消息
    assistant_msg: ChatMessage | None = None
    if message_id:
        try:
            assistant_msg = ChatMessage.objects.get(
                id=int(message_id),
                session=session,
                role="assistant",
            )
        except (ChatMessage.DoesNotExist, ValueError, TypeError):
            assistant_msg = None

    if assistant_msg is None:
        # 回退：取该会话最新的一条 assistant 消息
        assistant_msg = (
            ChatMessage.objects.filter(
                session=session,
                role="assistant",
            )
            .order_by("-created_at")
            .first()
        )

    if assistant_msg is None:
        logger.warning(f"[persist_stream_result] 找不到助手消息: session_id={session_id}, message_id={message_id}")
        return None

    return session, assistant_msg


def _merge_message_content(assistant_msg: Any, content: str) -> bool:
    """合并正文（正文合并段），返回是否发生变更。

    合并策略：追加保留 + 尾部重叠去重（修复业务等待挂起恢复后的历史段覆盖/重复）：
        - 新内容以已有内容为前缀（正常流式累积 / 恢复基线到位）：直接采用新内容；
        - 新内容尾部与已有内容尾部重叠（恢复轮 LLM 从 checkpoint 重新输出，重叠于
          挂起前流式中途的尾部字符，如「…汇总结果。三个」+「三个子代理全部完成…」）：
          按最长重叠去重拼接，避免「三个三个」类重复；
        - 新内容更长但无重叠（恢复轮从空重建，缺失挂起前流式段）：追加到已有内容后，
          保留历史段，避免「仅更长覆盖」把挂起前流式段覆盖丢失；
        - 新内容更短/相等且非前缀扩展：保留已有版本（避免覆盖前端更长版本）。

    Args:
        assistant_msg: 目标助手消息（就地更新 content）
        content: 本次流式产出的完整正文

    Returns:
        bool: content 是否发生变更
    """
    existing_content = assistant_msg.content or ""
    if not content or content == existing_content:
        return False

    if content.startswith(existing_content):
        assistant_msg.content = content
        return True

    # 尾部重叠检测：content 前缀与 existing 后缀重叠（恢复轮 LLM 从
    # checkpoint 重生成，重叠于挂起前流式中途的尾部字符）。
    # 重叠判定与拼接复用 stream_broadcast 唯一权威实现
    # （find_content_overlap / _merge_content_with_overlap），禁止本地复制。
    from Django_xm.apps.chat.services.stream_broadcast import (
        _merge_content_with_overlap,
        find_content_overlap,
    )

    _overlap = find_content_overlap(existing_content, content)
    if _overlap > 0:
        # 去重拼接（不要求 content 更长——恢复轮 content 可能仅含
        # 重叠尾 + 少量新增）
        _merged = _merge_content_with_overlap(existing_content, content)
        if len(_merged) > len(existing_content):
            assistant_msg.content = _merged
            return True
    elif len(content) > len(existing_content):
        # 无重叠且更长：纯追加保留历史段（恢复轮从空重建，缺失挂起前流式段）
        assistant_msg.content = _merge_content_with_overlap(existing_content, content)
        return True
    # 其余（无重叠且更短/等长）：保留已有版本，避免覆盖前端更长版本
    return False


def _merge_message_tool_calls(
    assistant_msg: Any,
    new_tool_calls: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    """增量合并工具调用（工具调用合并段）。

    复用同模块 ``merge_tool_calls_incremental`` 做字段级状态演进（P3-R2）：
    已匹配条目演进 status/state/result/error（保留 approval 字段），
    未匹配的新 tool_call 追加。本函数只读 ``assistant_msg.tool_calls``，
    合并结果赋值由保存段统一执行。

    变更检测：深度比较而非仅比较长度——字段级演进（如 pending→completed）
    不改变列表长度，若仅按长度判断会漏存（P3-R2 落库的关键一环）。

    Args:
        assistant_msg: 目标助手消息（只读 tool_calls）
        new_tool_calls: 已构建的可持久化 tool_calls 列表

    Returns:
        tuple[list, bool]: ``(merged_tool_calls, tool_calls_changed)``
    """
    existing_tool_calls = assistant_msg.tool_calls
    merged_tool_calls = merge_tool_calls_incremental(existing_tool_calls, new_tool_calls)
    tool_calls_changed = merged_tool_calls != existing_tool_calls
    return merged_tool_calls, tool_calls_changed


def _apply_reasoning_override(assistant_msg: Any, reasoning: dict[str, Any] | None) -> bool:
    """覆盖 reasoning（reasoning 覆盖段），返回是否发生变更。

    覆盖策略：仅当传入非空 reasoning 且其 content 与已有值不同时覆盖。

    Args:
        assistant_msg: 目标助手消息（就地更新 reasoning）
        reasoning: 推理内容（``{"content": "..."}`` 格式），None 时不变更

    Returns:
        bool: reasoning 是否发生变更
    """
    if not (reasoning and reasoning.get("content")):
        return False
    existing_reasoning = assistant_msg.reasoning
    if not isinstance(existing_reasoning, dict):
        existing_reasoning = {}
    if reasoning.get("content") != existing_reasoning.get("content"):
        assistant_msg.reasoning = reasoning
        return True
    return False


def _apply_subagent_contents_override(
    assistant_msg: Any,
    subagent_contents: dict[str, dict[str, Any]] | None,
) -> bool:
    """覆盖子代理图层正文（subagent 覆盖段），返回是否发生变更。

    子代理图层正文覆盖策略（Agent 图层嵌套规范 Task 1.5）：
    仅当传入非空 subagent_contents 且与已有值不同时覆盖（避免空覆盖清除历史）。

    Args:
        assistant_msg: 目标助手消息（就地更新 subagent_contents）
        subagent_contents: 子代理图层正文映射，None/空 dict 时不变更

    Returns:
        bool: subagent_contents 是否发生变更
    """
    if not (isinstance(subagent_contents, dict) and subagent_contents):
        return False
    existing_subagent = assistant_msg.subagent_contents
    if not isinstance(existing_subagent, dict):
        existing_subagent = {}
    if subagent_contents != existing_subagent:
        assistant_msg.subagent_contents = subagent_contents
        return True
    return False


def _save_message_and_touch_session(
    assistant_msg: Any,
    session: Any,
    merged_tool_calls: list[dict[str, Any]],
    content_changed: bool,
    tool_calls_changed: bool,
    reasoning_changed: bool,
    subagent_contents_changed: bool,
) -> None:
    """按需保存消息并刷新会话 updated_at（变更检测与保存段）。

    - 仅在任一字段组发生变更时保存（``update_fields`` 固定包含四个业务字段
      与 updated_at）；``tool_calls`` 在保存前统一赋值为合并结果
    - 会话 updated_at 无条件刷新（修复 P0：ChatMessage 持久化未 touch
      session，ChatSession.updated_at=auto_now 仅在 session.save() 时更新，
      导致刷新后会话排序失真，loadSessionsFromBackend fallback 选中错误会话。
      用 filter().update() 而非 session.save()，避免触发 AuditModel.save 的
      request context 依赖）

    Args:
        assistant_msg: 已应用各字段组合并结果的助手消息
        session: 目标会话（仅使用 pk）
        merged_tool_calls: 合并后的 tool_calls（save 前统一赋值）
        content_changed: 正文是否变更
        tool_calls_changed: 工具调用是否变更
        reasoning_changed: reasoning 是否变更
        subagent_contents_changed: subagent_contents 是否变更
    """
    # 延迟导入避免循环依赖
    from django.utils import timezone

    from Django_xm.apps.chat.models import ChatSession

    if content_changed or tool_calls_changed or reasoning_changed or subagent_contents_changed:
        assistant_msg.tool_calls = merged_tool_calls
        assistant_msg.save(
            update_fields=["content", "tool_calls", "reasoning", "subagent_contents", "updated_at"]
        )

    ChatSession.objects.filter(pk=session.pk).update(updated_at=timezone.now())


def _persist_to_db_sync(
    session_id: str,
    message_id: str | None,
    content: str,
    new_tool_calls: list[dict[str, Any]],
    reasoning: dict[str, Any] | None = None,
    subagent_contents: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """同步执行所有 DB 操作（供 ``persist_stream_result`` 通过 ``sync_to_async`` 调用）。

    将 ORM 操作从 async 上下文抽离到同步函数，规避 Django ``SynchronousOnlyOperation``：
    async 函数中直接调用 ORM 会被 Django 阻断（"You cannot call this from an async context"）。

    编排（各段合并策略详见对应拆出函数）：
        - ``_locate_session_and_message``：定位会话与助手消息
        - ``_merge_message_content``：正文合并（追加保留 + 尾部重叠去重）
        - ``_merge_message_tool_calls``：工具调用增量合并（字段级状态演进，P3-R2）
        - ``_apply_reasoning_override`` / ``_apply_subagent_contents_override``：
          reasoning / 子代理图层正文按需覆盖
        - ``_save_message_and_touch_session``：变更检测保存 + 会话 updated_at 刷新

    Args:
        session_id: 会话 ID
        message_id: 关联的助手消息 ID（None 时尝试查找最新助手消息）
        content: AI 回复的最终完整内容
        new_tool_calls: 已构建的可持久化 tool_calls 列表
        reasoning: 推理内容（``{"content": "..."}`` 格式），None 时不变更
        subagent_contents: 子代理图层正文映射，None 时不变更

    Returns:
        dict | None: 成功时返回 ``{message_id, content_len, tool_calls_count,
        content_changed, tool_calls_changed, reasoning_changed}``；
        None 表示跳过持久化（会话或消息不存在）
    """
    located = _locate_session_and_message(session_id, message_id)
    if located is None:
        return None
    session, assistant_msg = located

    content_changed = _merge_message_content(assistant_msg, content)
    merged_tool_calls, tool_calls_changed = _merge_message_tool_calls(assistant_msg, new_tool_calls)
    reasoning_changed = _apply_reasoning_override(assistant_msg, reasoning)
    subagent_contents_changed = _apply_subagent_contents_override(assistant_msg, subagent_contents)

    _save_message_and_touch_session(
        assistant_msg,
        session,
        merged_tool_calls,
        content_changed,
        tool_calls_changed,
        reasoning_changed,
        subagent_contents_changed,
    )

    return {
        "message_id": str(assistant_msg.id),
        "content_len": len(assistant_msg.content),
        "tool_calls_count": len(merged_tool_calls),
        "content_changed": content_changed,
        "tool_calls_changed": tool_calls_changed,
        "reasoning_changed": reasoning_changed,
    }


def _collect_tool_calls_with_subagents(
    tool_calls_map: dict[str, dict[str, Any]],
    subagent_tool_entries: dict[str, dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """构建可持久化 tool_calls 列表并合并子代理工具条目（tool_calls 构造阶段）。

    - 主代理工具经 ``_build_persisted_tool_calls`` 从 tool_calls_map 构建
      （清理内部字段、补全 parameters/seq/position）
    - 子代理工具条目合并（图层字段贯通，刷新后子代理卡片归集依据）：
      主 ctx.tool_calls_map 只含主代理工具；子代理工具由 chat_service
      _on_subagent_tool_event 聚合（含 subagent_thread_id/agent_name/depth/
      position/seq/status/result）。与主条目按 id 天然去重（merge_tool_calls_incremental
      增量合并），审批重建路径写入的条目经演进保留图层字段不丢失。

    Args:
        tool_calls_map: 流式累积的 tool_calls_map
        subagent_tool_entries: 子代理工具条目映射（key=tool_call_id）

    Returns:
        list[dict]: 含子代理条目的可持久化 tool_calls 列表
    """
    new_tool_calls = _build_persisted_tool_calls(tool_calls_map)

    if subagent_tool_entries:
        existing_ids = {tc.get("id") for tc in new_tool_calls if tc.get("id")}
        for _tc_id, _entry in subagent_tool_entries.items():
            if not _tc_id or _tc_id in existing_ids:
                continue
            new_tool_calls.append(dict(_entry))

    return new_tool_calls


async def _execute_db_persist(
    session_id: str,
    message_id: str | None,
    content: str,
    new_tool_calls: list[dict[str, Any]],
    reasoning: dict[str, Any] | None,
    subagent_contents: dict[str, dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """经 sync_to_async 执行 DB 持久化（DB 执行阶段）。

    Django ORM 不允许在 async 上下文直接调用（抛 SynchronousOnlyOperation），
    因此所有 ORM 操作下沉到 ``_persist_to_db_sync`` 同步函数，通过
    ``sync_to_async(thread_sensitive=True)`` 调用，确保与调用线程共用连接/事务。

    Args:
        session_id: 会话 ID
        message_id: 关联的助手消息 ID（None 时尝试查找最新助手消息）
        content: AI 回复的最终完整内容
        new_tool_calls: 已构建的可持久化 tool_calls 列表
        reasoning: 推理内容（``{"content": "..."}`` 格式），None 时不变更
        subagent_contents: 子代理图层正文映射，None 时不变更

    Returns:
        dict | None: ``_persist_to_db_sync`` 的结果；None 表示跳过持久化
    """
    # 延迟导入避免循环依赖
    from asgiref.sync import sync_to_async

    return await sync_to_async(_persist_to_db_sync, thread_sensitive=True)(
        session_id, message_id, content, new_tool_calls, reasoning, subagent_contents
    )


async def _broadcast_message_updated(session_id: str, result: dict[str, Any]) -> None:
    """广播 MESSAGE_UPDATED 事件（事件广播阶段）。

    通知同会话其他浏览器拉取完整数据（非触发浏览器依赖 STREAM_FINALIZED
    后拉取的 ``Message.content`` + ``Message.tool_calls``，与触发浏览器一致）。
    广播失败仅记录 warning，不中断持久化流程。

    Args:
        session_id: 会话 ID
        result: ``_persist_to_db_sync`` 返回的持久化结果
    """
    # 延迟导入避免循环依赖
    from Django_xm.common.event_schema import EventSource, EventType
    from Django_xm.common.realtime_events import publish_event

    try:
        await publish_event(
            EventType.MESSAGE_UPDATED,
            {
                "message_id": result["message_id"],
                "session_id": session_id,
                "source": EventSource.CHAT.value,
                "source_id": session_id,
                "content_len": result["content_len"],
                "tool_calls_count": result["tool_calls_count"],
            },
            session_id=session_id,
        )
    except Exception as broadcast_err:
        logger.warning(
            f"[persist_stream_result] MESSAGE_UPDATED 广播失败: session_id={session_id}, err={broadcast_err}"
        )


async def persist_stream_result(
    session_id: str,
    user_id: int | None,
    content: str,
    tool_calls_map: dict[str, dict[str, Any]],
    message_id: str | None = None,
    reasoning: dict[str, Any] | None = None,
    subagent_contents: dict[str, dict[str, Any]] | None = None,
    subagent_tool_entries: dict[str, dict[str, Any]] | None = None,
) -> str | None:
    """流结束时持久化 AI 回复内容与工具调用到 ``ChatMessage``。

    修复跨浏览器一致性问题：
        - 触发浏览器通过 SSE 流式接收完整 content + N 个 tool_calls
        - 非触发浏览器依赖 STREAM_FINALIZED 后拉取的 ``Message.content`` +
          ``Message.tool_calls``，必须与触发浏览器一致

    编排（各阶段策略详见对应拆出函数）：
        - ``_collect_tool_calls_with_subagents``：构建 tool_calls（含子代理
          条目合并）
        - ``_execute_db_persist``：经 sync_to_async 执行 DB 持久化（content
          追加保留、tool_calls 增量合并、reasoning/subagent 按需覆盖）
        - ``_broadcast_message_updated``：广播 MESSAGE_UPDATED 事件到同会话
          其他浏览器

    实现注意：
        本函数为 async（被 ``finalizer.finalize_stream`` await 调用）。
        Django ORM 不允许在 async 上下文直接调用（抛 SynchronousOnlyOperation），
        因此所有 ORM 操作下沉到 ``_persist_to_db_sync`` 同步函数，通过
        ``sync_to_async(thread_sensitive=True)`` 调用，确保与调用线程共用连接/事务。

    Args:
        session_id: 会话 ID（必填）
        user_id: 用户 ID（用于权限校验，None 时跳过权限检查）
        content: AI 回复的最终完整内容
        tool_calls_map: 流式累积的 tool_calls_map
        message_id: 关联的助手消息 ID（None 时尝试查找最新助手消息）
        reasoning: 推理内容（``{"content": "..."}`` 格式），None 时不变更
        subagent_contents: 子代理图层正文映射，None 时不变更
        subagent_tool_entries: 子代理工具条目映射，None 时不变更

    Returns:
        str | None: 持久化的 message_id（成功时）；None 表示跳过持久化
    """
    if not session_id:
        return None

    # 构建持久化的 tool_calls 列表（含子代理条目合并）
    new_tool_calls = _collect_tool_calls_with_subagents(tool_calls_map, subagent_tool_entries)

    try:
        result = await _execute_db_persist(
            session_id, message_id, content, new_tool_calls, reasoning, subagent_contents
        )

        if result is None:
            return None

        logger.info(
            f"[persist_stream_result] 持久化成功: session_id={session_id}, "
            f"message_id={result['message_id']}, content_len={result['content_len']}, "
            f"tool_calls_count={result['tool_calls_count']}, "
            f"content_changed={result['content_changed']}, "
            f"tool_calls_changed={result['tool_calls_changed']}, "
            f"reasoning_changed={result.get('reasoning_changed', False)}"
        )

        await _broadcast_message_updated(session_id, result)

        return result["message_id"]

    except Exception:
        logger.exception(f"[persist_stream_result] 持久化失败: session_id={session_id}, message_id={message_id}")
        return None


async def persist_chat_tool_calls(
    data: dict, content_state: dict, session_id: str, message_id
) -> None:
    """落库当前 content + tool_calls_map（复用唯一持久化入口 persist_stream_result）。

    从 services/fastapi_service/chat_executor_core.py 归位（C3第②批依赖方向
    债务清偿：DB 持久化逻辑属 chat 业务层，apps 层禁止反向依赖 services 层）。

    供审批中断挂起前（保证已完成工具 result 及时可见）、业务等待挂起前
    （子代理基线）与会话最终结束（finally）共用。
    """
    if not session_id or not message_id:
        return
    try:
        ctx = data.get("_chat_ctx")
        tool_calls_map = ctx.tool_calls_map if ctx is not None else {}
        final_content = content_state.get("content", "")
        if ctx is not None and ctx.current_message_content:
            final_content = ctx.current_message_content

        await persist_stream_result(
            session_id=session_id,
            user_id=None,
            content=final_content,
            tool_calls_map=tool_calls_map,
            message_id=str(message_id),
            subagent_contents=data.get("_subagent_contents") or None,
            subagent_tool_entries=data.get("_subagent_tool_entries") or None,
        )
    except Exception:
        logger.warning(
            f"[persist_chat_tool_calls] 落库 content/tool_calls 失败: session={session_id}",
            exc_info=True,
        )
