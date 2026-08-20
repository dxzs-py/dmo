"""用户频道历史事件的幽灵会话过滤（chat 业务层）。

从 common/realtime_events.py 迁入：过滤逻辑依赖 ChatSession 模型，
属于 chat 业务数据校验，归位业务层后 common 基础层不再反向依赖 apps。
common/realtime_events.py 通过 ``set_user_history_filter`` 注册点在
chat app ready() 时注入本函数（依赖反转）。
"""

import logging

logger = logging.getLogger(__name__)


def filter_ghost_session_created(events, user_id):
    """过滤 user 频道历史事件中的幽灵 session_created。

    对 user 频道的 session_created 历史事件做数据库存在性校验：
    - 会话在数据库不存在（已删除 / 从未创建）→ 跳过该事件（幽灵会话）。
    - 会话存在 → 用数据库最新记录覆盖事件 payload 的权威字段
      （title / updated_at / message_count / mode / selected_knowledge_bases），
      避免旧事件标题覆盖数据库正确标题。

    Args:
        events: 已按 seq 升序的历史事件列表
        user_id: 用户 ID（channel_id）

    Returns:
        list[dict]: 过滤后的事件列表
    """
    # 收集所有 session_created 事件的会话 ID（批量查询，避免 N+1）
    created_ids = []
    for event in events:
        if event.get("type") != "session_created":
            continue
        payload = event.get("payload") or {}
        session_id = payload.get("session_id") or payload.get("id")
        if session_id:
            created_ids.append(session_id)
    if not created_ids:
        return events

    try:
        from django.db.models import Count

        from Django_xm.apps.chat.models import ChatSession

        sessions = list(
            ChatSession.objects.filter(session_id__in=created_ids, user_id=user_id)
            .annotate(message_count=Count("messages"))
            .only("session_id", "title", "mode", "updated_at", "selected_knowledge_bases")
        )
        session_map = {s.session_id: s for s in sessions}
    except Exception:
        # 数据库校验失败时保持原样回放，避免影响实时同步可用性
        logger.exception("[SessionHistory] 幽灵会话过滤数据库查询失败，保持原样回放")
        return events

    filtered = []
    for event in events:
        if event.get("type") != "session_created":
            filtered.append(event)
            continue
        payload = event.get("payload") or {}
        session_id = payload.get("session_id") or payload.get("id")
        session = session_map.get(session_id)
        if session is None:
            logger.info(
                f"[SessionHistory] 跳过幽灵 session_created: session={session_id}, user={user_id}"
            )
            continue
        # 用数据库最新字段覆盖事件载荷（保持 snake_case 网络键名）
        new_payload = dict(payload)
        new_payload["title"] = session.title
        new_payload["mode"] = session.mode
        new_payload["message_count"] = getattr(session, "message_count", 0)
        new_payload["selected_knowledge_bases"] = session.selected_knowledge_bases
        new_payload["updated_at"] = session.updated_at.isoformat() if session.updated_at else payload.get("updated_at")
        new_event = dict(event)
        new_event["payload"] = new_payload
        filtered.append(new_event)
    return filtered
