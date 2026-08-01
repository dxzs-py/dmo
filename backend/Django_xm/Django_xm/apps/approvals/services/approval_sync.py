"""审批状态同步到 ChatMessage 模块（从 approval_service.py 拆分，Task 15.1）。

将 Approval 的完整字段（state + UI 展示字段）同步到关联 ChatMessage.tool_calls[].approval，
确保前端刷新后 loadSessionDetail 能拿到完整数据，UI 元素不再缺失。

覆盖全状态链：pending → processing → waiting → approved/rejected/timeout，
统一深度研究模式与代理模式的行为。

核心函数：
- _match_tool_call_in_list：在 tool_calls 列表中匹配 approval 关联项
- _build_approval_sync_fields：构造完整同步字段字典
- _apply_sync_fields_to_approval：应用同步字段到 tool_call.approval（幂等）
- sync_approval_state_to_chat_message：同步入口（含模型懒加载与匹配回退重建）
"""

import logging
from typing import Any

from Django_xm.apps.approvals.models import Approval

logger = logging.getLogger(__name__)


def _match_tool_call_in_list(tool_calls, approval: Approval):
    """在 tool_calls 列表中查找与 approval 关联的 toolCall。

    匹配策略（与前端 findToolCallById 一致）：
      1. tool_call_id 精确匹配（tc.id 或 tc.tool_call_id 或 tc.approval.tool_call_id）
      2. approval.interrupt_id 匹配（tc.id 或 tc.approval.interrupt_id）
    """
    if not tool_calls:
        return None
    approval_extra = approval.extra if isinstance(approval.extra, dict) else {}
    tool_call_id = approval_extra.get("tool_call_id") or ""
    interrupt_id = approval.interrupt_id or ""

    # 1. tool_call_id 匹配
    if tool_call_id:
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            if (
                tc.get("id") == tool_call_id
                or tc.get("tool_call_id") == tool_call_id
                or (isinstance(tc.get("approval"), dict) and tc["approval"].get("tool_call_id") == tool_call_id)
            ):
                return tc

    # 2. interrupt_id 匹配
    if interrupt_id:
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            if tc.get("id") == interrupt_id:
                return tc
            appr = tc.get("approval")
            if isinstance(appr, dict) and (
                appr.get("interrupt_id") == interrupt_id or appr.get("tool_call_id") == interrupt_id
            ):
                return tc

    return None


def _build_approval_sync_fields(
    approval: Approval,
    state: str,
    approval_extra: dict,
    tc_id: str,
    graph_interrupt_id: str | None,
) -> dict[str, Any]:
    """构造要同步到 tool_call.approval 的完整字段字典。

    根本性修复（V1/V2）：同步完整 UI 字段到 ChatMessage.tool_calls[].approval，
    确保前端刷新后 loadSessionDetail 能拿到 title/description/operation 等 UI 字段。

    仅包含非空值字段，调用方负责字段比对与写库。

    Args:
        approval: Approval 模型实例
        state: 审批状态
        approval_extra: approval.extra 字典
        tc_id: tool_call_id（从 extra 提取）
        graph_interrupt_id: 批次 ID（从 extra 提取）

    Returns:
        Dict[str, Any]: 同步字段字典
    """
    sync_fields: dict[str, Any] = {
        "state": state,
        "interrupt_id": approval.interrupt_id,
    }
    if tc_id:
        sync_fields["tool_call_id"] = tc_id
    if graph_interrupt_id:
        sync_fields["graph_interrupt_id"] = graph_interrupt_id
    # UI 展示字段（V1/V2 根因修复）
    if approval.title:
        sync_fields["title"] = approval.title
    if approval.description:
        sync_fields["description"] = approval.description
    if approval.operation:
        sync_fields["operation"] = approval.operation
    if approval.danger_level:
        sync_fields["danger_level"] = approval.danger_level
    if approval.parameters:
        sync_fields["parameters"] = approval.parameters
    if approval.tool_name:
        sync_fields["tool_name"] = approval.tool_name
    if approval.action:
        sync_fields["action"] = approval.action
    if approval.user_input:
        sync_fields["user_input"] = approval.user_input
    # 路由字段
    if approval.source:
        sync_fields["source"] = approval.source
    if approval.source_id:
        sync_fields["source_id"] = approval.source_id
    if approval.chat_session_id:
        sync_fields["chat_session_id"] = approval.chat_session_id
    # 时间字段（ISO 格式，与 _build_payload 一致）
    if approval.created_at:
        sync_fields["created_at"] = approval.created_at.isoformat().replace("+00:00", "Z")
    if approval.expires_at:
        sync_fields["expires_at"] = approval.expires_at.isoformat().replace("+00:00", "Z")
    # 透传 extra 中的 message_id（前端依赖此字段精确定位消息）
    if approval_extra.get("message_id") is not None:
        sync_fields["message_id"] = approval_extra["message_id"]

    return sync_fields


