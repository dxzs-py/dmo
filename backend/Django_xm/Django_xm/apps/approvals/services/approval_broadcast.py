"""审批事件广播与发件箱模块（从 approval_service.py 拆分，Task 15.1）。

集中管理审批事件的发布逻辑与 outbox 补偿模式：
- 工具调用生命周期事件（TIMEOUT/WAITING/RUNNING）：通过 tool_call_lifecycle.service
  状态机入口发布，让前端 ToolCallCard 与审批面板状态分离
- 审批事件广播（同步/异步）：双写 + 补偿模式
  1. 构建事件参数（复用 approval_payload._build_approval_event_params）
  2. 创建 outbox 条目（补偿用）
  3. 尝试直接发布（publish_approval_sync / publish_approval）
  4. 直接发布成功 → 标记 outbox 为 delivered
  5. 直接发布失败 → outbox 保持 pending，由 Celery 任务补偿
- 可观测性指标采集（_record_metrics_for_state）

依赖 approval_payload 提供的纯函数参数构建，不反向依赖 approval_service（避免循环导入）。
"""

import logging
from datetime import UTC, datetime

from asgiref.sync import sync_to_async

from Django_xm.apps.approvals.models import Approval, ApprovalOutboxEntry
from Django_xm.common.event_schema import EventType, PayloadValidationError
from Django_xm.common.observability.approval_metrics import approval_metrics
from Django_xm.common.realtime_sync import publish_approval_sync
from Django_xm.common.risk_levels import RiskLevel
from Django_xm.common.tool_call_lifecycle import ToolCallContext, service

from .approval_payload import (
    _approval_created_timestamp,
    _build_approval_event_params,
    _extract_risk_level,
    _extract_tool_call_event_kwargs,
)

logger = logging.getLogger(__name__)


def _now():
    """本地 _now 副本（避免反向依赖 approval_service 导致循环导入）。"""
    return datetime.now(UTC)


def publish_tool_call_timeout_event(approval: Approval):
    """发布 TOOL_CALL_TIMEOUT 事件（通过 tool_call_lifecycle.service.transition 状态机入口）。

    审批超时时，除了发布 APPROVAL_TIMEOUT 更新审批面板，
    还需发布 TOOL_CALL_TIMEOUT 让前端 ToolCallCard 显示"审批超时"状态。

    通过 service.register + service.transition 入口发布：
    - register：幂等注册上下文（chat 模块可能已注册，不覆盖非空字段）
    - transition：状态机校验 + 去重 + 内部调用 publish_tool_call_sync（底层传输不变）
    """
    kwargs = _extract_tool_call_event_kwargs(approval)
    tool_call_id = kwargs["tool_call_id"]
    try:
        # 注册上下文（幂等：已存在时不覆盖非空字段，仅补全空字段）
        service.register(
            ToolCallContext(
                tool_call_id=tool_call_id,
                tool_name=kwargs["tool_name"],
                module=kwargs["module"],
                module_id=kwargs["module_id"],
                message_id=kwargs["message_id"],
                parameters=kwargs["parameters"],
                cross_module_id=kwargs["cross_module_id"],
                graph_interrupt_id=kwargs["graph_interrupt_id"],
            )
        )
        # 状态机转换并发布事件（内部调用 publish_tool_call_sync）
        service.transition(
            tool_call_id,
            EventType.TOOL_CALL_TIMEOUT,
            parameters=kwargs["parameters"] or None,
        )
    except PayloadValidationError:
        logger.exception(
            f"[ApprovalService] TOOL_CALL_TIMEOUT payload 校验失败，终止审批流程: "
            f"interrupt_id={approval.interrupt_id}, tool_name={approval.tool_name}",
        )
        raise  # 审批事件丢失是严重问题，让上层感知
    except Exception as e:
        logger.warning(
            f"[ApprovalService] 发布 TOOL_CALL_TIMEOUT 事件失败: interrupt_id={approval.interrupt_id}, error={e}"
        )


