import json
import os
import time
import uuid
from functools import wraps
from django.conf import settings
from django.db import models, transaction
from django.utils import timezone
from django.core.paginator import Paginator
from django.http import StreamingHttpResponse, HttpResponse
from django.core.exceptions import ObjectDoesNotExist
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import BaseRenderer

from Django_xm.apps.core.throttling import ChatStreamRateThrottle
from Django_xm.apps.common.responses import success_response, error_response, validation_error_response
from Django_xm.apps.common.error_codes import ErrorCode

from .serializers import (
    ChatRequestSerializer,
    ChatResponseSerializer,
    ChatSessionListSerializer,
    ChatSessionDetailSerializer,
    ChatSessionCreateSerializer,
    ChatSessionUpdateSerializer,
    ChatMessageSerializer
)
from .models import ChatSession, ChatMessage, ChatAttachment, AttachmentStatus, AttachmentCleanupLog, StorageAlert
from .services import SecureSessionCacheService
from .services.chat_service import ChatService, ChatModeService

import logging

logger = logging.getLogger(__name__)


def log_view_action(view_func):
    """视图操作日志装饰器"""
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
    """
    聊天模块基础API视图
    
    提供通用的权限控制和辅助方法，减少子类重复代码
    """
    permission_classes = [IsAuthenticated]

    def get_session_or_404(self, session_id, user, prefetch_attachments=False):
        """获取会话对象或返回404错误响应"""
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
        """获取消息对象或返回404错误响应"""
        try:
            return ChatMessage.objects.select_related('session').get(
                id=message_id,
                session__user=user,
                is_deleted=False
            )
        except ObjectDoesNotExist:
            return None

    def paginate_queryset(self, queryset, page_size=20):
        """分页查询集"""
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


class ChatView(APIView):
    """
    聊天视图 - 非流式聊天接口
    
    使用 ChatService 处理业务逻辑，视图只负责：
    1. 请求验证（序列化器）
    2. 调用服务层
    3. 返回统一格式的响应
    """
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        serializer = ChatRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(
                errors=serializer.errors,
                message="数据验证失败"
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
            # 更新会话的知识库选择
            if data.get('selected_knowledge_base') is not None:
                session.selected_knowledge_base = data['selected_knowledge_base']
                session.save()

        try:
            chat_service = ChatService(user_id=request.user.id if request.user.is_authenticated else None)
            result = chat_service.process_chat_request(data)
            
            logger.info(f"聊天请求处理完成，响应长度: {len(result.get('message', ''))} 字符")

            with transaction.atomic():
                from .services.chat_service import MessagePersistenceService
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


class ChatStreamView(APIView):
    """
    流式聊天视图

    使用 ChatService 处理业务逻辑，视图只负责：
    1. 请求验证（序列化器）
    2. 将服务层的异步生成器转换为 SSE 响应
    3. 处理连接异常

    使用同步生成器 + asyncio 事件循环桥接，
    避免为每个请求创建独立线程和事件循环。
    """
    permission_classes = [IsAuthenticated]
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
        if attachment_ids:
            try:
                from Django_xm.apps.chat.services.chat_service import AttachmentService
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
            import asyncio
            loop = None
            gen = None
            try:
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_closed():
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)

                chat_service = ChatService(user_id=request.user.id if request.user.is_authenticated else None)
                gen = chat_service.process_stream_chat_request(data).__aiter__()

                while True:
                    try:
                        event = loop.run_until_complete(gen.__anext__())
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    except StopAsyncIteration:
                        break
            except Exception as e:
                logger.error(f"流式处理出错: {str(e)}", exc_info=True)
                error_data = {
                    "type": "error",
                    "message": "抱歉，处理您的请求时出现错误",
                    "error": str(e),
                }
                yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"
            finally:
                if gen is not None:
                    loop.run_until_complete(gen.aclose())
                yield "data: [DONE]\n\n"

        return StreamingHttpResponse(
            generate(),
            content_type='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
                'X-Content-Type-Options': 'nosniff',
            }
        )


