"""子代理业务等待（wait_for_subagent）超时清扫 Celery 任务。

背景：父图（嵌套子代理）经 wait_for_subagent 挂起在 ``_subagent_wait``（非审批
interrupt）后，正常由全部孙代理终态回调唤醒。当孙代理挂死、或服务重启丢失进程内
awaiter 时，父实例将永久 RUNNING——这是 SessionExecutor 挂起 watchdog（仅覆盖
主图会话槽）覆盖不到的盲区。本任务周期扫描 SubAgentInstance 中挂起超过 deadline
的等待，经 SubAgentRuntime.resume 以超时 payload 唤醒父图；wait_for_subagent
识别 ``_subagent_wait_timeout`` 标记后走失败路径，逐 id 报告 DB 真实子代理状态
（不再伪造失败结果），父 Agent 据此自行决策后续动作。

覆盖范围边界（与 settings.AGENT_SUBAGENT_WAIT_TIMEOUT 注释一致）：
- 本任务仅覆盖"父=子代理"的挂起（SubAgentInstance 持久化 pending_interrupt_info，
  可跨进程定位与 resume）；
- 主图（chat/research 会话）的等待不在 SubAgentInstance 表内，仍由 FastAPI
  进程内 watchdog（SessionExecutor._finalize_suspended_timeout）兜底，本任务
  不触碰主图。

超时唤醒边界语义（不终止子代理执行）：父图收到超时结果后继续推进业务，仍在
运行的孙代理不受影响——其执行线程独立存活，完成后正常落库（终态回调
on_subagent_finished 找不到父 awaiter 仅记日志"无父 Graph 等待"，状态记录无损失）。
"""

import asyncio
import logging

from celery import shared_task
from django.utils import timezone

from Django_xm.apps.agent_hub.subagent_tools.wait import (
    DEFAULT_WAIT_TIMEOUT_SECONDS,
    SUBAGENT_WAIT_TIMEOUT_FLAG,
)
from Django_xm.apps.ai_engine.models import SubAgentInstance, SubAgentStatus

logger = logging.getLogger(__name__)

# 任务名（与 settings.CELERY_TASK_ROUTES / CELERY_BEAT_SCHEDULE 对应）。
SUBAGENT_WAIT_SWEEP_TASK_NAME = "subagent_wait.cleanup_expired_waits"


def _is_wait_expired(
    pending: dict | None,
    updated_at,
    now_ts: float,
    timeout_seconds: int,
) -> bool:
    """判定一条 ``_subagent_wait`` 挂起是否已超过 deadline。

    - payload 携带 deadline（wait_for_subagent 新语义，epoch 秒）→ 直接比较；
    - 历史挂起无 deadline → 以挂起写入时间兜底：挂起期间 pending_interrupt_info
      与 status 不再变化，updated_at 即挂起时刻（测试可经 queryset.update 伪造，
      绕开 auto_now）；
    - deadline 非法（非数值）→ 视为未过期（清扫放弃该条，避免误杀）。
    """
    if not isinstance(pending, dict):
        return False
    deadline = pending.get("deadline")
    if deadline is not None:
        if not isinstance(deadline, (int, float)):
            return False
        return deadline < now_ts
    return updated_at.timestamp() + timeout_seconds < now_ts


def _build_timeout_resume_payload(pending: dict, updated_at, now_ts: float, timeout_seconds: int) -> dict:
    """构造超时 resume payload（wait_for_subagent 据此识别并走失败路径）。"""
    deadline = pending.get("deadline")
    if isinstance(deadline, (int, float)):
        started_ts = deadline - timeout_seconds
    else:
        started_ts = updated_at.timestamp()
    return {
        SUBAGENT_WAIT_TIMEOUT_FLAG: True,
        "deadline": deadline,
        "waited_seconds": max(int(now_ts - started_ts), 0),
    }


@shared_task(
    bind=True,
    name=SUBAGENT_WAIT_SWEEP_TASK_NAME,
    soft_time_limit=120,
    autoretry_for=(ConnectionError, TimeoutError, OSError),
    max_retries=3,
    default_retry_delay=10,
    retry_backoff=True,
    retry_backoff_max=60,
)
def cleanup_expired_subagent_waits(self):
    """扫描父图仍挂起在 ``_subagent_wait`` 且超过 deadline 的等待并 resume 父图。

    每 60 秒由 Celery beat 调度执行（参照 approvals.cleanup_expired_approvals
    模式：iterator 逐条 + 单条故障隔离 + 网络异常自动重试）。

    幂等/防重说明：
    - resume 前逐条复核记录仍为 RUNNING + 挂起态（扫描与 resume 间状态可能变化）；
    - 与正常唤醒存在极小竞态窗口（等待仅恢复一次），重复 Command(resume) 的
      二次副作用受 LangGraph interrupt checkpoint 单次消费语义约束（interrupt
      恢复后 checkpoint 不再挂起，重复 resume 无法二次进入），可接受；
    - runtime.resume 对非挂起实例抛 SubAgentInvalidStateError，按失败计数不中断。
    """
    from django.conf import settings

    from Django_xm.apps.ai_engine.subagent_runtime import get_subagent_runtime

    timeout_seconds = int(
        getattr(settings, "AGENT_SUBAGENT_WAIT_TIMEOUT", DEFAULT_WAIT_TIMEOUT_SECONDS)
        or DEFAULT_WAIT_TIMEOUT_SECONDS
    )
    now_ts = timezone.now().timestamp()

    candidates = SubAgentInstance.objects.filter(
        status=SubAgentStatus.RUNNING,
        pending_interrupt_info__has_key="_subagent_wait",
    )
    expired: list[SubAgentInstance] = []
    for instance in candidates.iterator():
        pending = instance.pending_interrupt_info
        if not isinstance(pending, dict):
            continue
        if _is_wait_expired(pending, instance.updated_at, now_ts, timeout_seconds):
            expired.append(instance)

    if not expired:
        logger.debug("[SubAgentWaitSweep] 无超时等待")
        return {"expired_count": 0}

    logger.info(f"[SubAgentWaitSweep] 发现 {len(expired)} 条超时等待，开始清扫")
    runtime = get_subagent_runtime()
    resumed = 0
    failed = 0
    for instance in expired:
        # 复核状态（iterator 遍历与 resume 之间的窗口）：非挂起态跳过
        fresh_pending = instance.pending_interrupt_info
        if instance.status != SubAgentStatus.RUNNING or not isinstance(fresh_pending, dict):
            continue
        resume_payload = _build_timeout_resume_payload(
            fresh_pending, instance.updated_at, now_ts, timeout_seconds
        )
        try:
            asyncio.run(runtime.resume(instance.thread_id, resume_payload))
            resumed += 1
            logger.warning(
                f"[SubAgentWaitSweep] 等待超时已清扫: parent={instance.thread_id}, "
                f"deadline={resume_payload['deadline']}, "
                f"waited_seconds={resume_payload['waited_seconds']}"
            )
        except Exception:
            failed += 1
            logger.exception(f"[SubAgentWaitSweep] 超时清扫 resume 失败: parent={instance.thread_id}")

    logger.info(f"[SubAgentWaitSweep] 完成: expired={len(expired)}, resumed={resumed}, failed={failed}")
    return {"expired_count": len(expired), "resumed": resumed, "failed": failed}
