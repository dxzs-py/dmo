"""
工作流API视图
使用类视图和服务层实现，遵循项目统一的响应格式规范
"""

import json
import uuid
from urllib.parse import quote

from django.db.models import Q
from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import BaseRenderer
from rest_framework.views import APIView

from Django_xm.apps.core.config import get_logger
from Django_xm.apps.core.services.file_manager import get_file_manager
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.event_schema import EventSource, EventType
from Django_xm.common.permissions import IsAuthenticatedOrQueryParam
from Django_xm.common.realtime_events import publish_event_sync
from Django_xm.common.responses import error_response, not_found_response, success_response, validation_error_response
from Django_xm.common.serializers import EmptySerializer
from Django_xm.common.sse_utils import sse_error_event, sse_response

from .models import WorkflowAttempt, WorkflowQuestion, WorkflowQuestionStatus, WorkflowQuestionType, WorkflowSession
from .serializers import (
    WorkflowAttemptSerializer,
    WorkflowQuestionSerializer,
    WorkflowResponseSerializer,
    WorkflowSessionSerializer,
    WorkflowStartSerializer,
    WorkflowSubmitSerializer,
)
from .services import WorkflowService
from .services.resilience import stream_with_resilience
from .services.study_flow import WorkflowAlreadyFinishedError, _get_study_flow, get_workflow_state


class SSERenderer(BaseRenderer):
    media_type = "text/event-stream"
    format = "txt"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


logger = get_logger(__name__)
file_manager = get_file_manager()


