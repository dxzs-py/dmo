import json
import asyncio
import time
import uuid
from django.conf import settings
from django.db import models, transaction
from django.core.paginator import Paginator
from django.http import StreamingHttpResponse
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
from .models import ChatSession, ChatMessage
from .services import SecureSessionCacheService
from .services.chat_service import ChatService, ChatModeService

import logging

logger = logging.getLogger(__name__)


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

        try:
            result = ChatService.process_chat_request(data)
            
            logger.info(f"聊天请求处理完成，响应长度: {len(result.get('message', ''))} 字符")

            return success_response(
                data=ChatResponseSerializer(result).data,
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
                    'error': str(e)
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
    """
    permission_classes = [IsAuthenticated]
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

        async def generate():
            """异步生成器 - 使用 ChatService 处理流式聊天"""
            try:
                async for event in ChatService.process_stream_chat_request(data):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

            except Exception as e:
                error_msg = f"流式处理出错: {str(e)}"
                logger.error(error_msg)
                logger.exception(e)

                error_data = {
                    "type": "error",
                    "message": "抱歉，处理您的请求时出现错误",
                    "error": str(e),
                }
                yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"

        def sync_generate():
            """同步包装器 - 在同步 Django 环境中运行异步代码"""
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            client_connected = True

            try:
                gen = generate()
                while client_connected:
                    try:
                        chunk = loop.run_until_complete(gen.__anext__())
                        yield chunk
                    except StopAsyncIteration:
                        break
                    except (BrokenPipeError, ConnectionResetError, IOError) as e:
                        logger.info(f"客户端断开连接，停止生成: {e}")
                        client_connected = False
                        break
                    except Exception as e:
                        logger.error(f"生成过程出错: {e}")
                        raise
            finally:
                loop.close()

        return StreamingHttpResponse(
            sync_generate(),
            content_type='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
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


class ChatSessionListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user_id = request.user.id
        page_size = int(request.query_params.get('page_size', 20))

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
                    return success_response(
                        data=sessions_data
                    )
            except Exception as e:
                logger.warning(f"Cache read failed, falling back to DB: {str(e)}")

        sessions = ChatSession.objects.filter(
            user=request.user
        ).select_related(
            'user'
        ).annotate(
            message_count=models.Count('messages')
        ).order_by('-updated_at')

        paginator = Paginator(sessions, page_size)
        page_number = request.query_params.get('page', 1)
        page_obj = paginator.get_page(page_number)

        serializer = ChatSessionListSerializer(page_obj.object_list, many=True)
        response_data = {
            'items': serializer.data,
            'total': paginator.count,
            'page': page_obj.number,
            'page_size': page_size,
            'total_pages': paginator.num_pages,
        }

        for item_data in response_data['items']:
            SecureSessionCacheService.cache_session(user_id, item_data)

        return success_response(
            data=response_data
        )


class ChatSessionCreateView(APIView):
    permission_classes = [IsAuthenticated]

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


class ChatSessionDetailView(APIView):
    permission_classes = [IsAuthenticated]
    
    def get_object(self, session_id, user):
        try:
            return ChatSession.objects.select_related(
                'user'
            ).prefetch_related(
                'messages'
            ).annotate(
                _message_count=models.Count('messages')
            ).get(session_id=session_id, user=user)
        except ChatSession.DoesNotExist:
            return None
    
    def get(self, request, session_id):
        session = self.get_object(session_id, request.user)
        if not session:
            return error_response(
                code=ErrorCode.NOT_FOUND,
                message='会话不存在',
                http_status=status.HTTP_404_NOT_FOUND
            )

        serializer = ChatSessionDetailSerializer(session)
        return success_response(data=serializer.data)

    def put(self, request, session_id):
        session = self.get_object(session_id, request.user)
        if not session:
            return error_response(
                code=ErrorCode.NOT_FOUND,
                message='会话不存在',
                http_status=status.HTTP_404_NOT_FOUND
            )

        serializer = ChatSessionUpdateSerializer(session, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return success_response(
                data=ChatSessionDetailSerializer(session).data,
                message='会话更新成功'
            )
        
        return validation_error_response(
            errors=serializer.errors,
            message='更新参数错误'
        )

    def delete(self, request, session_id):
        session = self.get_object(session_id, request.user)
        if not session:
            return error_response(
                code=ErrorCode.NOT_FOUND,
                message='会话不存在',
                http_status=status.HTTP_404_NOT_FOUND
            )

        session.soft_delete()
        SecureSessionCacheService.invalidate_user_session(request.user.id, session.session_id)
        
        return success_response(message='会话删除成功')


class ChatMessageCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, session_id):
        try:
            session = ChatSession.objects.get(session_id=session_id, user=request.user)
        except ChatSession.DoesNotExist:
            return error_response(
                code=ErrorCode.NOT_FOUND,
                message='会话不存在',
                http_status=status.HTTP_404_NOT_FOUND
            )

        serializer = ChatMessageSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(errors=serializer.errors)

        message = serializer.save(session=session, role='user')
        
        return success_response(
            data=ChatMessageSerializer(message).data,
            message='消息发送成功',
            status_code=status.HTTP_201_CREATED
        )


class ChatMessageBatchCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, session_id):
        try:
            session = ChatSession.objects.get(session_id=session_id, user=request.user)
        except ChatSession.DoesNotExist:
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


class ChatMessageUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    def put(self, request, message_id):
        try:
            message = ChatMessage.objects.select_related('session').get(
                id=message_id, 
                session__user=request.user
            )
        except ChatMessage.DoesNotExist:
            return error_response(
                code=ErrorCode.NOT_FOUND,
                message='消息不存在',
                http_status=status.HTTP_404_NOT_FOUND
            )

        serializer = ChatMessageSerializer(message, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return success_response(
                data=ChatMessageSerializer(message).data,
                message='消息更新成功'
            )
        
        return validation_error_response(
            errors=serializer.errors,
            message='更新参数错误'
        )
