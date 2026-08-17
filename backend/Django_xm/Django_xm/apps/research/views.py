import logging
import uuid

from django.db import IntegrityError, transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from Django_xm.apps.chat.services.cross_app import get_active_session_ids_for_research_task
from Django_xm.apps.core.services.file_manager import get_file_manager
from Django_xm.apps.core.throttling import ResearchRateThrottle
from Django_xm.services.fastapi_service.event_bus import (
    SESSION_TYPE_RESEARCH,
    SIGNAL_START,
    publish_retry_subagent_signal,
    publish_signal,
)
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.event_schema import EventType
from Django_xm.common.responses import error_response, not_found_response, success_response
from Django_xm.common.serializers import EmptySerializer

from .models import ResearchTask, ResearchTaskStatus
from .serializers import (
    ResearchContinueSerializer,
    ResearchRetrySubagentSerializer,
    ResearchStartSerializer,
    ResearchTaskSerializer,
)
from .services.task_manager import get_task_manager, get_task_status

logger = logging.getLogger(__name__)
task_manager = get_task_manager()
file_manager = get_file_manager()

# 子代理工具调用失败终态（lifecycle 上下文 last_event_type / 持久化 status / 审批 state 判定）
# - lifecycle 事件：tool_call_failed / tool_call_timeout / tool_call_rejected
# - ChatMessage.tool_calls status：failed / timeout / rejected
# - Approval.state：rejected / timeout（审批类失败终态）
_FAILED_TOOL_EVENTS: frozenset[str] = frozenset(
    {
        EventType.TOOL_CALL_FAILED.value,
        EventType.TOOL_CALL_TIMEOUT.value,
        EventType.TOOL_CALL_REJECTED.value,
    }
)
_FAILED_TOOL_STATUSES: frozenset[str] = frozenset({"failed", "timeout", "rejected"})
_FAILED_APPROVAL_STATES: frozenset[str] = frozenset({"rejected", "timeout"})


