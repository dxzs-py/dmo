"""
核心聊天、会话和消息视图

提供聊天交互、流式响应、会话管理、消息管理等接口。
"""

import json
import time
import asyncio
from functools import wraps
from asgiref.sync import sync_to_async

from django.db import models, transaction
from django.core.paginator import Paginator
from django.core.exceptions import ObjectDoesNotExist
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import BaseRenderer

from Django_xm.async_utils import run_async
from Django_xm.apps.core.throttling import ChatStreamRateThrottle
from Django_xm.common.responses import success_response, error_response, validation_error_response
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.sse_utils import sse_response, sse_error_response, sse_error_event

from .serializers import (
    ChatRequestSerializer,
    ChatResponseSerializer,
    ChatSessionListSerializer,
    ChatSessionDetailSerializer,
    ChatSessionCreateSerializer,
    ChatSessionUpdateSerializer,
    ChatMessageSerializer,
)
from .models import ChatSession, ChatMessage, MessageRole
from Django_xm.apps.attachments.services.cross_app import soft_delete_session_attachments
from Django_xm.apps.cache_manager.services.secure_session_cache import SecureSessionCacheService
from .services.chat_service import ChatService, ChatModeService

import logging

logger = logging.getLogger(__name__)


async def _cleanup_checkpoint_messages_async_by_id(session_id, deleted_message_ids):
    """删除 checkpoint 中对应的消息，确保 agent 不会记住已删除的对话。

    策略：
    1. 删除整个 thread 的 checkpoint 数据
    2. 从数据库中剩余的未删除消息重建 checkpoint
    这样既移除了被删除的消息，又保留了未删除消息的上下文。

    使用异步 Checkpointer 与流式聊天保持一致，避免同步/异步实例隔离问题。
    通过 session_id 查询而非传入 model 实例，确保在事务提交后能读到最新数据。
    """
    try:
        from Django_xm.apps.ai_engine.services.checkpointer_factory import get_async_checkpointer
        from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolCall
        from langgraph.checkpoint.base import empty_checkpoint

        checkpointer = await get_async_checkpointer()
        if checkpointer is None:
            return

        thread_id = session_id

        # Step 1: 删除整个 thread 的旧 checkpoint
        if hasattr(checkpointer, 'adelete_thread'):
            await checkpointer.adelete_thread(thread_id=thread_id)
            logger.info(f"Checkpoint 旧数据已删除: thread={thread_id}")
        else:
            logger.warning(f"Checkpointer 不支持 adelete_thread，跳过重建: thread={thread_id}")
            return

        # Step 2: 从数据库中剩余的未删除消息重建 checkpoint
        remaining_messages = await sync_to_async(list)(
            ChatMessage.objects.filter(
                session__session_id=session_id,
                is_deleted=False,
            ).order_by('created_at')
        )

        if not remaining_messages:
            logger.info(f"Checkpoint 重建: 无剩余消息，跳过: thread={thread_id}")
            return

        # 将数据库消息转为 LangChain 消息对象
        lc_messages = []
        for msg in remaining_messages:
            content = msg.content or ""
            if msg.role == 'user':
                lc_messages.append(HumanMessage(content=content))
            elif msg.role == 'assistant':
                ai_kwargs = {}
                # 恢复 tool_calls：将 JSON dict 转为 LangChain ToolCall 对象
                if msg.tool_calls:
                    tool_calls = []
                    for tc in msg.tool_calls:
                        if isinstance(tc, dict):
                            tool_calls.append(ToolCall(
                                name=tc.get("name", ""),
                                args=tc.get("args", {}),
                                id=tc.get("id", ""),
                            ))
                        elif isinstance(tc, ToolCall):
                            tool_calls.append(tc)
                    ai_kwargs["tool_calls"] = tool_calls
                lc_messages.append(AIMessage(content=content, **ai_kwargs))
            elif msg.role == 'system':
                lc_messages.append(SystemMessage(content=content))

        if not lc_messages:
            return

        # Step 3: 使用 LangGraph 官方 empty_checkpoint 创建规范的新 checkpoint
        new_checkpoint = empty_checkpoint()
        new_checkpoint["channel_values"] = {"messages": lc_messages}
        new_checkpoint["channel_versions"] = {"messages": "00000000000000000000000000000001"}

        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

        await checkpointer.aput(
            config,
            new_checkpoint,
            {"source": "update", "step": 0, "writes": {}},
            {"messages": "00000000000000000000000000000001"},
        )
        logger.info(
            f"Checkpoint 已重建: thread={thread_id}, "
            f"消息数={len(lc_messages)}, "
            f"删除了 {len(deleted_message_ids)} 条消息"
        )
    except Exception as e:
        logger.warning(f"Checkpoint 消息清理失败（非致命）: session={session_id}, error={e}", exc_info=True)


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
        from .services.message_service import get_user_session
        return get_user_session(user, session_id, prefetch_attachments=prefetch_attachments)

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
                code=str(int(ErrorCode.VALIDATION_FAILED)),
            )

        data = serializer.validated_data
        logger.info(f"收到聊天请求: {data['message'][:50]}...")

        session = None
        session_id = data.get('session_id')
        if session_id:
            from .services.message_service import get_user_session
            session = get_user_session(request.user, session_id)

        if not session:
            with transaction.atomic():
                session = ChatSession.objects.create(
                    user=request.user,
                    mode=data.get('mode', 'agent'),
                    selected_knowledge_base=data.get('selected_knowledge_base'),
                    selected_knowledge_bases=data.get('selected_knowledge_bases'),
                    title=data['message'][:100] if len(data['message']) > 100 else data['message']
                )
            logger.info(f"创建新会话: {session.session_id}")
        else:
            if data.get('selected_knowledge_base') is not None:
                session.selected_knowledge_base = data['selected_knowledge_base']
                session.save()
            if data.get('selected_knowledge_bases') is not None:
                session.selected_knowledge_bases = data['selected_knowledge_bases']
                session.save()

        try:
            chat_service = ChatService(user_id=request.user.id if request.user.is_authenticated else None)
            result = run_async(chat_service.process_chat_request(data))

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
                    'mode': data.get('mode', 'agent'),
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

        # 持久化知识库选择到会话
        session_id = data.get('session_id')
        selected_kb = data.get('selected_knowledge_base')
        if session_id and selected_kb:
            try:
                from .services.message_service import get_user_session
                session = get_user_session(request.user, session_id)
                if session and session.selected_knowledge_base != selected_kb:
                    session.selected_knowledge_base = selected_kb
                    session.save(update_fields=['selected_knowledge_base'])
            except Exception as e:
                logger.warning(f"持久化知识库选择失败: {e}")

        selected_kbs = data.get('selected_knowledge_bases')
        if session_id and selected_kbs:
            try:
                from .services.message_service import get_user_session
                session = get_user_session(request.user, session_id)
                if session and session.selected_knowledge_bases != selected_kbs:
                    session.selected_knowledge_bases = selected_kbs
                    session.save(update_fields=['selected_knowledge_bases'])
            except Exception as e:
                logger.warning(f"持久化多知识库选择失败: {e}")

        attachment_ids = data.get('attachment_ids') or []
        original_attachment_ids = list(attachment_ids)
        pending_progress = []
        if attachment_ids:
            try:
                from Django_xm.apps.attachments.services.cross_app import get_attachment_service
                att_svc = get_attachment_service()

                def progress_callback(stage, message):
                    pending_progress.append({
                        'type': 'attachment_processing',
                        'data': {'stage': stage, 'message': message},
                    })

                user_content = att_svc.build_user_content(data['message'], attachment_ids, progress_callback=progress_callback)
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
            loop = asyncio.new_event_loop()
            # 共享状态：async generator 被强制关闭时（aclose/GeneratorExit），
            # try/except 之后的代码不会执行，导致 _pending_content 无法刷新。
            # 通过共享状态字典，在 finally 块中兜底刷新。
            stream_state = {"pending_content": ""}
            data['_stream_state'] = stream_state
            try:
                if original_attachment_ids:
                    yield f"data: {json.dumps({'type': 'attachment_ids', 'data': original_attachment_ids}, ensure_ascii=False)}\n\n"

                for evt in pending_progress:
                    yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
                pending_progress.clear()

                chat_service = ChatService(
                    user_id=request.user.id if request.user.is_authenticated else None,
                    thread_id=data.get('session_id'),
                )
                gen = chat_service.process_stream_chat_request(data).__aiter__()
                pending_task = None

                while True:
                    try:
                        # 使用 asyncio.wait 而非 wait_for，超时不取消底层任务
                        if pending_task is None:
                            pending_task = loop.create_task(gen.__anext__())

                        done, _ = loop.run_until_complete(
                            asyncio.wait({pending_task}, timeout=30.0)
                        )

                        if not done:
                            # 超时但任务仍在运行，发送心跳保活，不取消任务
                            yield f"data: {json.dumps({'type': 'heartbeat'}, ensure_ascii=False)}\n\n"
                            continue

                        # 任务完成
                        event = pending_task.result()
                        pending_task = None

                        if isinstance(event, dict) and event.get('type') == 'error':
                            yield sse_error_event(
                                code=str(int(ErrorCode.SERVER_ERROR)),
                                message=event.get('message', '处理出错'),
                            )
                        else:
                            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    except StopAsyncIteration:
                        pending_task = None
                        logger.debug("generate(): StopAsyncIteration, 流式处理完成")
                        break
            except Exception as e:
                logger.error(f"流式处理出错: {str(e)}", exc_info=True)
                from Django_xm.apps.ai_engine.services.exceptions import classify_exception
                classified = classify_exception(e)
                yield sse_error_event(
                    code=str(int(ErrorCode.SERVER_ERROR)),
                    message=classified.user_message,
                )
            finally:
                if pending_task is not None:
                    pending_task.cancel()
                if gen is not None:
                    try:
                        loop.run_until_complete(gen.aclose())
                    except Exception:
                        pass
                # 释放当前事件循环的异步 Checkpointer 连接池，防止 PostgreSQL 连接泄漏
                try:
                    from Django_xm.apps.ai_engine.services.checkpointer_factory import release_async_checkpointer
                    loop.run_until_complete(release_async_checkpointer())
                except Exception:
                    pass
                # 兜底刷新：async generator 被强制关闭时，_pending_content 可能未被刷新
                pending_content = stream_state.get("pending_content", "")
                if pending_content:
                    logger.debug(f"generate() finally: 兜底刷新 _pending_content ({len(pending_content)} 字符)")
                    yield f"data: {json.dumps({'type': 'chunk', 'content': pending_content}, ensure_ascii=False)}\n\n"
                # 取消所有残留 Task，避免 "Task was destroyed but it is pending!" 警告
                try:
                    # 先让事件循环运行一小段时间，让 aclose 产生的清理任务有机会完成
                    loop.run_until_complete(asyncio.sleep(0.05))
                    pending = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    if pending:
                        loop.run_until_complete(
                            asyncio.gather(*pending, return_exceptions=True)
                        )
                except Exception:
                    pass
                loop.close()
                yield "data: [DONE]\n\n"

        return sse_response(generate())


