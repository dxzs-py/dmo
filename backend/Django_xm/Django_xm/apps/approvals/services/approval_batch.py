"""审批批次决策/终态化/创建公共模块（chat / research 双链路唯一实现）。

将原 chat（``chat_executor_core._collect_chat_batch_decisions`` /
``_finalize_chat_batch_approvals``）与 research（``research_runner.collect_batch_decisions`` /
``finalize_batch_approvals`` / ``create_approvals_for_interrupts``）仅 source 不同的
重复逻辑收敛为本模块，通过 ``source`` 参数区分，避免"修一处漏一处"。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from asgiref.sync import sync_to_async

from Django_xm.apps.approvals.models import Approval
from Django_xm.common.constants import TIMEOUT_DECISION

logger = logging.getLogger(__name__)


def _find_approval_by_interrupt_id(interrupt_id: str):
    """按 interrupt_id 查询审批记录（审批创建幂等保护用）。"""
    return Approval.objects.filter(interrupt_id=interrupt_id).first()


def collect_batch_decisions(
    source: str,
    source_id: str,
    graph_interrupt_id: str,
) -> tuple[dict, bool]:
    """收集同批次所有审批决策。

    Command(resume=...) 的 key 必须是 LangGraph Interrupt.id（langgraph_resume_id）。

    Args:
        source: 审批来源（``Approval.Source.CHAT`` / ``Approval.Source.DEEP_RESEARCH``）。
        source_id: 会话/任务 ID（chat=session_id, deep_research=task_id）。
        graph_interrupt_id: 批次 ID。

    Returns:
        (resume_by_interrupt, all_resolved)
        - resume_by_interrupt: {langgraph_resume_id: {tool_call_id: bool|TIMEOUT_DECISION}}
        - all_resolved: 是否所有审批都已决断（approved/rejected/timeout）
    """
    batch_approvals = Approval.objects.filter(
        source=source,
        source_id=source_id,
        extra__graph_interrupt_id=graph_interrupt_id,
    )

    resume_by_interrupt: dict[str, dict[str, Any]] = {}
    all_resolved = True

    for approval in batch_approvals:
        extra = approval.extra if isinstance(approval.extra, dict) else {}
        # tool_call_id 缺失时以 approval.interrupt_id 等价回退：parser 恒写二者同值，
        # 等价无害，保留（spec approval-id-fallback-refactor A4）。
        tc_id = extra.get("tool_call_id") or approval.interrupt_id
        # F-C（spec A5）：extra 缺 langgraph_resume_id 不再回退批次 uuid——批次 uuid
        # 不是合法 resume key（LangGraph 按 Interrupt.id 匹配 Command(resume) 键），
        # 会静默发起注定失败的恢复。跳过该行并告警，批次完整性由 300 秒超时自愈收尾。
        langgraph_id = extra.get("langgraph_resume_id")
        if not langgraph_id:
            logger.warning(
                "[ApprovalBatch] 审批行缺失 extra.langgraph_resume_id"
                "（interrupt_id=%s, batch=%s），跳过收集（批次将由超时自愈收尾）",
                approval.interrupt_id,
                graph_interrupt_id,
            )
            all_resolved = False
            continue

        if approval.state == Approval.State.APPROVED:
            resume_by_interrupt.setdefault(langgraph_id, {})[tc_id] = True
        elif approval.state in (Approval.State.PROCESSING, Approval.State.WAITING):
            # PROCESSING/WAITING 实际决策写入 extra._approved，
            # 缺失 _approved 时默认视为确认（True）
            _approved = extra.get("_approved")
            resume_by_interrupt.setdefault(langgraph_id, {})[tc_id] = bool(
                _approved is None or _approved is True
            )
        elif approval.state == Approval.State.REJECTED:
            resume_by_interrupt.setdefault(langgraph_id, {})[tc_id] = False
        elif approval.state == Approval.State.TIMEOUT:
            # 超时决策标记（TIMEOUT_DECISION）：与拒绝（False）区分，
            # middleware 据以注入"审批超时"ToolMessage，agent 调整策略继续
            resume_by_interrupt.setdefault(langgraph_id, {})[tc_id] = TIMEOUT_DECISION
        else:
            all_resolved = False

    return resume_by_interrupt, all_resolved


def finalize_batch_approvals(all_resume_values: dict, source: str, source_id: str) -> None:
    """审批终态化：对批次内所有已决断的审批记录调用 complete_approval。

    Args:
        all_resume_values: {langgraph_resume_id: {tool_call_id: bool}} 或 {tool_call_id: bool}。
        source: 审批来源（``Approval.Source.CHAT`` / ``Approval.Source.DEEP_RESEARCH``）。
        source_id: 会话/任务 ID。
    """
    from django.db import models

    from Django_xm.apps.approvals.services.approval_service import complete_approval

    flat_decisions: dict[str, bool] = {}
    for key, val in all_resume_values.items():
        if isinstance(val, dict):
            flat_decisions.update(val)
        elif isinstance(val, bool):
            flat_decisions[key] = val

    for tc_or_int_id, decision in flat_decisions.items():
        try:
            approval = Approval.objects.filter(
                source=source,
                source_id=source_id,
            ).filter(
                models.Q(interrupt_id=tc_or_int_id) | models.Q(extra__tool_call_id=tc_or_int_id)
            ).first()

            if approval is None:
                continue

            if approval.state in (Approval.State.PROCESSING, Approval.State.WAITING):
                final_state = Approval.State.APPROVED if decision is True else Approval.State.REJECTED
                complete_approval(approval.interrupt_id, final_state)
        except Exception:
            # 终态化失败不阻断恢复流程（幂等，下次重试可补齐）
            logger.warning(
                f"[ApprovalBatch] 终态化审批失败（幂等，下次重试可补齐）: "
                f"interrupt_id={tc_or_int_id}",
                exc_info=True,
            )
            continue


def _create_batch_approval_rows(
    source: str,
    source_id: str,
    prepared_items: list[tuple[str, dict[str, Any]]],
) -> list[Approval]:
    """单事务批量落库审批行（同步实现，经 sync_to_async 包装调用，REL-04）。

    整批同生共死：循环包裹 ``transaction.atomic()``，批内任一条创建失败即整批
    回滚并向上抛错——消除半批状态（缺失行永不在 collect_batch_decisions 遍历集
    内 → Command(resume) 决策不完整 / 批次悬挂至超时自愈）。research 主链路的
    异常经 adapter astream 兜底转 failed 终止任务（session_executor._handle_failure），
    子代理链路由调用方 try/except 兜底（langgraph_adapter._execute，状态已挂起）。

    事务内仅 DB 操作（幂等检查 + 落库，无网络调用）；Redis 待办/广播由调用方
    在事务提交后逐条执行。幂等保护：同一 interrupt_id 已有已决断审批
    （非 PENDING）时跳过，避免恢复执行中 agent 再次经过已决断 interrupt
    暂停点时用户决策被覆盖。
    """
    from django.db import transaction

    from Django_xm.apps.approvals.services.approval_service import (
        create_approval_record,
        prepare_approval_data,
    )

    created: list[Approval] = []
    with transaction.atomic():
        for interrupt_id, approval_data in prepared_items:
            # 幂等保护：同一 interrupt_id 已有已决断审批（非 PENDING）时跳过
            _existing = _find_approval_by_interrupt_id(interrupt_id)
            if _existing is not None and _existing.state != Approval.State.PENDING:
                continue
            normalized, now_ts, expires_at = prepare_approval_data(source, interrupt_id, approval_data)
            created.append(
                create_approval_record(
                    source,
                    source_id,
                    interrupt_id,
                    normalized,
                    now_ts=now_ts,
                    expires_at=expires_at,
                )
            )
    return created


async def create_approvals_for_interrupts(
    interrupts_data,
    *,
    source: str,
    source_id: str,
    user_id: int | None = None,
    chat_session_id: str | None = None,
    message_id: str = "",
    data: dict[str, Any] | None = None,
) -> str:
    """为已解析的审批中断批量创建 Approval DB 记录（整批同生共死，REL-04）。

    chat 与 research 双链路共用本函数（通过 ``source`` 区分）：
    - 共用 ``prepare_approval_data`` / ``create_approval_record``（统一持久化）
      与 ``build_approval_extra``；
    - 事务边界（REL-04）：批量落库包裹单事务，任一条失败整批回滚并向上抛错
      （走调用方既有终止/错误路径），替代原"单条失败 warning+continue"的半批语义；
    - 网络/Redis 副作用（待办列表 + PENDING 广播）在事务提交后逐条执行，
      广播失败不阻断批次（DB 已提交，挂起方 5s DB 轮询兜底）；
    - 幂等保护：同一 interrupt_id 已有已决断审批（非 PENDING）时不重置 state，
      避免恢复执行中 agent 再次经过已决断 interrupt 暂停点时用户决策被覆盖。

    Args:
        interrupts_data: 单个 interrupt dict 或 interrupt dict list（已解析的
            approval_data 列表，来自 ``common.approval_parser.parse_approval_interrupt``）。
        source: 审批来源（``Approval.Source.CHAT`` / ``Approval.Source.DEEP_RESEARCH``）。
        source_id: 审批归属 ID（chat=session_id, deep_research=task_id）。
        user_id: 任务归属用户 ID（approval.user 外键）。
        chat_session_id: 关联 chat 会话 ID（跨模块同步事件路由）。
        message_id: 关联 chat message ID（前端定位）。
        data: 任务上下文数据（透传到 approval extra）。

    Returns:
        str: 批次 ID（graph_interrupt_id），无有效审批时返回空字符串。
    """
    if isinstance(interrupts_data, dict):
        interrupts_data = [interrupts_data]
    if not interrupts_data:
        return ""

    from Django_xm.apps.approvals.services.approval_service import (
        broadcast_approval_pending,
        build_approval_extra,
    )

    first_interrupt = interrupts_data[0] if interrupts_data else {}
    graph_interrupt_id = first_interrupt.get("graph_interrupt_id", "") or ""
    langgraph_resume_id = first_interrupt.get("langgraph_resume_id", "") or ""

    # 阶段 0（纯 Python）：预构建各审批的 approval_data（无 DB / 网络副作用）
    prepared_items: list[tuple[str, dict[str, Any]]] = []
    for interrupt_data in interrupts_data:
        interrupt_id = interrupt_data.get("interrupt_id", "")
        if not interrupt_id:
            continue

        tool_name = interrupt_data.get("tool_name", "unknown")
        tool_call_id = interrupt_data.get("tool_call_id", "") or interrupt_id

        base_extra: dict[str, Any] = {}
        risk_level = interrupt_data.get("risk_level")
        if risk_level:
            base_extra["risk_level"] = risk_level
        for field in ("parent_tool_call_id", "depth", "agent_name", "agent_path", "subagent_thread_id"):
            val = interrupt_data.get(field)
            if val is not None and val not in ("", []):
                base_extra[field] = val

        approval_data = {
            "tool_name": tool_name,
            "title": interrupt_data.get("title", "确认操作"),
            "description": interrupt_data.get("description", ""),
            "operation": interrupt_data.get("operation", ""),
            "danger_level": interrupt_data.get("danger_level", "medium"),
            # 输入恒为 approval_parser 产物：字段名为 parameters（值源自生产者 args），
            # 不再重复实现 args 兼容（原 parameters or args 双份兼容已收敛到 parser）
            "parameters": interrupt_data.get("parameters") or {},
            "action": interrupt_data.get("action", Approval.ACTION_CONFIRM),
            "session_id": chat_session_id,
            "message_id": message_id,
            "extra": build_approval_extra(
                data or {},
                tool_call_id=tool_call_id,
                graph_interrupt_id=graph_interrupt_id,
                langgraph_resume_id=langgraph_resume_id,
                message_id=message_id,
                base_extra=base_extra,
            ),
        }
        prepared_items.append((interrupt_id, approval_data))

    # 阶段 1（单事务批量落库，REL-04 整批同生共死）：任一条失败整批回滚并
    # 向上抛错，走调用方既有终止/错误路径
    try:
        created_approvals = await sync_to_async(_create_batch_approval_rows)(
            source, source_id, prepared_items
        )
    except Exception:
        logger.exception(
            "[ApprovalBatch] 审批批次创建失败，整批回滚（任务走终止路径）: "
            f"graph_interrupt_id={graph_interrupt_id}, source={source}, source_id={source_id}"
        )
        raise

    # 阶段 2（事务提交后的 Redis 收尾）：待办列表 + PENDING 广播逐条发布；
    # 广播失败不阻断批次（DB 已提交，挂起方 5s DB 轮询兜底），仅告警。
    for approval in created_approvals:
        try:
            await broadcast_approval_pending(approval)
        except Exception:
            logger.warning(
                f"[ApprovalBatch] 审批 PENDING 广播失败（不阻断批次）: "
                f"interrupt_id={approval.interrupt_id}",
                exc_info=True,
            )

    return graph_interrupt_id


def assert_non_empty_decision(result) -> None:
    """审批决策不得为空：空 dict 表示审批创建失败，直接抛错终止任务。

    会话级单执行流语义：interrupt 回调必须返回非空决策（审批批次已创建），
    空返回意味着审批链路异常，任务无法等待恢复，只能终止。
    chat 与 research 双链路共用（抽象为模块级函数，避免 astream 循环
    try 块内直接 raise 的 TRY301）。
    """
    if isinstance(result, dict) and not result:
        raise RuntimeError(
            "审批创建失败：审批中断回调返回空决策，无审批批次可等待（任务终止）"
        )


def self_heal_expired_approvals(source: str, source_id: str, graph_interrupt_id: str) -> int:
    """批次自愈：对批次内已过期且仍为 pending 的审批终态化为 TIMEOUT。

    B⑦ 收敛：chat 与 research 双链路统一实现（source 参数化）。chat 链路
    此前缺失自愈语义（与 research 版行为不一致），现统一补齐。过期判定：
    - ``expires_at`` 已过；或
    - ``expires_at`` 缺失且 ``created_at`` 距今超过 300 秒（兜底）。

    Args:
        source: 审批来源（``Approval.Source.CHAT`` / ``Approval.Source.DEEP_RESEARCH``）。
        source_id: 会话/任务 ID（chat=session_id, deep_research=thread_id）。
        graph_interrupt_id: 批次 ID。

    Returns:
        int: 自愈终态化数量
    """
    from datetime import UTC, datetime, timedelta

    from django.db.models import Q

    from Django_xm.apps.approvals.services.approval_service import timeout_approval

    now = datetime.now(UTC)
    expired_ids = list(
        Approval.objects.filter(
            source=source,
            source_id=source_id,
            extra__graph_interrupt_id=graph_interrupt_id,
            state=Approval.State.PENDING,
        )
        .filter(
            Q(expires_at__lt=now)
            | Q(expires_at__isnull=True, created_at__lt=now - timedelta(seconds=300))
        )
        .values_list("interrupt_id", flat=True)
    )
    for interrupt_id in expired_ids:
        timeout_approval(interrupt_id, dispatch_resume=False)
    if expired_ids:
        logger.info(
            f"[ApprovalBatch] 批次自愈终态化 {len(expired_ids)} 条过期审批: "
            f"source={source}, source_id={source_id}, graph_interrupt_id={graph_interrupt_id}"
        )
    return len(expired_ids)


def abandon_session_pending_approvals(source: str, source_id: str) -> int:
    """会话终态收尾：该会话全部遗留 PENDING 审批终态化为 TIMEOUT（不触发恢复信令）。

    B4 配套（P2-11）：会话执行已终态（成功/停止/失败）后，遗留 PENDING 审批
    无法再被本运行消费 → 终态化，防止审批恢复扫描
    （SessionManager._recover_chat_approvals）反复重建已死会话。语义为
    TIMEOUT（用户未决策），非用户主动拒绝；dispatch_resume=False 与
    self_heal_expired_approvals 同构（无执行器可唤醒）。

    与批次级自愈的区别：不限定 graph_interrupt_id，收编该会话所有批次
    （含历史遗留的陈旧批次——恢复轮 newest-first 只消费 checkpoint 对应的
    最新批次，旧批次由本函数兜底收尾）。

    Args:
        source: 审批来源（``Approval.Source.CHAT`` / ``Approval.Source.DEEP_RESEARCH``）。
        source_id: 会话/任务 ID（chat=session_id, deep_research=thread_id）。

    Returns:
        int: 终态化数量
    """
    from Django_xm.apps.approvals.services.approval_service import timeout_approval

    stale_ids = list(
        Approval.objects.filter(
            source=source,
            source_id=source_id,
            state=Approval.State.PENDING,
        ).values_list("interrupt_id", flat=True)
    )
    for interrupt_id in stale_ids:
        timeout_approval(interrupt_id, dispatch_resume=False)
    if stale_ids:
        logger.info(
            f"[ApprovalBatch] 会话终态收尾终态化 {len(stale_ids)} 条遗留审批: "
            f"source={source}, source_id={source_id}"
        )
    return len(stale_ids)


async def await_wait_for_batch_decision(
    executor,
    *,
    source: str,
    source_id: str,
    graph_interrupt_id: str,
    poll_interval: int = 5,
    log_label: str = "ApprovalBatch",
    id_label: str = "source_id",
) -> dict:
    """挂起等待同批次全部审批决断，返回 Command(resume=...) 的 decisions。

    chat（``chat_executor_core._wait_for_chat_batch_decision``）与 research
    （``SessionExecutor._wait_for_batch_decision``）双链路唯一实现（B⑦ 收敛），
    仅 source / source_id / 轮询间隔 / 日志标签差异（参数化）。机制：

    - 优先检查 DB 批次完整性（可能已被前端提前全部确认）；
    - 否则挂起 ``executor._pending_events`` 中的 ``asyncio.Event``，
      由审批信令（on_approval_signal）唤醒；
    - 挂起期间周期性自愈过期审批（终态化 TIMEOUT）并轮询 DB
      （信令丢失最终一致兜底）；
    - 仅当同批次全部决断才退出（避免部分决断即恢复导致重入）；
    - 收到停止请求（``executor._stop_requested``）立即以 CancelledError 退出，
      由外层 finally 落库并广播。

    Args:
        executor: 执行器实例（提供 ``_pending_events`` / ``_stop_requested``，
            chat 与 research 执行器同构）。
        source: 审批来源（``Approval.Source.CHAT`` / ``Approval.Source.DEEP_RESEARCH``）。
        source_id: 会话/任务 ID（chat=session_id, deep_research=thread_id）。
        graph_interrupt_id: 批次 ID。
        poll_interval: 信令等待超时（秒），超时后轮询 DB（自愈/兜底）。
        log_label: 日志前缀（区分链路）。
        id_label: 日志中 source_id 的字段名（"session" / "thread_id"）。

    Returns:
        resume_by_interrupt: {langgraph_resume_id: {tool_call_id: bool|TIMEOUT_DECISION}}
    """
    event = asyncio.Event()
    executor._pending_events[graph_interrupt_id] = event
    try:
        while True:
            # 安全点（Task 9）：收到停止请求立即退出等待，由外层 finally 落库并广播
            if executor._stop_requested:
                raise asyncio.CancelledError("用户停止生成")
            resume_by_interrupt, all_resolved = await sync_to_async(collect_batch_decisions)(
                source, source_id, graph_interrupt_id
            )
            if all_resolved:
                break
            # 批次自愈：过期 pending 终态化为 TIMEOUT（chat/research 统一，B⑦）
            await sync_to_async(self_heal_expired_approvals)(source, source_id, graph_interrupt_id)
            # 等待审批信令（周期轮询兜底）。信令唤醒或轮询超时均重新读取 DB，
            # 只有全部决断才退出循环——保证 Command(resume=...) 携带完整批次决策。
            event.clear()
            try:
                await asyncio.wait_for(event.wait(), timeout=poll_interval)
            except TimeoutError:
                pass  # 未收到信令：继续轮询校验
    finally:
        executor._pending_events.pop(graph_interrupt_id, None)

    logger.info(
        f"[{log_label}] 批次全部决断，恢复执行: {id_label}={source_id}, "
        f"graph_interrupt_id={graph_interrupt_id}, decisions={resume_by_interrupt}"
    )
    await sync_to_async(finalize_batch_approvals)(resume_by_interrupt, source, source_id)
    return resume_by_interrupt