class DeepResearchStartView(APIView):
    throttle_classes = [ResearchRateThrottle]
    permission_classes = [IsAuthenticated]

    @extend_schema(request=ResearchStartSerializer, responses={200: EmptySerializer})
    def post(self, request):
        serializer = ResearchStartSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                code=ErrorCode.VALIDATION_FAILED,
                message="数据验证失败",
                data=serializer.errors,
                http_status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        thread_id = data.get("thread_id") or f"research_{uuid.uuid4().hex[:12]}"

        logger.info(f"收到研究请求：{data['query'][:50]}...")

        try:
            knowledge_base_ids = data.get("knowledge_base_ids", [])

            with transaction.atomic():
                if (
                    ResearchTask.objects.select_for_update()
                    .filter(task_id=thread_id, created_by=request.user, is_deleted=False)
                    .exists()
                ):
                    return error_response(
                        code=ErrorCode.DUPLICATE_RESOURCE,
                        message=f"研究任务 {thread_id} 已存在",
                        http_status=status.HTTP_400_BAD_REQUEST,
                    )

                task_manager.create_task(
                    thread_id,
                    data["query"],
                    enable_web_search=data.get("enable_web_search", True),
                    enable_doc_analysis=data.get("enable_doc_analysis", False),
                    created_by=request.user,
                )

                ResearchTask.objects.filter(task_id=thread_id).update(
                    knowledge_base_ids=knowledge_base_ids,
                    use_mcp=data.get("use_mcp", False),
                    selected_mcp_servers=data.get("selected_mcp_servers", []),
                    selected_tools=data.get("selected_tools", []),
                )

            research_depth = data.get("research_depth", "standard")
            if research_depth == "basic":
                estimated_time = "3-5 分钟"
            elif research_depth == "comprehensive":
                estimated_time = "10-15 分钟"
            else:
                estimated_time = "5-10 分钟"

            publish_signal(
                SIGNAL_START,
                thread_id,
                {
                    "thread_id": thread_id,
                    "session_type": SESSION_TYPE_RESEARCH,
                    "query": data["query"],
                    "user_id": request.user.id,
                    "session_id": None,
                    "message_id": "",
                    "publish_to_redis": False,
                    "enable_web_search": data.get("enable_web_search", True),
                    "enable_doc_analysis": data.get("enable_doc_analysis", False),
                    "knowledge_base_ids": knowledge_base_ids,
                    "use_mcp": data.get("use_mcp", False),
                    "selected_mcp_servers": data.get("selected_mcp_servers", []),
                    "selected_tools": data.get("selected_tools", []),
                    "provider_id": data.get("provider_id"),
                    "model_name": data.get("model_name"),
                    "enable_deep_thinking": data.get("enable_deep_thinking", False),
                    "temperature": data.get("temperature"),
                    "max_tokens": data.get("max_tokens"),
                    "special_params": data.get("special_params"),
                },
            )

            logger.info(f"研究任务已下发执行服务：{thread_id}")

            # 发布 task_created 实时事件，通知所有浏览器刷新深度研究任务列表
            try:
                from Django_xm.common.event_schema import EventType
                from Django_xm.common.realtime_events import publish_event_sync
                publish_event_sync(
                    EventType.TASK_CREATED,
                    {"task_id": thread_id},
                    user_id=request.user.id,
                )
            except Exception:
                logger.warning(f"发布 task_created 事件失败: task_id={thread_id}", exc_info=True)

            return success_response(
                data={
                    "task_id": thread_id,
                    "status": "pending",
                    "query": data["query"],
                    "created_at": timezone.now().isoformat(),
                    "updated_at": timezone.now().isoformat(),
                    "enable_web_search": data.get("enable_web_search", True),
                    "enable_doc_analysis": data.get("enable_doc_analysis", False),
                    "knowledge_base_ids": knowledge_base_ids,
                    "use_mcp": data.get("use_mcp", False),
                    "selected_mcp_servers": data.get("selected_mcp_servers", []),
                    "selected_tools": data.get("selected_tools", []),
                    "provider_id": data.get("provider_id"),
                    "model_name": data.get("model_name"),
                    "enable_deep_thinking": data.get("enable_deep_thinking", False),
                    "estimated_time": estimated_time,
                },
                message="研究任务已创建",
            )

        except IntegrityError:
            return error_response(
                code=ErrorCode.DUPLICATE_RESOURCE,
                message=f"研究任务 {thread_id} 已存在",
                http_status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception:
            logger.exception("启动研究任务失败：")
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message="研究任务启动失败，请稍后重试",
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DeepResearchContinueView(APIView):
    throttle_classes = [ResearchRateThrottle]
    permission_classes = [IsAuthenticated]

    @extend_schema(request=ResearchContinueSerializer, responses={200: EmptySerializer})
    def post(self, request, task_id):
        serializer = ResearchContinueSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                code=ErrorCode.VALIDATION_FAILED,
                message="数据验证失败",
                data=serializer.errors,
                http_status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data

        try:
            with transaction.atomic():
                parent_task = ResearchTask.objects.select_for_update().get(
                    task_id=task_id,
                    created_by=request.user,
                    is_deleted=False,
                )

                if parent_task.status != ResearchTaskStatus.COMPLETED:
                    return error_response(
                        code=ErrorCode.VALIDATION_FAILED,
                        message="只能继续已完成的研究任务",
                        http_status=status.HTTP_400_BAD_REQUEST,
                    )

                additional_query = data.get("additional_query", "").strip()
                # 续研查询：如果有补充说明则用补充说明，否则用增量指令
                if additional_query:
                    new_query = additional_query
                else:
                    new_query = "请在之前研究的基础上继续深入，探索未覆盖的方面，补充更多细节和证据"

                new_thread_id = f"research_{uuid.uuid4().hex[:12]}"
                new_version = parent_task.version + 1

                new_task = ResearchTask(
                    task_id=new_thread_id,
                    query=new_query,
                    enable_web_search=data.get("enable_web_search", parent_task.enable_web_search),
                    enable_doc_analysis=data.get("enable_doc_analysis", parent_task.enable_doc_analysis),
                    knowledge_base_ids=data.get("knowledge_base_ids", parent_task.knowledge_base_ids),
                    use_mcp=data.get("use_mcp", parent_task.use_mcp),
                    selected_mcp_servers=data.get("selected_mcp_servers", parent_task.selected_mcp_servers),
                    selected_tools=data.get("selected_tools", parent_task.selected_tools),
                    research_depth=parent_task.research_depth,
                    created_by=request.user,
                    parent_task=parent_task,
                    version=new_version,
                    session_id=parent_task.session_id,
                )
                new_task.save()

            publish_signal(
                SIGNAL_START,
                new_thread_id,
                {
                    "thread_id": new_thread_id,
                    "session_type": SESSION_TYPE_RESEARCH,
                    "query": new_query,
                    "user_id": request.user.id,
                    "session_id": parent_task.session_id,
                    "message_id": "",
                    "publish_to_redis": False,
                    "enable_web_search": new_task.enable_web_search,
                    "enable_doc_analysis": new_task.enable_doc_analysis,
                    "knowledge_base_ids": new_task.knowledge_base_ids,
                    "use_mcp": new_task.use_mcp,
                    "selected_mcp_servers": new_task.selected_mcp_servers,
                    "selected_tools": new_task.selected_tools,
                    "provider_id": data.get("provider_id"),
                    "model_name": data.get("model_name"),
                    "enable_deep_thinking": data.get("enable_deep_thinking", False),
                    "temperature": data.get("temperature"),
                    "max_tokens": data.get("max_tokens"),
                    "special_params": data.get("special_params"),
                    "continue_task_id": task_id,
                },
            )

            logger.info(f"续研任务已下发执行服务：{new_thread_id} (v{new_version}), 父任务: {task_id}")

            return success_response(
                data={
                    "task_id": new_thread_id,
                    "status": "pending",
                    "query": new_query,
                    "parent_task_id": task_id,
                    "version": new_version,
                    "created_at": timezone.now().isoformat(),
                },
                message=f"续研任务已创建（v{new_version}）",
            )

        except ResearchTask.DoesNotExist:
            return not_found_response(message="研究任务不存在")
        except Exception:
            logger.exception("启动续研任务失败：")
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message="续研任务启动失败，请稍后重试",
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DeepResearchStatusView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request, task_id):
        try:
            cached_status = get_task_status(task_id)

            try:
                task = ResearchTask.objects.select_related("created_by").get(
                    task_id=task_id,
                    created_by=request.user,
                    is_deleted=False,
                )

                response_data = {
                    "task_id": task.task_id,
                    "status": task.status,
                    "query": task.query,
                    "session_id": task.session_id or "",
                    "created_at": task.created_at.isoformat() if task.created_at else "",
                    "updated_at": task.updated_at.isoformat() if task.updated_at else "",
                    "enable_web_search": task.enable_web_search,
                    "enable_doc_analysis": task.enable_doc_analysis,
                    "knowledge_base_ids": task.knowledge_base_ids or [],
                    "current_step": cached_status.get("current_step", "unknown") if cached_status else task.status,
                    "final_report": task.final_report if task.status == "completed" else "",
                    "tool_calls": task.tool_calls or [],
                    "subagent_contents": task.subagent_contents or {},
                }

                if task.status == "completed" and task.final_report:
                    if cached_status:
                        result = cached_status.get("result", {})
                        response_data["plan"] = result.get("plan")
                        response_data["steps_completed"] = result.get("steps_completed")

                if task.status == "completed":
                    try:
                        from Django_xm.apps.core.services.file_manager import get_file_manager

                        fm = get_file_manager()
                        file_list = fm.list_task_files(task_id, "research")
                        response_data["files"] = [f.to_dict() for f in file_list]
                    except Exception:
                        response_data["files"] = []

                return success_response(data=response_data)

            except ResearchTask.DoesNotExist:
                return not_found_response(message="研究任务不存在")

        except Exception:
            logger.exception("查询研究状态失败：")
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message="查询研究状态失败，请稍后重试",
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DeepResearchResultView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request, task_id):
        try:
            try:
                task = ResearchTask.objects.select_related("created_by").get(
                    task_id=task_id,
                    created_by=request.user,
                    is_deleted=False,
                )

                if task.status != "completed":
                    status_msg = "研究任务尚未完成" if task.status == "running" else "研究任务失败"
                    return success_response(
                        data={
                            "status": task.status,
                            "thread_id": task.task_id,
                            "query": task.query,
                        },
                        message=status_msg,
                    )

                cached_status = get_task_status(task_id)
                result = cached_status.get("result", {}) if cached_status else {}

                return success_response(
                    data={
                        "status": "completed",
                        "task_id": task.task_id,
                        "query": task.query,
                        "report": task.final_report,
                        "plan": result.get("plan"),
                        "steps_completed": result.get("steps_completed"),
                        "created_at": task.created_at.isoformat() if task.created_at else "",
                        "updated_at": task.updated_at.isoformat() if task.updated_at else "",
                        "enable_web_search": task.enable_web_search,
                        "enable_doc_analysis": task.enable_doc_analysis,
                        "knowledge_base_ids": task.knowledge_base_ids or [],
                    }
                )

            except ResearchTask.DoesNotExist:
                return not_found_response(message="研究任务不存在")

        except Exception:
            logger.exception("获取研究结果失败：")
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message="获取研究结果失败，请稍后重试",
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DeepResearchTaskDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def delete(self, request, task_id):
        try:
            logger.info(f"删除研究任务：{task_id}")

            with transaction.atomic():
                task_obj = (
                    ResearchTask.objects.select_for_update()
                    .filter(task_id=task_id, created_by=request.user, is_deleted=False)
                    .first()
                )

                if not task_obj:
                    return not_found_response(message="研究任务不存在或无权删除")

                # 保留 ChatMessage.research_task_id 引用，不置空
                # 原因：置空后删除聊天会话时无法通过 ChatMessage 反查关联的已删除研究任务，
                # 导致磁盘文件无法清理。前端应通过 research_task_id 对应的 ResearchTask.is_deleted
                # 判断是否显示"查看研究"按钮。

                result = task_manager.delete_task(task_id, user_id=request.user.id)

                if not result:
                    return not_found_response(message="研究任务不存在或无权删除")

            # 事务提交后检查是否需要清理后端数据
            # 规则：无活跃聊天关联 → 清理后端数据；有活跃聊天关联 → 保留
            should_cleanup = True
            # 统一通过 chat cross_app 门面反查所有活跃聊天会话
            # （覆盖 session_id 和非 session_id 两种情况）
            active_session_ids = get_active_session_ids_for_research_task(task_id)
            if active_session_ids:
                should_cleanup = False
                logger.info(f"研究任务 {task_id} 仍有活跃聊天关联 {active_session_ids}，保留后端数据")

            if should_cleanup:
                self._cleanup_backend_data(task_id, task_obj.created_by_id)

            # 事务提交后发布 task_deleted 实时事件，通知所有浏览器刷新深度研究任务列表
            # （与 task_created 对称，实现删除的跨浏览器实时同步）
            transaction.on_commit(
                lambda: self._publish_task_deleted(task_id, request.user.id)
            )

            return success_response(
                data={
                    "status": "success",
                    "message": f"研究任务 {task_id} 已删除",
                    "backend_cleaned": should_cleanup,
                },
                message="删除成功",
            )

        except Exception:
            logger.exception("删除研究任务失败：")
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message="删除研究任务失败，请稍后重试",
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _cleanup_backend_data(self, task_id: str, user_id: int):
        """清理后端数据：checkpoint、store、磁盘文件（复用 cross_app 统一清理函数）"""
        from Django_xm.apps.research.services.cross_app import _cleanup_research_backend_data

        _cleanup_research_backend_data(task_id, user_id)

        # D7.4：研究任务删除时上层递归回收子代理（父线程 = task_id，仅终止非终态实例）
        try:
            from Django_xm.async_utils import run_async

            from Django_xm.apps.ai_engine.models import SubAgentStatus
            from Django_xm.apps.ai_engine.subagent_runtime import get_subagent_runtime

            async def _terminate():
                runtime = get_subagent_runtime()
                instances = await runtime.list_instances(task_id)
                for inst in instances:
                    if inst.status not in (
                        SubAgentStatus.RUNNING,
                        SubAgentStatus.INTERRUPTED_PENDING_USER_INPUT,
                    ):
                        continue
                    try:
                        await runtime.terminate(inst.thread_id)
                    except Exception:
                        logger.warning(
                            f"终止子代理失败（非致命）: thread={inst.thread_id}, task={task_id}"
                        )

            run_async(_terminate())
        except Exception:
            logger.warning(f"回收子代理失败: task={task_id}", exc_info=True)

    @staticmethod
    def _publish_task_deleted(task_id: str, user_id: int):
        """发布 task_deleted 实时事件（与 task_created 对称），通知所有浏览器刷新任务列表"""
        try:
            from Django_xm.common.event_schema import EventType
            from Django_xm.common.realtime_events import publish_event_sync

            publish_event_sync(
                EventType.TASK_DELETED,
                {"task_id": task_id},
                user_id=user_id,
            )
        except Exception:
            logger.warning(f"发布 task_deleted 事件失败: task_id={task_id}", exc_info=True)


class DeepResearchTaskListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request):
        try:
            status_filter = request.query_params.get("status")
            search_query = request.query_params.get("search")
            page = int(request.query_params.get("page", 1))
            page_size = min(int(request.query_params.get("page_size", 20)), 100)

            queryset = ResearchTask.objects.filter(
                created_by=request.user,
                is_deleted=False,
            ).select_related("created_by")

            if status_filter:
                queryset = queryset.filter(status=status_filter)

            if search_query:
                queryset = queryset.filter(query__icontains=search_query)

            queryset = queryset.order_by("-created_at")

            total = queryset.count()
            start = (page - 1) * page_size
            end = start + page_size
            tasks = queryset[start:end]

            serializer = ResearchTaskSerializer(tasks, many=True)

            return success_response(
                data={
                    "items": serializer.data,
                    "total": total,
                    "page": page,
                    "page_size": page_size,
                    "total_pages": (total + page_size - 1) // page_size,
                }
            )

        except Exception:
            logger.exception("获取研究任务列表失败：")
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message="获取研究任务列表失败，请稍后重试",
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DeepResearchRetrySubagentView(APIView):
    """单独重启失败子代理（Task 3）。

    校验任务归属、目标子代理存在且处于失败状态后，发布 Redis 信令
    （SIGNAL_RETRY_SUBAGENT），由执行服务向会话注入重试指令：
    - 会话运行中：直接注入（adapter astream 循环按 chunk 消费并注入 graph state）
    - 会话已结束/服务重启恢复态：从 checkpoint 恢复会话后注入

    POST /api/v1/research/task/<task_id>/retry-subagent/
    Body: {"agent_path": ["main", "web-researcher"], "tool_call_id": "call_xxx"}
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(request=ResearchRetrySubagentSerializer, responses={200: EmptySerializer})
    def post(self, request, task_id):
        serializer = ResearchRetrySubagentSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                code=ErrorCode.VALIDATION_FAILED,
                message="数据验证失败",
                data=serializer.errors,
                http_status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        agent_path = data["agent_path"]
        tool_call_id = data["tool_call_id"]

        try:
            task = ResearchTask.objects.get(
                task_id=task_id,
                created_by=request.user,
                is_deleted=False,
            )
        except ResearchTask.DoesNotExist:
            return not_found_response(message="研究任务不存在或无权访问")

        if task.status == ResearchTaskStatus.COMPLETED:
            return error_response(
                code=ErrorCode.VALIDATION_FAILED,
                message="研究任务已完成，无法重试失败子代理",
                http_status=status.HTTP_400_BAD_REQUEST,
            )

        subagent_info = self._validate_failed_subagent(task, agent_path, tool_call_id)
        if subagent_info is None:
            return error_response(
                code=ErrorCode.VALIDATION_FAILED,
                message="目标子代理不存在或未处于失败状态，无法重试",
                data={"agent_path": agent_path, "tool_call_id": tool_call_id},
                http_status=status.HTTP_400_BAD_REQUEST,
            )

        agent_name = agent_path[-1] if agent_path else ""
        publish_retry_subagent_signal(
            task_id,
            agent_path=agent_path,
            tool_call_id=tool_call_id,
            agent_name=agent_name,
            original_args=subagent_info.get("original_args") or {},
            user_id=request.user.id,
            chat_session_id=task.session_id,
        )

        return success_response(
            data={
                "task_id": task_id,
                "agent_path": agent_path,
                "tool_call_id": tool_call_id,
                "status": "retry_scheduled",
            },
            message="已提交子代理重试指令",
        )

    @classmethod
    def _validate_failed_subagent(cls, task, agent_path: list[str], tool_call_id: str) -> dict | None:
        """校验目标子代理确实存在且处于失败状态，并尽量恢复原始委派入参。

        数据源优先级（失败状态 + agent_path 归属判定）：
        1. tool_call_lifecycle Redis 上下文（实时权威，24h TTL）：
           module_id == task_id、agent_path 一致、last_event_type 为失败终态
        2. Approval 记录（持久化，审批链路）：extra.tool_call_id 匹配、
           extra.agent_path 一致、state 为拒绝/超时
        3. ChatMessage.tool_calls（聊天关联场景持久化）：条目 id/tool_call_id
           匹配、status 为失败终态

        Args:
            task: ResearchTask 实例
            agent_path: 目标子代理完整调用链路
            tool_call_id: 失败子代理工具调用 ID

        Returns:
            dict | None：{original_args, failed_tool_name}；未找到失败子代理时返回 None
        """
        from django.db.models import Q

        from Django_xm.apps.approvals.models import Approval
        from Django_xm.common.tool_call_lifecycle import service as lifecycle_service

        # 1. tool_call_lifecycle 上下文（实时权威）
        ctx = lifecycle_service.get_context(tool_call_id)
        if ctx and str(ctx.get("module_id", "")) == task.task_id:
            ctx_path = ctx.get("agent_path") or []
            if cls._normalize_path(ctx_path) == agent_path and ctx.get("last_event_type") in _FAILED_TOOL_EVENTS:
                original_args = cls._recover_subagent_original_args(ctx, lifecycle_service)
                return {
                    "original_args": original_args,
                    "failed_tool_name": str(ctx.get("tool_name") or ""),
                }
            # 上下文存在但 agent_path / 状态不匹配：目标子代理并非失败态，直接拒绝
            return None

        # 2. Approval 记录（持久化，审批链路）
        approval = (
            Approval.objects.filter(
                source=Approval.SOURCE_DEEP_RESEARCH,
                source_id=task.task_id,
            )
            .filter(Q(extra__tool_call_id=tool_call_id) | Q(interrupt_id=tool_call_id))
            .order_by("-created_at")
            .first()
        )
        if approval is not None:
            extra = approval.extra if isinstance(approval.extra, dict) else {}
            if cls._normalize_path(extra.get("agent_path")) == agent_path and approval.state in _FAILED_APPROVAL_STATES:
                return {
                    "original_args": approval.parameters if isinstance(approval.parameters, dict) else {},
                    "failed_tool_name": approval.tool_name or "",
                }
            return None

        # 3. ChatMessage.tool_calls（聊天关联场景持久化）
        if task.session_id:
            from Django_xm.apps.chat.models import ChatMessage

            chat_msg = (
                ChatMessage.objects.filter(
                    research_task_id=task.task_id,
                    role="assistant",
                    is_deleted=False,
                )
                .order_by("-created_at")
                .first()
            )
            if chat_msg is not None:
                for tc in chat_msg.tool_calls or []:
                    if not isinstance(tc, dict):
                        continue
                    tc_id = tc.get("tool_call_id") or tc.get("id")
                    if str(tc_id) != tool_call_id:
                        continue
                    approval_info = tc.get("approval") if isinstance(tc.get("approval"), dict) else {}
                    # 条目携带 approval.agent_path 时校验归属（旧数据无此字段则跳过）
                    entry_path = cls._normalize_path(approval_info.get("agent_path"))
                    if entry_path and entry_path != agent_path:
                        return None
                    if str(tc.get("status") or "") in _FAILED_TOOL_STATUSES:
                        return {
                            "original_args": tc.get("parameters")
                            if isinstance(tc.get("parameters"), dict)
                            else (tc.get("args") if isinstance(tc.get("args"), dict) else {}),
                            "failed_tool_name": str(tc.get("name") or ""),
                        }
                    return None
        return None

    @staticmethod
    def _normalize_path(value) -> list[str]:
        """将 agent_path 值规范化为字符串列表（None/非 list 返回空列表）。"""
        if not isinstance(value, (list, tuple)):
            return []
        return [str(item) for item in value]

    @staticmethod
    def _recover_subagent_original_args(ctx: dict, lifecycle_service) -> dict:
        """恢复子代理原始委派入参（task 工具参数）。

        优先取父级 task 工具调用（子代理内工具事件的 parent_tool_call_id）
        的 parameters（即主 agent 委派子代理时的原始参数）；父级上下文
        缺失时回退到失败工具调用自身的 parameters。
        """
        parent_id = ctx.get("parent_tool_call_id") or ""
        if parent_id:
            parent_ctx = lifecycle_service.get_context(parent_id)
            parent_params = parent_ctx.get("parameters") if parent_ctx else None
            if isinstance(parent_params, dict) and parent_params:
                return parent_params
        params = ctx.get("parameters")
        return params if isinstance(params, dict) else {}
