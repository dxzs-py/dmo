"""审批超时清理 Celery 任务。

定期扫描 pending 状态超时的审批，调用 timeout_approval 将其标记为拒绝（resume_value=False）。
timeout_approval 内部会通过 check_and_trigger_research_resume 触发批量恢复逻辑，
确保超时审批不会导致任务永久挂起。
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from celery import shared_task
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from Django_xm.apps.approvals.models import Approval, ApprovalOutboxEntry
from Django_xm.apps.approvals.services import approval_service

logger = logging.getLogger(__name__)

APPROVAL_TIMEOUT_SECONDS = 300


@shared_task(
    bind=True,
    name="approvals.cleanup_expired_approvals",
    soft_time_limit=120,
    autoretry_for=(ConnectionError, TimeoutError, OSError),
    max_retries=3,
    default_retry_delay=10,
    retry_backoff=True,
    retry_backoff_max=60,
)
def cleanup_expired_approvals(self):
    """扫描 pending 超时审批并处理。每 60 秒由 Celery beat 调度执行。

    M5: 使用 expires_at 判断过期（替代 created_at + APPROVAL_TIMEOUT_SECONDS）。
    expires_at 在审批创建时设置为 now + APPROVAL_TIMEOUT_SECONDS（M4），
    复用 interrupt_id 时同步重置（M16）。历史数据在 M3 迁移时已回填。

    幂等性保障：
    - 通过 select_for_update + state 二次校验，避免并发 beat 触发重复处理
    - timeout_approval 内部对终态审批直接 return，重试场景不会产生副作用
    - soft_time_limit=120 防止大批量审批卡死 worker
    - autoretry_for 覆盖网络/Redis 异常，配合 backoff 避免雪崩
    """
    logger.info("[ApprovalCleanup] 任务被调用: 扫描超时审批")
    now = datetime.now(UTC)
    # M5: 使用 expires_at 判断过期；同时兜底处理未回填 expires_at 的历史数据
    expired_qs = Approval.objects.filter(
        state=Approval.STATE_PENDING,
    ).filter(
        Q(expires_at__lt=now)
        | Q(expires_at__isnull=True, created_at__lt=now - timedelta(seconds=APPROVAL_TIMEOUT_SECONDS))
    )

    count = expired_qs.count()
    if count == 0:
        logger.debug("[ApprovalCleanup] 无超时审批")
        return {"expired_count": 0}

    logger.info(f"[ApprovalCleanup] 发现 {count} 条超时审批，开始处理")

    processed = 0
    failed = 0

    for approval in expired_qs.iterator():
        try:
            with transaction.atomic():
                locked = Approval.objects.select_for_update().get(pk=approval.pk)
                if locked.state != Approval.STATE_PENDING:
                    continue
                approval_service.timeout_approval(locked.interrupt_id)
                processed += 1
        except Exception:
            failed += 1
            logger.exception(f"[ApprovalCleanup] 处理超时审批失败: id={approval.interrupt_id}")

    logger.info(f"[ApprovalCleanup] 完成: processed={processed}, failed={failed}")
    return {"expired_count": count, "processed": processed, "failed": failed}


@shared_task(bind=True, name="approvals.resume_chat_after_timeout")
def resume_chat_after_timeout(self, interrupt_id):
    """审批超时后异步恢复 chat agent 执行。

    由 timeout_approval 在 source=chat 场景下触发。
    通过 Command(resume=TIMEOUT_DECISION) 恢复 LangGraph，ApprovalMiddleware 识别
    TIMEOUT_DECISION 并注入"审批超时"ToolMessage，agent 收到后可调整策略继续执行。

    事件通过 Redis Pub/Sub 推送给前端（WebSocket），无需 HTTP SSE 通道。
    _stream_chat_resume_generator 内部的 publish_event
    会将关键事件推送到 Redis，前端通过 WebSocket 实时接收。

    Args:
        interrupt_id: 超时审批的 interrupt_id（tool_call_id）
    """
    logger.info(f"[ResumeChatTimeout] 任务被调用: interrupt_id={interrupt_id}")

    from Django_xm.common.constants import TIMEOUT_DECISION

    try:
        approval = Approval.objects.get(interrupt_id=interrupt_id)
    except Approval.DoesNotExist:
        logger.warning(f"[ResumeChatTimeout] 审批记录不存在: {interrupt_id}")
        return

    if approval.source != Approval.SOURCE_CHAT:
        logger.info(f"[ResumeChatTimeout] 非 chat 来源，跳过: {interrupt_id}, source={approval.source}")
        return

    if not approval.chat_session_id:
        # 防御性恢复：尝试从同批次的 sibling approval 中获取 chat_session_id
        extra = approval.extra if isinstance(approval.extra, dict) else {}
        graph_interrupt_id_for_recover = extra.get("graph_interrupt_id")
        if graph_interrupt_id_for_recover:
            sibling = (
                Approval.objects.filter(
                    extra__graph_interrupt_id=graph_interrupt_id_for_recover,
                    source=Approval.SOURCE_CHAT,
                    chat_session_id__isnull=False,
                )
                .exclude(chat_session_id="")
                .first()
            )
            if sibling and sibling.chat_session_id:
                approval.chat_session_id = sibling.chat_session_id
                approval.save(update_fields=["chat_session_id"])
                logger.warning(
                    f"[ResumeChatTimeout] chat_session_id 从 sibling 恢复: "
                    f"interrupt_id={interrupt_id}, session={approval.chat_session_id}, "
                    f"graph_interrupt_id={graph_interrupt_id_for_recover}"
                )

        if not approval.chat_session_id:
            logger.error(
                f"[ResumeChatTimeout] 缺少 chat_session_id 且无法从 sibling 恢复: "
                f"interrupt_id={interrupt_id}, source={approval.source}, "
                f"source_id={approval.source_id}, extra={approval.extra}"
            )
            # 终态化审批，避免任务永久卡死
            try:
                from Django_xm.apps.approvals.services.approval_service import complete_approval

                complete_approval(
                    interrupt_id,
                    Approval.STATE_REJECTED,
                    extra={"reason": "missing_chat_session_id_for_timeout_resume"},
                )
            except Exception:
                logger.exception(f"[ResumeChatTimeout] 终态化失败: interrupt_id={interrupt_id}")
            return

    # 通过 chat_session_id 查询 user_id
    from Django_xm.apps.chat.models import ChatSession

    session = ChatSession.objects.filter(
        session_id=approval.chat_session_id,
        is_deleted=False,
    ).first()
    if not session:
        logger.warning(f"[ResumeChatTimeout] 会话不存在: {approval.chat_session_id}")
        return

    user_id = session.user_id
    extra = approval.extra if isinstance(approval.extra, dict) else {}
    graph_interrupt_id = extra.get("graph_interrupt_id")

    # 构造批量场景的 resume_value：收集同批次所有 processing 审批的 resume_value
    resume_value = TIMEOUT_DECISION
    if graph_interrupt_id:
        processing_siblings = Approval.objects.filter(
            extra__graph_interrupt_id=graph_interrupt_id,
            source=Approval.SOURCE_CHAT,
            state=Approval.STATE_PROCESSING,
        )
        resume_map = {}
        for sib in processing_siblings:
            sib_extra = sib.extra if isinstance(sib.extra, dict) else {}
            sib_tc_id = sib_extra.get("tool_call_id") or sib.interrupt_id
            sib_rv = sib_extra.get("_resume_value", TIMEOUT_DECISION)
            resume_map[sib_tc_id] = sib_rv
        if resume_map:
            resume_value = resume_map

    # 构造简易 request 对象（_stream_chat_resume_generator 只用到 request.user.id）
    class _SimpleUser:
        def __init__(self, uid):
            self.id = uid

    class _SimpleRequest:
        def __init__(self, uid):
            self.user = _SimpleUser(uid)
            self.data = {}

    request = _SimpleRequest(user_id)

    # 从 approval.extra 获取持久化配置，构造 request_data
    tool_config = extra.get("tool_config", {})
    model_config = extra.get("model_config", {})
    request_data = {**tool_config, **model_config}

    logger.info(
        f"[ResumeChatTimeout] 触发超时恢复: interrupt_id={interrupt_id}, "
        f"session={approval.chat_session_id}, user={user_id}, "
        f"graph_interrupt_id={graph_interrupt_id}, "
        f"resume_value_type={'dict' if isinstance(resume_value, dict) else 'single'}, "
        f"batch_size={len(resume_value) if isinstance(resume_value, dict) else 1}"
    )

    async def _run_resume():
        from Django_xm.apps.chat.views_chat import _stream_chat_resume_generator

        logger.info(f"[ResumeChatTimeout] 开始执行 LangGraph 恢复流: interrupt_id={interrupt_id}")
        async for event in _stream_chat_resume_generator(
            request,
            approval,
            resume_value,
            approval.chat_session_id,
            request_data,
            graph_interrupt_id=graph_interrupt_id,
        ):
            # 消费 SSE 事件（关键事件已通过 Redis Pub/Sub 推送给前端）
            logger.debug(f"[ResumeChatTimeout] SSE event: {event[:200] if isinstance(event, str) else event}")
        logger.info(f"[ResumeChatTimeout] LangGraph 恢复执行完成，agent 已继续: interrupt_id={interrupt_id}")

    try:
        asyncio.run(_run_resume())
        logger.info(f"[ResumeChatTimeout] 恢复执行完成: interrupt_id={interrupt_id}")
    except RuntimeError as re:
        if "Event loop is closed" in str(re) or "This event loop is already running" in str(re):
            # 降级：使用新 event loop
            import asyncio as _asyncio

            loop = _asyncio.new_event_loop()
            _asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(_run_resume())
                logger.info(f"[ResumeChatTimeout] 恢复执行完成（降级路径）: interrupt_id={interrupt_id}")
            finally:
                loop.close()
        else:
            logger.exception(
                f"[ResumeChatTimeout] 恢复执行失败（RuntimeError）: interrupt_id={interrupt_id}",
            )
            raise
    except Exception:
        logger.exception(f"[ResumeChatTimeout] 恢复执行失败: interrupt_id={interrupt_id}")


# ============================================================
# Phase C: 审批发件箱补偿任务
# ============================================================

OUTBOX_BATCH_SIZE = 100
OUTBOX_MAX_BACKOFF_SECONDS = 300  # 最大退避 5 分钟


def _restore_event_enums(params: dict) -> dict:
    """将 outbox payload 中的字符串还原为 EventType / EventSource 枚举。

    JSONField 存储时枚举被序列化为字符串值，补偿重放时需还原为枚举类型，
    以满足 publish_approval_sync 的类型要求（EventType/EventSource 继承 str + Enum，
    直接传字符串也可工作，但还原枚举确保类型一致性）。
    """
    from Django_xm.common.event_schema import EventSource, EventType

    restored = dict(params)
    event_type_val = restored.get("event_type")
    if event_type_val is not None and not isinstance(event_type_val, EventType):
        try:
            restored["event_type"] = EventType(event_type_val)
        except (ValueError, TypeError):
            pass
    module_val = restored.get("module")
    if module_val is not None and not isinstance(module_val, EventSource):
        try:
            restored["module"] = EventSource(module_val)
        except (ValueError, TypeError):
            pass
    return restored


@shared_task(
    bind=True,
    name="approvals.process_outbox",
    soft_time_limit=60,
    autoretry_for=(ConnectionError, TimeoutError, OSError),
    max_retries=3,
    default_retry_delay=5,
)
def process_approval_outbox(self):
    """轮询审批发件箱，投递待发送事件（Phase C 补偿任务）。

    每 5 秒由 Celery beat 调度执行。处理流程：
    1. 查询 pending 状态且 next_retry_at 为空或已到期的条目（批量 OUTBOX_BATCH_SIZE）
    2. 对每条记录：还原枚举 → 调用 publish_approval_sync → 标记 delivered / 递增重试
    3. 超过 max_attempts 的条目标记为 failed

    设计原则：
    - 直接发布成功时 outbox 已被标记为 delivered，此任务仅处理 pending（补偿场景）
    - 幂等：publish_approval_sync 内部有去重机制，重复投递不会产生副作用
    - 故障隔离：单条投递失败不影响其他条目
    """
    from Django_xm.common.realtime_sync import publish_approval_sync

    now = timezone.now()
    # 查询待投递条目：pending 状态 + (next_retry_at 为空 或 <= now)
    pending_qs = (
        ApprovalOutboxEntry.objects.filter(
            state=ApprovalOutboxEntry.STATE_PENDING,
        )
        .filter(Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now))
        .order_by("created_at")[:OUTBOX_BATCH_SIZE]
    )

    entries = list(pending_qs)
    if not entries:
        return {"processed": 0, "delivered": 0, "failed": 0, "retried": 0}

    logger.info(f"[ApprovalOutbox] 开始处理 {len(entries)} 条待投递事件")

    delivered = 0
    failed = 0
    retried = 0

    for entry in entries:
        try:
            params = _restore_event_enums(entry.payload)
            publish_approval_sync(**params)
            entry.state = ApprovalOutboxEntry.STATE_DELIVERED
            entry.delivered_at = now
            entry.save(update_fields=["state", "delivered_at"])
            delivered += 1
        except Exception as e:
            entry.attempts += 1
            entry.error_message = str(e)[:500]
            if entry.attempts >= entry.max_attempts:
                entry.state = ApprovalOutboxEntry.STATE_FAILED
                failed += 1
                logger.exception(
                    f"[ApprovalOutbox] 投递失败(已达最大重试): "
                    f"entry_id={entry.id}, event_type={entry.event_type}, "
                    f"attempts={entry.attempts}"
                )
            else:
                # 指数退避：2^attempts 秒，上限 OUTBOX_MAX_BACKOFF_SECONDS
                backoff = min(2**entry.attempts, OUTBOX_MAX_BACKOFF_SECONDS)
                entry.next_retry_at = now + timedelta(seconds=backoff)
                retried += 1
                logger.warning(
                    f"[ApprovalOutbox] 投递失败(将重试): "
                    f"entry_id={entry.id}, event_type={entry.event_type}, "
                    f"attempts={entry.attempts}, next_retry_in={backoff}s, error={e}"
                )
            entry.save(update_fields=["attempts", "error_message", "state", "next_retry_at"])

    logger.info(
        f"[ApprovalOutbox] 完成: total={len(entries)}, delivered={delivered}, failed={failed}, retried={retried}"
    )
    return {
        "processed": len(entries),
        "delivered": delivered,
        "failed": failed,
        "retried": retried,
    }