def _publish_tool_call_waiting_event(approval: Approval):
    """发布 TOOL_CALL_WAITING 事件（通过 tool_call_lifecycle.service.transition 状态机入口）。

    语义统一：
    - 审批创建时（state=PENDING）发布：表示工具进入"等待审批"状态
    - 同批次场景（state=WAITING）发布：表示工具已审批但同批次还有其他 pending

    这两种语义都通过同一事件表达，前端根据 approval.state 区分展示。

    通过 service.register + service.transition 入口发布：
    - register：幂等注册上下文（chat 模块可能已注册，不覆盖非空字段）
    - transition：状态机校验 + 去重 + 内部调用 publish_tool_call_sync（底层传输不变）
    """
    kwargs = _extract_tool_call_event_kwargs(approval)
    tool_call_id = kwargs["tool_call_id"]
    try:
        # 注册上下文（幂等：已存在时不覆盖非空字段，仅补全空字段）
        service.register(
            ToolCallContext(
                tool_call_id=tool_call_id,
                tool_name=kwargs["tool_name"],
                module=kwargs["module"],
                module_id=kwargs["module_id"],
                message_id=kwargs["message_id"],
                parameters=kwargs["parameters"],
                cross_module_id=kwargs["cross_module_id"],
                graph_interrupt_id=kwargs["graph_interrupt_id"],
            )
        )
        # 状态机转换并发布事件（内部调用 publish_tool_call_sync）
        service.transition(
            tool_call_id,
            EventType.TOOL_CALL_WAITING,
            parameters=kwargs["parameters"] or None,
        )
    except PayloadValidationError:
        logger.exception(
            f"[ApprovalService] TOOL_CALL_WAITING payload 校验失败，终止审批流程: "
            f"interrupt_id={approval.interrupt_id}, tool_name={approval.tool_name}",
        )
        raise  # 审批事件丢失是严重问题，让上层感知
    except Exception as e:
        logger.warning(
            f"[ApprovalService] 发布 TOOL_CALL_WAITING 事件失败: interrupt_id={approval.interrupt_id}, error={e}"
        )


def _publish_tool_call_running_event(approval: Approval):
    """发布 TOOL_CALL_RUNNING 事件（通过 tool_call_lifecycle.service.transition 状态机入口）。

    审批通过时（state=PROCESSING）发布，表示工具开始执行。
    修复问题 P：审批通过前显示"等待中"，通过后显示"执行中"。

    通过 service.register + service.transition 入口发布：
    - register：幂等注册上下文（chat 模块可能已注册，不覆盖非空字段）
    - transition：状态机校验 + 去重 + 内部调用 publish_tool_call_sync（底层传输不变）
    """
    kwargs = _extract_tool_call_event_kwargs(approval)
    tool_call_id = kwargs["tool_call_id"]
    try:
        # 注册上下文（幂等：已存在时不覆盖非空字段，仅补全空字段）
        service.register(
            ToolCallContext(
                tool_call_id=tool_call_id,
                tool_name=kwargs["tool_name"],
                module=kwargs["module"],
                module_id=kwargs["module_id"],
                message_id=kwargs["message_id"],
                parameters=kwargs["parameters"],
                cross_module_id=kwargs["cross_module_id"],
                graph_interrupt_id=kwargs["graph_interrupt_id"],
            )
        )
        # 状态机转换并发布事件（内部调用 publish_tool_call_sync）
        service.transition(
            tool_call_id,
            EventType.TOOL_CALL_RUNNING,
            parameters=kwargs["parameters"] or None,
        )
    except PayloadValidationError:
        logger.exception(
            f"[ApprovalService] TOOL_CALL_RUNNING payload 校验失败，终止审批流程: "
            f"interrupt_id={approval.interrupt_id}, tool_name={approval.tool_name}",
        )
        raise  # 审批事件丢失是严重问题，让上层感知
    except Exception as e:
        logger.warning(
            f"[ApprovalService] 发布 TOOL_CALL_RUNNING 事件失败: interrupt_id={approval.interrupt_id}, error={e}"
        )


