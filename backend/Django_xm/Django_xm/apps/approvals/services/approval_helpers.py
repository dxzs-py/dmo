"""审批状态注入公共模块。

提供统一的 tool_calls approval 状态注入函数，供 ChatMessageSerializer
和 SnapshotView 共用，确保所有返回 tool_calls 的 API 响应包含完整的
审批中间态（pending/waiting/processing）与 UI 展示字段
（title/description/operation/danger_level 等），刷新后初始加载即可
恢复审批按钮与审批面板完整内容。

设计原则：
- 单一数据源：approval 状态始终从 Approval 表动态查询，避免 DB 字段陈旧
- 批量优化：支持预查询 approval_index 注入，避免 ListSerializer N+1
- 字段对齐：approval_index 字段与 WebSocket 事件 approval payload 结构对齐
  （title/description/operation/danger_level/action/user_input 等），
  确保触发浏览器（SSE/WebSocket）与非触发浏览器（快照 API）获取一致的
  approval 数据结构
- 缺失回退：当 ChatMessage.tool_calls 缺失某些 tool_call_id 时，
  通过 approval_index 中保存的完整 tool 信息（tool_name/parameters/extra/interrupt_id）
  重建 tool_call 项，作为统一回退数据源，避免刷新后工具调用卡片丢失
"""

import logging

from django.apps import apps

from Django_xm.common.approval_utils import derive_cross_module_id

logger = logging.getLogger(__name__)


def build_approval_index_item(apv):
    """从 Approval 实例构建索引项（与 WebSocket 事件 approval payload 字段对齐）。

    字段来源：
    - 模型字段：state/tool_name/parameters/title/description/operation/danger_level/
                action/user_input/source/source_id/chat_session_id/interrupt_id/created_at/expires_at
    - extra JSON：tool_call_id/message_id/graph_interrupt_id/tool_config 等

    与 approval_service._build_approval_extra_fields 输出的字段集合对齐，
    确保快照 API 返回的 approval 数据结构与 WebSocket 事件 approval payload 一致，
    非触发浏览器刷新后能拿到完整 UI 展示字段。

    Args:
        apv: Approval 模型实例

    Returns:
        dict: 索引项，包含完整 UI 展示字段
    """
    extra = apv.extra if isinstance(apv.extra, dict) else {}
    # cross_module_id：仅 DEEP_RESEARCH 关联 chat 时为 chat_session_id
    # （统一由 derive_cross_module_id 计算，与 approval_service 保持一致）
    cross_module_id = derive_cross_module_id(apv)
    return {
        "state": apv.state,
        "approval_id": apv.interrupt_id,
        "tool_name": apv.tool_name,
        "parameters": apv.parameters or {},
        "extra": extra,
        "interrupt_id": apv.interrupt_id,
        # UI 展示字段（与 publish_approval 的 extra_fields 对齐）
        "title": apv.title or "",
        "description": apv.description or "",
        "operation": apv.operation or "",
        "danger_level": apv.danger_level or "medium",
        "action": apv.action or "confirm",
        "user_input": apv.user_input,
        # risk_level（新标准风险等级，优先于 danger_level）
        "risk_level": extra.get("risk_level"),
        # 子 agent 嵌套层级字段（Phase E3，前端展示完整调用链路）
        "parent_tool_call_id": extra.get("parent_tool_call_id"),
        "depth": extra.get("depth"),
        "agent_name": extra.get("agent_name"),
        "agent_path": extra.get("agent_path"),
        # 路由字段
        "source": apv.source,
        "source_id": apv.source_id,
        "chat_session_id": apv.chat_session_id,
        "cross_module_id": cross_module_id,
        # 时间字段（ISO 格式，与 _build_payload 一致）
        "created_at": apv.created_at.isoformat().replace("+00:00", "Z") if apv.created_at else None,
        "expires_at": apv.expires_at.isoformat().replace("+00:00", "Z") if apv.expires_at else None,
    }


