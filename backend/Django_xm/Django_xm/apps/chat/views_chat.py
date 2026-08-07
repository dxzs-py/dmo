"""
核心聊天、会话和消息视图

提供聊天交互、流式响应、会话管理、消息管理等接口。
"""

import logging
import time
from functools import wraps

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
from Django_xm.apps.core.throttling import ChatStreamRateThrottle, MetaRateThrottle
from Django_xm.async_utils import run_async
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.event_schema import EventSource, EventType
from Django_xm.common.realtime_events import publish_event_sync
from Django_xm.common.responses import error_response, success_response, validation_error_response
from Django_xm.common.redis_utils import get_redis_client
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


# Task 6.4：消息发送幂等（client_message_id 去重）
DEDUP_TTL_SECONDS = 60
DEDUP_KEY_PREFIX = "chat:message_dedup"


def _acquire_message_dedup_lock(user_id, client_message_id, ttl=DEDUP_TTL_SECONDS):
    """按 client_message_id 获取消息受理权（Redis SETNX + 短 TTL）。

    同一用户短时间内相同 client_message_id 的请求仅一个受理，其余视为重复。

    Args:
        user_id: 用户 ID（IsAuthenticated 保证非空）
        client_message_id: 前端生成的幂等 ID（None/空串时不做去重，向后兼容）
        ttl: 锁有效期（秒），默认 60 秒

    Returns:
        True: 本请求获得受理权（或未携带 id / Redis 不可用降级放行）
        False: 该 id 已被受理，属重复请求
    """
    if not client_message_id:
        return True
    try:
        redis_client = get_redis_client()
        key = f"{DEDUP_KEY_PREFIX}:{user_id}:{client_message_id}"
        # SETNX 原子性：并发请求同时到达时只有一个返回 True
        acquired = redis_client.set(key, "1", nx=True, ex=ttl)
        return bool(acquired)
    except Exception as e:
        # Redis 故障降级放行，避免影响正常消息发送
        logger.warning(f"消息去重锁获取失败（降级放行）: {e}")
        return True


def _safe_publish_session_created(session_id, title, mode, knowledge_bases,
                                   created_at, updated_at, user_id, session_data):
    """安全发布 SESSION_CREATED 事件（与参考项目 _safe_publish_event_sync 对齐）。

    在 transaction.on_commit 回调中调用，确保事务已提交、其他浏览器可查询到该会话。
    失败仅记日志，不抛出异常（避免 on_commit 回调异常传播）。
    """
    try:
        publish_event_sync(
            EventType.SESSION_CREATED,
            {
                "id": session_id,          # UUID（与前端 handleUserEvent 对齐）
                "session_id": session_id,
                "title": title,
                "mode": mode,
                "message_count": 0,
                "selected_knowledge_bases": knowledge_bases,
                "created_at": created_at.isoformat() if created_at else None,
                "updated_at": updated_at.isoformat() if updated_at else created_at.isoformat() if created_at else None,
                "session": session_data,
            },
            user_id=str(user_id),
        )
    except Exception as e:
        logger.warning(f"广播 SESSION_CREATED 失败: session={session_id}, error={e}")


