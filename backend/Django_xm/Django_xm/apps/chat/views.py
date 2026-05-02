import json
import os
import time
import uuid
from functools import wraps
from django.conf import settings
from django.db import models, transaction
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
from .models import ChatSession, ChatMessage, ChatAttachment
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
        logger.info(f"收到流式聊天请求: {data['message'][:50]}...")

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
        modes = ChatModeService.get_supported_modes()
        
        return success_response(
            data={
                'modes': modes,
                'default': 'basic-agent'
            },
            message='操作成功'
        )


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
        serializer = ChatSessionCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(errors=serializer.errors)

        session = serializer.save(user=request.user)

        SecureSessionCacheService.invalidate_all_user_sessions(request.user.id)

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
        return success_response(
            data=ChatSessionDetailSerializer(session).data,
            message='会话更新成功'
        )

    @log_view_action
    def delete(self, request, session_id):
        session = self.get_session_or_404(session_id, request.user)
        if not session:
            return error_response(
                code=ErrorCode.NOT_FOUND,
                message='会话不存在',
                http_status=status.HTTP_404_NOT_FOUND
            )

        session.soft_delete()
        SecureSessionCacheService.invalidate_all_user_sessions(request.user.id)

        return success_response(message='会话删除成功')


class ChatMessageCreateView(BaseChatAPIView):
    @log_view_action
    @transaction.atomic
    def post(self, request, session_id):
        session = self.get_session_or_404(session_id, request.user)
        if not session:
            return error_response(
                code=ErrorCode.NOT_FOUND,
                message='会话不存在',
                http_status=status.HTTP_404_NOT_FOUND
            )

        serializer = ChatMessageSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(errors=serializer.errors)

        message = serializer.save(session=session)

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
        from .services.slash_commands import get_all_commands
        commands = get_all_commands()
        return success_response(data={'commands': commands})


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


class ChatCostView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from Django_xm.apps.ai_engine.services.cost_tracker import get_all_model_pricing, MODEL_PRICING
        return success_response(data={
            'modelPricing': get_all_model_pricing(),
            'supportedModels': list(MODEL_PRICING.keys()),
        })


class ProjectContextView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from Django_xm.apps.ai_engine.services.project_context import detect_project_context
        search_path = request.query_params.get('path')
        context = detect_project_context(search_path)
        return success_response(data=context.to_dict())


class ChatDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from django.db.models import Count, Sum, Avg
        from django.db.models.functions import TruncDate
        from django.utils import timezone
        from datetime import timedelta

        user = request.user
        now = timezone.now()
        seven_days_ago = now - timedelta(days=7)

        sessions = ChatSession.objects.filter(user=user, is_deleted=False)
        messages = ChatMessage.objects.filter(session__user=user, session__is_deleted=False)

        total_sessions = sessions.count()

        agg = messages.aggregate(
            total_messages=Count('id'),
            total_tokens=Sum('token_count'),
            total_cost=Sum('cost'),
            avg_response_time=Avg('response_time'),
        )
        total_messages = agg['total_messages'] or 0
        total_tokens = agg['total_tokens'] or 0
        total_cost = float(agg['total_cost'] or 0)
        avg_response_time = round(float(agg['avg_response_time'] or 0), 2)

        daily_stats = messages.filter(
            created_at__gte=seven_days_ago
        ).annotate(
            day=TruncDate('created_at')
        ).values('day').annotate(
            msg_count=Count('id'),
            token_sum=Sum('token_count'),
        ).order_by('day')

        daily_map = {stat['day']: stat for stat in daily_stats}
        usage_trend = []
        for i in range(7):
            day = (now - timedelta(days=6 - i)).date()
            stat = daily_map.get(day)
            usage_trend.append({
                'date': day.strftime('%m/%d'),
                'messages': stat['msg_count'] if stat else 0,
                'tokens': stat['token_sum'] if stat else 0,
            })

        model_distribution = []
        try:
            model_dist = messages.values('model').annotate(
                count=Count('id')
            ).order_by('-count')[:5]
            for item in model_dist:
                model_distribution.append({
                    'name': item['model'] or 'unknown',
                    'value': item['count'],
                })
        except Exception:
            model_distribution = [
                {'name': 'default', 'value': total_messages}
            ]

        mode_distribution = []
        try:
            mode_dist = sessions.values('mode').annotate(
                count=Count('id')
            ).order_by('-count')
            from .models import ChatMode
            mode_labels = {m.value: m.label for m in ChatMode}
            total_mode = sum(item['count'] for item in mode_dist) or 1
            for item in mode_dist:
                mode_distribution.append({
                    'name': mode_labels.get(item['mode'], item['mode']),
                    'value': round(item['count'] / total_mode * 100),
                })
        except Exception:
            mode_distribution = [
                {'name': '基础对话', 'value': 100}
            ]

        return success_response(data={
            'total_sessions': total_sessions,
            'total_messages': total_messages,
            'total_tokens': total_tokens,
            'total_cost': total_cost,
            'avg_response_time': avg_response_time,
            'usage_trend': usage_trend,
            'model_distribution': model_distribution,
            'mode_distribution': mode_distribution,
        })
