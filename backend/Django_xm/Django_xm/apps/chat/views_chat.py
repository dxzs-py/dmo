"""
核心聊天、会话和消息视图

提供聊天交互、流式响应、会话管理、消息管理等接口。
"""

import json
import time
import asyncio
from functools import wraps

from django.db import models, transaction
from django.utils import timezone
from django.core.paginator import Paginator
from django.core.exceptions import ObjectDoesNotExist
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import BaseRenderer

from Django_xm.apps.core.throttling import ChatStreamRateThrottle
from Django_xm.apps.common.responses import success_response, error_response, validation_error_response
from Django_xm.apps.common.error_codes import ErrorCode
from Django_xm.apps.common.sse_utils import sse_response, sse_error_response

from .serializers import (
    ChatRequestSerializer,
    ChatResponseSerializer,
    ChatSessionListSerializer,
    ChatSessionDetailSerializer,
    ChatSessionCreateSerializer,
    ChatSessionUpdateSerializer,
    ChatMessageSerializer,
)
from .models import ChatSession, ChatMessage
from Django_xm.apps.attachments.models import ChatAttachment, AttachmentStatus
from .services import SecureSessionCacheService
from .services.chat_service import ChatService, ChatModeService

import logging

logger = logging.getLogger(__name__)


def log_view_action(view_func):
    @wraps(view_func)
    def wrapper(self, request, *args, **kwargs):
        start_time = time.time()
        logger.info(
            f"请求开始: {request.method} {request.get_full_path()} - "
            f"用户: {request.user.id if request.user.is_authenticated else 'anonymous'}"
        )
        try:
            result = view_func(self, request, *args, **kwargs)
            duration = time.time() - start_time
            logger.info(f"请求完成: 耗时 {duration:.3f}s")
            return result
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"请求失败: 耗时 {duration:.3f}s - 错误: {str(e)}", exc_info=True)
            raise
    return wrapper


class BaseChatAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get_session_or_404(self, session_id, user, prefetch_attachments=False):
        try:
            queryset = ChatSession.objects
            if prefetch_attachments:
                from django.db.models import Prefetch
                queryset = queryset.prefetch_related(
                    Prefetch('messages', queryset=ChatMessage.objects.prefetch_related('attachments'))
                )
            else:
                queryset = queryset.prefetch_related('messages')

            return queryset.get(
                session_id=session_id,
                user=user,
                is_deleted=False
            )
        except ObjectDoesNotExist:
            return None

    def get_message_or_404(self, message_id, user):
        try:
            return ChatMessage.objects.select_related('session').get(
                id=message_id,
                session__user=user,
                is_deleted=False
            )
        except ObjectDoesNotExist:
            return None

    def paginate_queryset(self, queryset, page_size=20):
        paginator = Paginator(queryset, page_size)
        page_number = self.request.query_params.get('page', 1)
        page_obj = paginator.get_page(page_number)
        return {
            'items': page_obj.object_list,
            'total': paginator.count,
            'page': page_obj.number,
            'page_size': page_size,
            'total_pages': paginator.num_pages,
        }


class ChatView(BaseChatAPIView):
    def post(self, request):
        serializer = ChatRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return sse_error_response(
                message=f"数据验证失败: {serializer.errors}",
                status_code=400,
            )

        data = serializer.validated_data
        logger.info(f"收到聊天请求: {data['message'][:50]}...")

        session = None
        session_id = data.get('session_id')
        if session_id:
            session = ChatSession.objects.filter(
                session_id=session_id,
                user=request.user,
                is_deleted=False
            ).first()

        if not session:
            with transaction.atomic():
                session = ChatSession.objects.create(
                    user=request.user,
                    mode=data.get('mode', 'basic-agent'),
                    selected_knowledge_base=data.get('selected_knowledge_base'),
                    title=data['message'][:100] if len(data['message']) > 100 else data['message']
                )
            logger.info(f"创建新会话: {session.session_id}")
        else:
            if data.get('selected_knowledge_base') is not None:
                session.selected_knowledge_base = data['selected_knowledge_base']
                session.save()

        try:
            chat_service = ChatService(user_id=request.user.id if request.user.is_authenticated else None)
            loop = _get_sse_loop()
            result = loop.run_until_complete(chat_service.process_chat_request(data))

            logger.info(f"聊天请求处理完成，响应长度: {len(result.get('message', ''))} 字符")

            with transaction.atomic():
                from .services.message_service import MessagePersistenceService
                persistence = MessagePersistenceService()
                user_message, ai_message = persistence.save_message_pair(
                    session=session,
                    user_content=data['message'],
                    ai_content=result.get('message', ''),
                    attachment_ids=data.get('attachment_ids')
                )

            result_data = ChatResponseSerializer(result).data
            result_data['session_id'] = session.session_id
            result_data['user_message_id'] = user_message.id
            result_data['ai_message_id'] = ai_message.id

            return success_response(
                data=result_data,
                message='操作成功'
            )

        except Exception as e:
            error_msg = f"处理聊天请求时出错: {str(e)}"
            logger.error(error_msg)

            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message='抱歉，处理您的请求时出现错误',
                data=ChatResponseSerializer({
                    'message': '抱歉，处理您的请求时出现错误。',
                    'mode': data.get('mode', 'basic-agent'),
                    'tools_used': [],
                    'success': False,
                    'error': str(e),
                    'session_id': session.session_id if session else None
                }).data,
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class SSERenderer(BaseRenderer):
    media_type = 'text/event-stream'
    format = 'txt'

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


