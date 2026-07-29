"""
核心聊天、会话和消息视图

提供聊天交互、流式响应、会话管理、消息管理等接口。
"""

import json
import logging
import time
from functools import wraps

from asgiref.sync import sync_to_async
from django.core.exceptions import ObjectDoesNotExist
from django.core.paginator import Paginator
from django.db import models, transaction
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import BaseRenderer
from rest_framework.views import APIView

from Django_xm.apps.attachments.services.cross_app import soft_delete_session_attachments
from Django_xm.apps.cache_manager.services.secure_session_cache import SecureSessionCacheService
from Django_xm.apps.core.throttling import ChatStreamRateThrottle
from Django_xm.async_utils import run_async
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.responses import error_response, success_response, validation_error_response
from Django_xm.common.sse_utils import sse_error_event, sse_error_response, sse_response

from .models import ChatMessage, ChatSession, MessageRole
from .serializers import (
    ChatMessageSerializer,
    ChatRequestSerializer,
    ChatResponseSerializer,
    ChatSessionCreateSerializer,
    ChatSessionDetailSerializer,
    ChatSessionListSerializer,
    ChatSessionUpdateSerializer,
)
from .services.chat_service import ChatService

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
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolCall
        from langgraph.checkpoint.base import empty_checkpoint

        from Django_xm.apps.ai_engine.services.checkpointer_factory import get_async_checkpointer

        checkpointer = await get_async_checkpointer()
        if checkpointer is None:
            return

        thread_id = session_id

        # Step 1: 删除整个 thread 的旧 checkpoint
        if hasattr(checkpointer, "adelete_thread"):
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
            ).order_by("created_at")
        )

        if not remaining_messages:
            logger.info(f"Checkpoint 重建: 无剩余消息，跳过: thread={thread_id}")
            return

        # 将数据库消息转为 LangChain 消息对象
        lc_messages = []
        for msg in remaining_messages:
            content = msg.content or ""
            if msg.role == "user":
                lc_messages.append(HumanMessage(content=content))
            elif msg.role == "assistant":
                ai_kwargs = {}
                # 恢复 tool_calls：将 JSON dict 转为 LangChain ToolCall 对象
                if msg.tool_calls:
                    tool_calls = []
                    for tc in msg.tool_calls:
                        # ToolCall 是 TypedDict（dict 别名），isinstance(tc, dict) 已涵盖所有情况
                        if isinstance(tc, dict):
                            tool_calls.append(
                                ToolCall(
                                    name=tc.get("name", ""),
                                    args=tc.get("args", {}),
                                    id=tc.get("id", ""),
                                )
                            )
                    ai_kwargs["tool_calls"] = tool_calls
                lc_messages.append(AIMessage(content=content, **ai_kwargs))
            elif msg.role == "system":
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
        except Exception:
            duration = time.time() - start_time
            logger.exception(f"请求失败: 耗时 {duration:.3f}s - 错误")
            raise

    return wrapper


class BaseChatAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get_session_or_404(self, session_id, user, prefetch_attachments=False):
        from .services.message_service import get_user_session

        return get_user_session(user, session_id, prefetch_attachments=prefetch_attachments)

    def get_message_or_404(self, message_id, user):
        try:
            return ChatMessage.objects.select_related("session").get(
                id=message_id, session__user=user, is_deleted=False
            )
        except ObjectDoesNotExist:
            return None

    def paginate_queryset(self, queryset, page_size=20):
        paginator = Paginator(queryset, page_size)
        page_number = self.request.query_params.get("page", 1)
        page_obj = paginator.get_page(page_number)
        return {
            "items": page_obj.object_list,
            "total": paginator.count,
            "page": page_obj.number,
            "page_size": page_size,
            "total_pages": paginator.num_pages,
        }


class ChatView(BaseChatAPIView):
    @extend_schema(request=ChatRequestSerializer, responses=ChatResponseSerializer)
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
        session_id = data.get("session_id")
        if session_id:
            from .services.message_service import get_user_session

            session = get_user_session(request.user, session_id)

        if not session:
            with transaction.atomic():
                session = ChatSession.objects.create(
                    user=request.user,
                    mode=data.get("mode", "agent"),
                    selected_knowledge_base=data.get("selected_knowledge_base"),
                    selected_knowledge_bases=data.get("selected_knowledge_bases"),
                    title=data["message"][:100] if len(data["message"]) > 100 else data["message"],
                )
            logger.info(f"创建新会话: {session.session_id}")
        else:
            if data.get("selected_knowledge_base") is not None:
                session.selected_knowledge_base = data["selected_knowledge_base"]
                session.save()
            if data.get("selected_knowledge_bases") is not None:
                session.selected_knowledge_bases = data["selected_knowledge_bases"]
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
                    user_content=data["message"],
                    ai_content=result.get("message", ""),
                    attachment_ids=data.get("attachment_ids"),
                )

            result_data = ChatResponseSerializer(result).data
            result_data["session_id"] = session.session_id
            result_data["user_message_id"] = user_message.id
            result_data["ai_message_id"] = ai_message.id

            return success_response(data=result_data, message="操作成功")

        except Exception as e:
            error_msg = f"处理聊天请求时出错: {e!s}"
            logger.exception(error_msg)

            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message="抱歉，处理您的请求时出现错误",
                data=ChatResponseSerializer(
                    {
                        "message": "抱歉，处理您的请求时出现错误。",
                        "mode": data.get("mode", "agent"),
                        "tools_used": [],
                        "success": False,
                        "error": str(e),
                        "session_id": session.session_id if session else None,
                    }
                ).data,
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class SSERenderer(BaseRenderer):
    media_type = "text/event-stream"
    format = "txt"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


