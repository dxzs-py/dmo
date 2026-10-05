import logging
import uuid

from django.db import IntegrityError, transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from Django_xm.apps.chat.services.cross_app import get_active_session_ids_for_research_task
from Django_xm.apps.core.services.file_manager import get_file_manager
from Django_xm.apps.core.throttling import ResearchRateThrottle
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.exceptions import BaseAppError
from Django_xm.common.responses import success_response
from Django_xm.common.pagination import paginate_to_dict
from Django_xm.common.serializers import EmptySerializer
from Django_xm.common.signal_bus import (
    SESSION_TYPE_RESEARCH,
    SIGNAL_START,
    SIGNAL_STOP,
    publish_signal,
)

from .models import ResearchTask, ResearchTaskStatus
from .serializers import (
    ResearchContinueSerializer,
    ResearchStartSerializer,
    ResearchTaskSerializer,
)
from .services.task_manager import CANCELLABLE_STATUSES, get_task_manager, get_task_status

logger = logging.getLogger(__name__)
task_manager = get_task_manager()
file_manager = get_file_manager()


class DeepResearchStartView(APIView):
    throttle_classes = [ResearchRateThrottle]
    permission_classes = [IsAuthenticated]

    @extend_schema(request=ResearchStartSerializer, responses={200: EmptySerializer})
    def post(self, request):
        serializer = ResearchStartSerializer(data=request.data)
        if not serializer.is_valid():
            raise BaseAppError(
                "数据验证失败",
                business_code=ErrorCode.VALIDATION_FAILED,
                data=serializer.errors,
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
                    # http_status 400->409：原视图显式传 400，单轨制后由 DUPLICATE_RESOURCE 枚举决定（409）
                    raise BaseAppError(
                        f"研究任务 {thread_id} 已存在",
                        business_code=ErrorCode.DUPLICATE_RESOURCE,
                    )

                task_manager.create_task(
                    thread_id,
                    data["query"],
                    enable_web_search=data.get("enable_web_search", True),
                    enable_doc_analysis=data.get("enable_doc_analysis", False),
                    enable_sandbox=data.get("enable_sandbox", False),
                    created_by=request.user,
                )

                ResearchTask.objects.filter(task_id=thread_id).update(
                    knowledge_base_ids=knowledge_base_ids,
                    use_mcp=data.get("use_mcp", False),
                    selected_mcp_servers=data.get("selected_mcp_servers", []),
                    selected_tools=data.get("selected_tools", []),
                    enable_sandbox=data.get("enable_sandbox", False),
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
                    "enable_web_search": data.get("enable_web_search", True),
                    "enable_doc_analysis": data.get("enable_doc_analysis", False),
                    "enable_sandbox": data.get("enable_sandbox", False),
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
            # http_status 400->409：原视图显式传 400，单轨制后由 DUPLICATE_RESOURCE 枚举决定（409）
            raise BaseAppError(
                f"研究任务 {thread_id} 已存在",
                business_code=ErrorCode.DUPLICATE_RESOURCE,
            ) from None


class DeepResearchContinueView(APIView):
    throttle_classes = [ResearchRateThrottle]
    permission_classes = [IsAuthenticated]

    @extend_schema(request=ResearchContinueSerializer, responses={200: EmptySerializer})
    def post(self, request, task_id):
        serializer = ResearchContinueSerializer(data=request.data)
        if not serializer.is_valid():
            raise BaseAppError(
                "数据验证失败",
                business_code=ErrorCode.VALIDATION_FAILED,
                data=serializer.errors,
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
                    raise BaseAppError(
                        "只能继续已完成的研究任务",
                        business_code=ErrorCode.VALIDATION_FAILED,
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
                    enable_sandbox=data.get("enable_sandbox", parent_task.enable_sandbox),
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
                    "enable_web_search": new_task.enable_web_search,
                    "enable_doc_analysis": new_task.enable_doc_analysis,
                    "enable_sandbox": new_task.enable_sandbox,
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
            raise NotFound("研究任务不存在") from None


class DeepResearchStatusView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request, task_id):
        cached_status = get_task_status(task_id)

        try:
            task = ResearchTask.objects.select_related("created_by").get(
                task_id=task_id,
                created_by=request.user,
                is_deleted=False,
            )
        except ResearchTask.DoesNotExist:
            raise NotFound("研究任务不存在") from None

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
            "content": task.content or "",
        }

        if task.status == "completed" and task.final_report:
            if cached_status:
                result = cached_status.get("result", {})
                response_data["plan"] = result.get("plan")
                response_data["steps_completed"] = result.get("steps_completed")

        if task.status == "completed":
            # files 拉取失败降级为空列表（非致命容错，保留）
            try:
                from Django_xm.apps.core.services.file_manager import get_file_manager

                fm = get_file_manager()
                file_list = fm.list_task_files(task_id, "research")
                response_data["files"] = [f.to_dict() for f in file_list]
            except Exception:
                response_data["files"] = []

        return success_response(data=response_data)


class DeepResearchTaskCancelView(APIView):
    """取消研究任务（C1/P2-1）：发布 SIGNAL_STOP 信令，执行服务负责终态化与级联清理。

    职责边界：
    - 仅校验归属（created_by）与状态可取消（pending/running/awaiting_approval），
      随后发布 SIGNAL_STOP（session_type=research）并立即返回 200；
    - **不落任务状态**：终态化（CANCELLED + 状态广播）由执行服务完成——端点直写
      会与执行器的条件终态化形成双写竞争（如取消瞬间任务恰好完成/失败）；
    - 信令丢失兜底：会话无内存执行器时由 SessionManager.stop_session 以
      cancel_task_if_active 条件终态化（DB 层消除竞争）。
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request, task_id):
        with transaction.atomic():
            task = (
                ResearchTask.objects.select_for_update()
                .filter(task_id=task_id, created_by=request.user, is_deleted=False)
                .first()
            )
            if not task:
                raise NotFound("研究任务不存在或无权操作")
            if task.status not in CANCELLABLE_STATUSES:
                raise BaseAppError(
                    f"任务当前状态为「{task.get_status_display()}」，不可取消",
                    business_code=ErrorCode.TASK_NOT_CANCELLABLE,
                )
            publish_signal(
                SIGNAL_STOP,
                task.task_id,
                {"session_type": SESSION_TYPE_RESEARCH},
            )

        logger.info(f"研究任务取消请求已下发执行服务：{task_id} (status={task.status})")
        return success_response(
            data={
                "task_id": task.task_id,
                "status": task.status,
                "cancel_requested": True,
            },
            message="取消请求已发送",
        )


class DeepResearchTaskDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def delete(self, request, task_id):
        logger.info(f"删除研究任务：{task_id}")

        with transaction.atomic():
            task_obj = (
                ResearchTask.objects.select_for_update()
                .filter(task_id=task_id, created_by=request.user, is_deleted=False)
                .first()
            )

            if not task_obj:
                raise NotFound("研究任务不存在或无权删除")

            # 保留 ChatMessage.research_task_id 引用，不置空
            # 原因：置空后删除聊天会话时无法通过 ChatMessage 反查关联的已删除研究任务，
            # 导致磁盘文件无法清理。前端应通过 research_task_id 对应的 ResearchTask.is_deleted
            # 判断是否显示"查看研究"按钮。

            result = task_manager.delete_task(task_id, user_id=request.user.id)

            if not result:
                # delete_task 返回 False 的路径（任务不存在/归属不符）均未产生 DB 写操作，
                # raise 触发的事务回滚与原 return 行为等价
                raise NotFound("研究任务不存在或无权删除")

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

    def _cleanup_backend_data(self, task_id: str, user_id: int):
        """清理后端数据：checkpoint、store、磁盘文件（复用 cross_app 统一清理函数）"""
        from Django_xm.apps.research.services.cross_app import _cleanup_research_backend_data

        _cleanup_research_backend_data(task_id, user_id)

        # D7.4：研究任务删除时上层递归回收子代理（父线程 = task_id，仅终止非终态实例）
        try:
            from Django_xm.apps.ai_engine.models import SubAgentStatus
            from Django_xm.apps.ai_engine.subagent_runtime import get_subagent_runtime
            from Django_xm.async_utils import run_async

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
        status_filter = request.query_params.get("status")
        search_query = request.query_params.get("search")

        queryset = ResearchTask.objects.filter(
            created_by=request.user,
            is_deleted=False,
        ).select_related("created_by")

        if status_filter:
            queryset = queryset.filter(status=status_filter)

        if search_query:
            queryset = queryset.filter(query__icontains=search_query)

        queryset = queryset.order_by("-created_at")

        paginated = paginate_to_dict(queryset, request)
        serializer = ResearchTaskSerializer(paginated["items"], many=True)
        return success_response(data={**paginated, "items": serializer.data})