# 模块级 SSE 事件循环单例，避免每个请求创建新循环
_SSE_LOOP: asyncio.AbstractEventLoop = None


def _get_sse_loop():
    global _SSE_LOOP
    if _SSE_LOOP is None or _SSE_LOOP.is_closed():
        _SSE_LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_SSE_LOOP)
    return _SSE_LOOP


def _run_async_next(agen):
    return _get_sse_loop().run_until_complete(agen.__anext__())


def _run_async_close(agen):
    _get_sse_loop().run_until_complete(agen.aclose())


class ChatStreamView(BaseChatAPIView):
    renderer_classes = [SSERenderer]
    throttle_classes = [ChatStreamRateThrottle]

    def post(self, request):
        serializer = ChatRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(
                errors=serializer.errors,
                message="数据验证失败"
            )

        data = serializer.validated_data
        logger.info(f"收到流式聊天请求: {data['message'][:50]}..., attachment_ids={data.get('attachment_ids')}")

        attachment_ids = data.get('attachment_ids') or []
        original_attachment_ids = list(attachment_ids)
        if attachment_ids:
            try:
                from Django_xm.apps.attachments.services.attachment_content_service import AttachmentService
                att_svc = AttachmentService()
                user_content = att_svc.build_user_content(data['message'], attachment_ids)
                if user_content["type"] == "text":
                    data['message'] = user_content["content"]
                    data['_preloaded_attachment_type'] = 'text'
                else:
                    data['_preloaded_attachment_content'] = user_content["content"]
                    data['_preloaded_attachment_type'] = 'multimodal'
                data['_has_attachments'] = True
                data['attachment_ids'] = []
            except Exception as e:
                logger.error(f"预加载附件内容失败: {e}", exc_info=True)

        def generate():
            gen = None
            try:
                if original_attachment_ids:
                    yield f"data: {json.dumps({'type': 'attachment_ids', 'data': original_attachment_ids}, ensure_ascii=False)}\n\n"

                chat_service = ChatService(user_id=request.user.id if request.user.is_authenticated else None)
                gen = chat_service.process_stream_chat_request(data).__aiter__()

                while True:
                    try:
                        event = _run_async_next(gen)
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    except StopAsyncIteration:
                        break
            except Exception as e:
                logger.error(f"流式处理出错: {str(e)}", exc_info=True)
                from Django_xm.apps.ai_engine.services.exceptions import (
                    AgentExecutionError, ModelCallError, RateLimitExceededError,
                    RAGRetrievalError, GuardrailsValidationError, CheckpointError,
                    classify_exception,
                )
                classified = classify_exception(e)
                user_message = "抱歉，处理您的请求时出现错误"
                if isinstance(classified, ModelCallError):
                    user_message = "模型服务暂时不可用，请稍后重试"
                elif isinstance(classified, RateLimitExceededError):
                    user_message = "请求频率超限，请稍后再试"
                elif isinstance(classified, GuardrailsValidationError):
                    user_message = "内容验证未通过，请调整后重试"
                elif isinstance(classified, RAGRetrievalError):
                    user_message = "知识检索失败，请稍后重试"
                elif isinstance(classified, AgentExecutionError):
                    user_message = "智能体执行失败，请稍后重试"
                elif isinstance(classified, CheckpointError):
                    user_message = "状态保存失败，请稍后重试"
                elif isinstance(e, ConnectionError):
                    user_message = "网络连接失败，请检查网络后重试"
                elif isinstance(e, TimeoutError):
                    user_message = "请求超时，请稍后重试"
                elif not classified.recoverable:
                    user_message = "服务暂时不可用，请稍后重试"
                error_data = {
                    "type": "error",
                    "message": user_message,
                    "error": str(e),
                }
                yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"
            finally:
                if gen is not None:
                    _run_async_close(gen)
                yield "data: [DONE]\n\n"

        return sse_response(generate())


