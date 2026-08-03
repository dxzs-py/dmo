"""统一审批 API 视图。

提供单一审批端点，前端不再按 source 分支调用：
- POST /api/v1/approvals/{interrupt_id}/resume/: 恢复审批
- POST /api/v1/approvals/{interrupt_id}/reject/: 拒绝审批
- GET /api/v1/approvals/?source_id=...: 查询审批历史
- GET /api/v1/approvals/{interrupt_id}/: 查询单条审批
- GET /api/v1/approvals/{interrupt_id}/state/: 查询审批当前状态

Path D 架构：
    所有审批统一走 ApprovalGateway 路由：
    - chat → SSE 流式恢复（_stream_chat_resume_generator，HTTP 请求中执行）
    - deep_research → Celery 任务恢复（research_resume_task，worker 中执行）
    前端统一调用 POST /approvals/{interrupt_id}/resume/，无需 source 分支。
"""

import logging

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from Django_xm.apps.approvals.mixins import BaseApprovalAccessMixin
from Django_xm.apps.approvals.models import Approval
from Django_xm.apps.approvals.serializers import (
    ApprovalReadSerializer,
    ApprovalWriteSerializer,
    SSEEventSerializer,
)
from Django_xm.apps.approvals.services import approval_service
from Django_xm.apps.core.throttling import SensitiveOperationRateThrottle
from Django_xm.common.approval_gateway import CircuitBreakerError, gateway
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.responses import error_response, success_response
from Django_xm.common.sse_utils import SSERenderer

logger = logging.getLogger(__name__)


def _json_response(data, status_code=200):
    """构造 DRF Response 并显式选择 JSONRenderer。

    解决 ApprovalResumeView/ApprovalRejectView 的混合响应场景：
    - ``renderer_classes = [JSONRenderer, SSERenderer]`` 允许两种 renderer
    - 前端调用审批端点使用 fetchSSE，发送 ``Accept: text/event-stream``
    - DRF 默认按 Accept 头选择 SSERenderer，导致 ``success_response``/``error_response``
      返回的 JSON 数据被错误渲染为 ``text/event-stream`` content-type

    本函数通过显式设置 ``accepted_renderer``/``accepted_media_type`` 强制使用 JSONRenderer，
    实现"renderer 切换"：waiting/idempotent/error/success 走 JSONRenderer，
    SSE 流式响应走 SSERenderer（通过 ``sse_response`` 返回 ``StreamingHttpResponse``）。
    """
    response = Response(data, status=status_code)
    response.accepted_renderer = JSONRenderer()
    response.accepted_media_type = "application/json"
    response.renderer_context = {}
    return response


def _build_waiting_data(interrupt_id, approval, approved, message=None):
    """构建 waiting_for_others 响应数据。"""
    approval_extra = getattr(approval, "extra", None) or {}
    if not isinstance(approval_extra, dict):
        approval_extra = {}
    return {
        "code": 0,
        "message": message or "审批已记录，等待其他工具审批完成",
        "data": {
            "interrupt_id": interrupt_id,
            "source": approval.source,
            "state": "waiting",
            "status": "waiting_for_others",
            "graph_interrupt_id": approval_extra.get("graph_interrupt_id"),
            "langgraph_resume_id": approval_extra.get("langgraph_resume_id"),
            "approved": approved,
            "message": message or "本工具已审批，等待同批次其他工具审批完成后开始执行...",
        },
    }