class ChatModesView(BaseChatAPIView):
    def get(self, request):
        from Django_xm.apps.cache_manager.services.cache_service import CacheService, CacheTTL

        cache_key = "chat:modes"
        cached = CacheService.get(cache_key)
        if cached is not None:
            return success_response(data=cached)

        result = {
            "modes": [
                {"id": "agent", "label": "代理", "description": "智能代理，支持工具调用与纯对话", "icon": "⚡"},
                {"id": "deep-research", "label": "深度研究", "description": "多步骤深度研究分析", "icon": "🔬"},
            ],
            "default_mode": "agent",
            "capabilities": {
                "web_search": {"available": True, "modes": ["agent", "deep-research"]},
                "knowledge_base": {"available": True, "modes": ["agent", "deep-research"]},
                "deep_thinking": {"available": True, "modes": ["agent", "deep-research"], "model_dependent": True},
                "mcp": {"available": True, "modes": ["agent", "deep-research"]},
                "file_upload": {"available": True, "modes": ["agent", "deep-research"]},
                "tool_selection": {"available": True, "modes": ["agent", "deep-research"]},
            }
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
                sessions_data = SecureSessionCacheService.get_cached_sessions_batch(user_id, cached_sessions)

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
        from Django_xm.apps.cache_manager.services.cache_service import invalidate_chat_cache

        serializer = ChatSessionCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(errors=serializer.errors)

        session = serializer.save(user=request.user)

        SecureSessionCacheService.invalidate_all_user_sessions(request.user.id)
        invalidate_chat_cache(user_id=request.user.id)

        # prefetch messages + attachments 避免 ChatSessionDetailSerializer N+1
        session = ChatSession.objects.prefetch_related(
            'messages', 'messages__attachments'
        ).get(pk=session.pk)

        return success_response(
            data=ChatSessionDetailSerializer(session).data,
            message='会话创建成功',
            http_status=status.HTTP_201_CREATED
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
    def patch(self, request, session_id):
        from Django_xm.apps.cache_manager.services.cache_service import invalidate_chat_cache

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
        invalidate_chat_cache(user_id=request.user.id)
        # 重新查询以 prefetch messages + attachments，避免序列化 N+1
        session = ChatSession.objects.prefetch_related(
            'messages', 'messages__attachments'
        ).get(pk=session.pk)
        return success_response(
            data=ChatSessionDetailSerializer(session).data,
            message='会话更新成功'
        )

    @log_view_action
    @transaction.atomic
    def delete(self, request, session_id):
        from Django_xm.apps.cache_manager.services.cache_service import invalidate_chat_cache
        from Django_xm.apps.research.services.cross_app import get_linked_research_tasks

        session = self.get_session_or_404(session_id, request.user)
        if not session:
            return error_response(
                code=ErrorCode.NOT_FOUND,
                message='会话不存在',
                http_status=status.HTTP_404_NOT_FOUND
            )

        linked_tasks = get_linked_research_tasks(session.session_id)

        confirm_delete_linked = request.query_params.get('confirm_delete_linked', '').lower() in ('1', 'true', 'yes')

        if linked_tasks and not confirm_delete_linked:
            return success_response(
                data={
                    'has_linked_data': True,
                    'linked_research_tasks': linked_tasks,
                    'message': f'该会话关联了 {len(linked_tasks)} 个深度研究任务',
                },
                message='存在关联的深度研究任务，请确认是否一并删除',
            )

        session.soft_delete()

        soft_delete_session_attachments(session)

        # 清理 LangGraph checkpoint 和 Store 数据（PostgreSQL/SQLite 中的对话状态）
        try:
            import asyncio
            from Django_xm.apps.ai_engine.services.checkpointer_factory import (
                delete_thread_checkpoints, delete_thread_store_data,
            )

            async def _cleanup():
                await delete_thread_checkpoints(session.session_id)
                await delete_thread_store_data(request.user.id, session.session_id)

            run_async(_cleanup())
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"清理 checkpoint/Store 数据失败: {e}")

        if linked_tasks:
            from Django_xm.apps.research.services.cross_app import get_research_task_manager
            task_manager = get_research_task_manager()
            for task in linked_tasks:
                task_manager.delete_task(task['task_id'], user_id=request.user.id)

        SecureSessionCacheService.invalidate_all_user_sessions(request.user.id)
        invalidate_chat_cache(user_id=request.user.id)

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

        from Django_xm.apps.context_manager.services.manager import create_context_manager
        messages = ChatMessage.objects.filter(
            session=session
        ).only('role', 'content', 'tool_calls').order_by('created_at')
        message_list = list(messages.values('role', 'content', 'tool_calls'))

        context_manager = create_context_manager(user_id=request.user.id, thread_id=session_id)
        result = context_manager.build_structured_context(
            messages=message_list,
            query="",
            mode="agent",
        )
        metadata = result["metadata"]
        prune_info = metadata["prune_result"]

        return success_response(data={
            'compressed': prune_info["pruned_count"] > 0,
            'originalMessageCount': prune_info["original_count"],
            'keptMessageCount': prune_info["original_count"] - prune_info["pruned_count"],
            'prunedCount': prune_info["pruned_count"],
            'dedupedCount': prune_info["deduped_count"],
            'filteredCount': prune_info["filtered_count"],
            'injectionDetected': metadata["injection_detected"],
            'budgetOverSections': metadata["budget_over_sections"],
        })


class ChatMessageCreateView(BaseChatAPIView):
    @log_view_action
    @transaction.atomic
    def post(self, request, session_id):
        from Django_xm.apps.cache_manager.services.cache_service import invalidate_chat_cache

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
            from Django_xm.apps.attachments.services.cross_app import get_attachment_service
            get_attachment_service().link_attachments_to_message(message, attachment_ids)

        invalidate_chat_cache(user_id=request.user.id)

        return success_response(
            data=ChatMessageSerializer(message).data,
            message='消息发送成功',
            http_status=status.HTTP_201_CREATED
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
                    created_messages.append(message.pk)

        # 批量查询并 prefetch attachments，避免序列化时 N+1
        messages_qs = ChatMessage.objects.filter(
            pk__in=created_messages
        ).prefetch_related('attachments').order_by('created_at')

        return success_response(
            data={
                'created_count': len(created_messages),
                'messages': ChatMessageSerializer(messages_qs, many=True).data
            },
            message=f'批量创建 {len(created_messages)} 条消息成功'
        )


class ChatMessageDeleteView(BaseChatAPIView):
    @log_view_action
    @transaction.atomic
    def delete(self, request, message_id):
        message = self.get_message_or_404(message_id, request.user)
        if not message:
            return error_response(
                code=ErrorCode.NOT_FOUND,
                message='消息不存在',
                http_status=status.HTTP_404_NOT_FOUND
            )

        research_task_deleted = False
        research_task_id = message.research_task_id

        if research_task_id:
            try:
                from Django_xm.apps.research.services.cross_app import get_research_task_manager
                task_manager = get_research_task_manager()
                task_manager.delete_task(research_task_id, user_id=request.user.id)
                research_task_deleted = True
            except Exception as e:
                logger.error(f"删除关联研究任务失败: task_id={research_task_id}, error={e}")

        message.soft_delete()

        # 事务提交后再重建 checkpoint，否则新线程的数据库连接看不到未提交的 soft_delete
        session_id = message.session.session_id
        transaction.on_commit(
            lambda: run_async(_cleanup_checkpoint_messages_async_by_id(session_id, [message.id]))
        )

        return success_response(
            data={
                'message_id': message_id,
                'research_task_deleted': research_task_deleted,
                'research_task_id': research_task_id if research_task_deleted else None,
            },
            message='消息删除成功'
        )


class ChatMessagePairDeleteView(BaseChatAPIView):
    @log_view_action
    @transaction.atomic
    def delete(self, request, session_id):
        session = self.get_session_or_404(session_id, request.user)
        if not session:
            return error_response(
                code=ErrorCode.NOT_FOUND,
                message='会话不存在',
                http_status=status.HTTP_404_NOT_FOUND
            )

        user_message_id = request.data.get('user_message_id')
        if not user_message_id:
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message='缺少 user_message_id 参数'
            )

        try:
            user_message_id = int(user_message_id)
        except (ValueError, TypeError):
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message='user_message_id 必须为数字'
            )

        user_message = ChatMessage.objects.filter(
            id=user_message_id,
            session=session,
            is_deleted=False
        ).first()
        if not user_message:
            return error_response(
                code=ErrorCode.NOT_FOUND,
                message='用户消息不存在',
                http_status=status.HTTP_404_NOT_FOUND
            )

        assistant_message = ChatMessage.objects.filter(
            session=session,
            role=MessageRole.ASSISTANT,
            is_deleted=False,
            created_at__gt=user_message.created_at,
        ).order_by('created_at').first()

        deleted_messages = [user_message]
        research_task_deleted = False
        research_task_id = None

        if assistant_message:
            deleted_messages.append(assistant_message)
            if assistant_message.research_task_id:
                research_task_id = assistant_message.research_task_id
                try:
                    from Django_xm.apps.research.services.cross_app import get_research_task_manager
                    task_manager = get_research_task_manager()
                    task_manager.delete_task(research_task_id, user_id=request.user.id)
                    research_task_deleted = True
                except Exception as e:
                    logger.error(f"删除关联研究任务失败: task_id={research_task_id}, error={e}")

        for msg in deleted_messages:
            msg.soft_delete()

        # 事务提交后再重建 checkpoint，否则新线程的数据库连接看不到未提交的 soft_delete
        session_id = session.session_id
        deleted_ids = [m.id for m in deleted_messages]
        transaction.on_commit(
            lambda: run_async(_cleanup_checkpoint_messages_async_by_id(session_id, deleted_ids))
        )

        return success_response(
            data={
                'deleted_message_ids': [m.id for m in deleted_messages],
                'research_task_deleted': research_task_deleted,
                'research_task_id': research_task_id if research_task_deleted else None,
            },
            message='消息对删除成功'
        )