def build_approval_index(session_id):
    """构建 approval 索引：{tool_call_id: 索引项}

    从 Approval 表查询指定会话的所有审批记录，按 tool_call_id 索引。
    tool_call_id 取值优先级：extra.tool_call_id → interrupt_id（与 SnapshotView
    历史实现一致，与 approval_service._build_payload 中提取逻辑一致）。

    索引项字段与 WebSocket 事件 approval payload 对齐
    （build_approval_index_item），用于 reconstruct_tool_call_from_approval
    重建缺失的 tool_call 项与 enrich_tool_calls_with_approvals 注入完整
    approval 字段，确保刷新后非触发浏览器与触发浏览器获取一致的 approval 数据。

    Args:
        session_id: str - 会话 ID（对应 Approval.chat_session_id）

    Returns:
        dict[str, dict]: tool_call_id 到 approval 信息的映射；
                         无 session_id 或无记录时返回空 dict
    """
    if not session_id:
        return {}

    try:
        Approval = apps.get_model("approvals", "Approval")
    except Exception as e:
        logger.warning(f"[approval_helpers] 获取 Approval 模型失败: {e}")
        return {}

    approvals_qs = Approval.objects.filter(chat_session_id=session_id)
    index = {}
    for apv in approvals_qs:
        extra = apv.extra if isinstance(apv.extra, dict) else {}
        apv_tc_id = extra.get("tool_call_id") or apv.interrupt_id
        if not apv_tc_id:
            continue
        index[str(apv_tc_id)] = build_approval_index_item(apv)
    return index


def _is_missing(value):
    """判断 approval 字段值是否为缺失（需要从索引补充）。

    None 视为缺失；空字符串视为缺失；空 dict/list 视为合法值不补充
    （parameters 等字段空 dict 是合法值）。
    """
    if value is None:
        return True
    return bool(isinstance(value, str) and value == "")


def _build_approval_payload_from_index(apv_info):
    """从 approval 索引项构造 approval payload（与 WebSocket 事件 + 前端白名单对齐）。

    输出字段集 ⊇ 前端 APPROVAL_PAYLOAD_FIELDS（23 字段），缺失字段填充 null，
    确保前端 mergeApprovalNonNull 能正确合并 WebSocket 事件与快照 API 数据，
    避免快照 API 因字段缺失导致前端 approval 字段数不一致。

    与 approval_service._build_approval_extra_fields 输出的字段集合对齐，
    确保快照 API 返回的 approval 数据结构与 WebSocket 事件 approval payload 一致。
    供 reconstruct_tool_call_from_approval 与 enrich_tool_calls_with_approvals 复用，
    避免字段构造逻辑重复。

    Args:
        apv_info: dict - build_approval_index_item 返回的索引项

    Returns:
        dict: approval payload，包含前端 APPROVAL_PAYLOAD_FIELDS 全部 23 个字段
              （缺失字段以 null 表示，与 WebSocket 事件 ApprovalPayload 对齐）
    """
    extra = apv_info.get("extra") if isinstance(apv_info.get("extra"), dict) else {}
    tool_call_id = apv_info.get("interrupt_id") or extra.get("tool_call_id") or ""
    approval_id = apv_info.get("approval_id") or tool_call_id

    # tool_config 透传字段（前端用于显示 selected_tools/tool_tier）
    tool_config = extra.get("tool_config")
    if isinstance(tool_config, dict):
        selected_tools = tool_config.get("selected_tools")
        tool_tier = tool_config.get("tool_tier") or None
    else:
        selected_tools = None
        tool_tier = None

    # 始终输出前端 APPROVAL_PAYLOAD_FIELDS 全部 23 个字段（缺失填 null），
    # 与 WebSocket 事件 ApprovalPayload 字段集对齐。
    # 前端 mergeApprovalNonNull 跳过 null/空值，不会污染已有数据。
    return {
        # === 核心标识字段（11） ===
        "state": apv_info.get("state"),
        "approval_id": approval_id,
        "interrupt_id": approval_id,
        "tool_call_id": tool_call_id,
        "tool_name": apv_info.get("tool_name") or "",
        "parameters": apv_info.get("parameters") or {},
        "title": apv_info.get("title") or "",
        "description": apv_info.get("description") or "",
        "operation": apv_info.get("operation") or "",
        "danger_level": apv_info.get("danger_level") or "medium",
        "action": apv_info.get("action") or "confirm",
        # === 可选输入字段（1） ===
        "user_input": apv_info.get("user_input"),
        # === 路由字段（4） ===
        "source": apv_info.get("source"),
        "source_id": apv_info.get("source_id"),
        "chat_session_id": apv_info.get("chat_session_id"),
        "cross_module_id": apv_info.get("cross_module_id"),
        # === 时间字段（2，ISO 格式，与 _build_payload 一致） ===
        "created_at": apv_info.get("created_at"),
        "expires_at": apv_info.get("expires_at"),
        # === 关联字段（2，从 extra 透传到顶层） ===
        "message_id": extra.get("message_id"),
        "graph_interrupt_id": extra.get("graph_interrupt_id"),
        # === 风险等级 + 嵌套层级字段（从 extra 透传到顶层） ===
        "risk_level": apv_info.get("risk_level"),
        "parent_tool_call_id": apv_info.get("parent_tool_call_id"),
        "depth": apv_info.get("depth"),
        "agent_name": apv_info.get("agent_name"),
        "agent_path": apv_info.get("agent_path"),
        # === 工具配置字段（2，从 extra.tool_config 透传） ===
        "selected_tools": selected_tools,
        "tool_tier": tool_tier,
        # === 逻辑字段（1）：快照 API 无法重放 WS 事件时的剩余 pending 计数，置 null。
        # 前端 mergeApprovalNonNull 会保留 WS 事件中已写入的非空值，不影响 ?? 语义。 ===
        "remaining_pending_count": None,
    }


