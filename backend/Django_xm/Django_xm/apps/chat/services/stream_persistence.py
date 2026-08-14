"""流式结果持久化（Task 15.2 从 stream_helpers.py 拆分）。

集中处理流结束后 AI 回复内容与工具调用到 ``ChatMessage`` 的持久化：
- ``_build_persisted_tool_calls``：从 tool_calls_map 构建可持久化列表（清理内部字段）
- ``_merge_tool_calls_incremental``：增量合并工具调用列表（字段级状态演进，P3-R2）
- ``_persist_to_db_sync``：同步执行所有 DB 操作（供 sync_to_async 调用）
- ``persist_stream_result``：流结束时持久化并广播 MESSAGE_UPDATED 事件

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
    seq 由 ``sse_generator`` 构建 tool_calls_map 时从 ToolCallContext 补齐。

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
        # 保留 seq（register 全局递增序号，前端排序权威依据）：
        # 1) 快照优先——tool_calls_map 已补过 seq（sse_generator）则直接透传；
        # 2) 统一兜底——map 未补过的场景（context 过期或非 SSE 构建路径）经
        #    enrich_entry_seq 从 ToolCallContext 权威源补全，所有持久化出口收敛
        #    到同一函数，杜绝漏补。
        _seq = tool_info.get("seq")
        if isinstance(_seq, int) and _seq > 0:
            persisted_entry["seq"] = _seq
        tool_call_service.enrich_entry_seq(persisted_entry, tool_call_id)
        persisted.append(persisted_entry)
    return persisted


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

    # approval 等审批字段：existing 原样保留；existing 无 approval 而 new 有，以 new 为准
    if "approval" not in evolved and isinstance(new_tc.get("approval"), dict):
        evolved["approval"] = new_tc["approval"]

    existing_status = str(existing_tc.get("status") or "").lower()
    new_status = str(new_tc.get("status") or "").lower()

    # 终态保护：existing 已是终态 → 保持 existing 全部字段不变（终态不可逆）
    if existing_status in _TOOL_CALL_TERMINAL_STATUSES:
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


def _merge_tool_calls_incremental(
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
        # 匹配已存在条目：按 id 优先，name 降级
        matched_idx = existing_by_id.get(new_id) if new_id else None
        if matched_idx is None and new_name:
            matched_idx = existing_by_name.get(new_name)
        if matched_idx is None:
            merged.append(new_tc)  # 无匹配 → 追加
        else:
            merged[matched_idx] = _evolve_tool_call(merged[matched_idx], new_tc)

    return merged


def _persist_to_db_sync(
    session_id: str,
    message_id: str | None,
    content: str,
    new_tool_calls: list[dict[str, Any]],
    reasoning: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """同步执行所有 DB 操作（供 ``persist_stream_result`` 通过 ``sync_to_async`` 调用）。

    将 ORM 操作从 async 上下文抽离到同步函数，规避 Django ``SynchronousOnlyOperation``：
    async 函数中直接调用 ORM 会被 Django 阻断（"You cannot call this from an async context"）。

    合并策略：
        - ``content``：仅当新内容严格长于已有内容时覆盖（避免覆盖前端更长版本）；
          已有内容等长或更长时保留前端版本
        - ``tool_calls``：增量合并（``_merge_tool_calls_incremental``），
          已匹配条目做字段级状态演进（保留 approval 字段，演进
          status/state/result/error，P3-R2），未匹配的新 tool_call 追加；
          ``tool_calls_changed`` 采用深度比较，字段级演进（长度不变）
          也能被正确识别并落库
        - ``reasoning``：仅当传入非空 reasoning 且其 content 与已有值不同时覆盖

    Args:
        session_id: 会话 ID
        message_id: 关联的助手消息 ID（None 时尝试查找最新助手消息）
        content: AI 回复的最终完整内容
        new_tool_calls: 已构建的可持久化 tool_calls 列表
        reasoning: 推理内容（``{"content": "..."}`` 格式），None 时不变更

    Returns:
        dict | None: 成功时返回 ``{message_id, content_len, tool_calls_count,
        content_changed, tool_calls_changed, reasoning_changed}``；
        None 表示跳过持久化（会话或消息不存在）
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

    # 内容覆盖策略：仅当新内容严格长于已有内容时覆盖
    existing_content = assistant_msg.content or ""
    content_changed = False
    if len(content) > len(existing_content):
        assistant_msg.content = content
        content_changed = True

    # 增量合并 tool_calls（字段级状态演进，P3-R2）
    existing_tool_calls = assistant_msg.tool_calls or []
    merged_tool_calls = _merge_tool_calls_incremental(existing_tool_calls, new_tool_calls)
    # 变更检测：深度比较而非仅比较长度——字段级演进（如 pending→completed）
    # 不改变列表长度，若仅按长度判断会漏存（P3-R2 落库的关键一环）
    tool_calls_changed = merged_tool_calls != existing_tool_calls

    # reasoning 覆盖策略：仅当传入非空 reasoning 且 content 不同时覆盖
    reasoning_changed = False
    if reasoning and reasoning.get("content"):
        existing_reasoning = assistant_msg.reasoning or {}
        if not isinstance(existing_reasoning, dict):
            existing_reasoning = {}
        if reasoning.get("content") != existing_reasoning.get("content"):
            assistant_msg.reasoning = reasoning
            reasoning_changed = True

    # 仅在有变更时保存
    if content_changed or tool_calls_changed or reasoning_changed:
        assistant_msg.tool_calls = merged_tool_calls
        assistant_msg.save(update_fields=["content", "tool_calls", "reasoning", "updated_at"])

    # 刷新会话 updated_at（修复 P0：ChatMessage 持久化未 touch session，
    # ChatSession.updated_at=auto_now 仅在 session.save() 时更新，导致刷新后
    # 会话排序失真，loadSessionsFromBackend fallback 选中错误会话。
    # 用 filter().update() 而非 session.save()，避免触发 AuditModel.save 的 request context 依赖）
    from django.utils import timezone

    ChatSession.objects.filter(pk=session.pk).update(updated_at=timezone.now())

    return {
        "message_id": str(assistant_msg.id),
        "content_len": len(assistant_msg.content),
        "tool_calls_count": len(merged_tool_calls),
        "content_changed": content_changed,
        "tool_calls_changed": tool_calls_changed,
        "reasoning_changed": reasoning_changed,
    }