def _safe_publish_workflow_event(
    event_type: EventType,
    thread_id: str,
    data: dict,
    user_id=None,
) -> None:
    """安全发布工作流事件到 task 频道，吞掉异常以避免影响 SSE 主流程。

    用于在 SSE 流式输出时同步推送 WebSocket 事件，使其他浏览器也能收到进度。
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
            return validation_error_response(errors=serializer.errors, message="数据验证失败")

        try:
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

        except Exception as e:
            logger.exception("[API] 启动工作流失败：")
            return error_response(
                code=ErrorCode.SERVER_ERROR, message=str(e), http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class WorkflowStartStreamView(APIView):
    """启动学习工作流（SSE流式输出）"""

    permission_classes = [IsAuthenticated]
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
            return validation_error_response(errors=serializer.errors, message="数据验证失败")

        user_question = serializer.validated_data.get("user_question") or serializer.validated_data.get("query")
        thread_id = serializer.validated_data.get("thread_id") or f"study_{uuid.uuid4().hex[:12]}"
        user_id = request.user.id
        knowledge_base_ids = serializer.validated_data.get("knowledge_base_ids") or []

        logger.info(f"[API] 流式启动工作流，thread_id={thread_id}")

        def event_stream():
            try:
                # 工作流开始事件（统一格式 + 同步推送 WS）
                start_event = {
                    "type": "workflow_step",
                    "data": {"step": "start", "message": "工作流启动中...", "thread_id": thread_id},
                }
                yield f"data: {json.dumps(start_event, ensure_ascii=False)}\n\n"
                _safe_publish_workflow_event(
                    EventType.WORKFLOW_STEP,
                    thread_id,
                    {"step": "start", "message": "工作流启动中..."},
                    user_id=user_id,
                )


                from .study_flow import _get_study_flow

                study_flow = _get_study_flow(thread_id)

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
                _safe_publish_workflow_event(
                    EventType.WORKFLOW_STEP,
                    thread_id,
                    {"step": "planner", "message": "正在生成学习计划..."},
                    user_id=user_id,
                )

                # 使用 stream_with_resilience 替代直接 graph.stream
                for event in stream_with_resilience(study_flow.graph, initial_state, config, stream_mode="values"):
                    if not event:
                        continue

                    current_step = event.get("current_step", "unknown")

                    step_messages = {
                        "planner": "正在生成学习计划...",
                        "retrieval": "正在检索相关资料...",
                        "quiz_generator": "正在生成练习题...",
                        "waiting_for_answers": "等待您提交答案...",
                        "grading": "正在评分...",
                        "feedback": "正在生成反馈...",
                        "end": "工作流已完成",
                    }

                    step_message = step_messages.get(current_step, f"当前步骤: {current_step}")

                    step_evt = {"type": "workflow_step", "data": {"step": current_step, "message": step_message}}
                    yield f"data: {json.dumps(step_evt, ensure_ascii=False)}\n\n"
                    _safe_publish_workflow_event(
                        EventType.WORKFLOW_STEP,
                        thread_id,
                        {"step": current_step, "message": step_message},
                        user_id=user_id,
                    )

                    if current_step == "waiting_for_answers":
                        waiting_data = {
                            "type": "workflow_state_update",
                            "data": {
                                "step": current_step,
                                "state": "waiting_for_answers",
                                "thread_id": thread_id,
                                "learning_plan": event.get("learning_plan"),
                                "quiz": event.get("quiz"),
                                "current_step": current_step,
                            },
                        }
                        yield f"data: {json.dumps(waiting_data, ensure_ascii=False, cls=_WorkflowJSONEncoder)}\n\n"
                        _safe_publish_workflow_event(
                            EventType.WORKFLOW_STATE_UPDATE,
                            thread_id,
                            {
                                "step": current_step,
                                "state": "waiting_for_answers",
                                "message": "等待用户提交答案",
                                "learning_plan": event.get("learning_plan"),
                                "quiz": event.get("quiz"),
                            },
                            user_id=user_id,
                        )
                        break

                    state_data = {
                        "type": "workflow_state_update",
                        "data": {
                            "step": current_step,
                            "learning_plan": event.get("learning_plan"),
                            "retrieved_docs": event.get("retrieved_docs"),
                            "quiz": event.get("quiz"),
                        },
                    }
                    yield f"data: {json.dumps(state_data, ensure_ascii=False, cls=_WorkflowJSONEncoder)}\n\n"
                    _safe_publish_workflow_event(
                        EventType.WORKFLOW_STATE_UPDATE,
                        thread_id,
                        {
                            "step": current_step,
                            "learning_plan": event.get("learning_plan"),
                            "retrieved_docs": event.get("retrieved_docs"),
                            "quiz": event.get("quiz"),
                        },
                        user_id=user_id,
                    )

                # 工作流完成事件
                complete_evt = {"type": "workflow_completed", "data": {"thread_id": thread_id}}
                yield f"data: {json.dumps(complete_evt, ensure_ascii=False)}\n\n"
                _safe_publish_workflow_event(
                    EventType.WORKFLOW_COMPLETED,
                    thread_id,
                    {"step": "completed"},
                    user_id=user_id,
                )

                try:
                    from .services.persistence_service import get_persistence_service

                    persistence_service = get_persistence_service()
                    persistence_service.save_workflow_state(
                        thread_id=thread_id, state=study_flow.graph.get_state(config).values, user_id=user_id
                    )
                except Exception as persist_err:
                    logger.warning(f"持久化工作流会话失败: {persist_err}")

            except Exception as e:
                logger.exception("[API] 流式工作流执行失败：")
                # 工作流失败事件
                _safe_publish_workflow_event(
                    EventType.WORKFLOW_FAILED,
                    thread_id,
                    {"step": "planner", "error": str(e)},
                    user_id=user_id,
                )
                yield sse_error_event(code="50001", message=str(e))

        return sse_response(event_stream())


class WorkflowSubmitView(APIView):
    """提交用户答案视图"""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=WorkflowSubmitSerializer, responses={200: EmptySerializer})
    def post(self, request):
        """
        提交用户答案，继续执行工作流
        """
        serializer = WorkflowSubmitSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(errors=serializer.errors, message="数据验证失败")

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
                    return error_response(
                        code=ErrorCode.NOT_FOUND,
                        message="工作流会话不存在或无权访问",
                        http_status=status.HTTP_404_NOT_FOUND,
                    )

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
            return error_response(
                code=ErrorCode.WORKFLOW_ALREADY_FINISHED,
                message=str(e),
                http_status=status.HTTP_409_CONFLICT,
                data={"current_phase": getattr(e, "current_phase", "")},
            )
        except Exception as e:
            logger.exception("[API] 提交答案失败：")
            return error_response(
                code=ErrorCode.SERVER_ERROR, message=str(e), http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class WorkflowRestartView(APIView):
    """继续练习视图"""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def post(self, request, thread_id):
        try:
            session = WorkflowSession.objects.filter(
                thread_id=thread_id, created_by=request.user, is_deleted=False
            ).first()
            if not session:
                return error_response(
                    code=ErrorCode.NOT_FOUND,
                    message="工作流会话不存在或无权访问",
                    http_status=status.HTTP_404_NOT_FOUND,
                )

            result = WorkflowService.restart_workflow(thread_id=thread_id, user_id=request.user.id)

            return success_response(data=result, message="继续练习已启动")
        except ValueError as e:
            return error_response(
                code=ErrorCode.VALIDATION_FAILED,
                message=str(e),
                http_status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.exception("[API] 继续练习失败：")
            return error_response(
                code=ErrorCode.SERVER_ERROR, message=str(e), http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class WorkflowQuestionListView(APIView):
    """获取工作流所有题目列表视图"""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request, thread_id):
        try:
            session = WorkflowSession.objects.filter(
                thread_id=thread_id, created_by=request.user, is_deleted=False
            ).first()
            if not session:
                return error_response(
                    code=ErrorCode.NOT_FOUND,
                    message="工作流会话不存在或无权访问",
                    http_status=status.HTTP_404_NOT_FOUND,
                )

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
        except Exception as e:
            logger.exception("[API] 获取题目列表失败：")
            return error_response(
                code=ErrorCode.SERVER_ERROR, message=str(e), http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class WorkflowQuestionUpdateView(APIView):
    """修改单题答案并重新评分视图"""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def put(self, request, thread_id, question_id):
        try:
            session = WorkflowSession.objects.filter(
                thread_id=thread_id, created_by=request.user, is_deleted=False
            ).first()
            if not session:
                return error_response(
                    code=ErrorCode.NOT_FOUND,
                    message="工作流会话不存在或无权访问",
                    http_status=status.HTTP_404_NOT_FOUND,
                )

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
                return error_response(
                    code=ErrorCode.NOT_FOUND,
                    message="题目不存在",
                    http_status=status.HTTP_404_NOT_FOUND,
                )

            user_answer = request.data.get("user_answer")
            if user_answer is None:
                return validation_error_response(
                    errors={"user_answer": ["该字段为必填项"]},
                    message="数据验证失败",
                )

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
                total_points = sum(q.points for q in all_questions)
                total_earned = sum(q.points_earned or 0 for q in all_questions)
                attempt.total_score = int((total_earned / total_points) * 100) if total_points > 0 else 0
                attempt.save(update_fields=["total_score"])

            serializer = WorkflowQuestionSerializer(question)
            return success_response(
                data={
                    "question": serializer.data,
                    "attempt_total_score": attempt.total_score if attempt else None,
                },
                message="重新评分完成",
            )
        except Exception as e:
            logger.exception("[API] 重新评分失败：")
            return error_response(
                code=ErrorCode.SERVER_ERROR, message=str(e), http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class WorkflowAttemptListView(APIView):
    """获取工作流练习轮次历史视图"""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request, thread_id):
        try:
            session = WorkflowSession.objects.filter(
                thread_id=thread_id, created_by=request.user, is_deleted=False
            ).first()
            if not session:
                return error_response(
                    code=ErrorCode.NOT_FOUND,
                    message="工作流会话不存在或无权访问",
                    http_status=status.HTTP_404_NOT_FOUND,
                )

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
        except Exception as e:
            logger.exception("[API] 获取练习轮次历史失败：")
            return error_response(
                code=ErrorCode.SERVER_ERROR, message=str(e), http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


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
        try:
            session = WorkflowSession.objects.filter(
                thread_id=thread_id, created_by=request.user, is_deleted=False
            ).first()

            if not session:
                return error_response(
                    code=ErrorCode.NOT_FOUND,
                    message="工作流会话不存在或无权访问",
                    http_status=status.HTTP_404_NOT_FOUND,
                )

            state = WorkflowService.get_workflow_status(thread_id)

            if not state:
                return error_response(
                    code=ErrorCode.NOT_FOUND,
                    message="工作流会话不存在或无权访问",
                    http_status=status.HTTP_404_NOT_FOUND,
                )

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

        except Exception as e:
            logger.exception("[API] 查询状态失败：")
            return error_response(
                code=ErrorCode.SERVER_ERROR, message=str(e), http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class WorkflowHistoryView(APIView):
    """获取工作流历史视图"""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request, thread_id):
        try:
            session = WorkflowSession.objects.filter(
                thread_id=thread_id, created_by=request.user, is_deleted=False
            ).first()
            if not session:
                return error_response(
                    code=ErrorCode.NOT_FOUND,
                    message="工作流会话不存在或无权访问",
                    http_status=status.HTTP_404_NOT_FOUND,
                )

            history = WorkflowService.get_workflow_history(thread_id)
            return success_response(data={"thread_id": thread_id, "history": history})

        except Exception as e:
            logger.exception("[API] 查询历史失败：")
            return error_response(
                code=ErrorCode.SERVER_ERROR, message=str(e), http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class WorkflowDeleteView(APIView):
    """删除工作流视图"""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def delete(self, request, thread_id):
        try:
            result = WorkflowService.delete_workflow(thread_id, request.user.id)
            return success_response(data=result, message="操作成功")

        except Exception as e:
            logger.exception("[API] 删除工作流失败：")
            return error_response(
                code=ErrorCode.SERVER_ERROR, message=str(e), http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class WorkflowListView(APIView):
    """获取工作流列表视图"""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request):
        try:
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

        except Exception as e:
            logger.exception("[API] 获取工作流列表失败：")
            return error_response(
                code=ErrorCode.SERVER_ERROR, message=str(e), http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class WorkflowFilesListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request, thread_id):
        try:
            session = WorkflowSession.objects.filter(
                thread_id=thread_id, created_by=request.user, is_deleted=False
            ).first()
            if not session:
                return error_response(
                    code=ErrorCode.NOT_FOUND,
                    message="工作流会话不存在或无权访问",
                    http_status=status.HTTP_404_NOT_FOUND,
                )

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

        except Exception:
            logger.exception("列出工作流文件失败：")
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message="获取文件列表失败",
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class WorkflowFileDownloadView(APIView):
    permission_classes = [IsAuthenticatedOrQueryParam]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request, thread_id, filename):
        try:
            user = request.user if request.user.is_authenticated else None
            if not user:
                return error_response(
                    code=ErrorCode.UNAUTHORIZED, message="未认证", http_status=status.HTTP_401_UNAUTHORIZED
                )
            session = WorkflowSession.objects.filter(thread_id=thread_id, created_by=user, is_deleted=False).first()
            if not session:
                return error_response(
                    code=ErrorCode.NOT_FOUND,
                    message="工作流会话不存在或无权访问",
                    http_status=status.HTTP_404_NOT_FOUND,
                )

            file_info = file_manager.get_file_info(thread_id, filename, "workflow")
            if not file_info:
                return not_found_response(message="文件不存在")

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

        except Exception:
            logger.exception("下载文件失败：")
            return error_response(
                code=ErrorCode.SERVER_ERROR, message="文件下载失败", http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class WorkflowFileContentView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request, thread_id, filename):
        try:
            session = WorkflowSession.objects.filter(
                thread_id=thread_id, created_by=request.user, is_deleted=False
            ).first()
            if not session:
                return error_response(
                    code=ErrorCode.NOT_FOUND,
                    message="工作流会话不存在或无权访问",
                    http_status=status.HTTP_404_NOT_FOUND,
                )

            file_info = file_manager.get_file_info(thread_id, filename, "workflow")
            if not file_info:
                return not_found_response(message="文件不存在")

            content = file_manager.read_file_content(thread_id, filename, "workflow")

            from Django_xm.common.serializers import FileInfoSerializer

            return success_response(
                data={
                    "filename": filename,
                    "content": content,
                    "file_info": FileInfoSerializer(file_info.to_dict()).data,
                }
            )

        except Exception:
            logger.exception("读取文件内容失败：")
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message="读取文件内容失败",
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
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

        def event_stream():
            """生成 SSE 事件流（统一 workflow_* 事件格式，与 EventType 枚举对齐）"""
            try:
                state = get_workflow_state(thread_id)

                if not state:
                    yield sse_error_event(code="40401", message="工作流不存在")
                    return

                current_step = state.get("current_step", "unknown")
                # 工作流开始事件
                workflow_step_evt = {
                    "type": "workflow_step",
                    "data": {
                        "step": "start",
                        "message": "工作流启动中...",
                        "state": current_step,
                    },
                }
                yield f"data: {json.dumps(workflow_step_evt, ensure_ascii=False)}\n\n"
                _safe_publish_workflow_event(
                    EventType.WORKFLOW_STEP,
                    thread_id,
                    {"step": "start", "message": "工作流启动中...", "state": current_step},
                    user_id=user.id,
                )

                if current_step == "waiting_for_answers":
                    waiting_state_evt = {
                        "type": "workflow_state_update",
                        "data": {
                            "step": current_step,
                            "state": "waiting_for_answers",
                            "learning_plan": state.get("learning_plan"),
                            "quiz": state.get("quiz"),
                            "current_step": current_step,
                            "thread_id": thread_id,
                        },
                    }
                    yield f"data: {json.dumps(waiting_state_evt, ensure_ascii=False, cls=_WorkflowJSONEncoder)}\n\n"
                    _safe_publish_workflow_event(
                        EventType.WORKFLOW_STATE_UPDATE,
                        thread_id,
                        {
                            "step": current_step,
                            "state": "waiting_for_answers",
                            "message": "等待用户提交答案",
                        },
                        user_id=user.id,
                    )
                    complete_evt = {"type": "workflow_completed", "data": {"thread_id": thread_id}}
                    yield f"data: {json.dumps(complete_evt, ensure_ascii=False)}\n\n"
                    _safe_publish_workflow_event(
                        EventType.WORKFLOW_COMPLETED,
                        thread_id,
                        {"step": current_step},
                        user_id=user.id,
                    )
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
                    _safe_publish_workflow_event(
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
                    _safe_publish_workflow_event(
                        EventType.WORKFLOW_COMPLETED,
                        thread_id,
                        {"step": current_step},
                        user_id=user.id,
                    )
                    return

                study_flow = _get_study_flow(thread_id)
                config = {"configurable": {"thread_id": thread_id}}

                try:
                    # 使用 stream_with_resilience 替代直接 graph.stream
                    for event in stream_with_resilience(study_flow.graph, None, config, stream_mode="values"):
                        if event:
                            step = event.get("current_step", "unknown")
                            state_update_evt = {
                                "type": "workflow_state_update",
                                "data": {
                                    "step": step,
                                    "state": step,
                                    "payload": event,
                                },
                            }
                            state_payload = json.dumps(
                                state_update_evt, ensure_ascii=False, cls=_WorkflowJSONEncoder
                            )
                            yield f"data: {state_payload}\n\n"
                            _safe_publish_workflow_event(
                                EventType.WORKFLOW_STATE_UPDATE,
                                thread_id,
                                {"step": step, "state": step},
                                user_id=user.id,
                            )

                            if step == "waiting_for_answers":
                                waiting_evt = {
                                    "type": "workflow_state_update",
                                    "data": {
                                        "step": step,
                                        "state": "waiting_for_answers",
                                        "message": "等待用户提交答案",
                                    },
                                }
                                yield f"data: {json.dumps(waiting_evt, ensure_ascii=False)}\n\n"
                                _safe_publish_workflow_event(
                                    EventType.WORKFLOW_STATE_UPDATE,
                                    thread_id,
                                    {
                                        "step": step,
                                        "state": "waiting_for_answers",
                                        "message": "等待用户提交答案",
                                    },
                                    user_id=user.id,
                                )
                                complete_evt = {"type": "workflow_completed", "data": {"thread_id": thread_id}}
                                yield f"data: {json.dumps(complete_evt, ensure_ascii=False)}\n\n"
                                _safe_publish_workflow_event(
                                    EventType.WORKFLOW_COMPLETED,
                                    thread_id,
                                    {"step": step},
                                    user_id=user.id,
                                )
                                return

                    complete_evt = {"type": "workflow_completed", "data": {"thread_id": thread_id}}
                    yield f"data: {json.dumps(complete_evt, ensure_ascii=False)}\n\n"
                    _safe_publish_workflow_event(
                        EventType.WORKFLOW_COMPLETED,
                        thread_id,
                        {"step": "completed"},
                        user_id=user.id,
                    )

                except Exception as stream_error:
                    logger.warning(f"[API] 流式执行失败：{stream_error}")
                    _safe_publish_workflow_event(
                        EventType.WORKFLOW_FAILED,
                        thread_id,
                        {"step": "stream", "error": str(stream_error)},
                        user_id=user.id,
                    )
                    yield sse_error_event(code="50001", message=str(stream_error))
                    complete_evt = {"type": "workflow_completed", "data": {"thread_id": thread_id}}
                    yield f"data: {json.dumps(complete_evt, ensure_ascii=False)}\n\n"

            except Exception as e:
                logger.exception("[API] 流式输出失败：")
                _safe_publish_workflow_event(
                    EventType.WORKFLOW_FAILED,
                    thread_id,
                    {"step": "stream", "error": str(e)},
                    user_id=user.id if user else None,
                )
                yield sse_error_event(code="50001", message=str(e))

        return sse_response(event_stream())

    except Exception as e:
        error_msg = str(e)
        logger.exception(f"[API] 流式输出失败：{error_msg}")

        def error_event():
            yield sse_error_event(code="50001", message=error_msg)

        return StreamingHttpResponse(
            error_event(), content_type="text/event-stream", status=500, headers={"Cache-Control": "no-cache"}
        )
