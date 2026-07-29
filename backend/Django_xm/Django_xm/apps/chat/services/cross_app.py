"""
Chat 跨 app 服务层 - 供其他 app 调用的接口

解耦其他 app 对 chat.models 的直接导入，通过薄封装的 ORM 查询提供服务。
"""

from django.apps import apps

from ..models import ChatMode


def get_chat_session(session_id, user=None):
    """获取聊天会话（返回对象或 None）"""
    ChatSession = apps.get_model("chat", "ChatSession")
    qs = ChatSession.objects.filter(session_id=session_id, is_deleted=False)
    if user is not None:
        qs = qs.filter(user=user)
    return qs.first()


def get_chat_session_strict(session_id, user):
    """获取聊天会话，不存在则抛出 DoesNotExist"""
    ChatSession = apps.get_model("chat", "ChatSession")
    return ChatSession.objects.get(session_id=session_id, user=user, is_deleted=False)


def clear_knowledge_base_selection(user_id=None, kb_name=None):
    """清除会话中选中的知识库引用"""
    ChatSession = apps.get_model("chat", "ChatSession")
    qs = ChatSession.objects.filter(is_deleted=False)
    if user_id is not None:
        qs = qs.filter(user_id=user_id)
    if kb_name is not None:
        qs = qs.filter(selected_knowledge_base=kb_name)
    qs.update(selected_knowledge_base="")


def soft_delete_session(session_id):
    """软删除会话，返回是否成功

    checkpoint/Store 数据清理已由 chat/signals.py 的 on_session_delete
    通过自定义信号 ai_data_cleanup_needed 委托给 ai_engine 的 Celery 任务处理。
    """
    ChatSession = apps.get_model("chat", "ChatSession")
    session = ChatSession.objects.filter(session_id=session_id, is_deleted=False).first()
    if session:
        session.soft_delete()
        return True
    return False


def get_chat_mode_labels():
    """获取聊天模式标签映射 {value: label}"""
    return {m.value: m.label for m in ChatMode}


# ── 供 research 应用调用的服务封装（消除 research → chat.models 直接导入） ────


def get_chat_message_for_writeback(
    message_id: str | None = None,
    research_task_id: str | None = None,
) -> dict | None:
    """获取聊天消息用于深度研究回写。

    查找顺序：message_id 优先（稳定正向关联），research_task_id 回退（旧数据兼容）。
    仅返回 assistant 角色且未软删除的消息。

    Args:
        message_id: ChatMessage ID（字符串或整数）
        research_task_id: 深度研究任务 ID（fallback 路径）

    Returns:
        dict: {'id': str, 'session_id': str, 'content': str} 或 None
    """
    ChatMessage = apps.get_model("chat", "ChatMessage")
    qs = ChatMessage.objects.filter(role="assistant", is_deleted=False).select_related("session")

    if message_id:
        chat_msg = qs.filter(id=message_id).first()
    elif research_task_id:
        chat_msg = qs.filter(research_task_id=research_task_id).order_by("-created_at").first()
    else:
        return None

    if not chat_msg:
        return None

    return {
        "id": str(chat_msg.id),
        "session_id": chat_msg.session.session_id,
        "content": chat_msg.content or "",
    }


def update_chat_message_fields(
    message_id,
    *,
    content: str | None = None,
    is_streaming: bool | None = None,
    reasoning: dict | None = None,
) -> None:
    """更新聊天消息字段，仅更新非 None 的字段。

    用于深度研究回写场景，避免覆盖未提供的字段。

    Args:
        message_id: ChatMessage ID
        content: 消息内容（不更新则传 None）
        is_streaming: 流式状态（不更新则传 None）
        reasoning: 推理信息 dict（不更新则传 None）
    """
    ChatMessage = apps.get_model("chat", "ChatMessage")
    update_fields = []
    update_kwargs = {}
    if content is not None:
        update_kwargs["content"] = content
        update_fields.append("content")
    if is_streaming is not None:
        update_kwargs["is_streaming"] = is_streaming
        update_fields.append("is_streaming")
    if reasoning is not None:
        update_kwargs["reasoning"] = reasoning
        update_fields.append("reasoning")
    if not update_fields:
        return
    ChatMessage.objects.filter(id=message_id).update(**update_kwargs)


def get_active_session_ids_for_research_task(task_id: str) -> list:
    """查询关联研究任务的活跃聊天会话 ID 列表。

    用于研究任务删除时判断是否仍有活跃聊天引用：覆盖 session_id 和非 session_id
    两种关联情况（统一通过 ChatMessage.research_task_id 反查）。

    Args:
        task_id: 深度研究任务 ID

    Returns:
        list[str]: 活跃 session_id 列表（可能为空）
    """
    ChatMessage = apps.get_model("chat", "ChatMessage")
    return list(
        ChatMessage.all_objects.filter(
            research_task_id=task_id,
            session__is_deleted=False,
        ).values_list("session__session_id", flat=True)
    )