async def persist_stream_result(
    session_id: str,
    user_id: int | None,
    content: str,
    tool_calls_map: dict[str, dict[str, Any]],
    message_id: str | None = None,
    reasoning: dict[str, Any] | None = None,
) -> str | None:
    """流结束时持久化 AI 回复内容与工具调用到 ``ChatMessage``。

    修复跨浏览器一致性问题：
        - 触发浏览器通过 SSE 流式接收完整 content + N 个 tool_calls
        - 非触发浏览器依赖 STREAM_FINALIZED 后拉取的 ``Message.content`` +
          ``Message.tool_calls``，必须与触发浏览器一致

    合并策略：
        - ``content``：仅当新内容严格长于已有内容时覆盖（避免覆盖前端更长版本）；
          已有内容等长或更长时保留前端版本
        - ``tool_calls``：增量合并（``_merge_tool_calls_incremental``），
          已匹配条目做字段级状态演进（保留 approval 字段，演进
          status/state/result/error，P3-R2），未匹配的新 tool_call 追加
        - ``reasoning``：仅当传入非空 reasoning 且其 content 与已有值不同时覆盖

    持久化后广播 ``MESSAGE_UPDATED`` 事件到同会话其他浏览器，
    通知非触发浏览器拉取完整数据。

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

    Returns:
        str | None: 持久化的 message_id（成功时）；None 表示跳过持久化
    """
    if not session_id:
        return None

    # 延迟导入避免循环依赖
    from asgiref.sync import sync_to_async

    from Django_xm.common.event_schema import EventSource, EventType
    from Django_xm.common.realtime_events import publish_event

    # 构建持久化的 tool_calls 列表
    new_tool_calls = _build_persisted_tool_calls(tool_calls_map)

    try:
        # ORM 操作通过 sync_to_async 调用，规避 async 上下文限制
        result = await sync_to_async(_persist_to_db_sync, thread_sensitive=True)(
            session_id, message_id, content, new_tool_calls, reasoning
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

        # 广播 MESSAGE_UPDATED 事件，通知非触发浏览器拉取完整数据
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

        return result["message_id"]

    except Exception:
        logger.exception(f"[persist_stream_result] 持久化失败: session_id={session_id}, message_id={message_id}")
        return None
