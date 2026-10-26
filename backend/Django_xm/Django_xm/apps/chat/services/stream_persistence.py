"""流式结果持久化（Task 15.2 从 stream_helpers.py 拆分）。

集中处理流结束后 AI 回复内容与工具调用到 ``ChatMessage`` 的持久化：
- ``_build_entry_from_container``：从统一容器条目构建单条可持久化 tool_call（清理内部键）
- ``merge_tool_calls_incremental``：增量合并工具调用列表（字段级状态演进，P3-R2）
- ``_persist_to_db_sync``：同步执行所有 DB 操作（编排，供 sync_to_async 调用）：
    - ``_locate_session_and_message``：定位会话与助手消息（含最新 assistant 回退）
    - ``_merge_message_content``：正文合并（追加保留 + 尾部重叠去重）
    - ``_merge_message_tool_calls``：工具调用增量合并与变更检测（深度比较）
    - ``_apply_reasoning_override`` / ``_apply_subagent_contents_override``：
      reasoning / 子代理图层正文按需覆盖
    - ``_save_message_and_touch_session``：变更检测保存 + 会话 updated_at 刷新
- ``persist_stream_result``：流结束时持久化并广播 MESSAGE_UPDATED 事件（编排）：
    - ``_collect_tool_calls_with_subagents``：从统一容器构建 tool_calls
    - ``_execute_db_persist``：经 sync_to_async 执行 DB 持久化
    - ``_broadcast_message_updated``：广播 MESSAGE_UPDATED 事件
- ``persist_chat_tool_calls``：挂起前/流结束时的复用持久化入口
- ``revert_regenerate_archive``：重新生成失败回滚（弹出空版本 + 广播
  MESSAGE_REGENERATE_REVERTED，spec D6，与 _archive_current_version 镜像）

依赖方向：本模块不依赖其他 stream_* 子模块；ORM / broadcast 均通过函数内延迟导入，
规避 Django ``SynchronousOnlyOperation`` 与循环依赖。
"""

import logging
from typing import Any

from Django_xm.common.tool_call_aggregation import TERMINAL_STATUSES
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

# 工具调用终态集合（不可回退）统一引用 common.tool_call_aggregation.TERMINAL_STATUSES
# （全项目唯一权威，值域 completed/failed/timeout/rejected，与前端一致）。


