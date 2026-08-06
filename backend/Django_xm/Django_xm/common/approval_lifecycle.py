"""审批生命周期服务（三模块共享的状态机）。

管理审批从创建到终态的完整批次上下文：
  pending → processing/waiting → approved/rejected/timeout

核心保证：
1. graph_interrupt_id 单一来源（从 ApprovalMiddleware._meta 透传）
2. 批量终态化原子性（select_for_update 锁定同批次所有 approval）
3. 不重复终态化（已终态的 approval 跳过，不广播）
4. 三模块共享同一份代码，删除 _finalize_waiting_siblings_* 和 _complete_all_sibling_approvals_async

模块级单例：service = ApprovalLifecycleService()
"""

import logging
from datetime import UTC, datetime

from asgiref.sync import sync_to_async
from django.db import transaction

from Django_xm.apps.approvals.models import Approval
from Django_xm.apps.approvals.services.approval_service import (
    _clean_extra_temp_keys,
    _persist_and_broadcast,
    _publish_tool_call_timeout_event,
    _release_lock,
)

logger = logging.getLogger(__name__)

_TERMINAL_STATES = {
    Approval.STATE_APPROVED,
    Approval.STATE_REJECTED,
    Approval.STATE_TIMEOUT,
}


def _now():
    return datetime.now(UTC)


class ApprovalLifecycleService:
    """审批生命周期服务（模块级单例，三模块共享）。

    提供 complete_batch 唯一终态化入口，替代散落在各模块的
    _finalize_waiting_siblings_* / _complete_all_sibling_approvals_async。
    """

    @transaction.atomic
    def complete_batch(
        self,
        graph_interrupt_id: str,
        trigger_interrupt_id: str,
        trigger_final_state: str,
    ) -> dict:
        """原子化完成同批次所有审批（根因 C 修复）。

        1. select_for_update 锁定同 graph_interrupt_id 下所有 approval
        2. 遍历计算每个 approval 的终态（_timeout > _approved > trigger_final_state）
        3. 逐个 _persist_and_broadcast（已包含 Redis 同步 + 事件广播）
        4. 已终态的跳过（不重复广播）

        Args:
            graph_interrupt_id: 批次 ID（ApprovalMiddleware 生成，嵌入 _meta）
            trigger_interrupt_id: 触发终态化的审批 ID
            trigger_final_state: 触发审批的终态（approved/rejected/timeout）

        Returns:
            dict: {'total': N, 'success': N, 'skipped': N, 'failed': N}
        """
        if not graph_interrupt_id:
            return self._complete_single(trigger_interrupt_id, trigger_final_state)

        siblings = list(
            Approval.objects.select_for_update().filter(
                extra__graph_interrupt_id=graph_interrupt_id,
            )
        )
        if not siblings:
            logger.warning(f"[ApprovalLifecycle] 未找到同批次审批: graph_interrupt_id={graph_interrupt_id}")
            return self._complete_single(trigger_interrupt_id, trigger_final_state)

        result = {"total": len(siblings), "success": 0, "skipped": 0, "failed": 0}
        for sibling in siblings:
            # 已终态的跳过（包括触发审批自身，避免重复广播）
            if sibling.state in _TERMINAL_STATES:
                result["skipped"] += 1
                continue
            sib_final_state = self._compute_final_state(sibling, trigger_final_state)
            try:
                self._finalize_one(sibling, sib_final_state)
                result["success"] += 1
            except Exception:
                result["failed"] += 1
                logger.exception(
                    f"[ApprovalLifecycle] 终态化失败: "
                    f"interrupt_id={sibling.interrupt_id}, "
                    f"graph_interrupt_id={graph_interrupt_id}, err=",
                )
        logger.info(
            f"[ApprovalLifecycle] 批量终态化完成: "
            f"graph_interrupt_id={graph_interrupt_id}, "
            f"trigger={trigger_interrupt_id}, state={trigger_final_state}, "
            f"result={result}"
        )
        return result

    async def complete_batch_async(
        self,
        graph_interrupt_id: str,
        trigger_interrupt_id: str,
        trigger_final_state: str,
    ) -> dict:
        """异步版本：通过 sync_to_async 包装同步 complete_batch。

        保持与同步版本完全一致的事务语义（select_for_update + atomic）。
        """

        @sync_to_async
        def _do():
            return self.complete_batch(
                graph_interrupt_id,
                trigger_interrupt_id,
                trigger_final_state,
            )

        return await _do()

    def _compute_final_state(
        self,
        approval: Approval,
        trigger_final_state: str,
    ) -> str:
        """根据 approval.extra 中的标记计算终态。

        优先级：_timeout > _approved > trigger_final_state（兜底）
        - _timeout=True  → TIMEOUT（超时标记优先）
        - _approved=True → APPROVED
        - _approved=False → REJECTED
        - 缺失字段       → 与 trigger 终态一致（兜底，用于 pending 孤儿审批）
        """
        sib_extra = approval.extra if isinstance(approval.extra, dict) else {}
        if sib_extra.get("_timeout"):
            return Approval.STATE_TIMEOUT
        if "_approved" in sib_extra:
            return Approval.STATE_APPROVED if sib_extra["_approved"] else Approval.STATE_REJECTED
        return trigger_final_state

    def _finalize_one(self, approval: Approval, final_state: str) -> None:
        """终态化单个 approval（DB + Redis + 广播 + 同步 ChatMessage）。

        复用 approval_service 的 _persist_and_broadcast 确保 DB/Redis/广播三层一致。
        清理 extra 临时字段（_resume_value/_approved/_timeout），保留 graph_interrupt_id 等业务字段。
        """
        approval.state = final_state
        approval.resolved_at = _now()
        approval.extra = _clean_extra_temp_keys(approval)
        approval.save(update_fields=["state", "resolved_at", "extra"])

        _persist_and_broadcast(approval, final_state)
        # 终态为 timeout 时，额外发布 TOOL_CALL_TIMEOUT 事件
        # 让前端 ToolCallCard 显示"已超时"（审批事件与工具事件分离）
        if final_state == Approval.STATE_TIMEOUT:
            _publish_tool_call_timeout_event(approval)
        _release_lock(approval.interrupt_id)

        # 注意：不再重复调用 sync_approval_state_to_chat_message ——
        # _persist_and_broadcast 内部已统一调用（含全字段比对幂等检查），
        # 此处删除原显式重复调用，避免双重副作用（Task 2.3）。

    def _complete_single(
        self,
        interrupt_id: str,
        final_state: str,
    ) -> dict:
        """单审批场景（无 graph_interrupt_id）的兜底处理。"""
        try:
            approval = Approval.objects.get(interrupt_id=interrupt_id)
        except Approval.DoesNotExist:
            logger.warning(f"[ApprovalLifecycle] 审批记录不存在: {interrupt_id}")
            return {"total": 0, "success": 0, "skipped": 0, "failed": 0}

        if approval.state in _TERMINAL_STATES:
            return {"total": 1, "success": 0, "skipped": 1, "failed": 0}
        try:
            self._finalize_one(approval, final_state)
            return {"total": 1, "success": 1, "skipped": 0, "failed": 0}
        except Exception:
            logger.exception(
                f"[ApprovalLifecycle] 单审批终态化失败: interrupt_id={interrupt_id}, err=",
            )
            return {"total": 1, "success": 0, "skipped": 0, "failed": 1}


# 模块级单例
service = ApprovalLifecycleService()
