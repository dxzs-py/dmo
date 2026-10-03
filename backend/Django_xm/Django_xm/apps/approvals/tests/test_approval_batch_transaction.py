"""create_approvals_for_interrupts 批量创建事务测试（REL-04）。

覆盖：
- 全部成功 → 批量落库，且 PENDING 广播在事务提交后发出
  （经 transaction.on_commit 哨兵证明 commit 先于 broadcast）；
- 批内第 N 条 DB 失败 → 整批回滚：任何审批行不落库，异常向上抛出
  （走调用方既有终止/错误路径），广播不发出；
- 幂等保护保留：已有已决断（非 PENDING）审批的 interrupt 跳过，
  不重置 state 且不广播。

mock 策略：Redis 侧（broadcast_approval_pending）打桩；DB 使用真实测试库
（test_ 前缀临时库，与 test_approval_state_machine 一致）。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.approvals.tests.test_approval_batch_transaction

注：被测函数为 async 且经 sync_to_async 在执行线程连接上落库提交，
TestCase 的事务回滚无法覆盖，故使用 TransactionTestCase（每用例后 flush）。
"""

import asyncio
import os
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from asgiref.sync import sync_to_async
from django.db import connections, transaction
from django.test import TransactionTestCase

from Django_xm.apps.approvals.models import Approval
from Django_xm.apps.approvals.services import approval_service
from Django_xm.apps.approvals.services.approval_batch import create_approvals_for_interrupts

_AS_MODULE = "Django_xm.apps.approvals.services.approval_service"

_SOURCE = Approval.Source.DEEP_RESEARCH
_SOURCE_ID = "task-w2d-batch"
_BATCH = "batch-w2d-1"
_LR = "lr-batch-1"


async def _close_executor_thread_connections():
    """关闭 sync_to_async 执行线程的残留 DB 连接（用例收尾必须调用）。

    被测函数经 sync_to_async 在执行线程的 thread-local 连接上提交事务；该
    连接不随 asyncio.run 结束关闭，残留会导致测试库 teardown DROP 报
    ObjectInUse。与被测写入同处一个事件循环 → 同一执行线程，可正确关闭。
    """
    await sync_to_async(connections.close_all)()


def _interrupt(i: int) -> dict:
    # 批次契约：同批各行共享批次级 graph_interrupt_id / langgraph_resume_id
    # （首行 Interrupt.id，collect_batch_decisions 按其分组收集决策）
    return {
        "interrupt_id": f"int-{i}",
        "tool_name": "shell_exec",
        "tool_call_id": f"tc-{i}",
        "graph_interrupt_id": _BATCH,
        "langgraph_resume_id": _LR,
        "title": f"确认操作 {i}",
        "parameters": {"cmd": f"echo {i}"},
    }