def _safe_publish_session_deleted(session_id, user_id):
    """安全发布 SESSION_DELETED 事件（与 _safe_publish_session_created 对齐）。

    在 transaction.on_commit 回调中调用，确保事务已提交、其他浏览器可感知会话已删除。
    失败仅记日志，不抛出异常。
    """
    try:
        publish_event_sync(
            EventType.SESSION_DELETED,
            {
                "id": session_id,
                "session_id": session_id,
            },
            user_id=str(user_id),
        )
    except Exception as e:
        logger.warning(f"广播 SESSION_DELETED 失败: session={session_id}, error={e}")


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
        # 使用异步 ORM 迭代，避免 sync_to_async + CurrentThreadExecutor 嵌套提交导致的
        # "You cannot submit onto CurrentThreadExecutor from its own thread" 错误
        remaining_messages = [
            msg
            async for msg in ChatMessage.objects.filter(
                session__session_id=session_id,
                is_deleted=False,
            ).order_by("created_at")
        ]

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

        # Task 6.4：client_message_id 幂等去重（非流式入口同样生效）
        if not _acquire_message_dedup_lock(request.user.id, data.get("client_message_id")):
            logger.info(
                f"[Chat] 重复消息请求已拦截: user={request.user.id}, "
                f"client_message_id={data.get('client_message_id')}"
            )
            return error_response(
                code=ErrorCode.DUPLICATE_RESOURCE,
                message="该消息已在处理中，请勿重复发送",
                http_status=status.HTTP_409_CONFLICT,
            )

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

        # Task 6.4：client_message_id 幂等去重——必须在创建消息对/启动流之前拦截，
        # 否则并发重复请求会各创建一份 user/assistant 消息并对同一会话开双流。
        if not _acquire_message_dedup_lock(request.user.id, data.get("client_message_id")):
            logger.info(
                f"[ChatStream] 重复消息请求已拦截: user={request.user.id}, "
                f"client_message_id={data.get('client_message_id')}"
            )
            return sse_error_response(
                message="该消息已在处理中，请勿重复发送",
                status_code=status.HTTP_409_CONFLICT,
                code=str(int(ErrorCode.DUPLICATE_RESOURCE)),
            )

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

        # ── 预创建消息对 + WebSocket 广播 ──
        # 参考项目关键行为：流式输出开始前，先创建用户消息和 assistant 占位消息，
        # 并广播 MESSAGE_ADDED 事件，确保其他浏览器在 tool/approval 事件到达前已有消息可挂载。
        assistant_message_id = None
        user_message_id = None
        if session_id:
            try:
                session_obj = ChatSession.objects.get(session_id=session_id)
                _user = request.user if request.user.is_authenticated else None

                # 1. 创建用户消息
                user_msg = ChatMessage(
                    session=session_obj,
                    role=MessageRole.USER,
                    content=data.get("message", ""),
                    created_by=_user,
                    updated_by=_user,
                )
                user_msg.save()
                user_message_id = user_msg.id
                if original_attachment_ids:
                    from Django_xm.apps.attachments.services.cross_app import get_attachment_service
                    get_attachment_service().link_attachments_to_message(user_msg, original_attachment_ids)

                # 2. 清理残留的 is_streaming=True 空 assistant 消息
                ChatMessage.objects.filter(
                    session=session_obj,
                    role=MessageRole.ASSISTANT,
                    is_deleted=False,
                    is_streaming=True,
                    content="",
                ).update(is_streaming=False)

                # 3. 创建 assistant 占位消息
                ai_msg = ChatMessage(
                    session=session_obj,
                    role=MessageRole.ASSISTANT,
                    content="",
                    is_streaming=True,
                    created_by=_user,
                    updated_by=_user,
                )
                ai_msg.save()
                assistant_message_id = ai_msg.id
                logger.info(
                    f"[ChatStream] 创建流式消息对: user={user_msg.id}, assistant={ai_msg.id}"
                )

                # 将 assistant 占位消息 ID 传递给 chat_service
                data["_assistant_message_id"] = str(assistant_message_id)

                # 广播 MESSAGE_ADDED 到 WebSocket
                try:
                    from Django_xm.common.event_schema import EventSource, EventType
                    from Django_xm.common.realtime_events import publish_event_sync
                    from .serializers import ChatMessageSerializer as _MsgSerializer

                    for mid in (user_message_id, assistant_message_id):
                        msg = ChatMessage.objects.get(id=mid)
                        publish_event_sync(
                            EventType.MESSAGE_ADDED,
                            {
                                "session_id": session_id,
                                "message_id": str(mid),
                                "message": _MsgSerializer(msg).data,
                            },
                            session_id=session_id,
                        )
                except Exception as broadcast_err:
                    logger.warning(f"[ChatStream] 广播 MESSAGE_ADDED 失败: {broadcast_err}")
            except Exception as e:
                logger.warning(f"[ChatStream] 创建流式消息失败: {e}")

        # Task 23.1：generate() 闭包拆分为独立模块。
        # 使用 ASGI 原生事件循环的异步生成器（不再创建 new_event_loop）。
        # 详见 chat/services/sse_generator.py
        from .services.sse_generator import ChatStreamContext, generate_chat_stream

        ctx = ChatStreamContext(
            request=request,
            data=data,
            original_attachment_ids=original_attachment_ids,
            pending_progress=pending_progress,
            assistant_message_id=assistant_message_id,
            user_message_id=user_message_id,
        )
        return sse_response(generate_chat_stream(ctx))


class ChatModesView(BaseChatAPIView):
    # 页面加载即请求的只读接口，独立 meta 额度（Task 3.2）
    throttle_classes = [MetaRateThrottle]

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

        session_data = ChatSessionDetailSerializer(session).data

        # P16/P17/P18 修复：广播 SESSION_CREATED 事件
        # - 使用 transaction.on_commit 确保事务提交后再广播（防止其他浏览器查询时会话尚未提交）
        # - id 字段使用 session.session_id（UUID）而非 session.id（整数PK），与前端 handleUserEvent 对齐
        # - 提取局部变量避免 lambda 闭包中 ORM 对象失效
        _session_id = session.session_id
        _title = session.title
        _mode = session.mode
        _knowledge_bases = session.selected_knowledge_bases or []
        _created_at = session.created_at
        _updated_at = session.updated_at
        _user_id = request.user.id
        _session_data = session_data
        transaction.on_commit(lambda: _safe_publish_session_created(
            _session_id, _title, _mode, _knowledge_bases,
            _created_at, _updated_at, _user_id, _session_data,
        ))

        return success_response(
            data=session_data, message="会话创建成功", http_status=status.HTTP_201_CREATED
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

        # 广播 SESSION_DELETED 事件，实现跨浏览器实时同步
        # 与 _safe_publish_session_created 一致：在 transaction.on_commit 中发布，
        # 确保软删除已提交到 DB
        _sid = session.session_id
        _uid = request.user.id
        transaction.on_commit(lambda: _safe_publish_session_deleted(_sid, _uid))

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
                "original_message_count": prune_info["original_count"],
                "kept_message_count": prune_info["original_count"] - prune_info["pruned_count"],
                "pruned_count": prune_info["pruned_count"],
                "deduped_count": prune_info["deduped_count"],
                "filtered_count": prune_info["filtered_count"],
                "injection_detected": metadata["injection_detected"],
                "budget_over_sections": metadata["budget_over_sections"],
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