class ChatModesView(BaseChatAPIView):
    def get(self, request):
        from Django_xm.apps.cache_manager.services.cache_service import CacheService, CacheTTL

        cache_key = "chat:modes"
        cached = CacheService.get(cache_key)
        if cached is not None:
            return success_response(data=cached)

        modes = ChatModeService.get_supported_modes()

        result = {
            'modes': modes,
            'default': 'basic-agent'
        }
        CacheService.set(cache_key, result, CacheTTL.TOOL_LONG)
        return success_response(data=result, message='操作成功')


class ChatSessionListView(BaseChatAPIView):
    @log_view_action
    def get(self, request):
        user_id = request.user.id
        try:
            page_size = min(int(request.query_params.get('page_size', 20)), 100)
        except (ValueError, TypeError):
            page_size = 20

        cached_sessions = SecureSessionCacheService.get_user_sessions_list(user_id)

        if cached_sessions:
            try:
                sessions_data = []
                for session_id in cached_sessions:
                    cached_session = SecureSessionCacheService.get_cached_session(user_id, session_id)
                    if cached_session:
                        sessions_data.append(cached_session)

                if sessions_data:
                    sessions_data.sort(key=lambda x: x.get('updated_at', ''), reverse=True)
                    total = len(sessions_data)
                    page = 1
                    start = (page - 1) * page_size
                    end = start + page_size
                    paged_items = sessions_data[start:end]
                    logger.info(f"Returning {len(paged_items)} cached sessions for user {user_id}")
                    return success_response(data={
                        'items': paged_items,
                        'total': total,
                        'page': page,
                        'page_size': page_size,
                        'total_pages': (total + page_size - 1) // page_size,
                    })
            except Exception as e:
                logger.warning(f"Cache read failed, falling back to DB: {str(e)}")

        sessions = ChatSession.objects.filter(
            user=request.user,
            is_deleted=False
        ).select_related(
            'user'
        ).annotate(
            message_count=models.Count('messages')
        ).order_by('-updated_at')

        paginated_data = self.paginate_queryset(sessions, page_size)
        serializer = ChatSessionListSerializer(paginated_data['items'], many=True)
        response_data = {
            **paginated_data,
            'items': serializer.data,
        }

        for item_data in response_data['items']:
            SecureSessionCacheService.cache_session(user_id, item_data)

        return success_response(data=response_data)


class ChatSessionCreateView(BaseChatAPIView):
    @log_view_action
    @transaction.atomic
    def post(self, request):
        from Django_xm.apps.cache_manager.services.cache_service import CacheService

        serializer = ChatSessionCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(errors=serializer.errors)

        session = serializer.save(user=request.user)

        SecureSessionCacheService.invalidate_all_user_sessions(request.user.id)
        CacheService.delete(f"dashboard:user_{request.user.id}")

        return success_response(
            data=ChatSessionDetailSerializer(session).data,
            message='会话创建成功',
            status_code=status.HTTP_201_CREATED
        )


class ChatSessionDetailView(BaseChatAPIView):
    @log_view_action
    def get(self, request, session_id):
        session = self.get_session_or_404(session_id, request.user, prefetch_attachments=True)
        if not session:
            return error_response(
                code=ErrorCode.NOT_FOUND,
                message='会话不存在',
                http_status=status.HTTP_404_NOT_FOUND
            )

        serializer = ChatSessionDetailSerializer(session)
        return success_response(data=serializer.data)

    @log_view_action
    @transaction.atomic
    def put(self, request, session_id):
        from Django_xm.apps.cache_manager.services.cache_service import CacheService

        session = self.get_session_or_404(session_id, request.user)
        if not session:
            return error_response(
                code=ErrorCode.NOT_FOUND,
                message='会话不存在',
                http_status=status.HTTP_404_NOT_FOUND
            )

        serializer = ChatSessionUpdateSerializer(session, data=request.data, partial=True)
        if not serializer.is_valid():
            return validation_error_response(errors=serializer.errors, message='更新参数错误')

        serializer.save()
        CacheService.delete(f"dashboard:user_{request.user.id}")
        return success_response(
            data=ChatSessionDetailSerializer(session).data,
            message='会话更新成功'
        )

    @log_view_action
    def delete(self, request, session_id):
        from Django_xm.apps.cache_manager.services.cache_service import CacheService

        session = self.get_session_or_404(session_id, request.user)
        if not session:
            return error_response(
                code=ErrorCode.NOT_FOUND,
                message='会话不存在',
                http_status=status.HTTP_404_NOT_FOUND
            )

        session.soft_delete()

        ChatAttachment.objects.filter(
            session=session,
            is_deleted=False
        ).update(is_deleted=True, deleted_at=timezone.now(), status=AttachmentStatus.DELETED)

        SecureSessionCacheService.invalidate_all_user_sessions(request.user.id)
        CacheService.delete(f"dashboard:user_{request.user.id}")

        return success_response(message='会话删除成功')


