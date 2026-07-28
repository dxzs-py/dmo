"""
分析模块信号处理

仅负责记录分析事件（UserEvent），不处理缓存失效或日志。
缓存失效逻辑已由各应用自己的 signals.py 处理。

信号注册在 AnalyticsConfig.ready() 中完成，使用 apps.get_model() 延迟获取跨 app 模型类，
避免模块级跨 app 导入产生的循环依赖。
"""

import logging

from django.db import transaction
from django.db.models.signals import post_delete, post_save

from Django_xm.apps.analytics.models import EventCategory, EventType, UserEvent
from Django_xm.common.request_utils import get_client_ip, get_user_agent

logger = logging.getLogger(__name__)


def _safe_record_event(**kwargs):
    def _do_create():
        try:
            UserEvent.objects.create(**kwargs)
        except Exception as e:
            logger.warning(f"记录分析事件失败: {e}")
    transaction.on_commit(_do_create)


def _get_request_info(instance):
    from Django_xm.apps.core.base_models import get_current_request
    request = get_current_request()
    return get_client_ip(request), get_user_agent(request)


def on_chat_session_created(sender, instance, created, **kwargs):
    if not created:
        return
    ip, ua = _get_request_info(instance)
    _safe_record_event(
        user=instance.user,
        event_type=EventType.CHAT_SESSION_CREATE,
        event_category=EventCategory.CHAT,
        session_id=instance.session_id,
        resource_id=instance.session_id,
        resource_type='chat_session',
        metadata={'mode': instance.mode, 'title': instance.title},
        ip_address=ip,
        user_agent=ua,
    )


def on_chat_message_created(sender, instance, created, **kwargs):
    if not created:
        return
    ip, ua = _get_request_info(instance)
    if instance.role == 'user':
        event_type = EventType.CHAT_MESSAGE_SEND
    elif instance.role == 'assistant':
        event_type = EventType.CHAT_MESSAGE_RECEIVE
    else:
        return
    metadata = {
        'model': instance.model or '',
        'token_count': instance.token_count,
        'token_detail': instance.token_detail,
        'response_time': instance.response_time,
    }
    if instance.session:
        metadata['mode'] = instance.session.mode
    _safe_record_event(
        user=instance.session.user if instance.session else None,
        event_type=event_type,
        event_category=EventCategory.CHAT,
        session_id=instance.session.session_id if instance.session else '',
        resource_id=str(instance.id),
        resource_type='chat_message',
        metadata=metadata,
        ip_address=ip,
        user_agent=ua,
        duration_ms=int(instance.response_time * 1000) if instance.response_time else None,
        is_success=True,
    )


def on_chat_attachment_created(sender, instance, created, **kwargs):
    if not created:
        return
    ip, ua = _get_request_info(instance)
    _safe_record_event(
        user=instance.session.user if instance.session else None,
        event_type=EventType.FILE_UPLOAD,
        event_category=EventCategory.FILE,
        session_id=instance.session.session_id if instance.session else '',
        resource_id=str(instance.id),
        resource_type='chat_attachment',
        metadata={
            'file_type': instance.file_type,
            'file_size': instance.file_size,
            'original_name': instance.original_name,
        },
        ip_address=ip,
        user_agent=ua,
    )


def on_document_created(sender, instance, created, **kwargs):
    if not created:
        return
    ip, ua = _get_request_info(instance)
    _safe_record_event(
        user=instance.index.user if instance.index else None,
        event_type=EventType.RAG_DOCUMENT_UPLOAD,
        event_category=EventCategory.RAG,
        resource_id=str(instance.id),
        resource_type='document',
        metadata={
            'filename': instance.filename,
            'file_type': instance.file_type,
            'file_size': instance.file_size,
            'index_name': instance.index.index_name if instance.index else '',
        },
        ip_address=ip,
        user_agent=ua,
    )


def on_document_deleted(sender, instance, **kwargs):
    ip, ua = _get_request_info(instance)
    _safe_record_event(
        user=instance.index.user if instance.index else None,
        event_type=EventType.RAG_DOCUMENT_DELETE,
        event_category=EventCategory.RAG,
        resource_id=str(instance.id),
        resource_type='document',
        metadata={
            'filename': instance.filename,
            'index_name': instance.index.index_name if instance.index else '',
        },
        ip_address=ip,
        user_agent=ua,
    )


def on_index_created(sender, instance, created, **kwargs):
    if not created:
        return
    ip, ua = _get_request_info(instance)
    _safe_record_event(
        user=instance.user,
        event_type=EventType.RAG_INDEX_CREATE,
        event_category=EventCategory.RAG,
        resource_id=str(instance.id),
        resource_type='document_index',
        metadata={'index_name': instance.index_name},
        ip_address=ip,
        user_agent=ua,
    )


def on_workflow_session_created(sender, instance, created, **kwargs):
    if not created:
        return
    ip, ua = _get_request_info(instance)
    _safe_record_event(
        user=instance.created_by,
        event_type=EventType.WORKFLOW_START,
        event_category=EventCategory.WORKFLOW,
        session_id=instance.thread_id,
        resource_id=instance.thread_id,
        resource_type='workflow_session',
        metadata={'user_question': instance.user_question[:200]},
        ip_address=ip,
        user_agent=ua,
    )


def on_research_task_created(sender, instance, created, **kwargs):
    if not created:
        return
    ip, ua = _get_request_info(instance)
    _safe_record_event(
        user=instance.created_by,
        event_type=EventType.RESEARCH_START,
        event_category=EventCategory.RESEARCH,
        session_id=instance.task_id,
        resource_id=instance.task_id,
        resource_type='research_task',
        metadata={
            'query': instance.query[:200],
            'depth': instance.research_depth,
            'enable_web_search': instance.enable_web_search,
        },
        ip_address=ip,
        user_agent=ua,
    )


def register_signals():
    """在 AppConfig.ready() 中调用，使用 apps.get_model() 延迟注册信号"""
    from django.apps import apps

    ChatSession = apps.get_model('chat', 'ChatSession')
    ChatMessage = apps.get_model('chat', 'ChatMessage')
    ChatAttachment = apps.get_model('attachments', 'ChatAttachment')
    Document = apps.get_model('knowledge', 'Document')
    DocumentIndex = apps.get_model('knowledge', 'DocumentIndex')
    WorkflowSession = apps.get_model('learning', 'WorkflowSession')
    ResearchTask = apps.get_model('research', 'ResearchTask')

    post_save.connect(on_chat_session_created, sender=ChatSession)
    post_save.connect(on_chat_message_created, sender=ChatMessage)
    post_save.connect(on_chat_attachment_created, sender=ChatAttachment)
    post_save.connect(on_document_created, sender=Document)
    post_delete.connect(on_document_deleted, sender=Document)
    post_save.connect(on_index_created, sender=DocumentIndex)
    post_save.connect(on_workflow_session_created, sender=WorkflowSession)
    post_save.connect(on_research_task_created, sender=ResearchTask)
