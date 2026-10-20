"""await_wait_for_batch_decision 共享等待收敛测试（审计 B⑦）。

覆盖：
- chat 链路过期审批触发自愈（补齐 research 版语义，source=chat）；
- research 链路自愈 source 保持 deep_research（行为不变）；
- 两执行器（chat / research）对同一输入产出一致决策，
  finalize 仅 source / source_id 不同；
- 停止请求安全点（CancelledError）。

隔离策略：不触碰真实 DB/Redis——collect_batch_decisions / self_heal_expired_approvals /
finalize_batch_approvals 全部 mock（patch 打在 approval_batch 模块命名空间），
仅验证共享实现的编排逻辑与两执行器委托参数。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.services.fastapi_service.tests.test_await_wait_for_batch_decision

注：原位于 apps/approvals/tests，因"apps 业务层禁止依赖 services 执行编排层"
importlinter 契约迁至本目录（fastapi_service 双向导入合法）。
"""

import asyncio
import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from Django_xm.apps.approvals.models import Approval
from Django_xm.services.fastapi_service.chat_executor_core import _wait_for_chat_batch_decision
from Django_xm.services.fastapi_service.session_executor import SessionExecutor

_AB_MODULE = "Django_xm.apps.approvals.services.approval_batch"
_SE_MODULE = "Django_xm.services.fastapi_service.session_executor"
_CHAT_MODULE = "Django_xm.services.fastapi_service.chat_executor_core"


def _make_executor(thread_id="t1", session_id="s1"):
    """构造真实 SessionExecutor（提供 _pending_events / _stop_requested / 信令唤醒）。"""
    return SessionExecutor(
        mock.Mock(),
        thread_id=thread_id,
        query="q",
        user_id=1,
        session_id=session_id,
        message_id="m1",
        params={},
    )


def _patch_shared(collect=None, heal=None, finalize=None):
    """patch 共享等待循环的三个依赖（approval_batch 模块命名空间）。

    三个依赖恒 patch：任一漏 patch 都会让共享循环触发真实 ORM（测试为
    unittest.TestCase，无 Django 测试库事务隔离）。
    """
    return mock.patch.multiple(
        _AB_MODULE,
        collect_batch_decisions=collect if collect is not None else mock.DEFAULT,
        self_heal_expired_approvals=heal if heal is not None else mock.DEFAULT,
        finalize_batch_approvals=finalize if finalize is not None else mock.DEFAULT,
    )


class TestChatSelfHeal(unittest.TestCase):
    """chat 链路过期审批自愈（B⑦ 补齐，原 chat 版缺失该语义）。"""

    def test_chat_pending_triggers_self_heal_with_chat_source(self):
        """chat 批次未决断时周期性自愈，且 source=chat（参数化生效）。"""
        async def _case():
            ex = _make_executor(thread_id="t-chat", session_id="sess-chat")
            collect = mock.Mock(return_value=(None, False))
            heal = mock.Mock()
            with _patch_shared(collect=collect, heal=heal), mock.patch(
                f"{_CHAT_MODULE}._CHAT_BATCH_POLL_INTERVAL", 0.01
            ):
                async def trigger():
                    await asyncio.sleep(0.05)
                    ex.on_approval_signal({"graph_interrupt_id": "g1"})
                t = asyncio.create_task(trigger())
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        _wait_for_chat_batch_decision(ex, "sess-chat", "g1"), timeout=0.15
                    )
                await t
            heal.assert_called()
            # 自愈必须限定 chat 来源 + chat 会话，避免误终态化其他链路审批
            heal.assert_called_with(Approval.Source.CHAT, "sess-chat", "g1")

        asyncio.run(_case())

    def test_research_pending_triggers_self_heal_with_research_source(self):
        """research 批次未决断时周期性自愈，source=deep_research（行为不变）。"""
        async def _case():
            ex = _make_executor(thread_id="t-res", session_id="s-res")
            collect = mock.Mock(return_value=(None, False))
            heal = mock.Mock()
            with _patch_shared(collect=collect, heal=heal), mock.patch(
                f"{_SE_MODULE}._BATCH_POLL_INTERVAL", 0.01
            ):
                async def trigger():
                    await asyncio.sleep(0.05)
                    ex.on_approval_signal({"graph_interrupt_id": "g1"})
                t = asyncio.create_task(trigger())
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(ex._wait_for_batch_decision("g1"), timeout=0.15)
                await t
            heal.assert_called()
            heal.assert_called_with(Approval.Source.DEEP_RESEARCH, "t-res", "g1")

        asyncio.run(_case())