def _build_entry_from_container(entry: dict[str, Any], key: str = "") -> dict[str, Any] | None:
    """从统一容器条目构建单条可持久化 tool_call（过滤内部键）。

    统一容器 ``data["_tool_entries"]``（tool_call_id → entry，主/子代理共享）
    的条目已由 ``aggregate_tool_entry`` 聚合，含
    id/name/type/state/status/parameters/result/error + depth/图层字段 +
    seq/position + approval 等。此处仅做白名单过滤与默认值兜底：
        - 保留 id/name/type/state/status/parameters/result/error
        - 保留图层字段（subagent_thread_id/agent_name/depth）与 approval
        - seq/position 经 enrich_entry_* 从 ToolCallContext 权威源补全

    Args:
        entry: 统一容器中的工具条目
        key: 容器键（tool_call_id），条目缺 id 时兜底

    Returns:
        dict | None: 可持久化条目；无有效 tool_call_id 时返回 None（跳过）
    """
    if not isinstance(entry, dict):
        return None
    tool_call_id = entry.get("id") or key or ""
    tool_name = entry.get("name") or ""
    if not tool_call_id:
        return None

    persisted_entry: dict[str, Any] = {
        "id": tool_call_id,
        "name": tool_name,
        "type": entry.get("type") or f"tool-call-{tool_name}",
        "state": entry.get("state") or "input-available",
        "status": entry.get("status") or "pending",
        "parameters": entry.get("parameters") or {},
        "result": entry.get("result"),
        "error": entry.get("error"),
    }
    # 审批字段（审批服务单独写入，LLM 输出不含）：容器含则透传
    if isinstance(entry.get("approval"), dict):
        persisted_entry["approval"] = entry["approval"]
    # 图层字段（Agent 图层嵌套规范）：subagent_thread_id/agent_name 非空才写，
    # depth 恒写入（主代理为 0，与容器一致）
    if entry.get("subagent_thread_id"):
        persisted_entry["subagent_thread_id"] = entry["subagent_thread_id"]
    if entry.get("agent_name"):
        persisted_entry["agent_name"] = entry["agent_name"]
    if entry.get("depth") is not None:
        persisted_entry["depth"] = entry["depth"]
    # seq（register 全局递增序号，前端排序权威依据）：容器已含则透传，
    # 统一经 enrich_entry_seq 从 ToolCallContext 权威源兜底补全。
    if isinstance(entry.get("seq"), int) and entry["seq"] > 0:
        persisted_entry["seq"] = entry["seq"]
    tool_call_service.enrich_entry_seq(persisted_entry, tool_call_id)
    # position（Agent 图层嵌套规范 D3，图层内联布局恢复依据）：同 seq 策略。
    if isinstance(entry.get("position"), int) and entry["position"] >= 0:
        persisted_entry["position"] = entry["position"]
    tool_call_service.enrich_entry_position(persisted_entry, tool_call_id)
    # 参数兜底：容器参数为空时从 ctx 权威源补全（不覆盖已解析参数）。
    tool_call_service.enrich_entry_parameters(persisted_entry, tool_call_id)
    return persisted_entry


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
    if existing_status in TERMINAL_STATUSES:
        for field in ("state", "result", "error"):
            if field in new_tc and new_tc[field] is not None:
                evolved[field] = new_tc[field]
        return evolved

    # 非终态演进：仅当 new 状态不构成回退时演进 status
    if not new_status:
        can_evolve_status = False
    elif new_status == existing_status:
        can_evolve_status = False  # 等值幂等，避免无意义变更
    elif new_status in TERMINAL_STATUSES:
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
    """定位目标会话与助手消息（消息定位段，会话与消息行均加 select_for_update）。

    要求调用方处于 ``transaction.atomic()`` 事务内（由 ``_persist_to_db_sync``
    提供），行锁覆盖 locate → merge → save 全程（A17 原子化）。

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

    # 会话定位与消息行锁（A17）：select_for_update 要求事务上下文，由调用方
    # ``_persist_to_db_sync`` 的 transaction.atomic 提供——锁覆盖 locate → merge →
    # save 全程，消除并发写同一助手消息的读-改-写末写胜出竞态。
    session = ChatSession.objects.select_for_update().filter(session_id=session_id).first()
    if session is None:
        logger.warning(f"[persist_stream_result] 会话不存在: session_id={session_id}")
        return None

    # 定位助手消息
    assistant_msg: ChatMessage | None = None
    if message_id:
        try:
            assistant_msg = (
                ChatMessage.objects.select_for_update().get(
                    id=int(message_id),
                    session=session,
                    role="assistant",
                )
            )
        except (ChatMessage.DoesNotExist, ValueError, TypeError):
            assistant_msg = None

    if assistant_msg is None:
        # 回退：取该会话最新的一条 assistant 消息（回退定位保留，更新仍走行锁）
        assistant_msg = (
            ChatMessage.objects.filter(
                session=session,
                role="assistant",
            )
            .order_by("-created_at")
            .select_for_update()
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
    # 延迟导入避免循环依赖
    from django.db import transaction

    # A17：locate → merge → save 全程置于单事务 + select_for_update 行锁内，
    # 并发写同一助手消息（如挂起恢复补写与流结束落库重叠）时后写者基于
    # 已提交的最新行内容合并，而非基于过期快照末写胜出。
    with transaction.atomic():
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
            "content": assistant_msg.content,
            "content_len": len(assistant_msg.content),
            "tool_calls_count": len(merged_tool_calls),
            "content_changed": content_changed,
            "tool_calls_changed": tool_calls_changed,
            "reasoning_changed": reasoning_changed,
        }


def _collect_tool_calls_with_subagents(
    tool_entries: dict[str, dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """从统一容器构建可持久化 tool_calls 列表（tool_calls 构造阶段）。

    统一容器 ``data["_tool_entries"]``（tool_call_id → entry）由
    ``common.tool_call_aggregation.aggregate_tool_entry`` 聚合，主代理与子代理
    工具条目共用同一 dict（图层字段 subagent_thread_id/agent_name/depth 已在
    容器内）。逐条白名单过滤（``_build_entry_from_container``）+ 按 id 去重，
    统一经 enrich_entry_parameters/seq/position 从 ToolCallContext 权威源补全。

    Args:
        tool_entries: 统一工具条目容器（key=tool_call_id）

    Returns:
        list[dict]: 可持久化 tool_calls 列表（含子代理图层字段）
    """
    new_tool_calls: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    # 快照迭代：容器是主/子代理共享的活字典，工具事件发布协程可能在
    # "审批/业务等待挂起前落库"的并发窗口中追加新 key（挂起 ≠ 流结束），
    # 直接 .items() 会撞 RuntimeError: dictionary changed size during
    # iteration。取"此刻为止"的一致快照；晚到的条目由会话最终结束
    # （finally）的再次落库补齐（persist_chat_tool_calls 为多次调用语义）。
    for _key, _entry in list((tool_entries or {}).items()):
        persisted_entry = _build_entry_from_container(_entry, _key)
        if persisted_entry is None:
            continue
        dedup_key = persisted_entry.get("id") or persisted_entry.get("name") or ""
        if dedup_key in seen_ids:
            continue
        seen_ids.add(dedup_key)
        new_tool_calls.append(persisted_entry)
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

    载荷携带完整 ``content``（而非仅 ``content_len``）：正文快照
    （STREAM_CONTENT_UPDATE）是 ``persist_history=False`` 不入回放历史的，
    断线重连时若只能拿到长度，正文就永久丢失；带上正文后，重连回放可直接
    自愈（前端 mergeMessageFromBackend 的保护规则是“本地为空或后端更长才
    覆盖”，不会把已收到的更新内容回退）。

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
                "content": result.get("content", ""),
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
    tool_entries: dict[str, dict[str, Any]] | None = None,
    message_id: str | None = None,
    reasoning: dict[str, Any] | None = None,
    subagent_contents: dict[str, dict[str, Any]] | None = None,
) -> str | None:
    """流结束时持久化 AI 回复内容与工具调用到 ``ChatMessage``。

    修复跨浏览器一致性问题：
        - 触发浏览器通过 SSE 流式接收完整 content + N 个 tool_calls
        - 非触发浏览器依赖 STREAM_FINALIZED 后拉取的 ``Message.content`` +
          ``Message.tool_calls``，必须与触发浏览器一致

    编排（各阶段策略详见对应拆出函数）：
        - ``_collect_tool_calls_with_subagents``：从统一容器构建 tool_calls
          （主/子代理条目共用容器，图层字段贯通）
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
        tool_entries: 统一工具条目容器（``data["_tool_entries"]``，key=tool_call_id）
        message_id: 关联的助手消息 ID（None 时尝试查找最新助手消息）
        reasoning: 推理内容（``{"content": "..."}`` 格式），None 时不变更
        subagent_contents: 子代理图层正文映射，None 时不变更

    Returns:
        str | None: 持久化的 message_id（成功时）；None 表示跳过持久化
    """
    if not session_id:
        return None

    # 从统一容器构建持久化的 tool_calls 列表
    new_tool_calls = _collect_tool_calls_with_subagents(tool_entries)

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
    """落库当前 content + 统一工具条目容器（复用唯一持久化入口 persist_stream_result）。

    从 services/fastapi_service/chat_executor_core.py 归位（C3第②批依赖方向
    债务清偿：DB 持久化逻辑属 chat 业务层，apps 层禁止反向依赖 services 层）。

    供审批中断挂起前（保证已完成工具 result 及时可见）、业务等待挂起前
    （子代理基线）与会话最终结束（finally）共用。工具条目取自统一容器
    ``data["_tool_entries"]``（主/子代理共享）。
    """
    if not session_id or not message_id:
        return
    try:
        ctx = data.get("_chat_ctx")
        final_content = content_state.get("content", "")
        if ctx is not None and ctx.current_message_content:
            final_content = ctx.current_message_content

        await persist_stream_result(
            session_id=session_id,
            user_id=None,
            content=final_content,
            tool_entries=data.get("_tool_entries") or None,
            message_id=str(message_id),
            subagent_contents=data.get("_subagent_contents") or None,
        )
    except Exception:
        logger.warning(
            f"[persist_chat_tool_calls] 落库 content/tool_calls 失败: session={session_id}",
            exc_info=True,
        )


def _revert_regenerate_archive_sync(session_id: str, message_id: str) -> tuple[Any, Any | None]:
    """同步执行重新生成失败回滚的 DB 操作 + 回滚后消息序列化。

    与 views_chat._archive_current_version 严格镜像（spec D6 / 前端
    messageHandlers.handleMessageRegenerateReverted 契约）：
        1. versions 末尾为空版本（content 为空，归档时追加的 ``{"content": ""}``）→ 弹出；
        2. current_version 指向回退后的最后版本；
        3. 顶层字段从回退后当前版本快照恢复（content/sources/plan/tool_calls/
           reasoning/suggestions/model）；
        4. is_streaming 置 False。

    严格按 message_id 定位（不回退最新 assistant 消息）：回滚写操作宁可跳过
    也不允许误伤非目标消息。

    序列化在本同步上下文完成（ChatMessageSerializer 可能触发懒加载查询，
    async 上下文直接调用会抛 SynchronousOnlyOperation）。

    Returns:
        tuple: ``(assistant_msg | None, message_data | None)``；
            assistant_msg 为回滚成功的消息实例（无可回滚空版本/消息不存在时 None），
            message_data 为序列化结果（序列化失败时 None，前端降级本地版本恢复）
    """
    from Django_xm.apps.chat.models import ChatMessage, ChatSession

    session = ChatSession.objects.filter(session_id=session_id).first()
    if session is None:
        logger.warning(f"[revert_regenerate_archive] 会话不存在: session_id={session_id}")
        return None, None

    try:
        assistant_msg = ChatMessage.objects.get(
            id=int(message_id), session=session, role="assistant", is_deleted=False
        )
    except (ChatMessage.DoesNotExist, ValueError, TypeError):
        logger.warning(
            f"[revert_regenerate_archive] 目标助手消息不存在: session_id={session_id}, message_id={message_id}"
        )
        return None, None

    versions = list(assistant_msg.versions or [])
    last = versions[-1] if versions else None
    # 空版本判定与 _archive_current_version 追加的 {"content": ""} 对齐
    if len(versions) <= 1 or not isinstance(last, dict) or (last.get("content") or "").strip():
        logger.info(
            f"[revert_regenerate_archive] 无可回滚的空版本，跳过: session_id={session_id}, "
            f"message_id={message_id}, versions={len(versions)}"
        )
        return None, None

    versions.pop()
    assistant_msg.versions = versions
    assistant_msg.current_version = max(len(versions) - 1, 0)
    snapshot = versions[assistant_msg.current_version] or {}
    assistant_msg.content = snapshot.get("content") or ""
    assistant_msg.is_streaming = False

    update_fields = ["versions", "current_version", "content", "is_streaming"]
    # 归档快照中 JSON 字段可能存 None（_archive_current_version 对 reasoning 等
    # 空值写 None），恢复时按模型字段默认值收敛（DB 列 NOT NULL，禁止写 NULL）。
    _field_null_defaults: dict[str, Any] = {
        "sources": list,
        "plan": dict,
        "tool_calls": list,
        "reasoning": dict,
        "suggestions": list,
    }
    for field in ("sources", "plan", "tool_calls", "reasoning", "suggestions", "model"):
        if field in snapshot:
            value = snapshot.get(field)
            if value is None and field in _field_null_defaults:
                value = _field_null_defaults[field]()
            setattr(assistant_msg, field, value)
            update_fields.append(field)
    assistant_msg.save(update_fields=update_fields)

    # 序列化回滚后的完整消息（契约同 MESSAGE_ADDED：research_task_map 必传）
    message_data: dict | None = None
    try:
        from Django_xm.apps.chat.serializers import ChatMessageSerializer, build_research_task_map

        message_data = ChatMessageSerializer(
            assistant_msg, context={"research_task_map": build_research_task_map([assistant_msg])}
        ).data
    except Exception:
        logger.warning(
            f"[revert_regenerate_archive] 回滚消息序列化失败，降级最小 payload: message_id={assistant_msg.id}",
            exc_info=True,
        )
    return assistant_msg, message_data


async def revert_regenerate_archive(session_id: str, message_id: str) -> dict[str, Any] | None:
    """重新生成失败回滚（spec D6）：弹出空版本恢复上一版本，并广播 MESSAGE_REGENERATE_REVERTED。

    业务时机（前端死分支契约 stores/sync/messageHandlers.js handleMessageRegenerateReverted）：
    重新生成启动即失败（无任何输出内容）时，删除归档产生的空版本并恢复上一个版本数据；
    payload 携带 ``message_id`` 与回滚后的完整 ``message``（ChatMessageSerializer 序列化），
    前端据此镜像弹出本地空版本并恢复顶层字段。

    Returns:
        dict | None: 事件 payload（回滚成功时）；未回滚（无空版本/消息不存在）为 None
    """
    try:
        from asgiref.sync import sync_to_async

        reverted, message_data = await sync_to_async(_revert_regenerate_archive_sync, thread_sensitive=True)(
            session_id, message_id
        )
    except Exception:
        logger.exception(
            f"[revert_regenerate_archive] 回滚失败: session_id={session_id}, message_id={message_id}"
        )
        return None

    if reverted is None:
        return None

    payload: dict[str, Any] = {
        "session_id": session_id,
        "message_id": str(reverted.id),
    }
    if message_data is not None:
        payload["message"] = message_data

    try:
        from Django_xm.common.event_schema import EventType
        from Django_xm.common.realtime_events import publish_event

        await publish_event(
            EventType.MESSAGE_REGENERATE_REVERTED,
            payload,
            session_id=session_id,
        )
    except Exception as broadcast_err:
        logger.warning(
            f"[revert_regenerate_archive] MESSAGE_REGENERATE_REVERTED 广播失败: "
            f"session_id={session_id}, err={broadcast_err}"
        )
    return payload
