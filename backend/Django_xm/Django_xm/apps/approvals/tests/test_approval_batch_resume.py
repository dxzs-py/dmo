"""批量审批批次完整性测试（问题 21 回归：并发确认批次永久卡 waiting）。

覆盖：
- 串行确认：同批次逐个确认，前 N-1 个置 waiting，最后一个确认者触发恢复（非 waiting）
- waiting 复查：并发竞态残留的 waiting 在兄弟全部决断后，锁内复查升级 processing 并触发恢复
- waiting 复查保持：批次仍有 pending 兄弟时，复查保持 waiting（不误触发）

修复根因（approval_service.resume_approval）：
- 锁粒度从单审批（interrupt_id）提升为批次（graph_interrupt_id），同批次确认串行化，
  最后一个获取锁的请求必然看到兄弟全部落库 → 触发批次恢复
- waiting 状态锁内复查批次完整性，杜绝并发读旧值导致的永久 waiting

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.approvals.tests.test_approval_batch_resume
"""

import os
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.test import TestCase

from Django_xm.apps.approvals.models import Approval
from Django_xm.apps.approvals.services import approval_service


def _make_approval(interrupt_id, graph_interrupt_id, state=Approval.STATE_PENDING, extra=None):
    base = {"graph_interrupt_id": graph_interrupt_id}
    if extra:
        base.update(extra)
    return Approval.objects.create(
        interrupt_id=interrupt_id,
        source="chat",
        source_id="session-1",
        tool_name="shell_exec",
        state=state,
        action=Approval.ACTION_CONFIRM,
        extra=base,
    )


class TestBatchResumeCompleteness(TestCase):
    """批量审批批次完整性（问题 21）。"""

    def setUp(self):
        # 隔离 Redis/广播副作用：锁可用 + 广播/释放无操作，仅验证批次状态机逻辑
        patchers = [
            mock.patch.object(approval_service, "_acquire_lock_with_retry", return_value=True),
            mock.patch.object(approval_service, "_release_lock"),
            mock.patch.object(approval_service, "_persist_and_broadcast"),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_sequential_confirms_last_triggers_resume(self):
        """串行确认：前 4 个 waiting，最后 1 个触发恢复（非 waiting）。"""
        gid = "gid-seq"
        for i in range(5):
            _make_approval(f"call_{i}", gid)

        for i in range(4):
            result = approval_service.resume_approval(f"call_{i}", approved=True)
            self.assertEqual(result["state"], Approval.STATE_WAITING)
            self.assertEqual(
                Approval.objects.get(interrupt_id=f"call_{i}").state,
                Approval.STATE_WAITING,
            )

        result = approval_service.resume_approval("call_4", approved=True)
        # 批次完整：返回不含 waiting 状态 → 调用方触发恢复
        self.assertNotIn("state", result)
        # 前 4 个保持 waiting（等待批次恢复后统一消费），最后确认者置 processing 触发恢复
        for i in range(4):
            self.assertEqual(
                Approval.objects.get(interrupt_id=f"call_{i}").state,
                Approval.STATE_WAITING,
            )
        self.assertEqual(
            Approval.objects.get(interrupt_id="call_4").state,
            Approval.STATE_PROCESSING,
        )

    def test_waiting_recheck_upgrades_when_batch_complete(self):
        """waiting 复查：兄弟全部决断后，waiting 升级 processing 并触发恢复。"""
        gid = "gid-wait"
        for i in range(5):
            _make_approval(f"call_{i}", gid)

        # 模拟并发竞态残留：4 个已决断（processing），1 个 waiting（兄弟落库前误判）
        for i in range(4):
            a = Approval.objects.get(interrupt_id=f"call_{i}")
            a.state = Approval.STATE_PROCESSING
            a.extra["_resume_value"] = True
            a.extra["_approved"] = True
            a.save(update_fields=["state", "extra"])
        a = Approval.objects.get(interrupt_id="call_4")
        a.state = Approval.STATE_WAITING
        a.extra["_resume_value"] = True
        a.extra["_approved"] = True
        a.save(update_fields=["state", "extra"])

        # waiting 审批再次确认 → 锁内复查批次完整 → 升级 processing 触发恢复
        result = approval_service.resume_approval("call_4", approved=True)
        self.assertNotIn("state", result)
        self.assertEqual(
            Approval.objects.get(interrupt_id="call_4").state,
            Approval.STATE_PROCESSING,
        )

    def test_waiting_recheck_keeps_waiting_when_siblings_pending(self):
        """waiting 复查：批次仍有 pending 兄弟时保持 waiting（不误触发恢复）。"""
        gid = "gid-keep"
        for i in range(5):
            _make_approval(f"call_{i}", gid)

        a = Approval.objects.get(interrupt_id="call_0")
        a.state = Approval.STATE_WAITING
        a.extra["_resume_value"] = True
        a.extra["_approved"] = True
        a.save(update_fields=["state", "extra"])

        result = approval_service.resume_approval("call_0", approved=True)
        self.assertEqual(result["state"], Approval.STATE_WAITING)
        self.assertEqual(
            Approval.objects.get(interrupt_id="call_0").state,
            Approval.STATE_WAITING,
        )
