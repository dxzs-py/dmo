"""collect_batch_decisions 缺字段行为（spec approval-id-fallback-refactor A4/A5/A6）。

数据前置说明（A7 审计结论，2026-09-30）：
- extra.tool_call_id / extra.langgraph_resume_id 字段自 2026-08-03 起由
  build_approval_extra 恒写，生产路径不再产生缺字段行；
- 缺字段的 181 条历史僵尸行已一次性终态化为 TIMEOUT（audit_fix 标记），
  本测试构造的缺字段行仅用于锁定消费侧行为契约。
"""

import logging

from django.test import TestCase

from Django_xm.apps.approvals.models import Approval
from Django_xm.apps.approvals.services.approval_batch import collect_batch_decisions

BATCH_LOGGER = "Django_xm.apps.approvals.services.approval_batch"


def _make_approval(interrupt_id, extra, state=Approval.State.APPROVED):
    return Approval.objects.create(
        interrupt_id=interrupt_id,
        source=Approval.Source.CHAT,
        source_id="sess-1",
        tool_name="shell_exec",
        state=state,
        extra=extra,
    )


class CollectBatchDecisionsMissingFieldTests(TestCase):
    def test_missing_langgraph_resume_id_skipped_not_resolved(self):
        """A5：extra 缺 langgraph_resume_id → 跳过该行 + WARNING + all_resolved=False
        （旧行为回退批次 uuid 作为 resume key，会静默发起注定失败的恢复）。"""
        _make_approval(
            "tc-bad",
            {"tool_call_id": "tc-bad", "graph_interrupt_id": "g1"},
            state=Approval.State.APPROVED,
        )
        _make_approval(
            "tc-ok",
            {
                "tool_call_id": "tc-ok",
                "graph_interrupt_id": "g1",
                "langgraph_resume_id": "int-1",
            },
            state=Approval.State.REJECTED,
        )
        with self.assertLogs(BATCH_LOGGER, level=logging.WARNING) as cm:
            decisions, all_resolved = collect_batch_decisions(Approval.Source.CHAT, "sess-1", "g1")
        # tc-bad 被跳过：decisions 只含字段齐全的 tc-ok
        self.assertEqual(decisions, {"int-1": {"tc-ok": False}})
        # 跳过行阻断批次完整性判定：由超时自愈收尾（不静默恢复）
        self.assertFalse(all_resolved)
        self.assertTrue(
            any("langgraph_resume_id" in m for m in cm.output),
            f"期望缺失 langgraph_resume_id 告警，实际: {cm.output}",
        )

    def test_tool_call_id_equivalent_fallback_to_interrupt_id(self):
        """A4：extra.tool_call_id 缺失 → 等价回退 approval.interrupt_id（parser 恒写
        二者同值，等价无害，保留）。"""
        _make_approval(
            "tc-1",
            {"graph_interrupt_id": "g1", "langgraph_resume_id": "int-1"},
        )
        decisions, all_resolved = collect_batch_decisions(Approval.Source.CHAT, "sess-1", "g1")
        self.assertEqual(decisions, {"int-1": {"tc-1": True}})
        self.assertTrue(all_resolved)

    def test_normal_path_unchanged(self):
        """A6：字段齐全 → 与现行为一致（回归基线）。"""
        _make_approval(
            "tc-1",
            {
                "tool_call_id": "tc-1",
                "graph_interrupt_id": "g1",
                "langgraph_resume_id": "int-1",
            },
            state=Approval.State.REJECTED,
        )
        _make_approval(
            "tc-2",
            {
                "tool_call_id": "tc-2",
                "graph_interrupt_id": "g1",
                "langgraph_resume_id": "int-1",
            },
            state=Approval.State.TIMEOUT,
        )
        with self.assertNoLogs(BATCH_LOGGER, level=logging.WARNING):
            decisions, all_resolved = collect_batch_decisions(Approval.Source.CHAT, "sess-1", "g1")
        self.assertEqual(decisions, {"int-1": {"tc-1": False, "tc-2": "_timeout"}})
        self.assertTrue(all_resolved)
