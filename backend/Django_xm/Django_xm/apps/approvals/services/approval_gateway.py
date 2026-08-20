"""统一审批路由网关（Path D 核心组件）

职责：
    根据 Approval.source 路由审批恢复请求到正确的执行环境：
    - chat → 发布 Redis 信令唤醒执行服务中的挂起协程（与 deep_research 同构）
    - deep_research → 发布 Redis 信令唤醒执行服务中的挂起协程（事件驱动）

设计原则：
    1. Gateway 是路由器，不是阻塞器：不做阻塞等待，不持有内存状态
    2. DB 为唯一真相源：Approval DB 记录是审批状态的唯一来源
    3. Redis 仅用于信令发布：执行服务挂起协程被唤醒后从 DB 读取批次决策

返回值约定：
    - chat / deep_research source：均返回 None（已发布信令，调用方返回 JSON 响应）
    - 未知 source：抛出 ValueError

熔断机制（F3）：
    route_resume 前检查同一 source_id 在滑动窗口内的 HIGH 级操作数，
    超过阈值时抛出 CircuitBreakerError，调用方（views）捕获后：
    - 标记审批为 rejected（circuit_broken 原因）
    - 以 rejection resume_value 恢复 agent（让 agent 调整策略）
    - 返回熔断错误响应给前端

依赖关系：
    - chat 模块：common.signal_bus.publish_signal（Redis 信令）
    - deep_research 模块：common.signal_bus.publish_signal（Redis 信令）
    - 前端：统一调用 POST /api/v1/approvals/{interrupt_id}/resume/，无需 source 分支
"""

import logging
from typing import Any

from Django_xm.apps.approvals.models import Approval
from Django_xm.common.constants import TIMEOUT_DECISION
from Django_xm.common.observability.approval_metrics import (
    HIGH_RISK_WINDOW_THRESHOLD,
    approval_metrics,
)
from Django_xm.common.risk_levels import RiskLevel

logger = logging.getLogger(__name__)


class CircuitBreakerError(Exception):
    """高频高危熔断异常。

    当同一 source_id 在滑动窗口内 HIGH 级操作数超过阈值时抛出。
    调用方（ApprovalResumeView / ApprovalRejectView）捕获后：
    1. 标记审批为 rejected（extra.circuit_broken=True）
    2. 以 rejection resume_value 恢复 agent，让 agent 调整策略
    3. 返回熔断错误响应给前端

    Attributes:
        source_id: 触发熔断的会话/任务 ID
        window_count: 当前窗口内 HIGH 级操作数
        threshold: 熔断阈值
    """

    def __init__(self, source_id: str, window_count: int, threshold: int):
        self.source_id = source_id
        self.window_count = window_count
        self.threshold = threshold
        super().__init__(
            f"高频高危熔断: source_id={source_id}, 窗口内 HIGH 级操作数={window_count} 超过阈值={threshold}"
        )


