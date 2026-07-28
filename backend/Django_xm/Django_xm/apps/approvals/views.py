"""统一审批 API 视图。

提供单一审批端点，前端不再按 source 分支调用：
- POST /api/v1/approvals/{interrupt_id}/resume/: 恢复审批
- POST /api/v1/approvals/{interrupt_id}/reject/: 拒绝审批
- GET /api/v1/approvals/?source_id=...: 查询审批历史
- GET /api/v1/approvals/{interrupt_id}/: 查询单条审批
- GET /api/v1/approvals/{interrupt_id}/state/: 查询审批当前状态
"""

import logging

from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from Django_xm.apps.approvals.mixins import BaseApprovalAccessMixin
from Django_xm.apps.approvals.models import Approval
from Django_xm.apps.approvals.serializers import (
    ApprovalReadSerializer,
    ApprovalWriteSerializer,
)
from Django_xm.apps.approvals.services import approval_service
from Django_xm.apps.core.throttling import SensitiveOperationRateThrottle
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

    相比 ``JsonResponse`` 直接绕过 DRF，本方式保留 DRF Response 的中间件链路、
    异常处理与协商上下文，符合 DRF 设计理念。

    Args:
        data: 响应数据（dict）
        status_code: HTTP 状态码，默认 200

    Returns:
        配置好 JSONRenderer 的 DRF Response 对象
    """
    response = Response(data, status=status_code)
    response.accepted_renderer = JSONRenderer()
    response.accepted_media_type = 'application/json'
    response.renderer_context = {}
    return response


def _build_waiting_data(interrupt_id, approval, approved, message=None):
    """构建 waiting_for_others 响应数据。

    返回数据结构统一为 {code, message, data}，与 ``success_response`` 一致。
    前端通过 ``response.ok`` + ``response.json()`` 解析，识别 ``status='waiting_for_others'``
    触发"等待同批次"提示。

    Args:
        interrupt_id: 审批中断 ID
        approval: Approval 模型实例
        approved: 用户实际决策（True=确认, False=拒绝）
        message: 自定义提示消息（可选）

    Returns:
        dict: ``{code, message, data}`` 结构，可直接传给 ``_json_response``
    """
    approval_extra = getattr(approval, 'extra', None) or {}
    if not isinstance(approval_extra, dict):
        approval_extra = {}
    return {
        'code': 0,
        'message': message or '审批已记录，等待其他工具审批完成',
        'data': {
            'interrupt_id': interrupt_id,
            'source': approval.source,
            'state': 'waiting',
            'status': 'waiting_for_others',
            'graph_interrupt_id': approval_extra.get('graph_interrupt_id'),
            'langgraph_resume_id': approval_extra.get('langgraph_resume_id'),
            'approved': approved,
            'message': message or '本工具已审批，等待同批次其他工具审批完成后开始执行...',
        }
    }


def _build_idempotent_data(interrupt_id):
    """构建幂等响应的完整审批状态数据。

    重新查询 DB 获取最新状态，确保前端无需额外请求即可更新本地状态。
    返回足够完整的字段，供前端判断审批实际状态并同步 UI。
    """
    try:
        approval = Approval.objects.select_related('approved_by').get(interrupt_id=interrupt_id)
    except Approval.DoesNotExist:
        return {'interrupt_id': interrupt_id, 'idempotent': True}

    extra_data = approval.extra or {}
    resume_value = extra_data.get('_resume_value') if isinstance(extra_data, dict) else None

    approved_by_info = None
    if approval.approved_by:
        approved_by_info = {
            'id': approval.approved_by.id,
            'username': approval.approved_by.username,
        }

    # 统一幂等返回与正常返回格式：waiting 状态补充 status 和 message
    # 前端 _executeChatApproval 只需检查 status='waiting_for_others'，无需兼容 state
    idempotent_state = approval.state
    extra_fields = {}
    if idempotent_state == 'waiting':
        extra_fields['status'] = 'waiting_for_others'
        extra_fields['message'] = '本工具已审批，等待同批次其他工具审批完成后开始执行...'

    return {
        'interrupt_id': interrupt_id,
        'source': approval.source,
        'source_id': approval.source_id,
        'chat_session_id': approval.chat_session_id,
        'state': idempotent_state,
        'resume_value': resume_value,
        'user_input': approval.user_input,
        'approved_by': approved_by_info,
        'tool_name': approval.tool_name,
        'title': approval.title,
        'action': approval.action,
        'danger_level': approval.danger_level,
        'created_at': approval.created_at.isoformat() if approval.created_at else None,
        'resolved_at': approval.resolved_at.isoformat() if approval.resolved_at else None,
        'idempotent': True,
        **extra_fields,
    }


async def _stream_learning_resume_generator(request, approval, resume_value):
    """[已废弃] Learning 工作流审批恢复的异步 SSE 生成器。

    learning 模块是纯学习评测工作流（StateGraph），无工具调用、无 agent、
    不接入 ApprovalMiddleware（架构不匹配：ApprovalMiddleware 需挂载在
    create_react_agent 的 after_model 钩子，learning 无此机制）。

    原实现引用了 learning 模块中不存在的
    `_publish_learning_tool_events` / `_detect_and_handle_learning_approval_interrupt`
    函数，一旦 source='learning' 的 Approval 触发恢复流程会立即 ImportError。

    此函数保留为占位，仅用于在 ApprovalResumeView/ApprovalRejectView 中
    提供清晰的错误提示。若未来 learning 模块引入 agent 化改造并需要审批，
    应重新实现此函数及 learning 侧的辅助函数。
    """
    from Django_xm.common.sse_utils import sse_error_event
    yield sse_error_event(
        code="50001",
        message=(
            "Learning 模块当前不接入 ApprovalMiddleware 审批机制"
            "（learning 是纯学习评测工作流，无工具调用）。"
            "若需启用，需先将 learning 改造为 agent 形态。"
        ),
    )


def _stream_chat_resume_sse(request, approval, resume_value, session_id, approved,
                            graph_interrupt_id=None, langgraph_resume_id=None,
                            log_prefix='[ApprovalResume]'):
    """创建 SSE 流恢复 chat agent 执行。

    复用 views_chat.py 中的 _stream_chat_resume_generator 实现，统一处理：
    - regenerate 场景：路由到 stream_regenerate_resume
    - chat / deep_research 场景：路由到 _stream_chat_resume_generator（in-process SSE 流）

    Args:
        request: HTTP 请求（用于读取模型/工具配置）
        approval: Approval 模型实例
        resume_value: 恢复值（True/False/user_input，或批量场景的 {tool_call_id: bool}）
        session_id: 会话 ID
        approved: 是否批准
        graph_interrupt_id: 批次 UUID（从 _meta.graph_interrupt_id 读取，用于 DB 查询与前端 grouping）；
                           若为 None 则回退到 approval.interrupt_id
        langgraph_resume_id: LangGraph 恢复 ID（= intr.id，作为 Command(resume=...) 的 KEY）；
                             若为 None 则回退到 approval.interrupt_id
        log_prefix: 日志前缀，用于区分 resume / reject 调用路径
    """
    # 延迟导入以避免循环依赖（views_chat.py 导入了 approvals.services）
    from Django_xm.apps.chat.views_chat import _stream_chat_resume_generator
    from Django_xm.common.sse_utils import sse_async_heartbeat_generator, sse_response

    extra = getattr(approval, 'extra', None) or {}
    regen_info = extra.get('regenerate') if isinstance(extra, dict) else None

    if isinstance(regen_info, dict) and regen_info.get('thread_id'):
        from Django_xm.apps.chat.services.regenerate_service import stream_regenerate_resume
        logger.info(
            f"{log_prefix} regenerate审批恢复: "
            f"thread={regen_info.get('thread_id')}, session={session_id}, "
            f"graph_interrupt_id={graph_interrupt_id}, langgraph_resume_id={langgraph_resume_id}"
        )
        request_data = dict(request.data) if hasattr(request, 'data') else {}
        return sse_response(
            sse_async_heartbeat_generator(
                stream_regenerate_resume(
                    request, approval, resume_value, session_id,
                    request_data=request_data,
                    graph_interrupt_id=graph_interrupt_id,
                    langgraph_resume_id=langgraph_resume_id,
                )
            )
        )

    # chat / deep_research 审批恢复：in-process SSE 流（复用 views_chat.py 实现）
    # Command(resume=...) 的 KEY 必须是 LangGraph 真正的 interrupt_id（intr.id），
    # 而非批次 UUID（graph_interrupt_id）或 tool_call_id（approval.interrupt_id）。
    effective_resume_key = langgraph_resume_id or approval.interrupt_id
    logger.info(
        f"{log_prefix} chat审批恢复: session={session_id}, "
        f"interrupt_id={approval.interrupt_id}, graph_interrupt_id={graph_interrupt_id}, "
        f"langgraph_resume_id={langgraph_resume_id}, "
        f"effective_resume_key={effective_resume_key}, source={approval.source}, "
        f"approved={approved}"
    )
    request_data = dict(request.data) if hasattr(request, 'data') else {}
    return sse_response(
        sse_async_heartbeat_generator(
            _stream_chat_resume_generator(
                request, approval, resume_value, session_id, request_data,
                graph_interrupt_id=graph_interrupt_id,
                langgraph_resume_id=langgraph_resume_id,
            )
        )
    )


def _aggregate_batch_resume(request, approval, resume_value, session_id, approved,
                            interrupt_id, log_prefix):
    """批量审批聚合恢复检查（chat / deep_research 共用）。

    聚合恢复逻辑：
    1. 从 approval.extra 读取 graph_interrupt_id 与 langgraph_resume_id
    2. 若 graph_interrupt_id 存在且不等于当前 interrupt_id，说明是批量审批场景
    3. 检查同一 graph_interrupt_id 下是否还有 pending siblings
       - 有：返回 waiting 响应（不触发 LangGraph 恢复）
       - 无：构建 resume_value={tool_call_id: bool} 字典，触发聚合恢复流
    4. 否则（单个审批场景），直接调用 _stream_chat_resume_sse

    Returns:
        Response 对象（success_response 或 sse_response）。
    """
    approval_extra = getattr(approval, 'extra', None) or {}
    if not isinstance(approval_extra, dict):
        approval_extra = {}
    graph_interrupt_id = approval_extra.get('graph_interrupt_id')
    langgraph_resume_id = approval_extra.get('langgraph_resume_id')

    if graph_interrupt_id and graph_interrupt_id != interrupt_id:
        # 批量审批场景：检查同一 graph_interrupt_id 下所有审批是否完成
        # 修复 Bug 3&4：原仅过滤 source='chat'，深度研究模式审批被遗漏
        sibling_approvals = Approval.objects.filter(
            extra__graph_interrupt_id=graph_interrupt_id,
            source__in=[Approval.SOURCE_CHAT, Approval.SOURCE_DEEP_RESEARCH],
        )
        # pending 定义：仅 pending 状态（用户尚未点击确认/拒绝）
        # processing/waiting/approved/rejected/timeout 都视为"已处理"
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
            # 还有未审批的工具，返回等待状态（不触发 LangGraph 恢复）
            # 通过 _json_response 显式选择 JSONRenderer，确保 content-type=application/json，
            # 前端 _executeChatApproval 能正确识别 waiting_for_others 响应
            logger.info(
                f"{log_prefix} 聚合恢复等待: graph_interrupt_id={graph_interrupt_id}, "
                f"langgraph_resume_id={langgraph_resume_id}, "
                f"interrupt_id={interrupt_id}, approved={approved}, "
                f"pending={pending_siblings.count()}"
            )
            return _json_response(_build_waiting_data(interrupt_id, approval, approved))

        # 所有审批都完成，触发聚合恢复流
        # 构建 resume_value = {tool_call_id: bool, ...} 字典
        # 注意：审批状态可能是 processing（刚点击确认）或 approved（已完成），
        # 所以使用 extra._approved 标志判断用户实际决策
        batch_resume_value = {}
        for sib in sibling_approvals:
            sib_extra = sib.extra if isinstance(sib.extra, dict) else {}
            sib_tc_id = sib_extra.get('tool_call_id') or sib.interrupt_id
            # 优先使用 extra._approved（由 resume_approval 设置），回退到状态判断
            if '_approved' in sib_extra:
                batch_resume_value[sib_tc_id] = bool(sib_extra['_approved'])
            else:
                batch_resume_value[sib_tc_id] = (sib.state == Approval.STATE_APPROVED)

        logger.info(
            f"{log_prefix} 聚合恢复触发: graph_interrupt_id={graph_interrupt_id}, "
            f"langgraph_resume_id={langgraph_resume_id}, "
            f"resume_value={batch_resume_value}"
        )
        return _stream_chat_resume_sse(
            request, approval, batch_resume_value, session_id, approved,
            graph_interrupt_id=graph_interrupt_id,
            langgraph_resume_id=langgraph_resume_id,
            log_prefix=log_prefix,
        )

    # 单个审批场景（无 graph_interrupt_id 或 graph_interrupt_id == interrupt_id），直接恢复
    return _stream_chat_resume_sse(
        request, approval, resume_value, session_id, approved,
        graph_interrupt_id=graph_interrupt_id,
        langgraph_resume_id=langgraph_resume_id,
        log_prefix=log_prefix,
    )


class ApprovalListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="approvals_list",
        responses={200: ApprovalReadSerializer(many=True)},
    )
    def get(self, request):
        source_id = request.query_params.get('source_id')
        source = request.query_params.get('source')
        chat_session_id = request.query_params.get('chat_session_id')
        state = request.query_params.get('state')

        # 越权修复：直接按 user 字段过滤，无需跨表 JOIN
        qs = Approval.objects.filter(user=request.user)
        if source_id:
            qs = qs.filter(source_id=source_id)
        if source:
            qs = qs.filter(source=source)
        if chat_session_id:
            qs = qs.filter(chat_session_id=chat_session_id)
        if state:
            qs = qs.filter(state=state)

        qs = qs.order_by('-created_at')[:100]
        serializer = ApprovalReadSerializer(qs, many=True)
        return success_response(data=serializer.data)


class ApprovalDetailView(BaseApprovalAccessMixin, APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: ApprovalReadSerializer})
    def get(self, request, interrupt_id):
        try:
            approval = Approval.objects.get(interrupt_id=interrupt_id)
        except Approval.DoesNotExist:
            return error_response(code=ErrorCode.NOT_FOUND, message='审批不存在')
        self._assert_ownership(approval, request.user)
        serializer = ApprovalReadSerializer(approval)
        return success_response(data=serializer.data)


class ApprovalResumeView(BaseApprovalAccessMixin, APIView):
    """恢复审批：chat/deep_research 均返回 SSE 流式响应。"""

    permission_classes = [IsAuthenticated]
    throttle_classes = [SensitiveOperationRateThrottle]
    renderer_classes = [JSONRenderer, SSERenderer]

    @extend_schema(
        request=ApprovalWriteSerializer,
        responses={(200, 'application/json'): ApprovalReadSerializer, (200, 'text/event-stream'): None},
    )
    def post(self, request, interrupt_id):
        # 请求体校验：白名单 serializer，拒绝客户端设置 state/parameters 等字段
        write_serializer = ApprovalWriteSerializer(data=request.data)
        if not write_serializer.is_valid():
            return error_response(
                code=ErrorCode.VALIDATION_FAILED,
                message='请求参数校验失败',
                data={'details': write_serializer.errors},
            )
        validated = write_serializer.validated_data

        # 越权校验：审批存在时断言归属；不存在则跳过，交由 service 走 not_found 流程
        try:
            approval = Approval.objects.get(interrupt_id=interrupt_id)
        except Approval.DoesNotExist:
            approval = None
        else:
            self._assert_ownership(approval, request.user)

        approved = validated.get('approved', True)
        user_input = validated.get('user_input')

        try:
            result = approval_service.resume_approval(
                interrupt_id=interrupt_id,
                approved=approved,
                user_input=user_input,
                approved_by=request.user,
            )
        except ValueError as e:
            return error_response(code=ErrorCode.VALIDATION_FAILED, message=str(e))

        approval = result['approval']
        resume_value = result['resume_value']
        is_idempotent = result.get('idempotent', False)
        not_found = result.get('not_found', False)

        if not_found or approval is None:
            return error_response(code=ErrorCode.NOT_FOUND, message='审批不存在')

        # chat 和 deep_research 均走 SSE 流式恢复
        if approval.source in (Approval.SOURCE_CHAT, Approval.SOURCE_DEEP_RESEARCH):
            if is_idempotent:
                # 幂等响应：waiting 状态需显式选择 JSONRenderer，确保前端识别 application/json
                idempotent_data = _build_idempotent_data(interrupt_id)
                if idempotent_data.get('state') == 'waiting':
                    return _json_response({
                        'code': 0,
                        'message': '审批已处理（幂等）',
                        'data': idempotent_data,
                    })
                return success_response(data=idempotent_data)

            # 批量审批场景：service 层已检测到同批次还有其他 pending，返回 waiting 状态
            # 通过 _json_response 显式选择 JSONRenderer，确保前端识别 application/json
            if result.get('state') == 'waiting':
                return _json_response(_build_waiting_data(interrupt_id, approval, approved))

            session_id = approval.chat_session_id or approval.source_id
            # 聚合恢复检查（含 graph_interrupt_id 提取、sibling 检查、批量 resume_value 构建）
            return _aggregate_batch_resume(
                request, approval, resume_value, session_id, approved,
                interrupt_id, log_prefix='[ApprovalResume]',
            )

        # learning 工作流审批恢复
        if approval.source == Approval.SOURCE_LEARNING:
            if is_idempotent:
                # 幂等响应：waiting 状态需显式选择 JSONRenderer，确保前端识别 application/json
                idempotent_data = _build_idempotent_data(interrupt_id)
                if idempotent_data.get('state') == 'waiting':
                    return _json_response({
                        'code': 0,
                        'message': '审批已处理（幂等）',
                        'data': idempotent_data,
                    })
                return success_response(data=idempotent_data)
            # 批量审批场景：同批次还有其他 pending，不触发恢复，返回 waiting 状态
            # 通过 _json_response 显式选择 JSONRenderer，确保前端识别 application/json
            if result.get('state') == 'waiting':
                return _json_response(_build_waiting_data(interrupt_id, approval, approved))
            from Django_xm.common.sse_utils import sse_async_heartbeat_generator, sse_response
            return sse_response(
                sse_async_heartbeat_generator(
                    _stream_learning_resume_generator(request, approval, resume_value)
                )
            )

        # 其他未知 source
        try:
            approval_service.complete_approval(
                interrupt_id, Approval.STATE_REJECTED,
                extra={'reason': 'unknown_source'},
            )
        except Exception as complete_err:
            logger.error(f"[ApprovalResumeView] 释放未知 source 审批锁失败: {complete_err}")
        return error_response(
            code=ErrorCode.VALIDATION_FAILED,
            message=f'不支持的审批来源: {approval.source}',
        )


class ApprovalRejectView(BaseApprovalAccessMixin, APIView):
    """拒绝审批：chat/deep_research 均走 SSE 流式恢复。"""

    permission_classes = [IsAuthenticated]
    throttle_classes = [SensitiveOperationRateThrottle]
    renderer_classes = [JSONRenderer, SSERenderer]

    @extend_schema(
        request=ApprovalWriteSerializer,
        responses={(200, 'application/json'): ApprovalReadSerializer, (200, 'text/event-stream'): None},
    )
    def post(self, request, interrupt_id):
        # 请求体校验：白名单 serializer，拒绝客户端设置 state/parameters 等字段
        # 即使 reject 接口忽略 approved 字段（强制 False），仍校验请求体防御性深度
        write_serializer = ApprovalWriteSerializer(data=request.data)
        if not write_serializer.is_valid():
            return error_response(
                code=ErrorCode.VALIDATION_FAILED,
                message='请求参数校验失败',
                data={'details': write_serializer.errors},
            )

        # 越权校验：审批存在时断言归属；不存在则跳过，交由 service 走 not_found 流程
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

        approval = result['approval']
        resume_value = result['resume_value']
        is_idempotent = result.get('idempotent', False)
        not_found = result.get('not_found', False)

        if not_found or approval is None:
            return error_response(code=ErrorCode.NOT_FOUND, message='审批不存在')

        # chat 和 deep_research 均走 SSE 流式恢复
        if approval.source in (Approval.SOURCE_CHAT, Approval.SOURCE_DEEP_RESEARCH):
            if is_idempotent:
                # 幂等响应：waiting 状态需显式选择 JSONRenderer，确保前端识别 application/json
                idempotent_data = _build_idempotent_data(interrupt_id)
                if idempotent_data.get('state') == 'waiting':
                    return _json_response({
                        'code': 0,
                        'message': '审批已处理（幂等）',
                        'data': idempotent_data,
                    })
                return success_response(data=idempotent_data)

            # 批量审批场景：service 层已检测到同批次还有其他 pending，返回 waiting 状态
            # 通过 _json_response 显式选择 JSONRenderer，确保前端识别 application/json
            if result.get('state') == 'waiting':
                return _json_response(_build_waiting_data(
                    interrupt_id, approval, False,
                    message='本工具已拒绝，等待同批次其他工具审批完成后开始执行...',
                ))

            session_id = approval.chat_session_id or approval.source_id
            # 聚合恢复检查（含 graph_interrupt_id 提取、sibling 检查、批量 resume_value 构建）
            # 拒绝场景下 resume_value 已由 resume_approval 服务设置为 False
            return _aggregate_batch_resume(
                request, approval, resume_value, session_id, False,
                interrupt_id, log_prefix='[ApprovalReject]',
            )

        # learning 工作流审批拒绝
        if approval.source == Approval.SOURCE_LEARNING:
            if is_idempotent:
                # 幂等响应：waiting 状态需显式选择 JSONRenderer，确保前端识别 application/json
                idempotent_data = _build_idempotent_data(interrupt_id)
                if idempotent_data.get('state') == 'waiting':
                    return _json_response({
                        'code': 0,
                        'message': '审批已处理（幂等）',
                        'data': idempotent_data,
                    })
                return success_response(data=idempotent_data)
            # 批量审批场景：同批次还有其他 pending，不触发恢复，返回 waiting 状态
            # 通过 _json_response 显式选择 JSONRenderer，确保前端识别 application/json
            if result.get('state') == 'waiting':
                return _json_response(_build_waiting_data(
                    interrupt_id, approval, False,
                    message='本工具已拒绝，等待同批次其他工具审批完成后开始执行...',
                ))
            from Django_xm.common.sse_utils import sse_async_heartbeat_generator, sse_response
            return sse_response(
                sse_async_heartbeat_generator(
                    _stream_learning_resume_generator(request, approval, resume_value)
                )
            )

        # 其他未知 source
        try:
            approval_service.complete_approval(
                interrupt_id, Approval.STATE_REJECTED,
                extra={'reason': 'unknown_source'},
            )
        except Exception as complete_err:
            logger.error(f"[ApprovalRejectView] 释放未知 source 审批锁失败: {complete_err}")
        return error_response(
            code=ErrorCode.VALIDATION_FAILED,
            message=f'不支持的审批来源: {approval.source}',
        )


class ApprovalStateView(BaseApprovalAccessMixin, APIView):
    """查询审批当前状态。

    GET /api/v1/approvals/{interrupt_id}/state/
    返回指定审批的当前状态（state、resume_value 等），供前端轮询或断线恢复时使用。
    仅允许该审批关联会话的所属用户查询。
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(view=False)
    def get(self, request, interrupt_id):
        try:
            approval = Approval.objects.select_related('approved_by').get(interrupt_id=interrupt_id)
        except Approval.DoesNotExist:
            return error_response(code=ErrorCode.NOT_FOUND, message='审批不存在')

        if not self._user_owns_approval(request.user, approval):
            return error_response(code=ErrorCode.PERMISSION_DENIED, message='无权查看该审批')

        extra_data = approval.extra or {}
        resume_value = extra_data.get('_resume_value') if isinstance(extra_data, dict) else None

        approved_by_info = None
        if approval.approved_by:
            approved_by_info = {
                'id': approval.approved_by.id,
                'username': approval.approved_by.username,
            }

        data = {
            'interrupt_id': approval.interrupt_id,
            'source': approval.source,
            'source_id': approval.source_id,
            'chat_session_id': approval.chat_session_id,
            'state': approval.state,
            'resume_value': resume_value,
            'user_input': approval.user_input,
            'approved_by': approved_by_info,
            'tool_name': approval.tool_name,
            'title': approval.title,
            'description': approval.description,
            'action': approval.action,
            'operation': approval.operation,
            'danger_level': approval.danger_level,
            'parameters': approval.parameters,
            'created_at': approval.created_at.isoformat() if approval.created_at else None,
            'resolved_at': approval.resolved_at.isoformat() if approval.resolved_at else None,
        }
        return success_response(data=data)