class ChatModesView(APIView):
    """
    聊天模式列表视图
    
    使用 ChatModeService 获取支持的模式列表
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from Django_xm.apps.ai_engine.services.cache_service import CacheService, CacheTTL

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
        from Django_xm.apps.ai_engine.services.cache_service import CacheService

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
        from Django_xm.apps.ai_engine.services.cache_service import CacheService

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
        from Django_xm.apps.ai_engine.services.cache_service import CacheService

        session = self.get_session_or_404(session_id, request.user)
        if not session:
            return error_response(
                code=ErrorCode.NOT_FOUND,
                message='会话不存在',
                http_status=status.HTTP_404_NOT_FOUND
            )

        session.soft_delete()
        SecureSessionCacheService.invalidate_all_user_sessions(request.user.id)
        CacheService.delete(f"dashboard:user_{request.user.id}")

        return success_response(message='会话删除成功')


class ChatMessageCreateView(BaseChatAPIView):
    @log_view_action
    @transaction.atomic
    def post(self, request, session_id):
        from Django_xm.apps.ai_engine.services.cache_service import CacheService

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


class ChatAttachmentUploadView(BaseChatAPIView):
    ALLOWED_EXTENSIONS = {
        'txt', 'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
        'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg',
        'csv', 'json', 'xml', 'md', 'py', 'js', 'ts', 'html', 'css',
    }
    MAX_FILE_SIZE = 10 * 1024 * 1024

    @log_view_action
    def post(self, request, session_id):
        session = self.get_session_or_404(session_id, request.user)
        if not session:
            return error_response(
                code=ErrorCode.NOT_FOUND,
                message='会话不存在',
                http_status=status.HTTP_404_NOT_FOUND
            )

        uploaded_file = request.FILES.get('file')
        if not uploaded_file:
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message='请选择要上传的文件'
            )

        if uploaded_file.size > self.MAX_FILE_SIZE:
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message=f'文件大小不能超过{self.MAX_FILE_SIZE // (1024*1024)}MB'
            )

        ext = os.path.splitext(uploaded_file.name)[1].lower().lstrip('.')
        if ext not in self.ALLOWED_EXTENSIONS:
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message=f'不支持的文件类型: .{ext}，支持的类型: {", ".join(sorted(self.ALLOWED_EXTENSIONS))}'
            )

        import mimetypes
        mime_type, _ = mimetypes.guess_type(uploaded_file.name)

        VALIDATED_MIME_MAP = {
            'pdf': {'application/pdf'},
            'txt': {'text/plain'},
            'md': {'text/plain', 'text/markdown'},
            'csv': {'text/csv', 'text/plain', 'application/vnd.ms-excel'},
            'json': {'application/json', 'text/plain'},
            'py': {'text/x-python', 'text/plain'},
            'js': {'text/javascript', 'application/javascript', 'text/plain'},
            'html': {'text/html', 'text/plain'},
            'css': {'text/css', 'text/plain'},
            'png': {'image/png'},
            'jpg': {'image/jpeg'},
            'jpeg': {'image/jpeg'},
            'gif': {'image/gif'},
            'webp': {'image/webp'},
            'svg': {'image/svg+xml'},
            'docx': {'application/vnd.openxmlformats-officedocument.wordprocessingml.document'},
            'xlsx': {'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'},
        }

        if ext in VALIDATED_MIME_MAP:
            header = uploaded_file.read(512)
            uploaded_file.seek(0)
            import imghdr
            if ext in ('png', 'jpg', 'jpeg', 'gif', 'webp') and imghdr.what(None, header) is None:
                detected_mime = mimetypes.guess_type(uploaded_file.name)[0] or 'application/octet-stream'
                if detected_mime not in VALIDATED_MIME_MAP[ext]:
                    return error_response(
                        code=ErrorCode.INVALID_PARAMS,
                        message=f'文件内容与扩展名 .{ext} 不匹配，可能存在安全风险'
                    )

        attachment = ChatAttachment.objects.create(
            session=session,
            file=uploaded_file,
            original_name=uploaded_file.name,
            file_size=uploaded_file.size,
            file_type=ext,
            mime_type=mime_type or 'application/octet-stream',
        )

        from .services.attachment_lifecycle import AttachmentLifecycleService
        lifecycle = AttachmentLifecycleService()
        lifecycle.record_file_hash(attachment)

        file_url = attachment.file.url if hasattr(attachment.file, 'url') else ''

        return success_response(
            data={
                'id': attachment.id,
                'original_name': attachment.original_name,
                'file_size': attachment.file_size,
                'file_type': attachment.file_type,
                'mime_type': attachment.mime_type,
                'url': file_url,
                'created_at': attachment.created_at.isoformat(),
            },
            message='文件上传成功',
            status_code=status.HTTP_201_CREATED
        )


class ChatAttachmentListView(BaseChatAPIView):
    @log_view_action
    def get(self, request, session_id):
        session = self.get_session_or_404(session_id, request.user)
        if not session:
            return error_response(
                code=ErrorCode.NOT_FOUND,
                message='会话不存在',
                http_status=status.HTTP_404_NOT_FOUND
            )

        attachments = ChatAttachment.objects.filter(
            session=session
        ).order_by('-created_at')

        data = []
        for att in attachments:
            file_url = att.file.url if hasattr(att.file, 'url') else ''
            data.append({
                'id': att.id,
                'original_name': att.original_name,
                'file_size': att.file_size,
                'file_type': att.file_type,
                'mime_type': att.mime_type,
                'url': file_url,
                'message_id': att.message_id,
                'created_at': att.created_at.isoformat(),
            })

        return success_response(data=data)


class ChatAttachmentDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, attachment_id):
        try:
            attachment = ChatAttachment.objects.filter(
                id=attachment_id,
                session__user=request.user,
                is_deleted=False
            ).first()
            if not attachment:
                return error_response(
                    code=ErrorCode.NOT_FOUND,
                    message='附件不存在',
                    http_status=status.HTTP_404_NOT_FOUND
                )
            try:
                if attachment.file and hasattr(attachment.file, 'path'):
                    import os
                    if os.path.isfile(attachment.file.path):
                        os.remove(attachment.file.path)
            except Exception as file_err:
                logger.warning(f"删除附件文件失败: {file_err}")

            attachment.soft_delete()
            return success_response(message='附件删除成功')
        except Exception as e:
            logger.error(f"删除附件失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message='删除附件失败',
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


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

        from .services.session_compactor import SessionCompactor
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


class ChatCommandsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from Django_xm.apps.ai_engine.services.cache_service import CacheService, CacheTTL

        cache_key = "chat:commands"
        cached = CacheService.get(cache_key)
        if cached is not None:
            return success_response(data=cached)

        from .services.slash_commands import get_all_commands
        commands = get_all_commands()
        result = {'commands': commands}
        CacheService.set(cache_key, result, CacheTTL.TOOL_LONG)
        return success_response(data=result)


class ChatCommandExecuteView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        command = request.data.get('command', '')
        session_id = request.data.get('session_id')

        from .services.slash_commands import parse_command, execute_command

        parsed = parse_command(command)
        if not parsed:
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message='无效的命令格式',
            )

        command_name, args = parsed
        context = {
            "args": args,
            "user_id": request.user.id,
            "session_id": session_id,
        }

        if session_id:
            session = ChatSession.objects.filter(
                session_id=session_id,
                user=request.user,
                is_deleted=False,
            ).first()
            if session:
                messages = ChatMessage.objects.filter(session=session).order_by('created_at')
                context["session"] = {
                    "session_id": session.session_id,
                    "title": session.title,
                    "mode": session.mode,
                }
                context["messages"] = [
                    {"role": m.role, "content": m.content}
                    for m in messages
                ]

        result = execute_command(command_name, context)
        return success_response(data=result)


class ChatPermissionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from Django_xm.apps.core.permissions import PermissionService
        session_id = request.query_params.get('session_id')
        info = PermissionService.get_permission_info(request.user.id, session_id)
        return success_response(data=info)

    def put(self, request):
        from Django_xm.apps.core.permissions import PermissionService
        session_mode = request.data.get('session_mode')
        tool_permissions = request.data.get('tool_permissions', {})
        session_id = request.data.get('session_id')

        if session_mode:
            try:
                policy = PermissionService.update_session_mode(
                    request.user.id, session_mode, session_id
                )
            except ValueError as e:
                return error_response(
                    code=ErrorCode.INVALID_PARAMS,
                    message=str(e),
                )

        tool_errors = []
        for tool_name, permission in tool_permissions.items():
            try:
                PermissionService.set_tool_permission(
                    request.user.id, tool_name, permission, session_id
                )
            except ValueError as e:
                tool_errors.append(f'{tool_name}: {str(e)}')

        if tool_errors:
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message=f'部分工具权限设置失败: {"; ".join(tool_errors)}',
            )

        info = PermissionService.get_permission_info(request.user.id, session_id)
        return success_response(data=info, message='权限更新成功')


class ToolConfirmationView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from Django_xm.apps.core.permissions import get_pending_confirmation
        confirm_id = request.query_params.get('confirm_id')
        if not confirm_id:
            return error_response(code=ErrorCode.INVALID_PARAMS, message='缺少 confirm_id')

        entry = get_pending_confirmation(confirm_id)
        if not entry:
            return error_response(code=ErrorCode.NOT_FOUND, message='确认请求不存在或已过期')

        if entry['user_id'] != request.user.id:
            return error_response(code=ErrorCode.PERMISSION_DENIED, message='无权操作此确认请求')

        return success_response(data={
            'confirm_id': confirm_id,
            'tool_name': entry['tool_name'],
            'tool_args': entry['tool_args'],
            'status': entry['status'],
        })

    def post(self, request):
        from Django_xm.apps.core.permissions import (
            get_pending_confirmation, approve_tool_confirmation, deny_tool_confirmation,
        )
        confirm_id = request.data.get('confirm_id')
        action = request.data.get('action', 'approve')

        if not confirm_id:
            return error_response(code=ErrorCode.INVALID_PARAMS, message='缺少 confirm_id')

        entry = get_pending_confirmation(confirm_id)
        if not entry:
            return error_response(code=ErrorCode.NOT_FOUND, message='确认请求不存在或已过期')

        if entry['user_id'] != request.user.id:
            return error_response(code=ErrorCode.PERMISSION_DENIED, message='无权操作此确认请求')

        if action == 'approve':
            result = approve_tool_confirmation(confirm_id)
            if result is None:
                return error_response(code=ErrorCode.NOT_FOUND, message='确认请求不存在')
            return success_response(data={
                'confirm_id': confirm_id,
                'tool_name': entry['tool_name'],
                'status': 'executed',
                'result': result,
            }, message='工具已批准并执行')
        elif action == 'deny':
            success = deny_tool_confirmation(confirm_id)
            if not success:
                return error_response(code=ErrorCode.NOT_FOUND, message='确认请求不存在')
            return success_response(data={
                'confirm_id': confirm_id,
                'tool_name': entry['tool_name'],
                'status': 'denied',
            }, message='工具已拒绝')
        else:
            return error_response(code=ErrorCode.INVALID_PARAMS, message=f'无效的操作: {action}')


class ChatCostView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from Django_xm.apps.ai_engine.services.cache_service import CacheService, CacheTTL

        cache_key = "chat:cost_pricing"
        cached = CacheService.get(cache_key)
        if cached is not None:
            return success_response(data=cached)

        from Django_xm.apps.ai_engine.services.cost_tracker import get_all_model_pricing, MODEL_PRICING
        result = {
            'modelPricing': get_all_model_pricing(),
            'supportedModels': list(MODEL_PRICING.keys()),
        }
        CacheService.set(cache_key, result, CacheTTL.TOOL_LONG)
        return success_response(data=result)


class ProjectContextView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from Django_xm.apps.ai_engine.services.cache_service import CacheService, CacheTTL

        user = request.user
        cache_key = f"project_context:user_{user.id}"
        cached = CacheService.get(cache_key)
        if cached is not None:
            logger.info("项目上下文缓存命中")
            return success_response(data=cached)

        try:
            from Django_xm.apps.ai_engine.services.project_context import detect_project_context
            search_path = request.query_params.get('path')
            context = detect_project_context(search_path)
            result = context.to_dict()
            CacheService.set(cache_key, result, CacheTTL.QUERY_LONG)
            return success_response(data=result)
        except Exception as e:
            return error_response(
                code=ErrorCode.INTERNAL_ERROR,
                message=str(e),
            )


class AttachmentAdminListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.is_staff:
            return error_response(code=ErrorCode.PERMISSION_DENIED, message='无管理员权限', http_status=status.HTTP_403_FORBIDDEN)

        from .services.attachment_lifecycle import AttachmentLifecycleService
        service = AttachmentLifecycleService()

        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 20))
        status_filter = request.query_params.get('status', '')
        search = request.query_params.get('search', '')
        sort_by = request.query_params.get('sort_by', '-created_at')

        queryset = ChatAttachment.all_objects.select_related('session', 'session__user')

        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if search:
            queryset = queryset.filter(
                models.Q(original_name__icontains=search)
                | models.Q(session__session_id__icontains=search)
                | models.Q(session__title__icontains=search)
            )

        valid_sorts = ['created_at', '-created_at', 'file_size', '-file_size', 'last_accessed_at', '-last_accessed_at']
        if sort_by not in valid_sorts:
            sort_by = '-created_at'
        queryset = queryset.order_by(sort_by)

        paginator = Paginator(queryset, page_size)
        page_obj = paginator.get_page(page)

        items = []
        for att in page_obj:
            items.append({
                'id': att.id,
                'original_name': att.original_name,
                'file_size': att.file_size,
                'file_type': att.file_type,
                'mime_type': att.mime_type,
                'status': att.status,
                'reference_count': att.reference_count,
                'last_accessed_at': att.last_accessed_at.isoformat() if att.last_accessed_at else None,
                'retention_days': att.retention_days,
                'archived_path': att.archived_path,
                'archived_at': att.archived_at.isoformat() if att.archived_at else None,
                'file_hash': att.file_hash,
                'session_id': att.session.session_id if att.session else None,
                'session_title': att.session.title if att.session else None,
                'username': att.session.user.username if att.session and att.session.user else None,
                'created_at': att.created_at.isoformat(),
                'is_deleted': att.is_deleted,
            })

        stats = service.get_storage_stats()
        return success_response(data={
            'items': items,
            'total': paginator.count,
            'page': page,
            'page_size': page_size,
            'total_pages': paginator.num_pages,
            'stats': stats,
        })


class AttachmentAdminDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, attachment_id):
        if not request.user.is_staff:
            return error_response(code=ErrorCode.PERMISSION_DENIED, message='无管理员权限', http_status=status.HTTP_403_FORBIDDEN)

        try:
            att = ChatAttachment.all_objects.select_related('session', 'session__user').get(id=attachment_id)
        except ChatAttachment.DoesNotExist:
            return error_response(code=ErrorCode.NOT_FOUND, message='附件不存在', http_status=status.HTTP_404_NOT_FOUND)

        return success_response(data={
            'id': att.id,
            'original_name': att.original_name,
            'file_size': att.file_size,
            'file_type': att.file_type,
            'mime_type': att.mime_type,
            'status': att.status,
            'reference_count': att.reference_count,
            'last_accessed_at': att.last_accessed_at.isoformat() if att.last_accessed_at else None,
            'retention_days': att.retention_days,
            'archived_path': att.archived_path,
            'archived_at': att.archived_at.isoformat() if att.archived_at else None,
            'file_hash': att.file_hash,
            'session_id': att.session.session_id if att.session else None,
            'session_title': att.session.title if att.session else None,
            'username': att.session.user.username if att.session and att.session.user else None,
            'created_at': att.created_at.isoformat(),
            'updated_at': att.updated_at.isoformat(),
            'is_deleted': att.is_deleted,
            'is_expired': att.is_expired(),
            'can_safely_delete': att.can_safely_delete(),
        })

    def delete(self, request, attachment_id):
        if not request.user.is_staff:
            return error_response(code=ErrorCode.PERMISSION_DENIED, message='无管理员权限', http_status=status.HTTP_403_FORBIDDEN)

        from .services.attachment_lifecycle import AttachmentLifecycleService
        service = AttachmentLifecycleService()
        success, message = service.manual_delete(attachment_id, triggered_by=f'admin:{request.user.username}')
        if success:
            return success_response(message=message)
        if '不存在' in message or '已删除' in message:
            return error_response(code=ErrorCode.NOT_FOUND, message=message)
        return error_response(code=ErrorCode.SERVER_ERROR, message=message)


class AttachmentAdminActionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, attachment_id):
        if not request.user.is_staff:
            return error_response(code=ErrorCode.PERMISSION_DENIED, message='无管理员权限', http_status=status.HTTP_403_FORBIDDEN)

        action = request.data.get('action', '')

        from .services.attachment_lifecycle import AttachmentLifecycleService
        service = AttachmentLifecycleService()

        if action == 'archive':
            try:
                att = ChatAttachment.objects.get(id=attachment_id, status=AttachmentStatus.ACTIVE)
                service._archive_attachment(att)
                return success_response(message=f'附件已归档: {att.original_name}')
            except ChatAttachment.DoesNotExist:
                return error_response(code=ErrorCode.NOT_FOUND, message='附件不存在或非活跃状态')
            except Exception as e:
                return error_response(code=ErrorCode.SERVER_ERROR, message=f'归档失败: {str(e)}')

        elif action == 'restore':
            success = service.restore_attachment(attachment_id)
            if success:
                return success_response(message='附件已恢复')
            return error_response(code=ErrorCode.SERVER_ERROR, message='恢复失败')

        elif action == 'update_retention':
            days = request.data.get('retention_days')
            if days is None or days < 1:
                return error_response(code=ErrorCode.INVALID_PARAMS, message='保留天数必须大于0')
            ChatAttachment.objects.filter(pk=attachment_id).update(retention_days=days)
            return success_response(message=f'保留天数已更新为 {days} 天')

        else:
            return error_response(code=ErrorCode.INVALID_PARAMS, message=f'不支持的操作: {action}')


class AttachmentAdminCleanupView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not request.user.is_staff:
            return error_response(code=ErrorCode.PERMISSION_DENIED, message='无管理员权限', http_status=status.HTTP_403_FORBIDDEN)

        action = request.data.get('action', 'cleanup')
        dry_run = request.data.get('dry_run', False)

        from .services.attachment_lifecycle import AttachmentLifecycleService
        service = AttachmentLifecycleService()

        if action == 'cleanup':
            log = service.cleanup_expired(dry_run=dry_run, triggered_by=f'admin:{request.user.username}')
        elif action == 'archive':
            log = service.archive_old_attachments(dry_run=dry_run, triggered_by=f'admin:{request.user.username}')
        else:
            return error_response(code=ErrorCode.INVALID_PARAMS, message=f'不支持的操作: {action}')

        return success_response(data={
            'action': log.action,
            'files_processed': log.files_processed,
            'files_deleted': log.files_deleted,
            'files_archived': log.files_archived,
            'files_skipped': log.files_skipped,
            'space_freed_mb': round(log.space_freed / 1024 / 1024, 2),
            'space_archived_mb': round(log.space_archived / 1024 / 1024, 2),
            'errors': log.errors,
            'dry_run': dry_run,
        })


class AttachmentAdminStatsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.is_staff:
            return error_response(code=ErrorCode.PERMISSION_DENIED, message='无管理员权限', http_status=status.HTTP_403_FORBIDDEN)

        from .services.attachment_lifecycle import AttachmentLifecycleService
        service = AttachmentLifecycleService()
        stats = service.get_storage_stats()

        alert = service.check_storage_alerts()

        recent_logs = AttachmentCleanupLog.objects.all()[:10]
        log_data = []
        for log in recent_logs:
            log_data.append({
                'id': log.id,
                'action': log.action,
                'started_at': log.started_at.isoformat(),
                'finished_at': log.finished_at.isoformat() if log.finished_at else None,
                'files_processed': log.files_processed,
                'files_deleted': log.files_deleted,
                'files_archived': log.files_archived,
                'space_freed_mb': round(log.space_freed / 1024 / 1024, 2),
                'triggered_by': log.triggered_by,
            })

        active_alerts = StorageAlert.objects.filter(status='active')
        alert_data = []
        for a in active_alerts:
            alert_data.append({
                'id': a.id,
                'level': a.level,
                'usage_percent': a.usage_percent,
                'message': a.message,
                'created_at': a.created_at.isoformat(),
            })

        return success_response(data={
            'storage': stats,
            'alert': alert_data,
            'recent_logs': log_data,
        })


class StorageAlertView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.is_staff:
            return error_response(code=ErrorCode.PERMISSION_DENIED, message='无管理员权限', http_status=status.HTTP_403_FORBIDDEN)

        alerts = StorageAlert.objects.all().order_by('-created_at')[:20]
        data = []
        for a in alerts:
            data.append({
                'id': a.id,
                'level': a.level,
                'status': a.status,
                'storage_path': a.storage_path,
                'usage_percent': a.usage_percent,
                'threshold_percent': a.threshold_percent,
                'message': a.message,
                'created_at': a.created_at.isoformat(),
                'acknowledged_at': a.acknowledged_at.isoformat() if a.acknowledged_at else None,
                'resolved_at': a.resolved_at.isoformat() if a.resolved_at else None,
            })
        return success_response(data=data)

    def post(self, request, alert_id):
        if not request.user.is_staff:
            return error_response(code=ErrorCode.PERMISSION_DENIED, message='无管理员权限', http_status=status.HTTP_403_FORBIDDEN)

        action = request.data.get('action', 'acknowledge')
        try:
            alert = StorageAlert.objects.get(id=alert_id)
        except StorageAlert.DoesNotExist:
            return error_response(code=ErrorCode.NOT_FOUND, message='告警不存在')

        if action == 'acknowledge':
            alert.status = 'acknowledged'
            alert.acknowledged_at = timezone.now()
            alert.save()
            return success_response(message='告警已确认')
        elif action == 'resolve':
            alert.status = 'resolved'
            alert.resolved_at = timezone.now()
            alert.save()
            return success_response(message='告警已解决')

        return error_response(code=ErrorCode.INVALID_PARAMS, message=f'不支持的操作: {action}')