class TestConsistentDecisions(unittest.TestCase):
    """两执行器对同一输入产出一致决策（B⑦ 收敛验收）。"""

    def test_same_input_same_decision(self):
        """同一 collect 输入：chat / research 返回相同 decisions，
        finalize 参数仅 source / source_id 不同。"""
        async def _case():
            full = {"r1": {"t1": True, "t2": False}}

            # chat 链路（批次已全部决断：立即返回）
            ex_chat = _make_executor(thread_id="t1", session_id="s1")
            fin_chat = mock.Mock()
            with _patch_shared(
                collect=mock.Mock(return_value=(full, True)), finalize=fin_chat
            ):
                chat_decisions = await _wait_for_chat_batch_decision(ex_chat, "s1", "g1")

            # research 链路（同一输入）
            ex_res = _make_executor(thread_id="t1", session_id="s1")
            fin_res = mock.Mock()
            with _patch_shared(
                collect=mock.Mock(return_value=(full, True)), finalize=fin_res
            ):
                research_decisions = await ex_res._wait_for_batch_decision("g1")

            self.assertEqual(chat_decisions, research_decisions)
            self.assertEqual(chat_decisions, full)
            fin_chat.assert_called_once_with(full, Approval.Source.CHAT, "s1")
            fin_res.assert_called_once_with(full, Approval.Source.DEEP_RESEARCH, "t1")

        asyncio.run(_case())

    def test_signal_wake_revalidates_batch_for_both(self):
        """信令唤醒后必须重新校验批次完整性：两链路均不早退（共享语义）。"""
        async def _case():
            collect = mock.Mock(return_value=(None, False))
            ex = _make_executor()
            with _patch_shared(collect=collect), mock.patch(
                f"{_SE_MODULE}._BATCH_POLL_INTERVAL", 0.01
            ), mock.patch(f"{_CHAT_MODULE}._CHAT_BATCH_POLL_INTERVAL", 0.01):
                async def trigger():
                    await asyncio.sleep(0.02)
                    ex.on_approval_signal({"graph_interrupt_id": "g1"})
                t = asyncio.create_task(trigger())
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(ex._wait_for_batch_decision("g1"), timeout=0.12)
                research_calls = collect.call_count
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        _wait_for_chat_batch_decision(ex, "s1", "g1"), timeout=0.12
                    )
                await t
            # 信令后 chat 链路仍持续轮询 DB（collect 次数继续增长）
            self.assertGreater(collect.call_count, research_calls)

        asyncio.run(_case())


class TestStopRequestedSafetyPoint(unittest.TestCase):
    """停止请求安全点：等待中收到停止请求立即 CancelledError（共享语义）。"""

    def test_chat_stop_requested_raises_cancelled(self):
        async def _case():
            ex = _make_executor()
            ex._stop_requested = True
            with (
                _patch_shared(collect=mock.Mock(return_value=(None, False))),
                self.assertRaises(asyncio.CancelledError),
            ):
                await _wait_for_chat_batch_decision(ex, "s1", "g1")
            # 挂起事件已清理
            self.assertNotIn("g1", ex._pending_events)

        asyncio.run(_case())


if __name__ == "__main__":
    unittest.main()
