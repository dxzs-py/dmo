"""timeout_approval 锁内竞态测试（D-5 修复回归）。

背景（D-5）：
- timeout_approval 锁外读 state（非 PENDING 即 return），拿锁后不重读，
  随后无条件写 PROCESSING→TIMEOUT。
- 竞态：锁外读到 PENDING 后、拿锁前，用户 resume_approval 已置 APPROVED
  （resume_approval 与 timeout_approval 共用批次锁）；timeout 拿锁后不重读，
  直接覆盖用户决断 → 审批显示"已超时"但 agent 实际已恢复执行。

修复：对齐 resume_approval 锁内重读模式——拿锁后重读 state，
非 PENDING 一律幂等放弃。

覆盖：
- 锁内重读到 APPROVED：不覆盖（state 保持 APPROVED，extra 无 _timeout 标记）
- 锁内重读到 PENDING：正常终态化 TIMEOUT（正常路径不回归）
- 锁被持有：跳过，state 保持 PENDING（既有幂等语义不回归）

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.approvals.tests.test_approval_timeout_race --noinput
"""

import os
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.test import TestCase

from Django_xm.apps.approvals.models import Approval
from Django_xm.apps.approvals.services import approval_service


def _make_approval(interrupt_id, graph_interrupt_id, state=Approval.State.PENDING):
    return Approval.objects.create(
        interrupt_id=interrupt_id,
        source="chat",
        source_id="session-1",
        tool_name="shell_exec",
        state=state,
        action=Approval.ACTION_CONFIRM,
        extra={"graph_interrupt_id": graph_interrupt_id},
    )


class TimeoutApprovalRaceTests(TestCase):
    """timeout_approval 拿锁后重读 state（D-5）。"""

    def _patch_side_effects(self):
        """隔离 Redis/广播副作用（与 test_approval_batch_resume 同模式）。"""
        patchers = [
            mock.patch.object(approval_service, "release_lock"),
            mock.patch.object(approval_service, "persist_and_broadcast"),
            mock.patch.object(approval_service, "publish_tool_call_timeout_event"),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_lock_inner_approved_not_overridden(self):
        """锁外读 PENDING → 拿锁瞬间被 resume 置 APPROVED → 锁内重读放弃覆盖。"""
        self._patch_side_effects()
        approval = _make_approval("call-race", "gid-race")

        def acquire_lock_then_resume_wins(lock_key):
            # 模拟竞态：锁外读已过（PENDING），resume_approval 抢先落库 APPROVED
            Approval.objects.filter(interrupt_id="call-race").update(state=Approval.State.APPROVED)
            return True

        with mock.patch.object(
            approval_service, "_acquire_lock", side_effect=acquire_lock_then_resume_wins
        ):
            approval_service.timeout_approval("call-race", dispatch_resume=False)

        refreshed = Approval.objects.get(interrupt_id="call-race")
        self.assertEqual(refreshed.state, Approval.State.APPROVED)
        self.assertIs(refreshed.extra.get("_timeout"), None)
        approval_service.persist_and_broadcast.assert_not_called()

    def test_pending_still_finalizes_to_timeout(self):
        """锁内重读仍为 PENDING：正常终态化 TIMEOUT（正常路径不回归）。"""
        self._patch_side_effects()
        _make_approval("call-ok", "gid-ok")

        with mock.patch.object(approval_service, "_acquire_lock", return_value=True):
            approval_service.timeout_approval("call-ok", dispatch_resume=False)

        refreshed = Approval.objects.get(interrupt_id="call-ok")
        self.assertEqual(refreshed.state, Approval.State.TIMEOUT)
        self.assertTrue(refreshed.extra.get("_timeout"))

    def test_lock_busy_skips_and_keeps_pending(self):
        """锁被持有：跳过超时处理，state 保持 PENDING（既有语义不回归）。"""
        self._patch_side_effects()
        _make_approval("call-busy", "gid-busy")

        with mock.patch.object(approval_service, "_acquire_lock", return_value=False):
            approval_service.timeout_approval("call-busy", dispatch_resume=False)

        self.assertEqual(
            Approval.objects.get(interrupt_id="call-busy").state,
            Approval.State.PENDING,
        )