class ChatStreamView(BaseChatAPIView):
    renderer_classes = [SSERenderer]
    throttle_classes = [ChatStreamRateThrottle]

    @extend_schema(exclude=True)
    def post(self, request):
        serializer = ChatRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(errors=serializer.errors, message="数据验证失败")

        data = serializer.validated_data
        logger.info(f"收到流式聊天请求: {data['message'][:50]}..., attachment_ids={data.get('attachment_ids')}")

        # 持久化知识库选择到会话
        session_id = data.get("session_id")
        selected_kb = data.get("selected_knowledge_base")
        if session_id and selected_kb:
            try:
                from .services.message_service import get_user_session

                session = get_user_session(request.user, session_id)
                if session and session.selected_knowledge_base != selected_kb:
                    session.selected_knowledge_base = selected_kb
                    session.save(update_fields=["selected_knowledge_base"])
            except Exception as e:
                logger.warning(f"持久化知识库选择失败: {e}")

        selected_kbs = data.get("selected_knowledge_bases")
        if session_id and selected_kbs:
            try:
                from .services.message_service import get_user_session

                session = get_user_session(request.user, session_id)
                if session and session.selected_knowledge_bases != selected_kbs:
                    session.selected_knowledge_bases = selected_kbs
                    session.save(update_fields=["selected_knowledge_bases"])
            except Exception as e:
                logger.warning(f"持久化多知识库选择失败: {e}")

        attachment_ids = data.get("attachment_ids") or []
        original_attachment_ids = list(attachment_ids)
        original_message = data["message"]  # 保存原始消息，用于深度研究任务名等场景
        data["_original_message"] = original_message
        pending_progress = []
        if attachment_ids:
            try:
                from Django_xm.apps.attachments.services.cross_app import get_attachment_service

                att_svc = get_attachment_service()

                def progress_callback(stage, message):
                    pending_progress.append(
                        {
                            "type": "attachment_processing",
                            "data": {"stage": stage, "message": message},
                        }
                    )

                user_content = att_svc.build_user_content(
                    data["message"], attachment_ids, progress_callback=progress_callback
                )
                if user_content["type"] == "text":
                    data["message"] = user_content["content"]
                    data["_preloaded_attachment_type"] = "text"
                else:
                    data["_preloaded_attachment_content"] = user_content["content"]
                    data["_preloaded_attachment_type"] = "multimodal"
                data["_has_attachments"] = True
                data["attachment_ids"] = []
            except Exception:
                logger.exception("预加载附件内容失败")

        # Task 23.1：generate() 闭包拆分为 _init_stream / _process_chunks / _cleanup_stream
        # 三个职责单一的子函数，共享状态通过 ChatStreamContext dataclass 传递。
        # 详见 chat/services/sse_generator.py
        from .services.sse_generator import ChatStreamContext, generate_chat_stream

        ctx = ChatStreamContext(
            request=request,
            data=data,
            original_attachment_ids=original_attachment_ids,
            pending_progress=pending_progress,
        )
        return sse_response(generate_chat_stream(ctx))


class ChatModesView(BaseChatAPIView):
    @extend_schema(exclude=True)
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
            },
        }
        CacheService.set(cache_key, result, CacheTTL.TOOL_LONG)
        return success_response(data=result, message="操作成功")