class ChatMessageUpdateView(BaseChatAPIView):
    @log_view_action
    @transaction.atomic
    def patch(self, request, message_id):
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
        # 重新查询以 prefetch attachments，避免序列化时 N+1
        message = ChatMessage.objects.prefetch_related('attachments').get(pk=message.pk)
        return success_response(
            data=ChatMessageSerializer(message).data,
            message='消息更新成功'
        )


class ChatApprovalView(BaseChatAPIView):
    """通用工具审批 API - 恢复被 interrupt 暂停的 Agent 执行

    任何工具通过 interrupt_for_approval() 触发的审批中断，
    前端点击"确认"或"拒绝"时调用此 API，
    通过 LangGraph Command(resume=...) 恢复 Agent 执行。

    支持：
    - CONFIRM 模式：resume=True/False
    - CONFIRM_WITH_INPUT 模式：resume=用户输入的值（字符串）或 None（取消）
    """
    renderer_classes = [SSERenderer]
    throttle_classes = [ChatStreamRateThrottle]

    def post(self, request):
        session_id = request.data.get('session_id')
        interrupt_id = request.data.get('interrupt_id')
        approved = request.data.get('approved', False)
        # CONFIRM_WITH_INPUT 模式：用户输入的值
        user_input = request.data.get('user_input')

        if not session_id or not interrupt_id:
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message='缺少 session_id 或 interrupt_id 参数'
            )

        # 验证会话归属
        session = ChatSession.objects.filter(
            session_id=session_id,
            user=request.user,
            is_deleted=False,
        ).first()
        if not session:
            return error_response(
                code=ErrorCode.NOT_FOUND,
                message='会话不存在',
                http_status=status.HTTP_404_NOT_FOUND
            )

        logger.info(f"审批请求: session={session_id}, interrupt_id={interrupt_id}, approved={approved}, has_input={user_input is not None}")

        # 从请求中读取模型配置
        provider_id = request.data.get('provider_id')
        model_name = request.data.get('model_name')
        use_deep_thinking = request.data.get('use_deep_thinking', False)
        special_params = request.data.get('special_params')
        temperature = request.data.get('temperature')
        max_tokens = request.data.get('max_tokens')
        # 从请求中读取工具配置，确保审批恢复时使用与原始请求一致的工具集
        use_tools = request.data.get('use_tools', True)
        use_web_search = request.data.get('use_web_search', False)
        use_mcp = request.data.get('use_mcp', False)
        selected_mcp_servers = request.data.get('selected_mcp_servers')
        selected_tools = request.data.get('selected_tools')
        use_knowledge_base = request.data.get('use_knowledge_base', False)
        selected_knowledge_bases = request.data.get('selected_knowledge_bases', [])

        def generate():
            try:
                from langgraph.types import Command
                from Django_xm.apps.chat.services.chat_service import ChatService

                # 构建 Agent（复用 ChatService 的逻辑，使用 checkpointer 恢复状态）
                chat_service = ChatService(user_id=request.user.id, thread_id=session_id)
                data = {
                    'session_id': session_id,
                    'mode': 'agent',
                    'use_tools': use_tools,
                    'use_web_search': use_web_search,
                    'use_mcp': use_mcp,
                    'selected_mcp_servers': selected_mcp_servers,
                    'selected_tools': selected_tools,
                    'use_knowledge_base': use_knowledge_base,
                    'selected_knowledge_bases': selected_knowledge_bases,
                    # 传递模型配置，确保审批恢复时使用正确的模型
                    'provider_id': provider_id,
                    'model_name': model_name,
                    'use_deep_thinking': use_deep_thinking,
                    'special_params': special_params,
                    'temperature': temperature,
                    'max_tokens': max_tokens,
                }
                # 深度思考模式标记
                enable_deep_thinking = use_deep_thinking and provider_id and model_name
                if enable_deep_thinking:
                    from Django_xm.apps.ai_engine.services.llm_factory import model_supports_capability
                    enable_deep_thinking = model_supports_capability(
                        provider_id, model_name, 'deep_thinking'
                    )
                if enable_deep_thinking:
                    data['_enable_deep_thinking'] = True

                # resume 值：根据审批结果决定
                if approved:
                    resume_value = user_input if user_input is not None else True
                else:
                    resume_value = False
                # 使用 dict 形式指定 interrupt_id，支持多个 pending interrupts 的场景
                # 当 LLM 一次生成多个 tool_call 且都触发 interrupt 时，
                # 必须指定具体的 interrupt_id 才能恢复，否则 LangGraph 报错
                command = Command(resume={interrupt_id: resume_value})

                # 将 agent 创建和流式恢复放在同一个事件循环中，
                # 避免 checkpointer 的异步连接在事件循环切换时被关闭
                async def _create_agent():
                    # 解析模型实例（与初始聊天请求一致，避免 model_resolver 创建 LazyFallbackChatModel）
                    model_instance = ChatService._resolve_model_instance(data)
                    # 获取工具列表
                    tools = await chat_service._get_tools(data)
                    return await chat_service._create_agent_with_memory(
                        data, prompt_mode='agent',
                        model_instance=model_instance,
                        tool_config=chat_service._build_tool_config(data),
                        tools=tools,
                    )

                async def _stream_resume(agent, thread_config):
                    async for chunk in agent.graph.astream(
                        command,
                        config=thread_config,
                        stream_mode=["messages", "updates"],
                    ):
                        yield chunk

                # 流式恢复 Agent 执行
                current_message_content = ""
                tool_calls_map = {}
                tool_call_count = {}
                accumulated_reasoning = {}
                tool_args_accumulator = {}

                loop = asyncio.new_event_loop()
                try:
                    # 在同一个事件循环中创建 agent
                    agent, thread_config, use_checkpointer = loop.run_until_complete(_create_agent())

                    if not use_checkpointer or not thread_config:
                        yield sse_error_event("approval_error", "无法恢复会话状态：checkpointer 不可用")
                        return

                    # 从 checkpoint 加载历史消息，提取已有的 tool_call 信息，
                    # 初始化 tool_calls_map，这样 ToolMessage 到达时能找到对应的 tool_info 并推送给前端
                    try:
                        graph = agent.graph if hasattr(agent, 'graph') else agent
                        if hasattr(graph, 'aget_state'):
                            history_state = loop.run_until_complete(
                                graph.aget_state(thread_config)
                            )
                            if history_state and hasattr(history_state, 'values'):
                                history_messages = history_state.values.get("messages", []) or []
                                for msg in history_messages:
                                    msg_tool_calls = getattr(msg, "tool_calls", None) or []
                                    for tc in msg_tool_calls:
                                        tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                                        tc_name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
                                        tc_args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {})
                                        if tc_id:
                                            tool_calls_map[tc_id] = {
                                                "id": tc_id,
                                                "name": tc_name or "unknown",
                                                "parameters": tc_args or {},
                                                "state": "input-available",
                                                "status": "running",
                                            }
                    except Exception as hist_err:
                        logger.warning(f"审批恢复加载历史 tool_call 失败（非致命）: {hist_err}")

                    # 在同一个事件循环中流式恢复
                    async_gen = _stream_resume(agent, thread_config)
                    pending_task = None

                    while True:
                        try:
                            if pending_task is None:
                                pending_task = loop.create_task(async_gen.__anext__())

                            done, _ = loop.run_until_complete(
                                asyncio.wait({pending_task}, timeout=30.0)
                            )

                            if not done:
                                # 超时但任务仍在运行，发送心跳保活，不取消任务
                                yield f"data: {json.dumps({'type': 'heartbeat'}, ensure_ascii=False)}\n\n"
                                continue

                            chunk = pending_task.result()
                            pending_task = None
                        except StopAsyncIteration:
                            pending_task = None
                            break

                        # 处理多 stream mode
                        if isinstance(chunk, tuple) and len(chunk) == 2:
                            mode_name, mode_data = chunk
                        else:
                            mode_name, mode_data = "messages", chunk

                        # 处理 updates 模式：提取工具执行结果（ToolMessage）推送给前端
                        # 同时处理 __interrupt__ 事件（审批恢复后 agent 又触发新审批）
                        if mode_name == "updates":
                            if isinstance(mode_data, dict):
                                # 处理 __interrupt__ 事件：审批恢复后 agent 又触发新的审批请求
                                if "__interrupt__" in mode_data:
                                    from langgraph.types import Interrupt
                                    from Django_xm.apps.tools.base import is_approval_interrupt
                                    interrupts = mode_data["__interrupt__"]
                                    if interrupts:
                                        for intr in interrupts:
                                            if isinstance(intr, Interrupt):
                                                interrupt_value = intr.value
                                            elif isinstance(intr, dict):
                                                interrupt_value = intr.get("value", intr)
                                            else:
                                                interrupt_value = intr

                                            if is_approval_interrupt(interrupt_value):
                                                tool_name = interrupt_value.get("tool_name", "unknown")
                                                new_interrupt_id = intr.id if isinstance(intr, Interrupt) else ""
                                                approval_data = {
                                                    'tool_name': tool_name,
                                                    'tool_call_id': new_interrupt_id,
                                                    'interrupt_id': new_interrupt_id,
                                                    'title': interrupt_value.get("title", "确认操作"),
                                                    'description': interrupt_value.get("description", ""),
                                                    'action': interrupt_value.get("action", "confirm"),
                                                    'danger_level': interrupt_value.get("danger_level", "medium"),
                                                }
                                                # 透传 operation（统一字段，兼容旧 command）
                                                op = interrupt_value.get("operation") or interrupt_value.get("command") or ""
                                                if op:
                                                    approval_data['operation'] = op
                                                    # 通用匹配：tool_name 一致 + parameters 中任意字段值等于 operation
                                                    for tc_key, tc_info in tool_calls_map.items():
                                                        if tc_info.get("name") != tool_name:
                                                            continue
                                                        tc_params = tc_info.get("parameters", {})
                                                        if any(str(v) == op for v in tc_params.values()):
                                                            approval_data['llm_tool_call_id'] = tc_info.get("id") or tc_key
                                                            break
                                                    # 回退：同名工具中第一个
                                                    if 'llm_tool_call_id' not in approval_data:
                                                        for tc_key, tc_info in tool_calls_map.items():
                                                            if tc_info.get("name") == tool_name:
                                                                approval_data['llm_tool_call_id'] = tc_info.get("id") or tc_key
                                                                break
                                                if interrupt_value.get("extra"):
                                                    approval_data['extra'] = interrupt_value["extra"]
                                                if interrupt_value.get("input_placeholder"):
                                                    approval_data['input_placeholder'] = interrupt_value["input_placeholder"]
                                                logger.info(f"审批恢复流中检测到新审批: tool={tool_name}, danger={approval_data['danger_level']}")
                                                yield f"data: {json.dumps({'type': 'approval', 'data': approval_data}, ensure_ascii=False)}\n\n"

                                for node_name, node_output in mode_data.items():
                                    if node_name == "__interrupt__":
                                        continue
                                    # tools 节点的输出包含 messages（ToolMessage）
                                    if isinstance(node_output, dict):
                                        node_messages = node_output.get("messages", [])
                                        if isinstance(node_messages, list):
                                            for msg in node_messages:
                                                # 提取 ToolMessage 的内容
                                                tool_content = None
                                                tool_call_id = None
                                                tool_name_from_msg = None
                                                if hasattr(msg, "content"):
                                                    tool_content = msg.content
                                                    tool_call_id = getattr(msg, "tool_call_id", None)
                                                    tool_name_from_msg = getattr(msg, "name", None) or node_name
                                                elif isinstance(msg, dict):
                                                    tool_content = msg.get("content")
                                                    tool_call_id = msg.get("tool_call_id")
                                                    tool_name_from_msg = msg.get("name") or node_name

                                                if tool_content is not None and tool_call_id:
                                                    # 查找对应的工具调用名称
                                                    tool_name = tool_name_from_msg
                                                    tc_info = tool_calls_map.get(tool_call_id)
                                                    if tc_info:
                                                        tool_name = tc_info.get("name", tool_name)
                                                        # 同步更新 tool_calls_map 中的状态和结果
                                                        tc_info["status"] = "completed"
                                                        tc_info["result"] = tool_content
                                                    # 写入 tool result，供前端展示
                                                    # 必须包含 id 字段（值等于 tool_call_id），否则前端
                                                    # _findMatchingToolCall 无法匹配，会新增而非更新
                                                    yield f"data: {json.dumps({'type': 'tool_result', 'data': {'id': tool_call_id, 'tool_call_id': tool_call_id, 'name': tool_name, 'content': tool_content, 'state': 'output-available', 'status': 'completed'}}, ensure_ascii=False)}\n\n"
                            continue

                        # 处理 messages 模式
                        # 审批恢复时，LangGraph 会重新执行 tools 节点（从头开始），
                        # messages 模式可能重新发送 AIMessage（含 tool_calls）和 ToolMessage。
                        # AIMessage 已在第一次请求中处理过，跳过以避免前端显示重复的工具调用。
                        # 只处理 ToolMessage（工具结果）和最终的文本回复。
                        from langchain_core.messages import AIMessage as LCAIMessage, ToolMessage as LCToolMessage
                        from Django_xm.apps.chat.services.stream_helpers import process_stream_chunk

                        # 解析 message 对象
                        msg_obj = mode_data
                        if isinstance(mode_data, tuple) and len(mode_data) == 2:
                            msg_obj = mode_data[0]

                        # 跳过含 tool_calls 的 AIMessage 中已有的 tool_call（已在第一次请求中处理），
                        # 但不跳过新增的 tool_call（审批恢复后 Agent 可能生成新的工具调用，如 fs_write_file）。
                        if isinstance(msg_obj, LCAIMessage) and (getattr(msg_obj, 'tool_calls', None) or getattr(msg_obj, 'tool_call_chunks', None)):
                            msg_tool_calls = getattr(msg_obj, 'tool_calls', None) or []
                            # 检查是否有新增的 tool_call（不在 tool_calls_map 中的）
                            new_tool_calls = []
                            for tc in msg_tool_calls:
                                tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                                if tc_id and tc_id not in tool_calls_map:
                                    new_tool_calls.append(tc)

                            if not new_tool_calls:
                                # 全部是已有的 tool_calls，跳过整条 AIMessage
                                continue
                            else:
                                # 有新增的 tool_calls，只处理新增部分
                                # 将新增的 tool_call 推送给前端
                                for tc in new_tool_calls:
                                    tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                                    tc_name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
                                    tc_args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {})
                                    if tc_id:
                                        tool_calls_map[tc_id] = {
                                            "id": tc_id,
                                            "name": tc_name or "unknown",
                                            "parameters": tc_args or {},
                                            "state": "input-available",
                                            "status": "running",
                                        }
                                        yield f"data: {json.dumps({'type': 'tool', 'data': tool_calls_map[tc_id]}, ensure_ascii=False)}\n\n"
                                # 跳过这条 AIMessage 的文本内容处理（tool_calls 消息通常没有文本内容）
                                continue

                        try:
                            for event in process_stream_chunk(
                                mode_data, tool_calls_map, current_message_content,
                                tool_call_count=tool_call_count,
                                lcp_func=lambda a, b: 0,
                                accumulated_reasoning=accumulated_reasoning,
                                tool_args_accumulator=tool_args_accumulator,
                                mode='agent',
                            ):
                                if event.get("type") == "chunk":
                                    current_message_content += event.get("content", "")
                                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                        except Exception as chunk_err:
                            logger.warning(f"审批恢复流式 chunk 处理失败: {chunk_err}")
                            continue

                finally:
                    # 释放当前事件循环的异步 Checkpointer 连接池，防止 PostgreSQL 连接泄漏
                    try:
                        from Django_xm.apps.ai_engine.services.checkpointer_factory import release_async_checkpointer
                        loop.run_until_complete(release_async_checkpointer())
                    except Exception:
                        pass
                    loop.close()

                # 深度思考模式兜底：当模型将全部输出放入 thinking 字段而 content 为空时，
                # 将推理内容作为主内容发送
                if (not current_message_content.strip()
                        and accumulated_reasoning
                        and accumulated_reasoning.get("content", "").strip()):
                    reasoning_text = accumulated_reasoning["content"].strip()
                    logger.info(
                        f"审批恢复深度思考兜底: content 为空，将推理内容 ({len(reasoning_text)} 字符) 作为主内容发送"
                    )
                    yield f"data: {json.dumps({'type': 'chunk', 'content': reasoning_text}, ensure_ascii=False)}\n\n"
                    current_message_content = reasoning_text

                # 注意：审批恢复后的消息由前端 syncLastMessageToBackend 统一保存到数据库，
                # 后端不再重复保存，避免创建重复消息。
                # 后端只在流式完成后发送 [DONE]，前端收到后触发同步。

                yield f"data: [DONE]\n\n"

            except Exception as e:
                logger.error(f"审批恢复执行失败: {e}", exc_info=True)
                yield sse_error_event("approval_error", f"审批恢复执行失败: {str(e)}")
            finally:
                # 确保事件循环被关闭，防止资源泄漏
                try:
                    pending = asyncio.all_tasks(loop)
                    if pending:
                        for task in pending:
                            task.cancel()
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                except Exception:
                    pass
                try:
                    loop.close()
                except Exception:
                    pass

        return sse_response(generate())
