"""
工作流API视图
使用类视图和服务层实现，遵循项目统一的响应格式规范
"""

import asyncio
import json
import threading
import uuid
from urllib.parse import quote

from asgiref.sync import sync_to_async
from django.db.models import Q
from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from Django_xm.apps.ai_engine.services.token_counter import TokenUsageCallbackHandler
from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.apps.core.permissions import IsAuthenticatedOrQueryParam
from Django_xm.apps.core.services.file_manager import get_file_manager
from Django_xm.apps.core.throttling import LearningRateThrottle
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.event_schema import EventSource, EventType
from Django_xm.common.exceptions import BaseAppError
from Django_xm.common.realtime_events import publish_event_sync
from Django_xm.common.responses import success_response
from Django_xm.common.serializers import EmptySerializer
from Django_xm.common.sse_utils import SSERenderer, sse_error_event, sse_response

from .models import WorkflowAttempt, WorkflowQuestion, WorkflowQuestionStatus, WorkflowQuestionType, WorkflowSession
from .serializers import (
    WorkflowAttemptSerializer,
    WorkflowQuestionSerializer,
    WorkflowQuestionUpdateSerializer,
    WorkflowResponseSerializer,
    WorkflowSessionSerializer,
    WorkflowStartSerializer,
    WorkflowSubmitSerializer,
)
from .services import WorkflowService
from .services.learning_stream import iter_workflow_events, pump_sync_events, safe_publish_workflow_event
from .services.study_flow import (
    WorkflowAlreadyFinishedError,
    _ensure_session_created,
    _get_cached_study_flow,
    _get_study_flow,
    get_workflow_state,
    prepare_restart,
)


logger = get_logger(__name__)
file_manager = get_file_manager()


def _safe_publish_workflow_event(
    event_type: EventType,
    thread_id: str,
    data: dict,
    user_id=None,
) -> None:
    """安全发布工作流事件到 task 频道，吞掉异常以避免影响 SSE 主流程。

    同步版本：供工作流线程泵的 publish 回调使用（``iter_workflow_events`` 的
    publish 在专用工作线程内同步调用，不能 await 异步 publish_event）。
    """
    try:
        payload = {
            "source": EventSource.LEARNING.value,
            "source_id": thread_id,
            "thread_id": thread_id,
            **data,
        }
        publish_event_sync(
            event_type,
            payload,
            task_id=thread_id,
            user_id=str(user_id) if user_id is not None else None,
        )
    except Exception as e:
        logger.warning(f"[API] publish_event_sync 失败: {event_type}, {e}")


class _WorkflowJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        from langchain_core.messages import BaseMessage

        if isinstance(obj, BaseMessage):
            return {"type": obj.type, "content": obj.content}
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
        if hasattr(obj, "dict"):
            return obj.dict()
        if hasattr(obj, "__dict__"):
            return str(obj)
        return super().default(obj)


class WorkflowStartView(APIView):
    """启动学习工作流视图"""

    permission_classes = [IsAuthenticated]
    throttle_classes = [LearningRateThrottle]

    @extend_schema(request=WorkflowStartSerializer, responses={200: WorkflowResponseSerializer})
    def post(self, request):
        """
        启动学习工作流

        工作流会自动执行以下步骤：
        1. 分析用户问题，生成学习计划
        2. 检索相关文档
        3. 生成练习题
        4. 暂停，等待用户提交答案
        """
        serializer = WorkflowStartSerializer(data=request.data)
        if not serializer.is_valid():
            raise ValidationError(serializer.errors)

        user_question = serializer.validated_data.get("user_question") or serializer.validated_data.get("query")
        result = WorkflowService.start_workflow(
            user_question=user_question,
            thread_id=serializer.validated_data.get("thread_id"),
            user_id=request.user.id,
            knowledge_base_ids=serializer.validated_data.get("knowledge_base_ids"),
            provider_id=serializer.validated_data.get("provider_id"),
            model_name=serializer.validated_data.get("model_name"),
            temperature=serializer.validated_data.get("temperature"),
            max_tokens=serializer.validated_data.get("max_tokens"),
            special_params=serializer.validated_data.get("special_params"),
            enable_deep_thinking=serializer.validated_data.get("use_deep_thinking", False),
            use_web_search=serializer.validated_data.get("use_web_search", False),
        )

        try:
            from .services.persistence_service import get_persistence_service

            persistence_service = get_persistence_service()
            persistence_service.save_workflow_state(
                thread_id=result["thread_id"], state=result, user_id=request.user.id
            )
        except Exception as persist_err:
            logger.warning(f"持久化工作流会话失败: {persist_err}")

        response_serializer = WorkflowResponseSerializer(result)
        return success_response(data=response_serializer.data, message="操作成功")