def reconstruct_tool_call_from_approval(approval_info):
    """从 approval 索引项重建 tool_call dict。

    当 ChatMessage.tool_calls 缺失某些 tool_call_id 但 Approval 表中有对应记录时，
    从索引项重建完整的 tool_call 项，作为统一回退数据源。

    approval 字段与 WebSocket 事件 approval payload 结构对齐
    （approval_service._build_approval_extra_fields），包含
    state/interrupt_id/tool_call_id/title/description/operation/danger_level/
    action/user_input/tool_name/parameters 等完整 UI 展示字段，
    确保重建的 tool_call 项与实时事件建立的 tool_call 项字段一致。

    Args:
        approval_info: dict - 来自 build_approval_index 的索引项

    Returns:
        dict: 重建的 tool_call 项，字段与 build_persisted_tool_calls 输出对齐：
            {id, tool_call_id, name, args, parameters, input, status, result, error,
             message_id, approval}
    """
    if not isinstance(approval_info, dict):
        return {}

    tool_call_id = approval_info.get("interrupt_id") or ""
    tool_name = approval_info.get("tool_name") or ""
    parameters = approval_info.get("parameters") or {}
    extra = approval_info.get("extra") if isinstance(approval_info.get("extra"), dict) else {}
    state = approval_info.get("state") or "pending"

    # 从 extra 中提取 args（如果存在），否则用 parameters
    args = extra.get("args") if extra.get("args") else parameters

    # 根据 approval.state 设置重建项的 status
    # 与 enrich_tool_calls_with_approvals 中的 status 注入逻辑对齐，
    # 确保重建的 tool_call 项也有正确的 status
    if state in ("pending", "waiting"):
        reconstructed_status = "waiting"
    elif state == "processing":
        reconstructed_status = "running"
    elif state == "timeout":
        reconstructed_status = "timeout"
    elif state == "rejected":
        reconstructed_status = "rejected"
    else:
        reconstructed_status = "pending"

    approval_payload = _build_approval_payload_from_index(approval_info)

    return {
        "id": tool_call_id,
        "tool_call_id": tool_call_id,
        "name": tool_name,
        "args": args,
        "parameters": parameters,
        "input": parameters,
        "status": reconstructed_status,
        # result/error：从 Approval.extra 透传（快照完整性：重建条目同样满足
        # status/result/error 字段必有；extra 无 result/error 时输出 None，
        # 与 build_persisted_tool_calls 输出的 result/error 结构保持一致）
        "result": extra.get("result"),
        "error": extra.get("error"),
        # message_id：与 SnapshotView 注入到 tool_calls 条目的 message_id 字段对齐，
        # 供前端快照校对通过 messageBackendId 将重建的工具调用挂载到对应消息
        # （重建场景下 Approval.extra 由 approval_parser 透传 message_id）
        "message_id": extra.get("message_id"),
        "approval": approval_payload,
    }