def _build_idempotent_data(interrupt_id):
    """构建幂等响应的完整审批状态数据。"""
    try:
        approval = Approval.objects.select_related("approved_by").get(interrupt_id=interrupt_id)
    except Approval.DoesNotExist:
        return {"interrupt_id": interrupt_id, "idempotent": True}

    extra_data = approval.extra or {}
    resume_value = extra_data.get("_resume_value") if isinstance(extra_data, dict) else None

    approved_by_info = None
    if approval.approved_by:
        approved_by_info = {
            "id": approval.approved_by.id,
            "username": approval.approved_by.username,
        }

    idempotent_state = approval.state
    extra_fields = {}
    if idempotent_state == "waiting":
        extra_fields["status"] = "waiting_for_others"
        extra_fields["message"] = "本工具已审批，等待同批次其他工具审批完成后开始执行..."

    return {
        "interrupt_id": interrupt_id,
        "source": approval.source,
        "source_id": approval.source_id,
        "chat_session_id": approval.chat_session_id,
        "state": idempotent_state,
        "resume_value": resume_value,
        "user_input": approval.user_input,
        "approved_by": approved_by_info,
        "tool_name": approval.tool_name,
        "title": approval.title,
        "action": approval.action,
        "danger_level": approval.danger_level,
        "created_at": approval.created_at.isoformat() if approval.created_at else None,
        "resolved_at": approval.resolved_at.isoformat() if approval.resolved_at else None,
        "idempotent": True,
        **extra_fields,
    }


def _check_batch_and_route(request, approval, resume_value, approved, interrupt_id, log_prefix="[ApprovalResume]"):
    """批量审批聚合检查 + Gateway 统一路由（Path D）。

    流程：
    1. 从 approval.extra 读取 graph_interrupt_id 与 langgraph_resume_id
    2. 若是批量场景（graph_interrupt_id 存在且 != interrupt_id）：
       a. 检查同批次是否还有 pending siblings
       b. 有 → 返回 waiting JSON 响应（不触发恢复）
       c. 无 → 构建 batch_resume_value={tool_call_id: bool} 字典
    3. 调用 gateway.route_resume() 统一路由：
       - chat → SSE 流式响应（StreamingHttpResponse）
       - deep_research → Celery 任务恢复（返回 None → JSON 响应）

    Returns:
        Response 对象（JSON 或 SSE 流）
    """
    approval_extra = getattr(approval, "extra", None) or {}
    if not isinstance(approval_extra, dict):
        approval_extra = {}
    graph_interrupt_id = approval_extra.get("graph_interrupt_id")
    langgraph_resume_id = approval_extra.get("langgraph_resume_id")

    effective_resume_value = resume_value

    if graph_interrupt_id and graph_interrupt_id != interrupt_id:
        # 批量审批场景：检查同一 graph_interrupt_id 下所有审批是否完成
        sibling_approvals = Approval.objects.filter(
            extra__graph_interrupt_id=graph_interrupt_id,
            source__in=[Approval.SOURCE_CHAT, Approval.SOURCE_DEEP_RESEARCH],
        )
        # pending 定义：仅 pending 状态（用户尚未点击确认/拒绝）
        pending_siblings = sibling_approvals.exclude(
            state__in=[
                Approval.STATE_PROCESSING,
                Approval.STATE_WAITING,
                Approval.STATE_APPROVED,
                Approval.STATE_REJECTED,
                Approval.STATE_TIMEOUT,
            ]
        )

        if pending_siblings.exists():
            logger.info(
                f"{log_prefix} 聚合恢复等待: graph_interrupt_id={graph_interrupt_id}, "
                f"interrupt_id={interrupt_id}, approved={approved}, "
                f"pending={pending_siblings.count()}"
            )
            return _json_response(_build_waiting_data(interrupt_id, approval, approved))

        # 所有审批都完成，构建 batch_resume_value
        batch_resume_value = {}
        for sib in sibling_approvals:
            sib_extra = sib.extra if isinstance(sib.extra, dict) else {}
            sib_tc_id = sib_extra.get("tool_call_id") or sib.interrupt_id
            if "_approved" in sib_extra:
                batch_resume_value[sib_tc_id] = bool(sib_extra["_approved"])
            else:
                batch_resume_value[sib_tc_id] = sib.state == Approval.STATE_APPROVED

        effective_resume_value = batch_resume_value
        logger.info(
            f"{log_prefix} 聚合恢复触发: graph_interrupt_id={graph_interrupt_id}, "
            f"langgraph_resume_id={langgraph_resume_id}, "
            f"resume_value={effective_resume_value}"
        )

    # 统一路由：Gateway 根据 source 路由到 chat SSE 或 deep_research Celery
    # F3 熔断保护：HIGH 级操作超过滑动窗口阈值时，Gateway 抛出 CircuitBreakerError
    try:
        result = gateway.route_resume(
            request,
            approval,
            effective_resume_value,
            graph_interrupt_id=graph_interrupt_id,
            langgraph_resume_id=langgraph_resume_id,
            approved=approved,
        )
    except CircuitBreakerError as cb_err:
        # 熔断处理：标记审批为 rejected（circuit_broken），以 rejection 恢复 agent
        logger.warning(f"{log_prefix} 熔断触发: {cb_err}, interrupt_id={interrupt_id}, tool_name={approval.tool_name}")
        # 1. 标记审批为 rejected（circuit_broken 原因）
        approval_service.complete_approval(
            interrupt_id=interrupt_id,
            state=Approval.STATE_REJECTED,
            extra={"circuit_broken": True},
        )
        # 2. 以 rejection 恢复 agent（resume_value=False），让 agent 调整策略
        # approved=False 跳过熔断检查，避免递归
        result = gateway.route_resume(
            request,
            approval,
            False,
            graph_interrupt_id=graph_interrupt_id,
            langgraph_resume_id=langgraph_resume_id,
            approved=False,
        )
    except ValueError as ve:
        # 不支持的审批来源（gateway.route_resume 抛出 ValueError）
        # 标记审批为 rejected 释放锁，返回 400 让前端感知非法来源
        logger.warning(
            f"{log_prefix} 路由失败（不支持的来源）: {ve}, interrupt_id={interrupt_id}, source={approval.source}"
        )
        approval_service.complete_approval(
            interrupt_id=interrupt_id,
            state=Approval.STATE_REJECTED,
            extra={"route_error": str(ve)},
        )
        return error_response(code=ErrorCode.VALIDATION_FAILED, message=str(ve))

    if result is None:
        # deep_research → Celery 任务已派发，返回 JSON 响应
        return _json_response(
            {
                "code": 0,
                "message": "研究恢复任务已启动",
                "data": {
                    "interrupt_id": interrupt_id,
                    "source": approval.source,
                    "status": "resumed",
                    "graph_interrupt_id": graph_interrupt_id,
                    "langgraph_resume_id": langgraph_resume_id,
                },
            }
        )
    else:
        # chat → SSE 流式响应（StreamingHttpResponse）
        return result


class ApprovalListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="approvals_list",
        responses={200: ApprovalReadSerializer(many=True)},
    )
    def get(self, request):
        source_id = request.query_params.get("source_id")
        source = request.query_params.get("source")
        chat_session_id = request.query_params.get("chat_session_id")
        state = request.query_params.get("state")

        qs = Approval.objects.filter(user=request.user)
        if source_id:
            qs = qs.filter(source_id=source_id)
        if source:
            qs = qs.filter(source=source)
        if chat_session_id:
            qs = qs.filter(chat_session_id=chat_session_id)
        if state:
            qs = qs.filter(state=state)

        qs = qs.order_by("-created_at")[:100]
        serializer = ApprovalReadSerializer(qs, many=True)
        return success_response(data=serializer.data)


class ApprovalDetailView(BaseApprovalAccessMixin, APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: ApprovalReadSerializer})
    def get(self, request, interrupt_id):
        try:
            approval = Approval.objects.get(interrupt_id=interrupt_id)
        except Approval.DoesNotExist:
            return error_response(code=ErrorCode.NOT_FOUND, message="审批不存在")
        self._assert_ownership(approval, request.user)
        serializer = ApprovalReadSerializer(approval)
        return success_response(data=serializer.data)


class ApprovalResumeView(BaseApprovalAccessMixin, APIView):
    """恢复审批：通过 ApprovalGateway 统一路由（Path D）。

    chat → SSE 流式恢复，deep_research → Celery 任务恢复。
    前端无需判断 source，统一调用此端点。
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [SensitiveOperationRateThrottle]
    renderer_classes = [JSONRenderer, SSERenderer]

    @extend_schema(
        request=ApprovalWriteSerializer,
        responses={
            (200, "application/json"): ApprovalReadSerializer,
            (200, "text/event-stream"): OpenApiResponse(
                response=SSEEventSerializer,
                description="SSE 流式响应（审批恢复事件流）",
            ),
        },
    )
    def post(self, request, interrupt_id):
        write_serializer = ApprovalWriteSerializer(data=request.data)
        if not write_serializer.is_valid():
            return error_response(
                code=ErrorCode.VALIDATION_FAILED,
                message="请求参数校验失败",
                data={"details": write_serializer.errors},
            )
        validated = write_serializer.validated_data

        try:
            approval = Approval.objects.get(interrupt_id=interrupt_id)
        except Approval.DoesNotExist:
            approval = None
        else:
            self._assert_ownership(approval, request.user)

        approved = validated.get("approved", True)
        user_input = validated.get("user_input")

        try:
            result = approval_service.resume_approval(
                interrupt_id=interrupt_id,
                approved=approved,
                user_input=user_input,
                approved_by=request.user,
            )
        except ValueError as e:
            return error_response(code=ErrorCode.VALIDATION_FAILED, message=str(e))

        approval = result["approval"]

        logger.info(
            f"[ApprovalResumeView] result: interrupt_id={interrupt_id}, "
            f"state={result.get('state')}, idempotent={result.get('idempotent')}, "
            f"not_found={result.get('not_found')}, "
            f"graph_interrupt_id={approval.extra.get('graph_interrupt_id') if approval and approval.extra else 'N/A'}"
        )
        resume_value = result["resume_value"]
        is_idempotent = result.get("idempotent", False)
        not_found = result.get("not_found", False)

        if not_found or approval is None:
            return error_response(code=ErrorCode.NOT_FOUND, message="审批不存在")

        # 幂等响应
        if is_idempotent:
            idempotent_data = _build_idempotent_data(interrupt_id)
            if idempotent_data.get("state") == "waiting":
                return _json_response(
                    {
                        "code": 0,
                        "message": "审批已处理（幂等）",
                        "data": idempotent_data,
                    }
                )
            return success_response(data=idempotent_data)

        # service 层检测到同批次还有其他 pending，返回 waiting 状态
        if result.get("state") == "waiting":
            return _json_response(_build_waiting_data(interrupt_id, approval, approved))

        # 统一路由：Gateway 根据 source 路由到 chat SSE 或 deep_research Celery
        return _check_batch_and_route(
            request,
            approval,
            resume_value,
            approved,
            interrupt_id,
            log_prefix="[ApprovalResume]",
        )


class ApprovalRejectView(BaseApprovalAccessMixin, APIView):
    """拒绝审批：通过 ApprovalGateway 统一路由（Path D）。

    chat → SSE 流式恢复，deep_research → Celery 任务恢复。
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [SensitiveOperationRateThrottle]
    renderer_classes = [JSONRenderer, SSERenderer]

    @extend_schema(
        request=ApprovalWriteSerializer,
        responses={
            (200, "application/json"): ApprovalReadSerializer,
            (200, "text/event-stream"): OpenApiResponse(
                response=SSEEventSerializer,
                description="SSE 流式响应（审批拒绝后恢复事件流）",
            ),
        },
    )
    def post(self, request, interrupt_id):
        write_serializer = ApprovalWriteSerializer(data=request.data)
        if not write_serializer.is_valid():
            return error_response(
                code=ErrorCode.VALIDATION_FAILED,
                message="请求参数校验失败",
                data={"details": write_serializer.errors},
            )

        try:
            approval = Approval.objects.get(interrupt_id=interrupt_id)
        except Approval.DoesNotExist:
            approval = None
        else:
            self._assert_ownership(approval, request.user)

        try:
            result = approval_service.resume_approval(
                interrupt_id=interrupt_id,
                approved=False,
                approved_by=request.user,
            )
        except ValueError as e:
            return error_response(code=ErrorCode.VALIDATION_FAILED, message=str(e))

        approval = result["approval"]
        resume_value = result["resume_value"]
        is_idempotent = result.get("idempotent", False)
        not_found = result.get("not_found", False)

        if not_found or approval is None:
            return error_response(code=ErrorCode.NOT_FOUND, message="审批不存在")

        # 幂等响应
        if is_idempotent:
            idempotent_data = _build_idempotent_data(interrupt_id)
            if idempotent_data.get("state") == "waiting":
                return _json_response(
                    {
                        "code": 0,
                        "message": "审批已处理（幂等）",
                        "data": idempotent_data,
                    }
                )
            return success_response(data=idempotent_data)

        # service 层检测到同批次还有其他 pending，返回 waiting 状态
        if result.get("state") == "waiting":
            return _json_response(
                _build_waiting_data(
                    interrupt_id,
                    approval,
                    False,
                    message="本工具已拒绝，等待同批次其他工具审批完成后开始执行...",
                )
            )

        # 统一路由：Gateway 根据 source 路由到 chat SSE 或 deep_research Celery
        return _check_batch_and_route(
            request,
            approval,
            resume_value,
            False,
            interrupt_id,
            log_prefix="[ApprovalReject]",
        )