def _create_outbox_entry(approval: Approval, params: dict) -> ApprovalOutboxEntry | None:
    """创建审批事件发件箱条目（Phase C 补偿模式）。

    存储完整的事件参数，供 process_approval_outbox Celery 任务在直接发布失败时重试。
    创建失败不阻塞主流程（仅记日志）。
    """
    event_type = params.get("event_type")
    if event_type is None:
        return None
    try:
        return ApprovalOutboxEntry.objects.create(
            approval=approval,
            event_type=event_type.value if hasattr(event_type, "value") else str(event_type),
            payload=params,
            state=ApprovalOutboxEntry.STATE_PENDING,
        )
    except Exception as e:
        logger.warning(
            f"[ApprovalService] 创建 outbox 条目失败(非致命): interrupt_id={approval.interrupt_id}, error={e}"
        )
        return None


def _mark_outbox_delivered(outbox_entry: ApprovalOutboxEntry | None) -> None:
    """直接发布成功后标记 outbox 条目为已投递。"""
    if outbox_entry is None:
        return
    try:
        outbox_entry.state = ApprovalOutboxEntry.STATE_DELIVERED
        outbox_entry.delivered_at = _now()
        outbox_entry.save(update_fields=["state", "delivered_at"])
    except Exception as e:
        logger.debug(f"[ApprovalService] 标记 outbox delivered 失败(非致命): {e}")


def _broadcast_approval_changed(approval: Approval, state: str, extra: dict | None = None):
    """统一发布审批事件（同步版，通过 publish_approval_sync 统一入口）。

    Phase C 集成：双写 + 补偿模式
    1. 构建事件参数（_build_approval_event_params）
    2. 创建 outbox 条目（补偿用）
    3. 尝试直接发布（publish_approval_sync，实时性）
    4. 直接发布成功 → 标记 outbox 为 delivered
    5. 直接发布失败 → outbox 保持 pending，由 Celery 任务补偿

    PayloadValidationError 仍向上抛出（数据错误不可补偿）。
    """
    params = _build_approval_event_params(approval, state, extra)

    # Phase C: 创建 outbox 条目（补偿用，创建失败不阻塞）
    outbox_entry = _create_outbox_entry(approval, params)

    try:
        publish_approval_sync(**params)
        # 直接发布成功，标记 outbox 为已投递
        _mark_outbox_delivered(outbox_entry)
    except PayloadValidationError:
        # 数据校验错误不可补偿，标记 outbox 为 failed 并向上抛出
        if outbox_entry is not None:
            try:
                outbox_entry.state = ApprovalOutboxEntry.STATE_FAILED
                outbox_entry.error_message = "PayloadValidationError"
                outbox_entry.save(update_fields=["state", "error_message"])
            except Exception:
                # 二次失败（更新 outbox 状态）不掩盖原始 PayloadValidationError
                logger.debug("标记 outbox 为 FAILED 失败", exc_info=True)
        logger.exception(
            f"[ApprovalService] 审批事件 payload 校验失败，终止审批流程: "
            f"state={state}, event_type={params['event_type'].value}, "
            f"interrupt_id={approval.interrupt_id}, tool_name={approval.tool_name}",
        )
        raise  # 审批事件丢失是严重问题，让上层感知
    except Exception as publish_err:
        # 非 payload 错误（如 Redis 连接失败）：outbox 保持 pending，由补偿任务重试
        logger.exception(
            f"[ApprovalService] 审批事件直接发布失败，已写入 outbox 等待补偿: "
            f"state={state}, event_type={params['event_type'].value}, "
            f"interrupt_id={approval.interrupt_id}"
        )
        # 标记 outbox 的错误信息（保持 pending 状态供补偿）
        if outbox_entry is not None:
            try:
                outbox_entry.error_message = str(publish_err)[:500]
                outbox_entry.save(update_fields=["error_message"])
            except Exception:
                # 二次失败（更新 outbox 错误信息）不掩盖原始发布错误
                logger.debug("更新 outbox 错误信息失败", exc_info=True)