def enrich_tool_calls_with_approvals(tool_calls, session_id, approval_index=None):
    """从 Approval 表查询审批记录，动态注入 approval 状态到 tool_calls。

    供 ChatMessageSerializer 和 SnapshotView 共用，确保所有返回 tool_calls
    的 API 响应包含完整的审批中间态（pending/waiting/processing）与 UI 展示字段
    （title/description/operation/danger_level 等）。

    合并策略：
    - tool_call_id 取值优先级：tc.tool_call_id → tc.id（兼容旧格式）
    - approval 字段缺失：设置为索引中的完整 approval 副本
      （包含 state/approval_id/tool_call_id/tool_name/parameters/title/
      description/operation/danger_level/action 等，与 WebSocket 事件对齐）
    - approval 字段已存在：仅补充缺失字段（state/approval_id 与 UI 展示字段），
      保留 tool_call 自身已有的 approval 信息（避免覆盖前端写入的
      graph_interrupt_id / interrupt_id 等）

    根据 approval.state 提升 tool_calls.status，确保刷新后浏览器与未刷新浏览器
    从后端获取一致的 status（与实时事件设置的值对齐）。
    仅提升非终态 status（pending/空），不覆盖 running/completed/failed/timeout。

    Args:
        tool_calls: list[dict] - 工具调用列表
        session_id: str - 会话 ID（用于查询 Approval 表；approval_index 传入时仅作日志用）
        approval_index: dict|null - 预查询的 approval 索引
                        （build_approval_index 返回值，字段与 WebSocket 事件 approval
                         payload 对齐），传入时跳过数据库查询，用于批量序列化性能优化

    Returns:
        list[dict] - 注入 approval 状态后的 tool_calls（原列表就地修改并返回；
                     缺失的 tool_call 项会被追加到列表末尾）
    """
    if not isinstance(tool_calls, list) or not tool_calls:
        return tool_calls

    # 未预查询时现场构建（单条消息场景可接受，ListSerializer 场景应通过 context 预查询）
    if approval_index is None:
        approval_index = build_approval_index(session_id)

    if not approval_index:
        return tool_calls

    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        # tool_call_id 取值优先级：tool_call_id 字段 → id 字段（兼容旧格式）
        tc_id = tc.get("tool_call_id") or tc.get("id")
        if not tc_id:
            continue
        apv_info = approval_index.get(str(tc_id))
        if not apv_info:
            continue

        complete_approval = _build_approval_payload_from_index(apv_info)
        existing = tc.get("approval")
        if not isinstance(existing, dict):
            # 不存在 approval 字段：设置为完整 approval 副本
            tc["approval"] = complete_approval
        else:
            # 已有 approval 字段：仅补充缺失字段，保留已有信息
            # 避免覆盖前端写入的 graph_interrupt_id / interrupt_id 等
            for key, value in complete_approval.items():
                if value is None:
                    continue
                if _is_missing(existing.get(key)):
                    existing[key] = value

        # 根据 approval.state 提升 tool_calls.status
        # 根因：刷新时 snapshot 返回的 tool_calls.status 为数据库存储的 "pending"，
        # 而实时事件（tool_call_waiting）设置的 status 为 "waiting"。
        # 刷新后浏览器显示"待执行"，未刷新浏览器显示"等待中"，UI 不一致。
        # 在 enrich 时根据 approval.state 统一注入 status，确保所有模块
        # 从后端获取一致的 tool_calls.status（与实时事件设置的值对齐）。
        # 仅提升非终态 status（pending/空），不覆盖 running/completed/failed/timeout。
        apv_state = apv_info.get("state")
        tc_status = tc.get("status")
        if apv_state in ("pending", "waiting") and tc_status in (None, "", "pending"):
            tc["status"] = "waiting"
        elif apv_state == "processing" and tc_status in (None, "", "pending", "waiting"):
            tc["status"] = "running"
        elif apv_state == "timeout" and tc_status not in ("completed", "failed"):
            tc["status"] = "timeout"
        elif apv_state == "rejected" and tc_status in (None, "", "pending", "waiting"):
            tc["status"] = "rejected"

    # 补全 approval_index 中存在但 tool_calls 中缺失的 tool_call 项
    # 场景：ChatResume 尚未保存新 tool_calls 时序问题导致 tool_call_id 缺失，
    # 但 Approval 表中已保存完整 tool 信息，从索引重建避免刷新后工具调用卡片丢失
    existing_tc_ids = set()
    for tc in tool_calls:
        if isinstance(tc, dict):
            tc_id = tc.get("tool_call_id") or tc.get("id")
            if tc_id:
                existing_tc_ids.add(str(tc_id))

    for tc_id, apv_info in approval_index.items():
        if tc_id not in existing_tc_ids:
            reconstructed = reconstruct_tool_call_from_approval(apv_info)
            if reconstructed:
                tool_calls.append(reconstructed)

    return tool_calls