class CreateApprovalsTransactionTests(TransactionTestCase):
    """批量创建事务边界（REL-04）：整批同生共死 + 广播在 commit 后。"""

    def setUp(self):
        self.broadcast_mock = mock.AsyncMock()
        patcher = mock.patch(f"{_AS_MODULE}.broadcast_approval_pending", self.broadcast_mock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _batch_ids(self) -> list[str]:
        return list(
            Approval.objects.filter(source=_SOURCE, source_id=_SOURCE_ID).values_list(
                "interrupt_id", flat=True
            )
        )

    def test_all_success_persists_batch_and_broadcasts_after_commit(self):
        """全成功：批量落库 + 每行一次广播，且 commit 先于 broadcast。"""
        commit_order: list[str] = []

        real_create = approval_service.create_approval_record

        def _spy_create(source, source_id, interrupt_id, approval_data, **kwargs):
            approval = real_create(source, source_id, interrupt_id, approval_data, **kwargs)
            # 注册在执行线程连接的当前事务上：仅在该事务真实 COMMIT 后触发
            transaction.on_commit(lambda: commit_order.append(f"commit:{interrupt_id}"))
            return approval

        async def _broadcast_probe(approval):
            commit_order.append(f"broadcast:{approval.interrupt_id}")

        self.broadcast_mock.side_effect = _broadcast_probe

        async def _case():
            try:
                with mock.patch(f"{_AS_MODULE}.create_approval_record", _spy_create):
                    batch_id = await create_approvals_for_interrupts(
                        [_interrupt(1), _interrupt(2), _interrupt(3)],
                        source=_SOURCE,
                        source_id=_SOURCE_ID,
                    )
                self.assertEqual(batch_id, _BATCH)
            finally:
                await _close_executor_thread_connections()

        asyncio.run(_case())

        # 批量落库：3 行全部存在，字段/extra 完整
        rows = Approval.objects.filter(source=_SOURCE, source_id=_SOURCE_ID)
        by_id = {r.interrupt_id: r for r in rows}
        self.assertEqual(set(by_id), {"int-1", "int-2", "int-3"})
        for i in (1, 2, 3):
            row = by_id[f"int-{i}"]
            self.assertEqual(row.state, Approval.State.PENDING)
            self.assertEqual(row.tool_name, "shell_exec")
            self.assertEqual(row.extra.get("tool_call_id"), f"tc-{i}")
            self.assertEqual(row.extra.get("graph_interrupt_id"), _BATCH)
            self.assertEqual(row.extra.get("langgraph_resume_id"), _LR)

        # 每行广播一次
        self.assertEqual(self.broadcast_mock.await_count, 3)

        # 事件在 commit 后发出：每行的 commit 哨兵先于对应 broadcast
        self.assertEqual(len(commit_order), 6)
        position = {event: idx for idx, event in enumerate(commit_order)}
        for i in (1, 2, 3):
            self.assertLess(
                position[f"commit:int-{i}"],
                position[f"broadcast:int-{i}"],
                f"int-{i} 的广播先于事务提交（违反事务内禁网络调用约束）",
            )

    def test_nth_item_failure_rolls_back_whole_batch(self):
        """批内第 2 条失败：整批回滚（第 1 条不残留），异常向上抛出，无广播。"""
        real_create = approval_service.create_approval_record
        create_calls = {"n": 0}

        def _boom_on_second(source, source_id, interrupt_id, approval_data, **kwargs):
            create_calls["n"] += 1
            if create_calls["n"] == 2:
                raise RuntimeError("模拟第 2 条审批创建失败（DB 抖动）")
            return real_create(source, source_id, interrupt_id, approval_data, **kwargs)

        async def _case():
            try:
                with mock.patch(f"{_AS_MODULE}.create_approval_record", _boom_on_second):
                    await create_approvals_for_interrupts(
                        [_interrupt(1), _interrupt(2), _interrupt(3)],
                        source=_SOURCE,
                        source_id=_SOURCE_ID,
                    )
            finally:
                await _close_executor_thread_connections()

        with self.assertRaises(RuntimeError):
            asyncio.run(_case())

        # 整批不落库：第 1 条已随事务回滚（无事务包裹时此处会残留 1 行）
        self.assertEqual(self._batch_ids(), [])
        self.assertEqual(
            Approval.objects.filter(extra__graph_interrupt_id=_BATCH).count(), 0
        )
        # 无任何广播发出
        self.broadcast_mock.assert_not_awaited()

    def test_idempotent_skip_keeps_existing_decided_approval(self):
        """幂等保护：已决断审批跳过（state 不被重置），仅新行落库并广播。"""
        Approval.objects.create(
            interrupt_id="int-1",
            source=_SOURCE,
            source_id=_SOURCE_ID,
            chat_session_id=None,
            tool_name="shell_exec",
            state=Approval.State.APPROVED,
            extra={"tool_call_id": "tc-1", "graph_interrupt_id": _BATCH, "langgraph_resume_id": _LR},
        )

        async def _case():
            try:
                await create_approvals_for_interrupts(
                    [_interrupt(1), _interrupt(2)],
                    source=_SOURCE,
                    source_id=_SOURCE_ID,
                )
            finally:
                await _close_executor_thread_connections()

        asyncio.run(_case())

        rows = Approval.objects.filter(source=_SOURCE, source_id=_SOURCE_ID)
        by_id = {r.interrupt_id: r for r in rows}
        self.assertEqual(set(by_id), {"int-1", "int-2"})
        # 已决断行不被重置
        self.assertEqual(by_id["int-1"].state, Approval.State.APPROVED)
        # 仅新行落库
        self.assertEqual(by_id["int-2"].state, Approval.State.PENDING)
        # 仅新行广播
        self.assertEqual(self.broadcast_mock.await_count, 1)
        self.assertEqual(self.broadcast_mock.await_args[0][0].interrupt_id, "int-2")
