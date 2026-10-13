"""审批超时清理 Celery 任务。

定期扫描 pending 状态超时的审批，调用 timeout_approval 将其终态化为 TIMEOUT
（resume_value=TIMEOUT_DECISION）。超时恢复已事件驱动化（Task 5）：
- chat 来源：审批终态化 + 发布 approval_timeout 实时事件，恢复由前端触发 SSE resume
- deep_research 来源：执行服务挂起协程轮询 DB 批次决策自驱动
timeout_approval 内部不再派发任何恢复任务。
"""

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
        state=Approval.State.PENDING,
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
                if locked.state != Approval.State.PENDING:
                    continue
                approval_service.timeout_approval(locked.interrupt_id)
                processed += 1
        except Exception:
            failed += 1
            logger.exception(f"[ApprovalCleanup] 处理超时审批失败: id={approval.interrupt_id}")

    logger.info(f"[ApprovalCleanup] 完成: processed={processed}, failed={failed}")
    return {"expired_count": count, "processed": processed, "failed": failed}


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
    - 行认领（D-9）：select_for_update 锁行 + state 二次校验，同一 pending 条目
      同一时刻仅被一个 worker 投递。publish_approval_sync 本身**无去重机制**
      （重复调用即重复发布实时事件，见 common/realtime_sync.py），投递语义为
      at-least-once，防重复靠行认领；下游事件消费方按最新状态幂等渲染
    - attempts 在同一行锁内读改写，避免并发 worker 覆盖重试计数（D-9）
    - 故障隔离：单条投递失败不影响其他条目
    """
    from Django_xm.common.realtime_sync import publish_approval_sync

    now = timezone.now()
    # 查询待投递条目：pending 状态 + (next_retry_at 为空 或 <= now)
    pending_qs = (
        ApprovalOutboxEntry.objects.filter(
            state=ApprovalOutboxEntry.State.PENDING,
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
            with transaction.atomic():
                # D-9：行认领 + state 二次校验（对齐 cleanup_expired_approvals /
                # approval_lifecycle.complete_batch 的锁模式），并发 worker 处理
                # 同一条目时后者阻塞等待或读到非 pending 后跳过
                locked = ApprovalOutboxEntry.objects.select_for_update().get(pk=entry.pk)
                if locked.state != ApprovalOutboxEntry.State.PENDING:
                    continue
                params = _restore_event_enums(locked.payload)
                publish_approval_sync(**params)
                locked.state = ApprovalOutboxEntry.State.DELIVERED
                locked.delivered_at = now
                locked.save(update_fields=["state", "delivered_at"])
            delivered += 1
        except Exception as e:
            # 失败记录独立事务 + 行锁：attempts 读改写在锁内完成，非原子 += 竞态消除
            with transaction.atomic():
                locked = ApprovalOutboxEntry.objects.select_for_update().get(pk=entry.pk)
                locked.attempts += 1
                locked.error_message = str(e)[:500]
                if locked.attempts >= locked.max_attempts:
                    locked.state = ApprovalOutboxEntry.State.FAILED
                    failed += 1
                    logger.exception(
                        f"[ApprovalOutbox] 投递失败(已达最大重试): "
                        f"entry_id={locked.id}, event_type={locked.event_type}, "
                        f"attempts={locked.attempts}"
                    )
                else:
                    # 指数退避：2^attempts 秒，上限 OUTBOX_MAX_BACKOFF_SECONDS
                    backoff = min(2**locked.attempts, OUTBOX_MAX_BACKOFF_SECONDS)
                    locked.next_retry_at = now + timedelta(seconds=backoff)
                    retried += 1
                    logger.warning(
                        f"[ApprovalOutbox] 投递失败(将重试): "
                        f"entry_id={locked.id}, event_type={locked.event_type}, "
                        f"attempts={locked.attempts}, next_retry_in={backoff}s, error={e}"
                    )
                locked.save(update_fields=["attempts", "error_message", "state", "next_retry_at"])

    logger.info(
        f"[ApprovalOutbox] 完成: total={len(entries)}, delivered={delivered}, failed={failed}, retried={retried}"
    )
    return {
        "processed": len(entries),
        "delivered": delivered,
        "failed": failed,
        "retried": retried,
    }