class ApprovalStateView(BaseApprovalAccessMixin, APIView):
    """查询审批当前状态。

    GET /api/v1/approvals/{interrupt_id}/state/
    返回指定审批的当前状态（state、resume_value 等），供前端轮询或断线恢复时使用。
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(exclude=True)
    def get(self, request, interrupt_id):
        try:
            approval = Approval.objects.select_related("approved_by").get(interrupt_id=interrupt_id)
        except Approval.DoesNotExist:
            return error_response(code=ErrorCode.NOT_FOUND, message="审批不存在")

        if not self._user_owns_approval(request.user, approval):
            return error_response(code=ErrorCode.PERMISSION_DENIED, message="无权查看该审批")

        extra_data = approval.extra or {}
        resume_value = extra_data.get("_resume_value") if isinstance(extra_data, dict) else None

        approved_by_info = None
        if approval.approved_by:
            approved_by_info = {
                "id": approval.approved_by.id,
                "username": approval.approved_by.username,
            }

        data = {
            "interrupt_id": approval.interrupt_id,
            "source": approval.source,
            "source_id": approval.source_id,
            "chat_session_id": approval.chat_session_id,
            "state": approval.state,
            "resume_value": resume_value,
            "user_input": approval.user_input,
            "approved_by": approved_by_info,
            "tool_name": approval.tool_name,
            "title": approval.title,
            "description": approval.description,
            "action": approval.action,
            "operation": approval.operation,
            "danger_level": approval.danger_level,
            "parameters": approval.parameters,
            "created_at": approval.created_at.isoformat() if approval.created_at else None,
            "resolved_at": approval.resolved_at.isoformat() if approval.resolved_at else None,
        }
        return success_response(data=data)


class ApprovalMetricsView(APIView):
    """审批可观测性指标端点（F2）。

    GET /api/v1/approvals/metrics/
    返回审批体系的实时指标快照，供监控系统与运维面板使用。

    指标包含：
    - pending_count: 当前待审批数
    - total: 累计创建/通过/拒绝/超时/自动通过数
    - risk_distribution: 风险等级分布（safe/controlled/high）
    - sandbox: 沙箱执行成功率
    - latency: 平均审批耗时
    - thresholds: 熔断阈值配置
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(exclude=True)
    def get(self, request):
        from Django_xm.common.observability.approval_metrics import approval_metrics

        snapshot = approval_metrics.snapshot()
        return success_response(data=snapshot)