class ChatSessionCompactView(BaseChatAPIView):
    @log_view_action
    def post(self, request, session_id):
        session = self.get_session_or_404(session_id, request.user, prefetch_attachments=True)
        if not session:
            return error_response(
                code=ErrorCode.NOT_FOUND,
                message='会话不存在',
                http_status=status.HTTP_404_NOT_FOUND
            )

        from Django_xm.apps.context_manager.services.session_compactor import SessionCompactor
        messages = ChatMessage.objects.filter(
            session=session
        ).only('role', 'content', 'tool_calls').order_by('created_at')
        message_list = list(messages.values('role', 'content', 'tool_calls'))

        compactor = SessionCompactor()
        result = compactor.compact(message_list)

        return success_response(data={
            'compressed': result.compressed,
            'originalMessageCount': result.original_message_count,
            'keptMessageCount': result.kept_message_count,
            'summary': result.summary,
            'summaryTokenEstimate': result.summary_token_estimate,
        })


class ChatMessageCreateView(BaseChatAPIView):
    @log_view_action
    @transaction.atomic
    def post(self, request, session_id):
        from Django_xm.apps.cache_manager.services.cache_service import CacheService

        session = self.get_session_or_404(session_id, request.user)
        if not session:
            return error_response(
                code=ErrorCode.NOT_FOUND,
                message='会话不存在',
                http_status=status.HTTP_404_NOT_FOUND
            )

        serializer = ChatMessageSerializer(data=request.data)
        if not serializer.is_valid():
            logger.warning(f"消息验证失败: {serializer.errors}, 请求数据: {request.data}")
            return validation_error_response(errors=serializer.errors)

        message = serializer.save(session=session)

        attachment_ids = request.data.get('attachment_ids') or []
        if attachment_ids:
            from Django_xm.apps.attachments.services.attachment_content_service import AttachmentService
            AttachmentService().link_attachments_to_message(message, attachment_ids)

        CacheService.delete(f"dashboard:user_{request.user.id}")

        return success_response(
            data=ChatMessageSerializer(message).data,
            message='消息发送成功',
            status_code=status.HTTP_201_CREATED
        )


class ChatMessageBatchCreateView(BaseChatAPIView):
    @log_view_action
    def post(self, request, session_id):
        session = self.get_session_or_404(session_id, request.user)
        if not session:
            return error_response(
                code=ErrorCode.NOT_FOUND,
                message='会话不存在',
                http_status=status.HTTP_404_NOT_FOUND
            )

        messages_data = request.data.get('messages', [])
        if not isinstance(messages_data, list):
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message='messages 字段必须是数组'
            )

        if len(messages_data) > 50:
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message='单次批量创建消息数量不能超过50条'
            )

        created_messages = []
        with transaction.atomic():
            for msg_data in messages_data:
                msg_data['session'] = session.pk
                serializer = ChatMessageSerializer(data=msg_data)
                if serializer.is_valid():
                    message = serializer.save()
                    created_messages.append(message)

        return success_response(
            data={
                'created_count': len(created_messages),
                'messages': ChatMessageSerializer(created_messages, many=True).data
            },
            message=f'批量创建 {len(created_messages)} 条消息成功'
        )


class ChatMessageUpdateView(BaseChatAPIView):
    @log_view_action
    @transaction.atomic
    def put(self, request, message_id):
        message = self.get_message_or_404(message_id, request.user)
        if not message:
            return error_response(
                code=ErrorCode.NOT_FOUND,
                message='消息不存在',
                http_status=status.HTTP_404_NOT_FOUND
            )

        serializer = ChatMessageSerializer(message, data=request.data, partial=True)
        if not serializer.is_valid():
            return validation_error_response(errors=serializer.errors, message='更新参数错误')

        serializer.save()
        return success_response(
            data=ChatMessageSerializer(message).data,
            message='消息更新成功'
        )