async def _broadcast_approval_changed_async(approval: Approval, state: str, extra: dict | None = None):
    """统一发布审批事件（异步版，通过 publish_approval 异步入口）。

    Phase C 集成：与同步版对称的双写 + 补偿模式。
    参数构建复用 _build_approval_event_params，确保同步/异步路径参数一致。
    outbox 创建使用 sync_to_async 包装（Django ORM 是同步的）。
    """
    from Django_xm.common.realtime_sync import publish_approval

    params = _build_approval_event_params(approval, state, extra)

    # Phase C: 创建 outbox 条目（异步路径用 sync_to_async 包装 ORM 调用）
    outbox_entry = await sync_to_async(_create_outbox_entry)(approval, params)

    try:
        await publish_approval(**params)
        await sync_to_async(_mark_outbox_delivered)(outbox_entry)
    except PayloadValidationError:
        if outbox_entry is not None:
            try:
                outbox_entry.state = ApprovalOutboxEntry.STATE_FAILED
                outbox_entry.error_message = "PayloadValidationError"
                await sync_to_async(outbox_entry.save)(update_fields=["state", "error_message"])
            except Exception:
                # 二次失败（更新 outbox 状态）不掩盖原始 PayloadValidationError
                logger.debug("异步标记 outbox 为 FAILED 失败", exc_info=True)
        logger.exception(
            f"[ApprovalService] 异步审批事件 payload 校验失败，终止审批流程: "
            f"state={state}, event_type={params['event_type'].value}, "
            f"interrupt_id={approval.interrupt_id}, tool_name={approval.tool_name}",
        )
        raise
    except Exception as publish_err:
        logger.exception(
            f"[ApprovalService] 异步审批事件直接发布失败，已写入 outbox 等待补偿: "
            f"state={state}, event_type={params['event_type'].value}, "
            f"interrupt_id={approval.interrupt_id}"
        )
        if outbox_entry is not None:
            try:
                outbox_entry.error_message = str(publish_err)[:500]
                await sync_to_async(outbox_entry.save)(update_fields=["error_message"])
            except Exception:
                # 二次失败（更新 outbox 错误信息）不掩盖原始发布错误
                logger.debug("异步更新 outbox 错误信息失败", exc_info=True)


def _record_metrics_for_state(approval: Approval, state: str) -> None:
    """根据审批状态变更累加可观测性指标（F2）。

    集成点：persist_and_broadcast / persist_and_broadcast_async 末尾统一调用。
    故障隔离：approval_metrics 内部已 try/except，此处不额外捕获。

    状态映射：
        PENDING → on_created（累加 pending + 风险分布）
        APPROVED → on_approved（递减 pending + 记录延迟）
        REJECTED → on_rejected（递减 pending + 记录延迟）
        TIMEOUT → on_timeout（递减 pending + 记录延迟）
        其他状态（PROCESSING/WAITING）→ 不累加（中间态，非终态）
    """
    if state == Approval.STATE_PENDING:
        risk_level = _extract_risk_level(approval)
        approval_metrics.on_created(risk_level)
        # HIGH 级审批创建时记录单会话滑动窗口（供 F3 熔断判定）
        if risk_level == RiskLevel.HIGH and approval.source_id:
            approval_metrics.record_high_risk_for_session(approval.source_id)
    elif state == Approval.STATE_APPROVED:
        approval_metrics.on_approved(_approval_created_timestamp(approval))
    elif state == Approval.STATE_REJECTED:
        approval_metrics.on_rejected(_approval_created_timestamp(approval))
    elif state == Approval.STATE_TIMEOUT:
        approval_metrics.on_timeout(_approval_created_timestamp(approval))
