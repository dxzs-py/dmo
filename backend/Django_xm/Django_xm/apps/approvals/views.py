"""统一审批 API 视图。

提供单一审批端点，前端不再按 source 分支调用：
- POST /api/v1/approvals/{interrupt_id}/resume/: 恢复审批（拒绝语义由 approved=false 承载）

Path D 架构：
    所有审批统一走 ApprovalGateway 路由：
    - chat / deep_research → Redis 信令唤醒执行服务挂起协程（事件驱动，DB 最终一致）
    前端统一调用 POST /approvals/{interrupt_id}/resume/，无需 source 分支。
"""

import logging

from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from Django_xm.apps.approvals.mixins import BaseApprovalAccessMixin
from Django_xm.apps.approvals.models import Approval
from Django_xm.apps.approvals.serializers import ApprovalWriteSerializer
from Django_xm.apps.approvals.services import approval_service
from Django_xm.apps.approvals.services.approval_gateway import CircuitBreakerError, gateway
from Django_xm.apps.core.throttling import SensitiveOperationRateThrottle
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.exceptions import BaseAppError
from Django_xm.common.observability import approval_metrics
from Django_xm.common.responses import success_response

logger = logging.getLogger(__name__)


def _json_response(data, status_code=200):
    """构造 DRF Response 并显式选择 JSONRenderer（审批端点统一 JSON 响应）。"""
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
    elif idempotent_state == "timeout":
        # N-1 组合项③：审批超时后用户确认被幂等丢弃，响应明示原因
        # （service 层已同步记 warning 日志；不新增事件类型，前端按 state 分支展示）
        extra_fields["message"] = "审批已超时，此次确认未生效"

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


