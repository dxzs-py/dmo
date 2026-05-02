import logging
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from Django_xm.apps.chat.models import ChatSession, ChatMessage, ChatAttachment
from Django_xm.apps.knowledge.models import Document, DocumentIndex
from Django_xm.apps.learning.models import WorkflowSession
from Django_xm.apps.research.models import ResearchTask

from Django_xm.apps.analytics.models import UserEvent, EventCategory, EventType

logger = logging.getLogger(__name__)


def _get_client_ip(request):
    if request is None:
        return None
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def _get_user_agent(request):
    if request is None:
        return ''
    return request.META.get('HTTP_USER_AGENT', '')[:500]


def _safe_record_event(**kwargs):
    try:
        UserEvent.objects.create(**kwargs)
    except Exception as e:
        logger.warning(f"记录分析事件失败: {e}")


@receiver(post_save, sender=ChatSession)
def on_chat_session_created(sender, instance, created, **kwargs):
    if not created:
        return
    from Django_xm.apps.core.base_models import get_current_request
    request = get_current_request()
    _safe_record_event(
        user=instance.user,
        event_type=EventType.CHAT_SESSION_CREATE,
        event_category=EventCategory.CHAT,
        session_id=instance.session_id,
        resource_id=instance.session_id,
        resource_type='chat_session',
        metadata={'mode': instance.mode, 'title': instance.title},
        ip_address=_get_client_ip(request),
        user_agent=_get_user_agent(request),
    )


@receiver(post_save, sender=ChatMessage)
def on_chat_message_created(sender, instance, created, **kwargs):
    if not created:
        return
    from Django_xm.apps.core.base_models import get_current_request
    request = get_current_request()
    if instance.role == 'user':
        event_type = EventType.CHAT_MESSAGE_SEND
    elif instance.role == 'assistant':
        event_type = EventType.CHAT_MESSAGE_RECEIVE
    else:
        return
    metadata = {
        'model': instance.model or '',
        'token_count': instance.token_count,
        'cost': str(instance.cost),
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
        ip_address=_get_client_ip(request),
        user_agent=_get_user_agent(request),
        duration_ms=int(instance.response_time * 1000) if instance.response_time else None,
        is_success=True,
    )


@receiver(post_save, sender=ChatAttachment)
def on_chat_attachment_created(sender, instance, created, **kwargs):
    if not created:
        return
    from Django_xm.apps.core.base_models import get_current_request
    request = get_current_request()
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
        ip_address=_get_client_ip(request),
        user_agent=_get_user_agent(request),
    )


@receiver(post_save, sender=Document)
def on_document_created(sender, instance, created, **kwargs):
    if not created:
        return
    from Django_xm.apps.core.base_models import get_current_request
    request = get_current_request()
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
        ip_address=_get_client_ip(request),
        user_agent=_get_user_agent(request),
    )


@receiver(post_delete, sender=Document)
def on_document_deleted(sender, instance, **kwargs):
    from Django_xm.apps.core.base_models import get_current_request
    request = get_current_request()
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
        ip_address=_get_client_ip(request),
        user_agent=_get_user_agent(request),
    )


@receiver(post_save, sender=DocumentIndex)
def on_index_created(sender, instance, created, **kwargs):
    if not created:
        return
    from Django_xm.apps.core.base_models import get_current_request
    request = get_current_request()
    _safe_record_event(
        user=instance.user,
        event_type=EventType.RAG_INDEX_CREATE,
        event_category=EventCategory.RAG,
        resource_id=str(instance.id),
        resource_type='document_index',
        metadata={'index_name': instance.index_name},
        ip_address=_get_client_ip(request),
        user_agent=_get_user_agent(request),
    )


@receiver(post_save, sender=WorkflowSession)
def on_workflow_session_created(sender, instance, created, **kwargs):
    if not created:
        return
    from Django_xm.apps.core.base_models import get_current_request
    request = get_current_request()
    _safe_record_event(
        user=instance.created_by,
        event_type=EventType.WORKFLOW_START,
        event_category=EventCategory.WORKFLOW,
        session_id=instance.thread_id,
        resource_id=instance.thread_id,
        resource_type='workflow_session',
        metadata={'user_question': instance.user_question[:200]},
        ip_address=_get_client_ip(request),
        user_agent=_get_user_agent(request),
    )


@receiver(post_save, sender=ResearchTask)
def on_research_task_created(sender, instance, created, **kwargs):
    if not created:
        return
    from Django_xm.apps.core.base_models import get_current_request
    request = get_current_request()
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
        ip_address=_get_client_ip(request),
        user_agent=_get_user_agent(request),
    )
