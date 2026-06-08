"""
Chat 跨 app 服务层 - 供其他 app 调用的接口

解耦其他 app 对 chat.models 的直接导入，通过薄封装的 ORM 查询提供服务。
"""
from django.apps import apps

from ..models import ChatMode


def get_chat_session(session_id, user=None):
    """获取聊天会话（返回对象或 None）"""
    ChatSession = apps.get_model('chat', 'ChatSession')
    qs = ChatSession.objects.filter(session_id=session_id, is_deleted=False)
    if user is not None:
        qs = qs.filter(user=user)
    return qs.first()


def get_chat_session_strict(session_id, user):
    """获取聊天会话，不存在则抛出 DoesNotExist"""
    ChatSession = apps.get_model('chat', 'ChatSession')
    return ChatSession.objects.get(session_id=session_id, user=user, is_deleted=False)


def clear_knowledge_base_selection(user_id=None, kb_name=None):
    """清除会话中选中的知识库引用"""
    ChatSession = apps.get_model('chat', 'ChatSession')
    qs = ChatSession.objects.filter(is_deleted=False)
    if user_id is not None:
        qs = qs.filter(user_id=user_id)
    if kb_name is not None:
        qs = qs.filter(selected_knowledge_base=kb_name)
    qs.update(selected_knowledge_base='')


def soft_delete_session(session_id):
    """软删除会话，返回是否成功

    checkpoint/Store 数据清理已由 chat/signals.py 的 on_session_delete
    通过自定义信号 ai_data_cleanup_needed 委托给 ai_engine 的 Celery 任务处理。
    """
    ChatSession = apps.get_model('chat', 'ChatSession')
    session = ChatSession.objects.filter(session_id=session_id, is_deleted=False).first()
    if session:
        session.soft_delete()
        return True
    return False


def get_chat_mode_labels():
    """获取聊天模式标签映射 {value: label}"""
    return {m.value: m.label for m in ChatMode}