def _check_batch_and_route(
    request,
    approval,
    resume_value,
    approved,
    interrupt_id,
    log_prefix="[ApprovalResume]",
):
    """批量审批聚合检查 + Gateway 统一路由（Path D）。

    流程：
    1. 从 approval.extra 读取 graph_interrupt_id 与 langgraph_resume_id
    2. 若是批量场景（graph_interrupt_id 存在且 != interrupt_id）：
       a. 检查同批次是否还有 pending siblings
       b. 有 → 返回 waiting JSON 响应（不触发恢复）
       c. 无 → 构建 batch_resume_value={tool_call_id: bool} 字典
    3. 调用 gateway.route_resume() 统一路由：
       - chat / deep_research → Redis 信令唤醒执行服务（返回 None → JSON 响应）

    Returns:
        Response 对象（JSON）
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
            source__in=[Approval.Source.CHAT, Approval.Source.DEEP_RESEARCH],
        )
        # pending 定义：仅 pending 状态（用户尚未点击确认/拒绝）
        pending_siblings = sibling_approvals.exclude(
            state__in=[
                Approval.State.PROCESSING,
                Approval.State.WAITING,
                Approval.State.APPROVED,
                Approval.State.REJECTED,
                Approval.State.TIMEOUT,
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
        # 优先使用 _resume_value（保留"审批超时"=TIMEOUT_DECISION 与"用户拒绝"=False 的语义差异，
        # 供 ApprovalMiddleware 注入不同的 ToolMessage）；缺失时回退 _approved 布尔。
        batch_resume_value = {}
        for sib in sibling_approvals:
            sib_extra = sib.extra if isinstance(sib.extra, dict) else {}
            sib_tc_id = sib_extra.get("tool_call_id") or sib.interrupt_id
            if "_resume_value" in sib_extra:
                batch_resume_value[sib_tc_id] = sib_extra["_resume_value"]
            elif "_approved" in sib_extra:
                batch_resume_value[sib_tc_id] = bool(sib_extra["_approved"])
            else:
                batch_resume_value[sib_tc_id] = sib.state == Approval.State.APPROVED

        effective_resume_value = batch_resume_value
        logger.info(
            f"{log_prefix} 聚合恢复触发: graph_interrupt_id={graph_interrupt_id}, "
            f"langgraph_resume_id={langgraph_resume_id}, "
            f"resume_value={effective_resume_value}"
        )

    # 统一路由：Gateway 根据 source 路由到 Redis 信令唤醒执行服务
    # F3 熔断保护：HIGH 级操作超过滑动窗口阈值时，Gateway 抛出 CircuitBreakerError
    try:
        gateway.route_resume(
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
            state=Approval.State.REJECTED,
            extra={"circuit_broken": True},
        )
        # 2. 以 rejection 恢复 agent（resume_value=False），让 agent 调整策略
        # approved=False 跳过熔断检查，避免递归
        gateway.route_resume(
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
            state=Approval.State.REJECTED,
            extra={"route_error": str(ve)},
        )
        raise BaseAppError(str(ve), business_code=ErrorCode.VALIDATION_FAILED) from ve

    # chat / deep_research 均已发布 Redis 信令，返回 JSON 响应
    return _json_response(
        {
            "code": 0,
            "message": "审批恢复任务已启动",
            "data": {
                "interrupt_id": interrupt_id,
                "source": approval.source,
                "status": "resumed",
                "graph_interrupt_id": graph_interrupt_id,
                "langgraph_resume_id": langgraph_resume_id,
            },
        }
    )


class ApprovalResumeView(BaseApprovalAccessMixin, APIView):
    """恢复审批：通过 ApprovalGateway 统一路由（Path D）。

    chat / deep_research → Redis 信令唤醒执行服务。
    前端无需判断 source，统一调用此端点。
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [SensitiveOperationRateThrottle]
    renderer_classes = [JSONRenderer]

    @extend_schema(
        request=ApprovalWriteSerializer,
        responses={
            200: {
                "type": "object",
                "properties": {
                    "code": {"type": "integer", "example": 0},
                    "message": {"type": "string", "example": "审批恢复任务已启动"},
                    "data": {
                        "type": "object",
                        "properties": {
                            "interrupt_id": {"type": "string"},
                            "source": {"type": "string"},
                            "status": {"type": "string", "example": "resumed"},
                            "graph_interrupt_id": {"type": "string"},
                            "langgraph_resume_id": {"type": "string"},
                        },
                    },
                },
            }
        },
    )
    def post(self, request, interrupt_id):
        write_serializer = ApprovalWriteSerializer(data=request.data)
        if not write_serializer.is_valid():
            raise BaseAppError(
                "请求参数校验失败",
                business_code=ErrorCode.VALIDATION_FAILED,
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
        except ValueError:
            # resume_approval 的 ValueError 均为内部契约错误（payload scope 非法 / 缺 session_id 等），
            # 细节只进日志，不向客户端回显
            logger.exception(f"[ApprovalResumeView] resume_approval 内部契约错误: interrupt_id={interrupt_id}")
            raise BaseAppError("请求处理失败，请稍后重试", business_code=ErrorCode.VALIDATION_FAILED) from None

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
            raise NotFound("审批不存在")

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

        # 统一路由：Gateway 根据 source 路由到 Redis 信令唤醒执行服务
        return _check_batch_and_route(
            request,
            approval,
            resume_value,
            approved,
            interrupt_id,
            log_prefix="[ApprovalResume]",
        )


class ApprovalMetricsSnapshotView(APIView):
    """审批监控指标快照（管理员专用，Spec D3）。

    暴露 approval_metrics.snapshot() 全量计数（写时累加 + 读时聚合）：
    - pending_count / total（created/approved/rejected/timeout/auto_approved）
    - risk_distribution（safe/controlled/high）
    - sandbox（success/failure/success_rate）
    - high_risk_windows（F3 熔断窗口聚合） / latency / thresholds

    Redis 不可用时指标采集器静默降级，返回全 0 快照而非抛异常。
    """

    permission_classes = [IsAdminUser]

    @extend_schema(
        responses={
            200: {
                "type": "object",
                "properties": {
                    "code": {"type": "integer", "example": 200},
                    "message": {"type": "string"},
                    "data": {
                        "type": "object",
                        "properties": {
                            "pending_count": {"type": "integer"},
                            "total": {
                                "type": "object",
                                "properties": {
                                    "created": {"type": "integer"},
                                    "approved": {"type": "integer"},
                                    "rejected": {"type": "integer"},
                                    "timeout": {"type": "integer"},
                                    "auto_approved": {"type": "integer"},
                                },
                            },
                            "risk_distribution": {
                                "type": "object",
                                "properties": {
                                    "safe": {"type": "integer"},
                                    "controlled": {"type": "integer"},
                                    "high": {"type": "integer"},
                                },
                            },
                            "sandbox": {
                                "type": "object",
                                "properties": {
                                    "success": {"type": "integer"},
                                    "failure": {"type": "integer"},
                                    "success_rate": {"type": "number", "nullable": True},
                                },
                            },
                            "high_risk_windows": {"type": "object"},
                            "latency": {"type": "object"},
                            "thresholds": {"type": "object"},
                        },
                    },
                },
            }
        },
    )
    def get(self, request):
        return success_response(data=approval_metrics.snapshot())
