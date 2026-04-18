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

from Django_xm.apps.core.throttling import ChatStreamRateThrottle
from Django_xm.utils.responses import success_response, error_response, validation_error_response
from Django_xm.utils.error_codes import ErrorCode

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
    
    @transaction.atomic
    def post(self, request):
        serializer = ChatRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(
                errors=serializer.errors,
                message="数据验证失败"
            )

        data = serializer.validated_data
        logger.info(f"收到聊天请求: {data['message'][:50]}...")

        # 获取或创建会话
        session = None
        session_id = data.get('session_id')
        if session_id:
            session = ChatSession.objects.filter(
                session_id=session_id,
                user=request.user,
                is_deleted=False
            ).first()
        
        if not session:
            # 创建新会话
            session = ChatSession.objects.create(
                user=request.user,
                mode=data.get('mode', 'basic-agent'),
                title=data['message'][:100] if len(data['message']) > 100 else data['message']
            )
            logger.info(f"创建新会话: {session.session_id}")

        try:
            result = ChatService.process_chat_request(data)
            
            logger.info(f"聊天请求处理完成，响应长度: {len(result.get('message', ''))} 字符")

            # 保存消息对
            from .services.chat_service import MessagePersistenceService
            user_message, ai_message = MessagePersistenceService.save_message_pair(
                session=session,
                user_content=data['message'],
                ai_content=result.get('message', ''),
                attachment_ids=data.get('attachment_ids')
            )
            
            # 为响应添加消息信息
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
                    'mode': data.get('mode', 'default'),
                    'tools_used': [],
                    'success': False,
                    'error': str(e),
                    'session_id': session.session_id if session else None
                }).data,
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class ChatStreamView(APIView):
    """
    流式聊天视图
    
    使用 ChatService 处理业务逻辑，视图只负责：
    1. 请求验证（序列化器）
    2. 将服务层的异步生成器转换为 SSE 响应
    3. 处理连接异常
    
    使用 asyncio.run() 在独立线程中运行异步生成器，
    通过 queue 实现跨线程数据传递，避免事件循环冲突。
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [ChatStreamRateThrottle]

    def options(self, request):
        response = HttpResponse(status=200)
        origin = request.headers.get('Origin', '*')
        response['Access-Control-Allow-Origin'] = origin
        response['Access-Control-Allow-Credentials'] = 'true'
        response['Access-Control-Allow-Headers'] = 'authorization,content-type,x-csrftoken,x-requested-with'
        response['Access-Control-Allow-Methods'] = 'GET,POST,OPTIONS'
        response['Access-Control-Max-Age'] = '86400'
        return response

    def post(self, request):
        serializer = ChatRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(
                errors=serializer.errors,
                message="数据验证失败"
            )

        data = serializer.validated_data
        logger.info(f"收到流式聊天请求: {data['message'][:50]}...")

        import queue
        result_queue = queue.Queue()
        sentinel = object()

        def run_async_generator():
            """在独立线程中运行异步生成器，通过队列传递结果"""
            import asyncio

            async def collect():
                try:
                    async for event in ChatService.process_stream_chat_request(data):
                        result_queue.put(f"data: {json.dumps(event, ensure_ascii=False)}\n\n")
                except Exception as e:
                    logger.error(f"流式处理出错: {str(e)}", exc_info=True)
                    error_data = {
                        "type": "error",
                        "message": "抱歉，处理您的请求时出现错误",
                        "error": str(e),
                    }
                    result_queue.put(f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n")
                finally:
                    result_queue.put(sentinel)

            asyncio.run(collect())

        import threading
        thread = threading.Thread(target=run_async_generator, daemon=True)
        thread.start()

        def generate():
            """同步生成器 - 从队列中读取异步结果"""
            while True:
                try:
                    item = result_queue.get(timeout=120)
                    if item is sentinel:
                        break
                    yield item
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    continue
                except GeneratorExit:
                    logger.info("客户端断开连接，停止生成")
                    break

        return StreamingHttpResponse(
            generate(),
            content_type='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
                'Access-Control-Allow-Origin': request.headers.get('Origin', '*'),
                'Access-Control-Allow-Credentials': 'true',
                'Access-Control-Allow-Headers': 'authorization,content-type,x-csrftoken,x-requested-with',
                'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
                'Access-Control-Max-Age': '86400',
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
        page_size = min(int(request.query_params.get('page_size', 20)), 100)

        cached_sessions = SecureSessionCacheService.get_user_sessions_list(user_id)

        if cached_sessions:
            try:
                sessions_data = []
                for session_id in cached_sessions:
                    cached_session = SecureSessionCacheService.get_cached_session(user_id, session_id)
                    if cached_session:
                        sessions_data.append(cached_session)

                if sessions_data:
                    logger.info(f"Returning {len(sessions_data)} cached sessions for user {user_id}")
                    return success_response(data=sessions_data)
            except Exception as e:
                logger.warning(f"Cache read failed, falling back to DB: {str(e)}")

        sessions = ChatSession.objects.filter(
            user=request.user,
            is_deleted=False
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

        SecureSessionCacheService.cache_session(
            request.user.id,
            {
                'session_id': session.session_id,
                'title': session.title,
                'mode': session.mode,
                'created_at': session.created_at.isoformat(),
                'updated_at': session.updated_at.isoformat(),
            }
        )

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
        SecureSessionCacheService.invalidate_user_session(request.user.id, session.session_id)

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

        # 使用请求中的 role，如果没有提供则默认使用 'user'
        role = request.data.get('role', 'user')
        message = serializer.save(session=session, role=role)

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
        messages = ChatMessage.objects.filter(session=session).order_by('created_at')
        message_list = []
        for msg in messages:
            message_list.append({
                'role': msg.role,
                'content': msg.content,
                'tool_calls': msg.tool_calls or [],
            })

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

        for tool_name, permission in tool_permissions.items():
            try:
                PermissionService.set_tool_permission(
                    request.user.id, tool_name, permission, session_id
                )
            except ValueError:
                pass

        info = PermissionService.get_permission_info(request.user.id, session_id)
        return success_response(data=info, message='权限更新成功')


class ChatCostView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from Django_xm.apps.core.cost_tracker import get_all_model_pricing, MODEL_PRICING
        return success_response(data={
            'modelPricing': get_all_model_pricing(),
            'supportedModels': list(MODEL_PRICING.keys()),
        })


class ProjectContextView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from Django_xm.apps.core.project_context import detect_project_context
        search_path = request.query_params.get('path')
        context = detect_project_context(search_path)
        return success_response(data=context.to_dict())