class WorkflowStartStreamView(APIView):
    """启动学习工作流（SSE流式输出）"""

    permission_classes = [IsAuthenticated]
    throttle_classes = [LearningRateThrottle]
    renderer_classes = [SSERenderer]

    def options(self, request):
        response = HttpResponse(status=200)
        origin = request.headers.get("Origin", "*")
        response["Access-Control-Allow-Origin"] = origin
        response["Access-Control-Allow-Credentials"] = "true"
        response["Access-Control-Allow-Headers"] = "authorization,content-type,x-csrftoken,x-requested-with"
        response["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
        response["Access-Control-Max-Age"] = "86400"
        return response

    @extend_schema(request=WorkflowStartSerializer, responses={200: EmptySerializer})
    def post(self, request):
        """
        启动工作流并以SSE流式返回执行进度

        SSE事件类型（统一为 workflow_* 格式，与 EventType 枚举对齐）：
        - workflow_step: 工作流步骤更新（含 step / message）
        - workflow_state_update: 状态更新（含完整数据，如 learning_plan / quiz 等）
        - workflow_completed: 工作流完成
        - workflow_failed: 工作流失败
        - error: 旧版兼容错误事件
        """
        serializer = WorkflowStartSerializer(data=request.data)
        if not serializer.is_valid():
            raise ValidationError(serializer.errors)

        user_question = serializer.validated_data.get("user_question") or serializer.validated_data.get("query")
        thread_id = serializer.validated_data.get("thread_id") or f"study_{uuid.uuid4().hex[:12]}"
        user_id = request.user.id
        knowledge_base_ids = serializer.validated_data.get("knowledge_base_ids") or []

        logger.info(f"[API] 流式启动工作流，thread_id={thread_id}")

        # 预创建 WorkflowSession（幂等）：必须在 SSE 流开始前完成，否则前端收到
        # start 事件后立即加载 questions/attempts 时会因 session 尚不存在而 404
        # （"加载练习历史失败"竞态）。quiz_generator_node._persist_questions 也依赖
        # 该记录定位 session，题目才能落库（练习历史/修改答案随之失效）。
        # post 为同步方法，直接调用同步 ORM 函数（无 async 生成器限制）。
        _ensure_session_created(
            thread_id=thread_id,
            user_question=user_question,
            user_id=user_id,
            knowledge_base_ids=knowledge_base_ids,
            provider_id=serializer.validated_data.get("provider_id"),
            model_name=serializer.validated_data.get("model_name"),
            temperature=serializer.validated_data.get("temperature"),
            max_tokens=serializer.validated_data.get("max_tokens"),
            special_params=serializer.validated_data.get("special_params"),
            enable_deep_thinking=serializer.validated_data.get("use_deep_thinking", False),
            use_web_search=serializer.validated_data.get("use_web_search", False),
        )

        async def event_stream():
            try:
                # 工作流开始事件（统一格式 + 同步推送 WS）
                start_event = {
                    "type": "workflow_step",
                    "data": {"step": "start", "message": "工作流启动中...", "thread_id": thread_id},
                }
                yield f"data: {json.dumps(start_event, ensure_ascii=False)}\n\n"
                await safe_publish_workflow_event(
                    EventType.WORKFLOW_STEP,
                    thread_id,
                    {"step": "start", "message": "工作流启动中..."},
                    user_id=user_id,
                )

                # 预创建 WorkflowSession 已在 post 中流开始前完成（避免前端加载
                # questions/attempts 的 404 竞态），此处直接获取缓存 StudyFlow
                study_flow = await sync_to_async(_get_study_flow)(thread_id)

                initial_state = {
                    "messages": [],
                    "user_question": user_question,
                    "user_id": user_id,
                    "knowledge_base_ids": list(knowledge_base_ids),
                    "learning_plan": None,
                    "retrieved_docs": None,
                    "quiz": None,
                    "user_answers": None,
                    "score": None,
                    "score_details": None,
                    "feedback": None,
                    "retry_count": 0,
                    "should_retry": False,
                    "current_step": "start",
                    "thread_id": thread_id,
                    "provider_id": serializer.validated_data.get("provider_id"),
                    "model_name": serializer.validated_data.get("model_name"),
                    "temperature": serializer.validated_data.get("temperature"),
                    "max_tokens": serializer.validated_data.get("max_tokens"),
                    "special_params": serializer.validated_data.get("special_params") or {},
                    "enable_deep_thinking": serializer.validated_data.get("use_deep_thinking", False),
                    "use_web_search": serializer.validated_data.get("use_web_search", False),
                    "created_at": timezone.now().isoformat(),
                    "updated_at": timezone.now().isoformat(),
                    "error": None,
                    "error_node": None,
                }

                config = {"configurable": {"thread_id": thread_id}}

                # planner 步骤事件
                planner_event = {"type": "workflow_step", "data": {"step": "planner", "message": "正在生成学习计划..."}}
                yield f"data: {json.dumps(planner_event, ensure_ascii=False)}\n\n"
                await safe_publish_workflow_event(
                    EventType.WORKFLOW_STEP,
                    thread_id,
                    {"step": "planner", "message": "正在生成学习计划..."},
                    user_id=user_id,
                )

                # 遍历图执行事件流：同步生成器在专用线程执行，事件经 call_soon_threadsafe
                # 泵入队列（PostgresSaver 持久化 + 事件循环不阻塞，见 pump_sync_events 注释）
                loop = asyncio.get_running_loop()
                queue = asyncio.Queue()
                worker = threading.Thread(
                    target=pump_sync_events,
                    args=(
                        iter_workflow_events(
                            study_flow,
                            initial_state,
                            config,
                            user_id=user_id,
                            thread_id=thread_id,
                            publish=lambda et, data: _safe_publish_workflow_event(et, thread_id, data, user_id=user_id),
                        ),
                        loop,
                        queue,
                    ),
                    daemon=True,
                    name=f"study-flow-{thread_id[-8:]}",
                )
                worker.start()

                interrupted_at_waiting = False
                while True:
                    evt = await queue.get()
                    if evt is None:
                        break
                    if evt["type"] == "interrupted":
                        # 等待答题是中断而非完成：不发 workflow_completed，避免前端误标 completed
                        interrupted_at_waiting = True
                        break
                    if evt["type"] == "error":
                        error_msg = evt["data"].get("message", "工作流执行失败")
                        yield sse_error_event(code="50001", message=error_msg)
                        interrupted_at_waiting = True
                        break
                    yield f"data: {json.dumps(evt, ensure_ascii=False, cls=_WorkflowJSONEncoder)}\n\n"

                # 工作流完成事件（仅在真正完成时发出）
                if not interrupted_at_waiting:
                    complete_evt = {"type": "workflow_completed", "data": {"thread_id": thread_id}}
                    yield f"data: {json.dumps(complete_evt, ensure_ascii=False)}\n\n"
                    await safe_publish_workflow_event(
                        EventType.WORKFLOW_COMPLETED,
                        thread_id,
                        {"step": "completed"},
                        user_id=user_id,
                    )

                try:
                    from .services.persistence_service import get_persistence_service

                    persistence_service = get_persistence_service()
                    state_values = await sync_to_async(lambda: study_flow.graph.get_state(config).values)()
                    await sync_to_async(persistence_service.save_workflow_state)(
                        thread_id=thread_id, state=state_values, user_id=user_id
                    )
                except Exception as persist_err:
                    logger.warning(f"持久化工作流会话失败: {persist_err}")

            except Exception:
                logger.exception("[API] 流式工作流执行失败：")
                # 工作流失败事件
                await safe_publish_workflow_event(
                    EventType.WORKFLOW_FAILED,
                    thread_id,
                    {"step": "planner", "error": "工作流执行失败"},
                    user_id=user_id,
                )
                yield sse_error_event(code="50001", message="工作流执行失败，请稍后重试")

        return sse_response(event_stream())


class WorkflowSubmitView(APIView):
    """提交用户答案视图"""

    permission_classes = [IsAuthenticated]
    throttle_classes = [LearningRateThrottle]

    @extend_schema(request=WorkflowSubmitSerializer, responses={200: EmptySerializer})
    def post(self, request):
        """
        提交用户答案，继续执行工作流
        """
        serializer = WorkflowSubmitSerializer(data=request.data)
        if not serializer.is_valid():
            raise ValidationError(serializer.errors)

        try:
            thread_id = serializer.validated_data["thread_id"]
            session = WorkflowSession.objects.filter(
                thread_id=thread_id, created_by=request.user, is_deleted=False
            ).first()
            if not session:
                from .services.persistence_service import get_persistence_service

                persistence_service = get_persistence_service()
                saved_state = persistence_service.load_workflow_state(thread_id)
                if saved_state:
                    persistence_service.save_workflow_state(
                        thread_id=thread_id, state=saved_state, user_id=request.user.id
                    )
                    logger.info(f"[API] 从持久化恢复工作流会话: {thread_id}")
                else:
                    raise NotFound("工作流会话不存在或无权访问")

            result = WorkflowService.submit_user_answers(
                thread_id=thread_id, answers=serializer.validated_data["answers"], user_id=request.user.id
            )

            try:
                from .services.persistence_service import get_persistence_service

                persistence_service = get_persistence_service()
                persistence_service.save_workflow_state(thread_id=thread_id, state=result, user_id=request.user.id)
            except Exception as persist_err:
                logger.warning(f"持久化工作流会话失败: {persist_err}")

            return success_response(data=result, message="操作成功")

        except WorkflowAlreadyFinishedError as e:
            # 工作流已结束（已完成/失败/错误态）：返回 409 + 当前阶段
            raise BaseAppError(
                str(e),
                business_code=ErrorCode.WORKFLOW_ALREADY_FINISHED,
                data={"current_phase": getattr(e, "current_phase", "")},
            ) from e


class WorkflowRestartStreamView(APIView):
    """继续练习（流式）视图

    快速创建新线程（不执行 LLM），SSE 逐步生成新一轮练习题，
    与 start/stream 一致的流式体验：步骤条实时推进、不阻塞浏览器。
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [LearningRateThrottle]
    renderer_classes = [SSERenderer]

    @extend_schema(request=None, responses={200: EmptySerializer})
    def post(self, request, thread_id):
        session = WorkflowSession.objects.filter(
            thread_id=thread_id, created_by=request.user, is_deleted=False
        ).first()
        if not session:
            raise NotFound("工作流会话不存在或无权访问")

        try:
            prepared = prepare_restart(thread_id=thread_id, user_id=request.user.id)
        except ValueError as e:
            raise ValidationError(str(e)) from e

        new_thread_id = prepared["new_thread_id"]
        user_id = request.user.id

        async def event_stream():
            try:
                # 新一轮启动事件（携带新线程 ID，前端据此回填 execution.threadId）
                start_evt = {
                    "type": "workflow_step",
                    "data": {"step": "start", "message": "新一轮练习启动中...", "thread_id": new_thread_id},
                }
                yield f"data: {json.dumps(start_evt, ensure_ascii=False)}\n\n"
                await safe_publish_workflow_event(
                    EventType.WORKFLOW_STEP,
                    new_thread_id,
                    {"step": "start", "message": "新一轮练习启动中..."},
                    user_id=user_id,
                )

                # 规划阶段提示（复用已有学习计划，planner 节点会跳过生成）
                planner_evt = {
                    "type": "workflow_step",
                    "data": {"step": "planner", "message": "正在准备学习内容..."},
                }
                yield f"data: {json.dumps(planner_evt, ensure_ascii=False)}\n\n"
                await safe_publish_workflow_event(
                    EventType.WORKFLOW_STEP,
                    new_thread_id,
                    {"step": "planner", "message": "正在准备学习内容..."},
                    user_id=user_id,
                )

                # async 生成器内避免同步阻塞：_get_study_flow 经 sync_to_async 入线程池
                study_flow = await sync_to_async(_get_study_flow)(new_thread_id)
                config = {
                    "configurable": {"thread_id": new_thread_id},
                    "callbacks": [TokenUsageCallbackHandler()],
                }

                # 遍历图执行事件流：同步生成器在专用线程执行，事件经 call_soon_threadsafe
                # 泵入队列（PostgresSaver 持久化 + 事件循环不阻塞，见 pump_sync_events 注释）
                loop = asyncio.get_running_loop()
                queue = asyncio.Queue()
                worker = threading.Thread(
                    target=pump_sync_events,
                    args=(
                        iter_workflow_events(
                            study_flow,
                            prepared["initial_state"],
                            config,
                            user_id=user_id,
                            thread_id=new_thread_id,
                            publish=lambda et, data: _safe_publish_workflow_event(
                                et, new_thread_id, data, user_id=user_id
                            ),
                        ),
                        loop,
                        queue,
                    ),
                    daemon=True,
                    name=f"study-flow-{new_thread_id[-8:]}",
                )
                worker.start()

                interrupted_at_waiting = False
                while True:
                    evt = await queue.get()
                    if evt is None:
                        break
                    if evt["type"] == "interrupted":
                        # 等待答题是中断而非完成：不发 workflow_completed，避免前端误标 completed
                        interrupted_at_waiting = True
                        break
                    if evt["type"] == "error":
                        error_msg = evt["data"].get("message", "工作流执行失败")
                        yield sse_error_event(code="50001", message=error_msg)
                        interrupted_at_waiting = True
                        break
                    yield f"data: {json.dumps(evt, ensure_ascii=False, cls=_WorkflowJSONEncoder)}\n\n"

                # 工作流完成事件（仅在真正完成时发出）
                if not interrupted_at_waiting:
                    complete_evt = {"type": "workflow_completed", "data": {"thread_id": new_thread_id}}
                    yield f"data: {json.dumps(complete_evt, ensure_ascii=False)}\n\n"
                    await safe_publish_workflow_event(
                        EventType.WORKFLOW_COMPLETED,
                        new_thread_id,
                        {"step": "completed"},
                        user_id=user_id,
                    )

                # 持久化新线程状态（ORM 同步调用，sync_to_async 包装）
                try:
                    from .services.persistence_service import get_persistence_service

                    persistence_service = get_persistence_service()
                    state_values = await sync_to_async(lambda: study_flow.graph.get_state(config).values)()
                    await sync_to_async(persistence_service.save_workflow_state)(
                        thread_id=new_thread_id, state=state_values, user_id=user_id
                    )
                except Exception as persist_err:
                    logger.warning(f"持久化工作流会话失败: {persist_err}")

                # 更新旧 session 的 retry_count（用于追踪总练习轮次，ORM 写入经 sync_to_async）
                old_session = prepared["old_session"]
                old_session.retry_count = prepared["new_retry_count"]
                await sync_to_async(old_session.save)(update_fields=["retry_count"])

            except Exception:
                logger.exception("[API] 流式继续练习失败：")
                await safe_publish_workflow_event(
                    EventType.WORKFLOW_FAILED,
                    new_thread_id,
                    {"step": "planner", "error": "工作流执行失败"},
                    user_id=user_id,
                )
                yield sse_error_event(code="50001", message="工作流执行失败，请稍后重试")

        return sse_response(event_stream())


class WorkflowQuestionListView(APIView):
    """获取工作流所有题目列表视图"""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request, thread_id):
        session = WorkflowSession.objects.filter(
            thread_id=thread_id, created_by=request.user, is_deleted=False
        ).first()
        if not session:
            raise NotFound("工作流会话不存在或无权访问")

        # 通过 root_thread_id 聚合所有相关 session（支持跨会话查看旧轮次题目）
        root_id = session.root_thread_id or session.thread_id
        related_sessions = WorkflowSession.objects.filter(
            Q(thread_id=root_id) | Q(root_thread_id=root_id),
            created_by=request.user,
            is_deleted=False,
        )

        # 支持按 attempt_index 过滤
        attempt_index = request.query_params.get("attempt_index")
        queryset = WorkflowQuestion.objects.filter(session__in=related_sessions)
        if attempt_index is not None:
            try:
                queryset = queryset.filter(attempt_index=int(attempt_index))
            except ValueError:
                pass

        questions = queryset.order_by("attempt_index", "question_index")
        serializer = WorkflowQuestionSerializer(questions, many=True)

        return success_response(data=serializer.data, message="操作成功")


class WorkflowQuestionUpdateView(APIView):
    """修改单题答案并重新评分视图"""

    permission_classes = [IsAuthenticated]
    throttle_classes = [LearningRateThrottle]

    @extend_schema(request=WorkflowQuestionUpdateSerializer, responses={200: EmptySerializer})
    def put(self, request, thread_id, question_id):
        session = WorkflowSession.objects.filter(
            thread_id=thread_id, created_by=request.user, is_deleted=False
        ).first()
        if not session:
            raise NotFound("工作流会话不存在或无权访问")

        # 通过 root_thread_id 聚合所有相关 session（支持跨会话修改旧轮次题目）
        root_id = session.root_thread_id or session.thread_id
        related_sessions = WorkflowSession.objects.filter(
            Q(thread_id=root_id) | Q(root_thread_id=root_id),
            created_by=request.user,
            is_deleted=False,
        )

        question = WorkflowQuestion.objects.filter(
            session__in=related_sessions, question_id=question_id
        ).first()
        if not question:
            raise NotFound("题目不存在")

        user_answer = request.data.get("user_answer")
        if user_answer is None:
            raise ValidationError({"user_answer": ["该字段为必填项"]})

        is_correct, points_earned = _regrade_question(question, user_answer)

        # 更新题目
        question.user_answer = user_answer
        question.is_correct = is_correct
        question.points_earned = points_earned
        question.scored_at = timezone.now()
        question.status = WorkflowQuestionStatus.SCORED
        question.save()

        # 重新计算所属 WorkflowAttempt 的 total_score（跨会话查询该轮次所有题目）
        attempt = WorkflowAttempt.objects.filter(
            session__in=related_sessions, attempt_index=question.attempt_index
        ).first()
        if attempt:
            all_questions = WorkflowQuestion.objects.filter(
                session__in=related_sessions, attempt_index=question.attempt_index
            )
            total_earned = sum(q.points_earned or 0 for q in all_questions)
            # 与 grading_node 保持同一口径：百分比分母取 quiz.total_points（LLM 生成的满分，
            # 而非题目 points 之和，避免修改答案后分数口径漂移），缺失时回退求和
            target_session = attempt.session
            quiz_total = None
            if target_session and target_session.quiz:
                quiz_total = target_session.quiz.get("total_points")
            total_points = quiz_total or sum(q.points for q in all_questions)
            attempt.total_score = int((total_earned / total_points) * 100) if total_points > 0 else 0
            attempt.save(update_fields=["total_score"])

            # 同步更新所属 session 的 user_answers/score/score_details 与内存 graph state，
            # 使 status 接口（get_workflow_state 优先读内存 state）返回最新评分，答题详情/测验结果即时一致
            if target_session:
                # 键与 quiz 原 id（q1）对齐（去掉 _r 轮次后缀），与 grading_node 生成的 user_answers 口径一致
                new_user_answers = {q.question_id.rsplit("_r", 1)[0]: q.user_answer or "" for q in all_questions}
                new_score_details = _build_score_details(all_questions)
                target_session.user_answers = new_user_answers
                target_session.score = attempt.total_score
                target_session.score_details = new_score_details
                target_session.save(update_fields=["user_answers", "score", "score_details"])

                # 同步更新内存 LangGraph state（进程重启后由 DB 恢复，两条路径都必须一致）
                try:
                    study_flow = _get_cached_study_flow(target_session.thread_id)
                    study_flow.graph.update_state(
                        config={"configurable": {"thread_id": target_session.thread_id}},
                        values={
                            "user_answers": new_user_answers,
                            "score": attempt.total_score,
                            "score_details": new_score_details,
                        },
                    )
                    logger.info(
                        f"[API] 修改答案后已同步内存工作流状态: thread_id={target_session.thread_id}, "
                        f"score={attempt.total_score}"
                    )
                except Exception as state_err:
                    logger.warning(f"[API] 更新内存工作流状态失败: {state_err}")

        serializer = WorkflowQuestionSerializer(question)
        return success_response(
            data={
                "question": serializer.data,
                "attempt_total_score": attempt.total_score if attempt else None,
            },
            message="重新评分完成",
        )


class WorkflowAttemptListView(APIView):
    """获取工作流练习轮次历史视图"""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request, thread_id):
        session = WorkflowSession.objects.filter(
            thread_id=thread_id, created_by=request.user, is_deleted=False
        ).first()
        if not session:
            raise NotFound("工作流会话不存在或无权访问")

        # 通过 root_thread_id 聚合所有相关 session
        root_id = session.root_thread_id or session.thread_id
        related_sessions = WorkflowSession.objects.filter(
            Q(thread_id=root_id) | Q(root_thread_id=root_id),
            created_by=request.user,
            is_deleted=False,
        )

        attempts = WorkflowAttempt.objects.filter(session__in=related_sessions).order_by("attempt_index")
        serializer = WorkflowAttemptSerializer(attempts, many=True)

        return success_response(data=serializer.data, message="操作成功")


def _build_score_details(questions) -> dict:
    """根据轮次题目集合重建 score_details（与 grading_node 的评分详情格式保持一致）。

    Args:
        questions: WorkflowQuestion QuerySet（同一轮次）

    Returns:
        评分详情字典：total_count / correct_count / question_scores
    """
    question_scores = []
    for q in questions:
        is_correct = bool(q.is_correct)
        feedback = (
            "回答正确！"
            if is_correct
            else f"回答错误。正确答案是：{q.correct_answer or ''}"
        )
        # question_id 形如 "q1_r0"，去掉轮次后缀保持与 grading_node 的评分详情一致（quiz 原 id "q1"）
        question_scores.append(
            {
                "question_id": q.question_id.rsplit("_r", 1)[0],
                "user_answer": q.user_answer or "",
                "correct_answer": q.correct_answer or "",
                "is_correct": is_correct,
                "points_earned": q.points_earned or 0,
                "points_possible": q.points or 0,
                "feedback": feedback,
            }
        )
    return {
        "total_count": len(question_scores),
        "correct_count": sum(1 for item in question_scores if item["is_correct"]),
        "question_scores": question_scores,
    }


def _regrade_question(question, user_answer: str) -> tuple[bool, int]:
    """单题重新评分（与 grading_node 的宽松评分规则保持一致）。

    Returns:
        (is_correct, points_earned)
    """
    from .nodes.grading_node import _is_option_index, _normalize_fill

    is_correct = False
    points_earned = 0

    if question.type == WorkflowQuestionType.MULTIPLE_CHOICE.value:
        options = question.options or []

        correct_answer_display = question.correct_answer
        if _is_option_index(question.correct_answer) and options:
            idx = ord(question.correct_answer.strip().upper()) - ord("A")
            if 0 <= idx < len(options):
                correct_answer_display = options[idx]

        user_answer_display = user_answer
        if _is_option_index(user_answer) and options:
            idx = ord(user_answer.strip().upper()) - ord("A")
            if 0 <= idx < len(options):
                user_answer_display = options[idx]

        is_correct = (
            user_answer.strip().upper() == question.correct_answer.strip().upper()
            or user_answer_display.strip() == correct_answer_display.strip()
        )
        points_earned = question.points if is_correct else 0

    elif question.type == WorkflowQuestionType.FILL_BLANK.value:
        normalized_user = _normalize_fill(user_answer)
        acceptable = [a.strip() for a in question.correct_answer.split("|") if a.strip()]
        is_correct = any(normalized_user == _normalize_fill(a) for a in acceptable)

        if not is_correct and user_answer.strip():
            # 归一化不匹配时用辅助模型语义等价判断（LLM 不可用时按错误处理）
            from Django_xm.apps.ai_engine.services.llm_factory import get_helper_model

            try:
                from .nodes.grading_node import _judge_fill_blank_semantic

                helper_model = get_helper_model()
                is_correct = _judge_fill_blank_semantic(
                    helper_model, question.question, question.correct_answer, user_answer
                )
            except Exception as llm_err:
                logger.warning(
                    f"[API] 填空题 LLM 语义判断失败，按错误处理: "
                    f"question_id={question.question_id}, err={llm_err}"
                )
                is_correct = False

        points_earned = question.points if is_correct else 0

    elif question.type == WorkflowQuestionType.SHORT_ANSWER.value:
        from Django_xm.apps.ai_engine.services.llm_factory import get_helper_model

        from .nodes.grading_node import _grade_short_answer

        try:
            helper_model = get_helper_model()
            points_earned, _ = _grade_short_answer(
                helper_model,
                {"question": question.question, "points": question.points},
                question.correct_answer,
                user_answer,
            )
        except Exception as llm_err:
            logger.warning(
                f"[API] 简答题 LLM 评分失败，按关键词匹配回退: "
                f"question_id={question.question_id}, err={llm_err}"
            )
            keywords = question.correct_answer.lower().split()[:5]
            matched = sum(1 for kw in keywords if kw in user_answer.lower())
            points_earned = int((matched / max(len(keywords), 1)) * question.points)

        is_correct = points_earned >= question.points * 0.6

    return is_correct, points_earned


class WorkflowStatusView(APIView):
    """获取工作流状态视图"""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request, thread_id):
        session = WorkflowSession.objects.filter(
            thread_id=thread_id, created_by=request.user, is_deleted=False
        ).first()

        if not session:
            raise NotFound("工作流会话不存在或无权访问")

        state = WorkflowService.get_workflow_status(thread_id)

        if not state:
            raise NotFound("工作流会话不存在或无权访问")

        return success_response(
            data={
                "thread_id": thread_id,
                "current_step": state.get("current_step"),
                "status": state.get("current_step"),
                "user_question": state.get("user_question"),
                "learning_plan": state.get("learning_plan"),
                "retrieved_docs": state.get("retrieved_docs"),
                "quiz": state.get("quiz"),
                "user_answers": state.get("user_answers"),
                "score": state.get("score"),
                "score_details": state.get("score_details"),
                "feedback": state.get("feedback"),
                "should_retry": state.get("should_retry", False),
                "retry_count": state.get("retry_count", 0),
                "created_at": state.get("created_at", ""),
                "updated_at": state.get("updated_at", ""),
            }
        )


class WorkflowHistoryView(APIView):
    """获取工作流历史视图"""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request, thread_id):
        session = WorkflowSession.objects.filter(
            thread_id=thread_id, created_by=request.user, is_deleted=False
        ).first()
        if not session:
            raise NotFound("工作流会话不存在或无权访问")

        history = WorkflowService.get_workflow_history(thread_id)
        return success_response(data={"thread_id": thread_id, "history": history})


class WorkflowTaskDeleteView(APIView):
    """删除工作流任务视图"""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def delete(self, request, thread_id):
        result = WorkflowService.delete_workflow(thread_id, request.user.id)
        return success_response(data=result, message="操作成功")


class WorkflowListView(APIView):
    """获取工作流列表视图"""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request):
        status_filter = request.query_params.get("status")
        search_query = request.query_params.get("search")
        page = int(request.query_params.get("page", 1))
        page_size = min(int(request.query_params.get("page_size", 20)), 100)

        queryset = WorkflowService.list_user_workflows(
            user_id=request.user.id, status=status_filter, search=search_query
        )

        total = queryset.count()
        start = (page - 1) * page_size
        end = start + page_size
        sessions = queryset[start:end]

        serializer = WorkflowSessionSerializer(sessions, many=True)

        return success_response(
            data={
                "items": serializer.data,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size,
            }
        )


class WorkflowFilesListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request, thread_id):
        session = WorkflowSession.objects.filter(
            thread_id=thread_id, created_by=request.user, is_deleted=False
        ).first()
        if not session:
            raise NotFound("工作流会话不存在或无权访问")

        files = file_manager.list_task_files(thread_id, "workflow")

        from Django_xm.common.serializers import FileInfoSerializer

        serializer = FileInfoSerializer([f.to_dict() for f in files], many=True)

        return success_response(
            data={
                "thread_id": thread_id,
                "files": serializer.data,
                "total": len(files),
            }
        )


class WorkflowFileDownloadView(APIView):
    permission_classes = [IsAuthenticatedOrQueryParam]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request, thread_id, filename):
        user = request.user if request.user.is_authenticated else None
        if not user:
            raise BaseAppError("未认证", business_code=ErrorCode.UNAUTHORIZED)
        session = WorkflowSession.objects.filter(thread_id=thread_id, created_by=user, is_deleted=False).first()
        if not session:
            raise NotFound("工作流会话不存在或无权访问")

        file_info = file_manager.get_file_info(thread_id, filename, "workflow")
        if not file_info:
            raise NotFound("文件不存在")

        file_path = file_info.path

        content_type = "application/octet-stream"
        if file_path.suffix.lower() in [".md", ".txt"]:
            content_type = "text/plain; charset=utf-8"
        elif file_path.suffix.lower() == ".json":
            content_type = "application/json"

        # 说明：FileResponse 惰性读取文件，响应关闭时自动关闭句柄，
        # 若用 with 提前关闭会导致流式读取失败，故保持 Django 官方 open() 模式。
        response = FileResponse(
            open(file_path, "rb"),  # noqa: SIM115
            content_type=content_type,
            as_attachment=True,
            filename=quote(file_path.name),
        )
        return response


class WorkflowFileContentView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request, thread_id, filename):
        session = WorkflowSession.objects.filter(
            thread_id=thread_id, created_by=request.user, is_deleted=False
        ).first()
        if not session:
            raise NotFound("工作流会话不存在或无权访问")

        file_info = file_manager.get_file_info(thread_id, filename, "workflow")
        if not file_info:
            raise NotFound("文件不存在")

        content = file_manager.read_file_content(thread_id, filename, "workflow")

        from Django_xm.common.serializers import FileInfoSerializer

        return success_response(
            data={
                "filename": filename,
                "content": content,
                "file_info": FileInfoSerializer(file_info.to_dict()).data,
            }
        )


def workflow_stream(request, thread_id):
    """
    流式获取工作流执行进度（Server-Sent Events）
    使用 LangGraph 的 stream 方法获取真实的事件流
    支持Authorization header和查询参数token认证
    """
    from Django_xm.common.sse_utils import authenticate_sse_request, sse_error_event, sse_error_response

    user = authenticate_sse_request(request)

    if not user:
        return sse_error_response("未登录或登录已过期", 401, code="40101")

    try:
        session = WorkflowSession.objects.filter(thread_id=thread_id, created_by=user, is_deleted=False).first()

        if not session:
            state = get_workflow_state(thread_id)
            if not state:
                return sse_error_response("工作流不存在或无权访问", 404, code="40401")

            try:
                from .services.persistence_service import get_persistence_service

                persistence_service = get_persistence_service()
                persistence_service.save_workflow_state(thread_id=thread_id, state=state, user_id=user.id)
                logger.info(f"[API] 从checkpointer恢复工作流会话: {thread_id}")
            except Exception as persist_err:
                logger.warning(f"[API] 恢复工作流会话失败: {persist_err}")

        logger.info(f"[API] 流式获取工作流，thread_id={thread_id}, user_id={user.id}")

        async def event_stream():
            """生成 SSE 事件流（统一 workflow_* 事件格式，与 EventType 枚举对齐）"""
            try:
                # async 生成器内禁止同步 ORM，sync_to_async 包装到线程池执行
                state = await sync_to_async(get_workflow_state)(thread_id)

                if not state:
                    yield sse_error_event(code="40401", message="工作流不存在")
                    return

                current_step = state.get("current_step", "unknown")
                # 首个步骤事件按真实状态生成：waiting/完成态不得被"start 启动中"覆盖，
                # 否则前端步骤条回退到 start、stepMessage 残留"工作流启动中..."
                if current_step == "waiting_for_answers":
                    initial_step, initial_message = current_step, "等待您提交答案..."
                elif current_step in ("completed", "end", "feedback_completed"):
                    initial_step, initial_message = "feedback_completed", "工作流已完成"
                else:
                    initial_step, initial_message = "start", "工作流启动中..."

                workflow_step_evt = {
                    "type": "workflow_step",
                    "data": {
                        "step": initial_step,
                        "message": initial_message,
                        "state": current_step,
                    },
                }
                yield f"data: {json.dumps(workflow_step_evt, ensure_ascii=False)}\n\n"
                await safe_publish_workflow_event(
                    EventType.WORKFLOW_STEP,
                    thread_id,
                    {"step": initial_step, "message": initial_message, "state": current_step},
                    user_id=user.id,
                )

                if current_step == "waiting_for_answers":
                    waiting_state_evt = {
                        "type": "workflow_state_update",
                        "data": {
                            "step": current_step,
                            "state": "waiting_for_answers",
                            "message": "等待您提交答案...",
                            "learning_plan": state.get("learning_plan"),
                            "quiz": state.get("quiz"),
                            "current_step": current_step,
                            "thread_id": thread_id,
                        },
                    }
                    yield f"data: {json.dumps(waiting_state_evt, ensure_ascii=False, cls=_WorkflowJSONEncoder)}\n\n"
                    await safe_publish_workflow_event(
                        EventType.WORKFLOW_STATE_UPDATE,
                        thread_id,
                        {
                            "step": current_step,
                            "state": "waiting_for_answers",
                            "message": "等待用户提交答案",
                        },
                        user_id=user.id,
                    )
                    # 等待答题是中断而非完成：不发 workflow_completed，避免前端误标 completed
                    return

                if current_step in ("completed", "end", "feedback_completed"):
                    completed_state_evt = {
                        "type": "workflow_state_update",
                        "data": {
                            "step": current_step,
                            "score": state.get("score"),
                            "feedback": state.get("feedback"),
                            "score_details": state.get("score_details"),
                        },
                    }
                    yield f"data: {json.dumps(completed_state_evt, ensure_ascii=False, cls=_WorkflowJSONEncoder)}\n\n"
                    await safe_publish_workflow_event(
                        EventType.WORKFLOW_STATE_UPDATE,
                        thread_id,
                        {
                            "step": current_step,
                            "score": state.get("score"),
                            "feedback": state.get("feedback"),
                            "score_details": state.get("score_details"),
                        },
                        user_id=user.id,
                    )
                    complete_evt = {"type": "workflow_completed", "data": {"thread_id": thread_id}}
                    yield f"data: {json.dumps(complete_evt, ensure_ascii=False)}\n\n"
                    await safe_publish_workflow_event(
                        EventType.WORKFLOW_COMPLETED,
                        thread_id,
                        {"step": current_step},
                        user_id=user.id,
                    )
                    return

                study_flow = await sync_to_async(_get_study_flow)(thread_id)
                config = {"configurable": {"thread_id": thread_id}}

                try:
                    # 恢复执行图事件流：同步生成器在专用线程执行，事件经 call_soon_threadsafe
                    # 泵入队列（PostgresSaver 持久化 + 事件循环不阻塞，见 pump_sync_events 注释）
                    loop = asyncio.get_running_loop()
                    queue = asyncio.Queue()
                    worker = threading.Thread(
                        target=pump_sync_events,
                        args=(
                            iter_workflow_events(
                                study_flow,
                                None,
                                config,
                                user_id=user.id,
                                thread_id=thread_id,
                                publish=lambda et, data: _safe_publish_workflow_event(
                                    et, thread_id, data, user_id=user.id
                                ),
                            ),
                            loop,
                            queue,
                        ),
                        daemon=True,
                        name=f"study-flow-{thread_id[-8:]}",
                    )
                    worker.start()

                    while True:
                        evt = await queue.get()
                        if evt is None:
                            break
                        if evt["type"] == "interrupted":
                            # 重试出题后再次进入等待答题：中断而非完成，不发 workflow_completed，
                            # 前端 isWaiting 分支会重新初始化答题表单并关闭 SSE
                            return
                        if evt["type"] == "error":
                            error_msg = evt["data"].get("message", "工作流执行失败")
                            yield sse_error_event(code="50001", message=error_msg)
                            break
                        yield f"data: {json.dumps(evt, ensure_ascii=False, cls=_WorkflowJSONEncoder)}\n\n"

                except Exception as stream_error:
                    logger.warning(f"[API] 流式执行失败：{stream_error}")
                    await safe_publish_workflow_event(
                        EventType.WORKFLOW_FAILED,
                        thread_id,
                        {"step": "stream", "error": "工作流执行失败"},
                        user_id=user.id,
                    )
                    yield sse_error_event(code="50001", message="工作流执行失败，请稍后重试")
                    complete_evt = {"type": "workflow_completed", "data": {"thread_id": thread_id}}
                    yield f"data: {json.dumps(complete_evt, ensure_ascii=False)}\n\n"

            except Exception:
                logger.exception("[API] 流式输出失败：")
                await safe_publish_workflow_event(
                    EventType.WORKFLOW_FAILED,
                    thread_id,
                    {"step": "stream", "error": "工作流执行失败"},
                    user_id=user.id if user else None,
                )
                yield sse_error_event(code="50001", message="工作流执行失败，请稍后重试")

        return sse_response(event_stream())

    except Exception:
        logger.exception("[API] 流式输出失败：")

        def error_event():
            yield sse_error_event(code="50001", message="工作流执行失败，请稍后重试")

        return StreamingHttpResponse(
            error_event(), content_type="text/event-stream", status=500, headers={"Cache-Control": "no-cache"}
        )