def _apply_sync_fields_to_approval(tc: dict, sync_fields: dict[str, Any]) -> bool:
    """将 sync_fields 应用到 tool_call.approval，返回是否有字段变更。

    仅写入非 None 值，后端为权威源（覆盖本地值）。调用方依赖返回值判断是否需要 save。

    Args:
        tc: tool_call 字典（含 approval 字段）
        sync_fields: _build_approval_sync_fields 返回的同步字段字典

    Returns:
        bool: 是否有字段变更
    """
    if not isinstance(tc.get("approval"), dict):
        tc["approval"] = {}
    approval_dict = tc["approval"]

    changed = False
    for key, value in sync_fields.items():
        if value is None:
            continue
        if approval_dict.get(key) != value:
            approval_dict[key] = value
            changed = True
    return changed


def sync_approval_state_to_chat_message(approval: Approval, state: str) -> bool:
    """同步审批完整字段到关联 ChatMessage.tool_calls 的 approval。

    根本性修复（V1/V2）：除 state 外，同步 title/description/operation/danger_level/
    parameters/tool_name/action/user_input 等 UI 字段，确保前端刷新后
    loadSessionDetail 能拿到完整数据，UI 元素不再缺失。

    覆盖全状态链：pending → processing → waiting → approved/rejected/timeout，
    确保刷新后 API 返回的 tool_calls 中 approval 字段反映最新状态与完整 UI 数据。

    深度研究审批通过后，前端非触发浏览器依赖全量同步读取审批状态。
    若后端 Message.tool_calls 中 approval.state 未更新，全量同步会用
    陈旧数据覆盖前端正确的本地状态。

    本函数确保所有审批状态（含 pending 等非终态）被持久化到 Message.tool_calls，
    统一深度研究模式与代理模式的行为（代理模式通过 SSE 流的 _debouncedToolSync
    持续 PATCH）。函数内部有全字段比对幂等检查，重复调用无副作用。

    Args:
        approval: Approval 模型实例
        state: 审批状态（pending/processing/waiting/approved/rejected/timeout）

    Returns:
        bool: 是否成功更新了 Message
    """
    if not approval.chat_session_id:
        return False

    try:
        from django.apps import apps

        ChatMessage = apps.get_model("chat", "ChatMessage")
    except Exception as e:
        logger.warning(f"[ApprovalService] 获取 ChatMessage 模型失败: {e}")
        return False

    # 查找关联的 ChatMessage
    # 1. 通过 research_task_id 查找（深度研究场景）
    # 2. 通过 approval.extra.message_id 精确匹配（重新生成场景，避免回退到错误消息）
    # 3. 回退到 session 内最新的 assistant 消息
    chat_msg = None
    if approval.source == Approval.SOURCE_DEEP_RESEARCH and approval.source_id:
        chat_msg = (
            ChatMessage.objects.filter(
                research_task_id=approval.source_id,
                role="assistant",
                is_deleted=False,
            )
            .order_by("-created_at")
            .first()
        )

    if chat_msg is None:
        approval_extra = approval.extra if isinstance(approval.extra, dict) else {}
        msg_id = approval_extra.get("message_id")
        if msg_id and approval.chat_session_id:
            try:
                chat_msg = ChatMessage.objects.get(
                    id=msg_id,
                    session__session_id=approval.chat_session_id,
                    is_deleted=False,
                )
            except (ChatMessage.DoesNotExist, ValueError):
                pass

    if chat_msg is None and approval.chat_session_id:
        chat_msg = (
            ChatMessage.objects.filter(
                session__session_id=approval.chat_session_id,
                role="assistant",
                is_deleted=False,
            )
            .order_by("-created_at")
            .first()
        )

    if chat_msg is None:
        logger.info(
            f"[ApprovalService] sync_approval_state_to_chat_message: 未找到关联 ChatMessage, "
            f"interrupt_id={approval.interrupt_id}, chat_session_id={approval.chat_session_id}"
        )
        return False

    tool_calls = list(chat_msg.tool_calls or [])
    target_tc = _match_tool_call_in_list(tool_calls, approval)
    if target_tc is None:
        # 重建缺失的 tool_call 项并追加到 tool_calls
        # 根本性修复：原实现 tool_calls 为空时静默 return False，
        # 导致 approval_pending 时（ChatMessage.tool_calls 尚未保存，时序竞态：
        # request_approval_async 在 views_chat.py L716 调用，而 tool_calls 在
        # finally 块 L826-L881 才保存）approval 字段未写入数据库，
        # 前端刷新后 approval UI 字段（title/description/operation 等）缺失。
        # 删除静默返回，让 tool_calls 为空时也进入重建逻辑，确保 approval 字段及时落库。
        # 同时覆盖 tool_calls 非空但未找到匹配项的场景（tool_call_id 不匹配）。
        # finally 块保存时 merge_existing_approval_fields 会保留此 approval 字段。
        approval_extra = approval.extra if isinstance(approval.extra, dict) else {}
        tool_call_id = approval_extra.get("tool_call_id") or approval.interrupt_id
        target_tc = {
            "id": tool_call_id,
            "tool_call_id": tool_call_id,
            "name": approval.tool_name,
            "args": approval.parameters or {},
            "parameters": approval.parameters or {},
            "input": approval.parameters or {},
            "approval": {},
        }
        tool_calls.append(target_tc)
        logger.info(
            f"[ApprovalService] sync_approval_state_to_chat_message: 已重建缺失 tool_call 项, "
            f"msg={chat_msg.id}, interrupt_id={approval.interrupt_id}, "
            f"tool_call_id={tool_call_id}, tool_calls_was_empty={len(tool_calls) == 1}"
        )
        # 继续进入后续的 approval.state 更新逻辑（old_state 为 None，会触发更新）

    # 构造完整同步字段（V1/V2 根因修复：同步全 UI 字段）
    approval_extra = approval.extra if isinstance(approval.extra, dict) else {}
    tc_id = approval_extra.get("tool_call_id") or ""
    graph_interrupt_id = approval_extra.get("graph_interrupt_id")
    sync_fields = _build_approval_sync_fields(approval, state, approval_extra, tc_id, graph_interrupt_id)

    # 全字段比对应用（幂等：无字段变更则跳过写库）
    old_state = target_tc.get("approval", {}).get("state") if isinstance(target_tc.get("approval"), dict) else None
    target_changed = _apply_sync_fields_to_approval(target_tc, sync_fields)
    if not target_changed and old_state == state:
        # 主字段无变更且 state 未变，跳过写库（保留幂等性）
        return False

    # 同步更新 versions 中的对应 toolCall（全字段）
    versions = chat_msg.versions or []
    version_updated = False
    if isinstance(versions, list) and versions:
        for ver in versions:
            if not isinstance(ver, dict):
                continue
            ver_tcs = ver.get("tool_calls") or []
            if not ver_tcs:
                continue
            ver_tc = _match_tool_call_in_list(ver_tcs, approval)
            if ver_tc is not None:
                if _apply_sync_fields_to_approval(ver_tc, sync_fields):
                    version_updated = True

    # 保存
    update_fields = ["tool_calls"]
    if version_updated:
        update_fields.append("versions")
    chat_msg.tool_calls = tool_calls
    if version_updated:
        chat_msg.versions = versions
    chat_msg.save(update_fields=update_fields)

    logger.info(
        f"[ApprovalService] sync_approval_state_to_chat_message: 已同步审批状态(全字段), "
        f"msg={chat_msg.id}, interrupt_id={approval.interrupt_id}, "
        f"old_state={old_state}, new_state={state}, version_updated={version_updated}"
    )
    return True