class ApprovalGateway:
    """统一审批路由网关。

    根据 Approval.source 将恢复请求路由到正确的执行环境。
    模块级单例 ``gateway`` 供 views.py 使用。

    使用方式：
        result = gateway.route_resume(request, approval, resume_value, ...)
        # chat / deep_research 均返回 None：Redis 信令已发布，调用方返回 JSON
        return Response({'status': 'resumed'})
    """

    def route_resume(
        self,
        request,
        approval: Approval,
        resume_value: Any,
        *,
        session_id: str | None = None,
        graph_interrupt_id: str | None = None,
        langgraph_resume_id: str | None = None,
        approved: bool = True,
        **kwargs,
    ):
        """路由审批恢复请求到正确的执行环境。

        熔断检查（F3）：在路由前检查同一 source_id 的 HIGH 级操作滑动窗口。
        仅对 HIGH 级审批且用户批准（approved=True）时触发检查。
        熔断时抛出 CircuitBreakerError，调用方负责标记 rejected 并以 rejection 恢复 agent。

        Args:
            request: HTTP 请求对象（保留签名兼容，chat 路径不再读取 request.data）
            approval: Approval 模型实例
            resume_value: 恢复值（True/False/user_input，或批量场景的 {tool_call_id: bool}）
            session_id: 会话 ID（chat 模块用作 thread_id）
            graph_interrupt_id: 批次 UUID（用于日志和 DB 查询）
            langgraph_resume_id: LangGraph 恢复 ID（Command resume 的 KEY）
            approved: 用户实际决策（True=确认, False=拒绝）
            **kwargs: 额外参数（透传到具体执行路径）

        Returns:
            - chat / deep_research source：返回 None（已发布 Redis 信令唤醒执行服务）

        Raises:
            CircuitBreakerError: HIGH 级操作数超过滑动窗口阈值
            ValueError: 不支持的审批来源
        """
        logger.info(
            f"[ApprovalGateway] route_resume 入口: source={approval.source}, "
            f"interrupt_id={approval.interrupt_id}, approved={approved}"
        )

        # F3 熔断检查：仅对用户批准的 HIGH 级操作触发
        if approved and self._is_high_risk_approval(approval):
            self._check_circuit_breaker(approval)

        if approval.source == Approval.Source.CHAT:
            return self._resume_chat(
                request,
                approval,
                resume_value,
                session_id=session_id,
                graph_interrupt_id=graph_interrupt_id,
                langgraph_resume_id=langgraph_resume_id,
                approved=approved,
                **kwargs,
            )
        elif approval.source == Approval.Source.DEEP_RESEARCH:
            return self._resume_deep_research(
                approval,
                resume_value,
                graph_interrupt_id=graph_interrupt_id,
                langgraph_resume_id=langgraph_resume_id,
                approved=approved,
                **kwargs,
            )
        else:
            logger.error(
                f"[ApprovalGateway] 不支持的审批来源: source={approval.source}, interrupt_id={approval.interrupt_id}"
            )
            raise ValueError(f"不支持的审批来源: {approval.source}")

    @staticmethod
    def _is_high_risk_approval(approval: Approval) -> bool:
        """判断审批是否为 HIGH 级风险操作。

        risk_level 优先从 approval.extra.risk_level 读取（新标准），
        回退到 danger_level 映射（兼容历史数据）。
        """
        approval_extra = approval.extra if isinstance(approval.extra, dict) else {}
        risk_level_str = approval_extra.get("risk_level")
        if risk_level_str:
            try:
                return RiskLevel(risk_level_str) == RiskLevel.HIGH
            except ValueError:
                pass
        # 回退：danger_level == 'high'
        return approval.danger_level == "high"

    @staticmethod
    def _check_circuit_breaker(approval: Approval) -> None:
        """检查单会话高频高危熔断（F3）。

        同一 source_id 在滑动窗口（5 分钟）内 HIGH 级操作数超过阈值（10）时，
        抛出 CircuitBreakerError，阻止本次 HIGH 级操作执行。

        熔断后调用方职责：
        1. 标记审批为 rejected（extra.circuit_broken=True）
        2. 以 rejection resume_value 恢复 agent，让 agent 收到拒绝后调整策略
        3. 返回熔断错误响应给前端

        Args:
            approval: Approval 模型实例

        Raises:
            CircuitBreakerError: 窗口内 HIGH 级操作数超过阈值
        """
        source_id = approval.source_id or ""
        if not source_id:
            return  # 无 source_id 无法计数，跳过熔断检查

        window_count = approval_metrics.get_high_risk_window_count(source_id)
        if window_count > HIGH_RISK_WINDOW_THRESHOLD:
            logger.warning(
                f"[ApprovalGateway] 熔断触发: source_id={source_id}, "
                f"window_count={window_count}, threshold={HIGH_RISK_WINDOW_THRESHOLD}, "
                f"interrupt_id={approval.interrupt_id}, tool_name={approval.tool_name}"
            )
            raise CircuitBreakerError(
                source_id=source_id,
                window_count=window_count,
                threshold=HIGH_RISK_WINDOW_THRESHOLD,
            )

    def _resume_chat(
        self,
        request,
        approval: Approval,
        resume_value: Any,
        *,
        session_id: str | None = None,
        graph_interrupt_id: str | None = None,
        langgraph_resume_id: str | None = None,
        approved: bool = True,
        **kwargs,
    ) -> None:
        """chat 模块：发布 Redis 信令唤醒执行服务中的挂起协程。

        chat 的 agent 现在同样在 FastAPI 执行服务单协程运行（与 deep_research 同构），
        审批决策已由 approval_service 落库终态，本方法仅发布 Redis 信令
        （agent:approval:{session_id}）即时唤醒对应执行协程；即使信令丢失，
        执行器挂起时周期性轮询 DB 批次决策，最终一致。

        不阻塞 HTTP 请求，立即返回 None（调用方返回 JSON 响应）。
        """
        from Django_xm.common.signal_bus import (
            SESSION_TYPE_CHAT,
            SIGNAL_APPROVAL,
            publish_signal,
        )

        # 会话 ID 优先使用传入参数，回退到 approval.chat_session_id 或 approval.source_id
        effective_session_id = session_id or approval.chat_session_id or approval.source_id
        if not effective_session_id:
            logger.error(
                f"[ApprovalGateway] chat 审批缺少会话 ID，无法路由: "
                f"interrupt_id={approval.interrupt_id}"
            )
            return

        approval_extra = approval.extra if isinstance(approval.extra, dict) else {}
        effective_graph_interrupt_id = graph_interrupt_id or approval_extra.get("graph_interrupt_id", "")
        effective_langgraph_resume_id = (
            langgraph_resume_id or approval_extra.get("langgraph_resume_id", "") or approval.interrupt_id
        )
        message_id = approval_extra.get("message_id", "")
        subagent_thread_id = approval_extra.get("subagent_thread_id", "")

        logger.info(
            f"[ApprovalGateway] chat 审批信令: session={effective_session_id}, "
            f"interrupt_id={approval.interrupt_id}, "
            f"graph_interrupt_id={effective_graph_interrupt_id}, "
            f"langgraph_resume_id={effective_langgraph_resume_id}, "
            f"approved={approved}, subagent_thread_id={subagent_thread_id or '(main)'}"
        )

        publish_signal(
            SIGNAL_APPROVAL,
            effective_session_id,
            {
                "thread_id": effective_session_id,
                "session_type": SESSION_TYPE_CHAT,
                "interrupt_id": approval.interrupt_id,
                "graph_interrupt_id": effective_graph_interrupt_id,
                "langgraph_resume_id": effective_langgraph_resume_id,
                "approved": approved,
                "resume_value": resume_value,
                "user_id": getattr(approval, "user_id", None),
                "message_id": message_id,
                "chat_session_id": approval.chat_session_id,
                # 子代理审批（统一审批链路，与主 agent 同端点）：恢复目标为子代理
                # 独立 thread，而非主会话挂起协程（SessionManager 据此路由 runtime.resume）
                "subagent_thread_id": subagent_thread_id,
            },
        )

    def _resume_deep_research(
        self,
        approval: Approval,
        resume_value: Any,
        *,
        graph_interrupt_id: str | None = None,
        langgraph_resume_id: str | None = None,
        approved: bool = True,
        **kwargs,
    ) -> None:
        """deep_research 模块：发布 Redis 信令唤醒执行服务中的挂起协程。

        深度研究的 agent 在独立 FastAPI 执行服务中运行（单协程，审批时挂起等待）。
        用户审批后，审批结果已由 approval_service 落库终态，本方法仅发布
        Redis 信令（research:approval:{thread_id}）即时唤醒对应执行协程；
        即使信令丢失，执行器挂起时周期性轮询 DB 批次决策，最终一致。

        不阻塞 HTTP 请求，立即返回 None（调用方返回 JSON 响应）。
        """
        from Django_xm.common.signal_bus import (
            SESSION_TYPE_RESEARCH,
            SIGNAL_APPROVAL,
            publish_signal,
        )

        approval_extra = approval.extra if isinstance(approval.extra, dict) else {}
        effective_graph_interrupt_id = graph_interrupt_id or approval_extra.get("graph_interrupt_id", "")
        effective_langgraph_resume_id = (
            langgraph_resume_id or approval_extra.get("langgraph_resume_id", "") or approval.interrupt_id
        )
        message_id = approval_extra.get("message_id", "")
        subagent_thread_id = approval_extra.get("subagent_thread_id", "")

        thread_id = approval.source_id or ""
        if not thread_id:
            logger.error(
                f"[ApprovalGateway] deep_research 审批缺少 source_id，无法路由: "
                f"interrupt_id={approval.interrupt_id}"
            )
            return

        logger.info(
            f"[ApprovalGateway] deep_research 审批信令: task_id={thread_id}, "
            f"interrupt_id={approval.interrupt_id}, "
            f"graph_interrupt_id={effective_graph_interrupt_id}, "
            f"langgraph_resume_id={effective_langgraph_resume_id}, "
            f"approved={approved}, message_id={message_id}, "
            f"subagent_thread_id={subagent_thread_id or '(main)'}"
        )

        publish_signal(
            SIGNAL_APPROVAL,
            thread_id,
            {
                "thread_id": thread_id,
                "session_type": SESSION_TYPE_RESEARCH,
                "interrupt_id": approval.interrupt_id,
                "graph_interrupt_id": effective_graph_interrupt_id,
                "langgraph_resume_id": effective_langgraph_resume_id,
                "approved": approved,
                "resume_value": resume_value,
                "user_id": getattr(approval, "user_id", None),
                "message_id": message_id,
                "chat_session_id": approval.chat_session_id,
                # 子代理审批（统一审批链路，与主 agent 同端点）：恢复目标为子代理
                # 独立 thread，而非主会话挂起协程（SessionManager 据此路由 runtime.resume）
                "subagent_thread_id": subagent_thread_id,
            },
        )

    def route_timeout(
        self,
        approval: Approval,
        resume_value: Any = None,
    ) -> None:
        """统一超时恢复路由（后台调用，无 HTTP 请求）。

        确认/拒绝/超时三种审批决策统一通过 ApprovalGateway 路由。
        超时由 Celery cleanup_expired_approvals 检测并触发（timeout_approval）。
        - chat / deep_research 来源：发布 Redis 信令唤醒执行服务挂起协程（最终一致）

        Args:
            approval: Approval 模型实例
            resume_value: 恢复值，默认 TIMEOUT_DECISION

        Returns:
            None（事件驱动，无需 SSE 流）
        """
        effective_resume_value = resume_value if resume_value is not None else TIMEOUT_DECISION

        if approval.source == Approval.Source.CHAT:
            self._resume_chat(
                None,
                approval,
                effective_resume_value,
                graph_interrupt_id=None,
                langgraph_resume_id=None,
                approved=False,
            )

        elif approval.source == Approval.Source.DEEP_RESEARCH:
            self._resume_deep_research(
                approval,
                effective_resume_value,
                graph_interrupt_id=None,
                langgraph_resume_id=None,
                approved=False,
            )

        else:
            logger.error(
                f"[ApprovalGateway] 不支持的审批来源（超时）: "
                f"source={approval.source}, interrupt_id={approval.interrupt_id}"
            )
            raise ValueError(f"不支持的审批来源: {approval.source}")


# 模块级单例：views.py 统一通过此实例路由
gateway = ApprovalGateway()