class ChatFinalizeView(BaseChatAPIView):
    """通知后端流式输出已最终化（前端已完成本地 FINALIZING → SYNCING → COMPLETED）。

    前端 ``useStreamFinalizer.finalizeStream`` 在本地同步完成后调用此端点，
    后端发布 ``STREAM_FINALIZED`` 事件到 session 频道，通知非请求浏览器
    可安全拉取后端数据（此时后端已持久化前端同步的 content）。

    请求体：
        - session_id: 会话 ID（必填）
        - message_id: 消息 ID（必填，用于 payload 携带）
    """

    @extend_schema(exclude=True)
    @log_view_action
    def post(self, request):
        session_id = request.data.get("session_id")
        message_id = request.data.get("message_id")

        if not session_id:
            return validation_error_response(message="session_id 不能为空")
        if not message_id:
            return validation_error_response(message="message_id 不能为空")

        # 校验会话归属权
        session = self.get_session_or_404(session_id, request.user)
        if session is None:
            return error_response(
                message="会话不存在或无权访问",
                code=ErrorCode.NOT_FOUND,
                status_code=status.HTTP_404_NOT_FOUND,
            )

        # 发布 STREAM_FINALIZED 事件到 session 频道
        from Django_xm.common.event_schema import EventSource, EventType, PayloadValidationError
        from Django_xm.common.realtime_events import publish_event_sync

        payload = {
            "source": EventSource.CHAT,
            "source_id": str(session_id),
            "message_id": str(message_id),
        }
        try:
            publish_event_sync(
                EventType.STREAM_FINALIZED,
                payload,
                session_id=str(session_id),
            )
        except PayloadValidationError:
            logger.exception(
                f"[ChatFinalize] STREAM_FINALIZED payload 校验失败: session_id={session_id}, message_id={message_id}",
            )
            return error_response(
                message="事件 payload 校验失败",
                code=ErrorCode.SERVER_ERROR,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        except Exception as e:
            logger.warning(f"[ChatFinalize] 广播 STREAM_FINALIZED 失败: {e}")
            return error_response(
                message="通知流式完成失败",
                code=ErrorCode.SERVER_ERROR,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return success_response(message="流式完成通知已发送")


class ChatSessionListView(BaseChatAPIView):
    @extend_schema(operation_id="chat_sessions_list", responses=ChatSessionListSerializer(many=True))
    @log_view_action
    def get(self, request):
        user_id = request.user.id
        try:
            page_size = min(int(request.query_params.get("page_size", 20)), 100)
        except (ValueError, TypeError):
            page_size = 20

        cached_sessions = SecureSessionCacheService.get_user_sessions_list(user_id)

        if cached_sessions:
            try:
                sessions_data = SecureSessionCacheService.get_cached_sessions_batch(user_id, cached_sessions)

                if sessions_data:
                    sessions_data.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
                    total = len(sessions_data)
                    page = 1
                    start = (page - 1) * page_size
                    end = start + page_size
                    paged_items = sessions_data[start:end]
                    logger.info(f"Returning {len(paged_items)} cached sessions for user {user_id}")
                    return success_response(
                        data={
                            "items": paged_items,
                            "total": total,
                            "page": page,
                            "page_size": page_size,
                            "total_pages": (total + page_size - 1) // page_size,
                        }
                    )
            except Exception as e:
                logger.warning(f"Cache read failed, falling back to DB: {e!s}")

        sessions = (
            ChatSession.objects.filter(user=request.user, is_deleted=False)
            .select_related("user")
            .annotate(message_count=models.Count("messages"))
            .order_by("-updated_at")
        )

        paginated_data = self.paginate_queryset(sessions, page_size)
        serializer = ChatSessionListSerializer(paginated_data["items"], many=True)
        response_data = {
            **paginated_data,
            "items": serializer.data,
        }

        for item_data in response_data["items"]:
            SecureSessionCacheService.cache_session(user_id, item_data)

        return success_response(data=response_data)


class ChatSessionCreateView(BaseChatAPIView):
    @extend_schema(request=ChatSessionCreateSerializer, responses=ChatSessionDetailSerializer)
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
        session = ChatSession.objects.prefetch_related("messages", "messages__attachments").get(pk=session.pk)

        return success_response(
            data=ChatSessionDetailSerializer(session).data, message="会话创建成功", http_status=status.HTTP_201_CREATED
        )


class ChatSessionDetailView(BaseChatAPIView):
    @extend_schema(operation_id="chat_sessions_detail", responses=ChatSessionDetailSerializer)
    @log_view_action
    def get(self, request, session_id):
        session = self.get_session_or_404(session_id, request.user, prefetch_attachments=True)
        if not session:
            return error_response(code=ErrorCode.NOT_FOUND, message="会话不存在", http_status=status.HTTP_404_NOT_FOUND)

        serializer = ChatSessionDetailSerializer(session)
        return success_response(data=serializer.data)

    @extend_schema(request=ChatSessionUpdateSerializer, responses=ChatSessionDetailSerializer)
    @log_view_action
    @transaction.atomic
    def patch(self, request, session_id):
        from Django_xm.apps.cache_manager.services.cache_service import invalidate_chat_cache

        session = self.get_session_or_404(session_id, request.user)
        if not session:
            return error_response(code=ErrorCode.NOT_FOUND, message="会话不存在", http_status=status.HTTP_404_NOT_FOUND)

        serializer = ChatSessionUpdateSerializer(session, data=request.data, partial=True)
        if not serializer.is_valid():
            return validation_error_response(errors=serializer.errors, message="更新参数错误")

        serializer.save()
        invalidate_chat_cache(user_id=request.user.id)
        # 重新查询以 prefetch messages + attachments，避免序列化 N+1
        session = ChatSession.objects.prefetch_related("messages", "messages__attachments").get(pk=session.pk)
        return success_response(data=ChatSessionDetailSerializer(session).data, message="会话更新成功")

    @extend_schema(exclude=True)
    @log_view_action
    @transaction.atomic
    def delete(self, request, session_id):
        from Django_xm.apps.cache_manager.services.cache_service import invalidate_chat_cache
        from Django_xm.apps.research.services.cross_app import get_linked_research_tasks

        session = self.get_session_or_404(session_id, request.user)
        if not session:
            return error_response(code=ErrorCode.NOT_FOUND, message="会话不存在", http_status=status.HTTP_404_NOT_FOUND)

        linked_tasks = get_linked_research_tasks(session.session_id)

        # 弱关联模式：保留 ResearchTask 的 session_id 作为历史元数据，
        # 即使对话被删除，深度研究的"在聊天中讨论"仍可通过 session_id 尝试跳转，
        # 前端做容错处理（会话不存在时降级到当前/新会话）。

        session.soft_delete()

        soft_delete_session_attachments(session)

        # 清理 LangGraph checkpoint 和 Store 数据（PostgreSQL/SQLite 中的对话状态）
        try:
            from Django_xm.apps.ai_engine.services.checkpointer_factory import (
                delete_thread_checkpoints,
                delete_thread_store_data,
            )

            async def _cleanup():
                await delete_thread_checkpoints(session.session_id)
                await delete_thread_store_data(request.user.id, session.session_id)

            run_async(_cleanup())
        except Exception as e:
            import logging

            logging.getLogger(__name__).warning(f"清理 checkpoint/Store 数据失败: {e}")

        # 检查关联的深度研究是否已删除（包括已软删除的），如果都已删除则清理后端数据
        try:
            from django.apps import apps as django_apps

            from Django_xm.apps.research.services.cross_app import (
                cleanup_research_if_both_deleted,
                get_linked_research_tasks_including_deleted,
            )

            # 通过 ResearchTask.session_id 查找
            all_linked = get_linked_research_tasks_including_deleted(session.session_id)

            # 通过 ChatMessage.research_task_id 补充查找（深度研究模块创建的任务可能没有 session_id）
            ChatMessage = django_apps.get_model("chat", "ChatMessage")
            ResearchTask = django_apps.get_model("research", "ResearchTask")
            # 用 all_objects，默认 objects 过滤了 is_deleted=True 的消息
            msg_task_ids = set(
                ChatMessage.all_objects.filter(
                    session__session_id=session.session_id,
                    research_task_id__isnull=False,
                )
                .exclude(research_task_id="")
                .values_list("research_task_id", flat=True)
            )
            linked_task_ids = {t["task_id"] for t in all_linked}
            for tid in msg_task_ids - linked_task_ids:
                # 必须用 all_objects，默认 objects 过滤了 is_deleted=True
                task = ResearchTask.all_objects.filter(task_id=tid).values("task_id", "is_deleted").first()
                if task:
                    all_linked.append(task)

            for task_info in all_linked:
                if task_info.get("is_deleted"):
                    cleanup_research_if_both_deleted(task_info["task_id"], request.user.id)
        except Exception as e:
            import logging

            logging.getLogger(__name__).warning(f"检查关联研究任务清理失败: {e}")

        SecureSessionCacheService.invalidate_all_user_sessions(request.user.id)
        invalidate_chat_cache(user_id=request.user.id)

        response_data = {"message": "会话删除成功"}
        if linked_tasks:
            response_data["linked_research_preserved"] = True
            response_data["linked_research_count"] = len(linked_tasks)

        return success_response(data=response_data, message="会话删除成功")


class ChatSessionCompactView(BaseChatAPIView):
    @extend_schema(exclude=True)
    @log_view_action
    def post(self, request, session_id):
        session = self.get_session_or_404(session_id, request.user, prefetch_attachments=True)
        if not session:
            return error_response(code=ErrorCode.NOT_FOUND, message="会话不存在", http_status=status.HTTP_404_NOT_FOUND)

        from Django_xm.apps.context_manager.services.manager import create_context_manager

        messages = (
            ChatMessage.objects.filter(session=session).only("role", "content", "tool_calls").order_by("created_at")
        )
        message_list = list(messages.values("role", "content", "tool_calls"))

        context_manager = create_context_manager(user_id=request.user.id, thread_id=session_id)
        result = context_manager.build_structured_context(
            messages=message_list,
            query="",
            mode="agent",
        )
        metadata = result["metadata"]
        prune_info = metadata["prune_result"]

        return success_response(
            data={
                "compressed": prune_info["pruned_count"] > 0,
                "originalMessageCount": prune_info["original_count"],
                "keptMessageCount": prune_info["original_count"] - prune_info["pruned_count"],
                "prunedCount": prune_info["pruned_count"],
                "dedupedCount": prune_info["deduped_count"],
                "filteredCount": prune_info["filtered_count"],
                "injectionDetected": metadata["injection_detected"],
                "budgetOverSections": metadata["budget_over_sections"],
            }
        )


class ChatMessageCreateView(BaseChatAPIView):
    @extend_schema(request=ChatMessageSerializer, responses=ChatMessageSerializer)
    @log_view_action
    @transaction.atomic
    def post(self, request, session_id):
        from Django_xm.apps.cache_manager.services.cache_service import invalidate_chat_cache

        session = self.get_session_or_404(session_id, request.user)
        if not session:
            return error_response(code=ErrorCode.NOT_FOUND, message="会话不存在", http_status=status.HTTP_404_NOT_FOUND)

        serializer = ChatMessageSerializer(data=request.data)
        if not serializer.is_valid():
            logger.warning(f"消息验证失败: {serializer.errors}, 请求数据: {request.data}")
            return validation_error_response(errors=serializer.errors)

        message = serializer.save(session=session)

        attachment_ids = request.data.get("attachment_ids") or []
        if attachment_ids:
            from Django_xm.apps.attachments.services.cross_app import get_attachment_service

            get_attachment_service().link_attachments_to_message(message, attachment_ids)

        invalidate_chat_cache(user_id=request.user.id)

        return success_response(
            data=ChatMessageSerializer(message).data, message="消息发送成功", http_status=status.HTTP_201_CREATED
        )


class ChatMessageBatchCreateView(BaseChatAPIView):
    @extend_schema(request=ChatMessageSerializer, responses=ChatMessageSerializer)
    @log_view_action
    def post(self, request, session_id):
        session = self.get_session_or_404(session_id, request.user)
        if not session:
            return error_response(code=ErrorCode.NOT_FOUND, message="会话不存在", http_status=status.HTTP_404_NOT_FOUND)

        messages_data = request.data.get("messages", [])
        if not isinstance(messages_data, list):
            return error_response(code=ErrorCode.INVALID_PARAMS, message="messages 字段必须是数组")

        if len(messages_data) > 50:
            return error_response(code=ErrorCode.INVALID_PARAMS, message="单次批量创建消息数量不能超过50条")

        created_messages = []
        with transaction.atomic():
            for msg_data in messages_data:
                msg_data["session"] = session.pk
                serializer = ChatMessageSerializer(data=msg_data)
                if serializer.is_valid():
                    message = serializer.save()
                    created_messages.append(message.pk)

        # 批量查询并 prefetch attachments，避免序列化时 N+1
        messages_qs = (
            ChatMessage.objects.filter(pk__in=created_messages).prefetch_related("attachments").order_by("created_at")
        )

        return success_response(
            data={
                "created_count": len(created_messages),
                "messages": ChatMessageSerializer(messages_qs, many=True).data,
            },
            message=f"批量创建 {len(created_messages)} 条消息成功",
        )


class ChatMessageDeleteView(BaseChatAPIView):
    @extend_schema(exclude=True)
    @log_view_action
    @transaction.atomic
    def delete(self, request, message_id):
        message = self.get_message_or_404(message_id, request.user)
        if not message:
            return error_response(code=ErrorCode.NOT_FOUND, message="消息不存在", http_status=status.HTTP_404_NOT_FOUND)

        message.soft_delete()

        # 事务提交后再重建 checkpoint，否则新线程的数据库连接看不到未提交的 soft_delete
        session_id = message.session.session_id
        transaction.on_commit(lambda: run_async(_cleanup_checkpoint_messages_async_by_id(session_id, [message.id])))

        return success_response(
            data={
                "message_id": message_id,
            },
            message="消息删除成功",
        )


class ChatMessagePairDeleteView(BaseChatAPIView):
    @extend_schema(exclude=True)
    @log_view_action
    @transaction.atomic
    def delete(self, request, session_id):
        session = self.get_session_or_404(session_id, request.user)
        if not session:
            return error_response(code=ErrorCode.NOT_FOUND, message="会话不存在", http_status=status.HTTP_404_NOT_FOUND)

        user_message_id = request.data.get("user_message_id")
        if not user_message_id:
            return error_response(code=ErrorCode.INVALID_PARAMS, message="缺少 user_message_id 参数")

        try:
            user_message_id = int(user_message_id)
        except (ValueError, TypeError):
            return error_response(code=ErrorCode.INVALID_PARAMS, message="user_message_id 必须为数字")

        user_message = ChatMessage.objects.filter(id=user_message_id, session=session, is_deleted=False).first()
        if not user_message:
            return error_response(
                code=ErrorCode.NOT_FOUND, message="用户消息不存在", http_status=status.HTTP_404_NOT_FOUND
            )

        assistant_message = (
            ChatMessage.objects.filter(
                session=session,
                role=MessageRole.ASSISTANT,
                is_deleted=False,
                created_at__gt=user_message.created_at,
            )
            .order_by("created_at")
            .first()
        )

        deleted_messages = [user_message]

        if assistant_message:
            deleted_messages.append(assistant_message)

        for msg in deleted_messages:
            msg.soft_delete()

        # 事务提交后再重建 checkpoint，否则新线程的数据库连接看不到未提交的 soft_delete
        session_id = session.session_id
        deleted_ids = [m.id for m in deleted_messages]
        transaction.on_commit(lambda: run_async(_cleanup_checkpoint_messages_async_by_id(session_id, deleted_ids)))

        return success_response(
            data={
                "deleted_message_ids": [m.id for m in deleted_messages],
            },
            message="消息对删除成功",
        )


class ChatMessageUpdateView(BaseChatAPIView):
    @extend_schema(request=ChatMessageSerializer, responses=ChatMessageSerializer)
    @log_view_action
    @transaction.atomic
    def patch(self, request, message_id):
        message = self.get_message_or_404(message_id, request.user)
        if not message:
            return error_response(code=ErrorCode.NOT_FOUND, message="消息不存在", http_status=status.HTTP_404_NOT_FOUND)

        serializer = ChatMessageSerializer(message, data=request.data, partial=True)
        if not serializer.is_valid():
            return validation_error_response(errors=serializer.errors, message="更新参数错误")

        serializer.save()
        # 重新查询以 prefetch attachments，避免序列化时 N+1
        message = ChatMessage.objects.prefetch_related("attachments").get(pk=message.pk)
        return success_response(data=ChatMessageSerializer(message).data, message="消息更新成功")


async def _stream_chat_resume_generator(
    request,
    approval,
    resume_value,
    session_id,
    request_data=None,
    graph_interrupt_id=None,
    langgraph_resume_id=None,
):
    """聊天审批恢复的异步 SSE 生成器。

    通过 ChatService 重建 agent，使用 ``Command(resume=...)`` 恢复 LangGraph 执行，
    并以 SSE 事件流推送后续输出。

    流程：
    1. 从 ``request_data`` 与 ``approval.extra`` 读取模型/工具配置
    2. 构造 ``Command(resume={langgraph_resume_id or approval.interrupt_id: resume_value})``
    3. 创建 ChatService 并构建 agent（含 checkpointer）
    4. 校验 interrupt 归属（防止深度研究 interrupt 被错误处理）
    5. 从 checkpoint 加载历史 tool_call 信息
    6. 流式恢复执行，处理 messages/updates 两种 stream_mode
    7. 检测新的审批中断（批量审批场景）并推送 approval 事件
    8. 流结束后发送 ``[DONE]``

    心跳保活由调用方通过 ``sse_async_heartbeat_generator`` 包装实现，
    本生成器只负责产出业务事件。

    Args:
        request: HTTP 请求对象（用到 ``request.user.id``）
        approval: Approval 模型实例
        resume_value: 恢复值（True/False/user_input，或批量场景的 {tool_call_id: bool}）
        session_id: 会话 ID（用作 thread_id）
        request_data: 预提取的 ``request.data`` 字典；为 None 时尝试从 request 读取。
        graph_interrupt_id: 批次 UUID（仅用于日志）；为 None 时回退到 ``approval.interrupt_id``
        langgraph_resume_id: LangGraph 恢复 ID（= ``intr.id``，作为 Command resume KEY）；
                             为 None 时回退到 ``approval.interrupt_id``

    Yields:
        SSE 格式字符串（``"data: ...\\n\\n"``）
    """
    from langgraph.types import Command

    from Django_xm.apps.ai_engine.services.checkpointer_factory import release_async_checkpointer
    from Django_xm.apps.chat.services.chat_service import ChatService

    if request_data is None:
        try:
            request_data = dict(request.data) if hasattr(request, "data") else {}
        except Exception as req_err:
            logger.warning(f"[ChatApprovalResume] 读取 request.data 失败: {req_err}")
            request_data = {}

    # 兼容历史 approval：extra 中持久化的 model_config / tool_config 优先级低于 request_data
    approval_extra = getattr(approval, "extra", None) or {}
    if not isinstance(approval_extra, dict):
        approval_extra = {}
    persisted_tool_config = (
        approval_extra.get("tool_config", {}) if isinstance(approval_extra.get("tool_config", {}), dict) else {}
    )
    persisted_model_config = (
        approval_extra.get("model_config", {}) if isinstance(approval_extra.get("model_config", {}), dict) else {}
    )

    provider_id = request_data.get("provider_id") or persisted_model_config.get("provider_id")
    model_name = request_data.get("model_name") or persisted_model_config.get("model_name")
    use_deep_thinking = request_data.get("use_deep_thinking", persisted_model_config.get("use_deep_thinking", False))
    special_params = request_data.get("special_params") or persisted_model_config.get("special_params")
    temperature = request_data.get("temperature") or persisted_model_config.get("temperature")
    max_tokens = request_data.get("max_tokens") or persisted_model_config.get("max_tokens")

    use_tools = request_data.get("use_tools", persisted_tool_config.get("use_tools", True))
    use_web_search = request_data.get("use_web_search", persisted_tool_config.get("use_web_search", False))
    use_mcp = request_data.get("use_mcp", persisted_tool_config.get("use_mcp", False))
    selected_mcp_servers = request_data.get("selected_mcp_servers") or persisted_tool_config.get("selected_mcp_servers")
    selected_tools = request_data.get("selected_tools") or persisted_tool_config.get("selected_tools")
    use_knowledge_base = request_data.get("use_knowledge_base", persisted_tool_config.get("use_knowledge_base", False))
    selected_knowledge_bases = request_data.get("selected_knowledge_bases") or persisted_tool_config.get(
        "selected_knowledge_bases", []
    )

    interrupt_id = approval.interrupt_id
    effective_resume_key = langgraph_resume_id or interrupt_id

    logger.info(
        f"[ChatApprovalResume] 恢复: session={session_id}, "
        f"interrupt_id={interrupt_id}, graph_interrupt_id={graph_interrupt_id}, "
        f"langgraph_resume_id={langgraph_resume_id}, "
        f"effective_resume_key={effective_resume_key}, "
        f"resume_value_type={type(resume_value).__name__}"
    )

    current_message_content = ""
    tool_calls_map = {}
    tool_call_count = {}
    accumulated_reasoning = {}
    tool_args_accumulator = {}

    try:
        chat_service = ChatService(user_id=request.user.id, thread_id=session_id)
        data = {
            "session_id": session_id,
            "mode": "agent",
            "use_tools": use_tools,
            "use_web_search": use_web_search,
            "use_mcp": use_mcp,
            "selected_mcp_servers": selected_mcp_servers,
            "selected_tools": selected_tools,
            "use_knowledge_base": use_knowledge_base,
            "selected_knowledge_bases": selected_knowledge_bases,
            "provider_id": provider_id,
            "model_name": model_name,
            "use_deep_thinking": use_deep_thinking,
            "special_params": special_params,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # 深度思考模式标记
        enable_deep_thinking = bool(use_deep_thinking) and bool(provider_id) and bool(model_name)
        if enable_deep_thinking:
            from Django_xm.apps.ai_engine.services.llm_factory import model_supports_capability

            enable_deep_thinking = model_supports_capability(provider_id, model_name, "deep_thinking")
        if enable_deep_thinking:
            data["_enable_deep_thinking"] = True

        # 使用 dict 形式指定 interrupt_id，支持多个 pending interrupts 的场景
        command = Command(resume={effective_resume_key: resume_value})

        # 解析模型实例（与初始聊天请求一致，避免 model_resolver 创建 LazyFallbackChatModel）
        model_instance = ChatService._resolve_model_instance(data)
        tools = await chat_service._get_tools(data)
        agent, thread_config, use_checkpointer = await chat_service._create_agent_with_memory(
            data,
            prompt_mode="agent",
            model_instance=model_instance,
            tool_config=chat_service._build_tool_config(data),
            tools=tools,
        )

        if not use_checkpointer or not thread_config:
            yield sse_error_event("approval_error", "无法恢复会话状态：checkpointer 不可用")
            return

        # 校验 interrupt 归属：检查 Agent 的 checkpoint 中是否有该 interrupt_id 的 pending interrupt
        # 如果没有，说明该 interrupt 属于其他 Agent（如深度研究），不应由聊天审批 API 处理
        try:
            graph = agent.graph if hasattr(agent, "graph") else agent
            if hasattr(graph, "aget_state"):
                check_state = await graph.aget_state(thread_config)
                if check_state and hasattr(check_state, "tasks") and check_state.tasks:
                    found_interrupt = False
                    for task in check_state.tasks:
                        if hasattr(task, "interrupts") and task.interrupts:
                            for intr in task.interrupts:
                                intr_id = intr.id if hasattr(intr, "id") else ""
                                if intr_id == effective_resume_key:
                                    found_interrupt = True
                                    break
                        if found_interrupt:
                            break
                    if not found_interrupt:
                        logger.warning(
                            f"[ChatApprovalResume] 校验失败: interrupt_id={interrupt_id} "
                            f"(effective_key={effective_resume_key}) 不属于会话 {session_id} 的 Agent，"
                            f"可能属于深度研究或其他 Agent"
                        )
                        yield sse_error_event("approval_error", "该审批请求不属于当前会话，可能属于深度研究任务")
                        return
        except Exception as check_err:
            # 校验异常时也终止，不再放行 — 防止深度研究 interrupt 被错误处理
            logger.exception("[ChatApprovalResume] 归属校验异常")
            yield sse_error_event("approval_error", f"审批校验异常，可能属于深度研究任务: {check_err!s}")
            return

        # 从 checkpoint 加载历史消息，提取已有的 tool_call 信息，
        # 初始化 tool_calls_map，这样 ToolMessage 到达时能找到对应的 tool_info 并推送给前端
        try:
            graph = agent.graph if hasattr(agent, "graph") else agent
            if hasattr(graph, "aget_state"):
                history_state = await graph.aget_state(thread_config)
                if history_state and hasattr(history_state, "values"):
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
            logger.warning(f"[ChatApprovalResume] 加载历史 tool_call 失败（非致命）: {hist_err}")

        # 流式恢复 Agent 执行
        from langchain_core.messages import AIMessage as LCAIMessage

        from Django_xm.apps.chat.services.stream_helpers import process_stream_chunk

        async for chunk in agent.graph.astream(
            command,
            config=thread_config,
            stream_mode=["messages", "updates"],
        ):
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
                                        "tool_name": tool_name,
                                        "tool_call_id": new_interrupt_id,
                                        "interrupt_id": new_interrupt_id,
                                        "title": interrupt_value.get("title", "确认操作"),
                                        "description": interrupt_value.get("description", ""),
                                        "action": interrupt_value.get("action", "confirm"),
                                        "danger_level": interrupt_value.get("danger_level", "medium"),
                                        "state": "pending",
                                    }
                                    # 透传 operation（统一字段，兼容旧 command）
                                    op = interrupt_value.get("operation") or interrupt_value.get("command") or ""
                                    if op:
                                        approval_data["operation"] = op
                                        # 通用匹配：tool_name 一致 + parameters 中任意字段值等于 operation
                                        for tc_key, tc_info in tool_calls_map.items():
                                            if tc_info.get("name") != tool_name:
                                                continue
                                            tc_params = tc_info.get("parameters", {})
                                            if any(str(v) == op for v in tc_params.values()):
                                                approval_data["llm_tool_call_id"] = tc_info.get("id") or tc_key
                                                break
                                        # 回退：同名工具中第一个
                                        if "llm_tool_call_id" not in approval_data:
                                            for tc_key, tc_info in tool_calls_map.items():
                                                if tc_info.get("name") == tool_name:
                                                    approval_data["llm_tool_call_id"] = tc_info.get("id") or tc_key
                                                    break
                                    if interrupt_value.get("extra"):
                                        approval_data["extra"] = interrupt_value["extra"]
                                    if interrupt_value.get("input_placeholder"):
                                        approval_data["input_placeholder"] = interrupt_value["input_placeholder"]
                                    logger.info(
                                        f"[ChatApprovalResume] 检测到新审批: tool={tool_name}, "
                                        f"danger={approval_data['danger_level']}"
                                    )
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
            # 解析 message 对象
            msg_obj = mode_data
            if isinstance(mode_data, tuple) and len(mode_data) == 2:
                msg_obj = mode_data[0]

            # 跳过含 tool_calls 的 AIMessage 中已有的 tool_call（已在第一次请求中处理），
            # 但不跳过新增的 tool_call（审批恢复后 Agent 可能生成新的工具调用，如 fs_write_file）。
            if isinstance(msg_obj, LCAIMessage) and (
                getattr(msg_obj, "tool_calls", None) or getattr(msg_obj, "tool_call_chunks", None)
            ):
                msg_tool_calls = getattr(msg_obj, "tool_calls", None) or []
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
                    mode_data,
                    tool_calls_map,
                    current_message_content,
                    tool_call_count=tool_call_count,
                    lcp_func=lambda a, b: 0,
                    accumulated_reasoning=accumulated_reasoning,
                    tool_args_accumulator=tool_args_accumulator,
                    mode="agent",
                ):
                    if event.get("type") == "chunk":
                        current_message_content += event.get("content", "")
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except Exception as chunk_err:
                logger.warning(f"[ChatApprovalResume] 流式 chunk 处理失败: {chunk_err}")
                continue

        # 深度思考模式兜底：当模型将全部输出放入 thinking 字段而 content 为空时，
        # 将推理内容作为主内容发送
        if (
            not current_message_content.strip()
            and accumulated_reasoning
            and accumulated_reasoning.get("content", "").strip()
        ):
            reasoning_text = accumulated_reasoning["content"].strip()
            logger.info(
                f"[ChatApprovalResume] 深度思考兜底: content 为空，将推理内容 ({len(reasoning_text)} 字符) 作为主内容发送"
            )
            yield f"data: {json.dumps({'type': 'chunk', 'content': reasoning_text}, ensure_ascii=False)}\n\n"
            current_message_content = reasoning_text

        # 注意：审批恢复后的消息由前端 syncLastMessageToBackend 统一保存到数据库，
        # 后端不再重复保存，避免创建重复消息。
        # 后端只在流式完成后发送 [DONE]，前端收到后触发同步。

        yield "data: [DONE]\n\n"

    except Exception as e:
        logger.exception("[ChatApprovalResume] 审批恢复执行失败")
        yield sse_error_event("approval_error", f"审批恢复执行失败: {e!s}")
    finally:
        # 释放异步 Checkpointer 连接池，防止 PostgreSQL 连接泄漏
        try:
            await release_async_checkpointer()
        except Exception:  # noqa: S110  # cleanup, checkpointer 资源释放失败可忽略
            pass
