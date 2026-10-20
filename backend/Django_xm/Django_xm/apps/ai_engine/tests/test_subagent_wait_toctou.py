"""子代理业务等待 fan-in TOCTOU 竞态测试（fix-review-round3 2.6）。

历史缺陷：_handle_subagent_wait_suspend「先查终态 → 再注册 awaiter」两步
之间存在竞态窗口——孙代理在查询之后、注册之前终态时，其终态回调找不到
awaiter 被丢弃，父会话永久等待（挂 watchdog 7200s）。

修复语义：
- 注册先行：awaiter 注册后 lifecycle 回调必然送达；
- 注册后复核：已终态孙代理经同一 awaiter 补投；
- resume-once 守卫：复核补投与 lifecycle 回调对同一/不同 sid 的重复并发
  投递（不同线程）只触发一次 runtime.resume。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.ai_engine.tests.test_subagent_wait_toctou
"""

import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from Django_xm.apps.ai_engine.models import SubAgentStatus
from Django_xm.apps.ai_engine.subagent_runtime.adapters.langgraph_adapter import LangGraphAdapter

_LIFECYCLE_MODULE = "Django_xm.apps.ai_engine.subagent_runtime.lifecycle"


def _make_runtime():
    runtime = mock.Mock()
    runtime._update_status = mock.AsyncMock()
    runtime.get_instance = mock.AsyncMock()
    runtime.resume = mock.AsyncMock()
    return runtime


def _make_instance(thread_id="sub_parent"):
    return SimpleNamespace(thread_id=thread_id, parent_thread_id="root-1", metadata={})


class SubagentWaitToctouTests(SimpleTestCase):
    """注册后复核补投 + resume-once 守卫。"""

    def _run_suspend(self, runtime, instance, subagent_thread_ids, terminal_side_effect=None):
        """执行 _handle_subagent_wait_suspend，返回 (lifecycle mock, captured awaiter)。"""
        adapter = LangGraphAdapter(runtime, graph_factory=mock.AsyncMock())
        if terminal_side_effect is not None:
            runtime.get_instance = mock.AsyncMock(side_effect=terminal_side_effect)
        with mock.patch(f"{_LIFECYCLE_MODULE}.get_lifecycle_manager") as glm:
            lm = mock.Mock()
            glm.return_value = lm
            asyncio.run(
                adapter._handle_subagent_wait_suspend(
                    instance,
                    {"subagent_thread_ids": subagent_thread_ids, "interrupt_id": "wait-t"},
                )
            )
        return lm, lm.register_parent_awaiter.call_args.args[1], adapter

    def test_terminal_grandchild_delivered_via_registered_awaiter(self):
        """复核补投：孙代理已终态 → 经注册后的 awaiter 补投并恢复（窗口关闭）。"""
        runtime = _make_runtime()
        # 复核期读到 COMPLETED（模拟「查询与注册之间终态」的竞态幸存者）
        runtime.get_instance = mock.AsyncMock(
            return_value=SimpleNamespace(status=SubAgentStatus.COMPLETED, result_preview="done")
        )
        instance = _make_instance()

        lm, _awaiter, _adapter = self._run_suspend(runtime, instance, ["g1"])

        # 注册先行 + 补投经 awaiter → 恢复触发
        lm.register_parent_awaiter.assert_called_once()
        runtime.resume.assert_awaited_once_with(
            "sub_parent",
            {"subagent_results": [{"subagent_thread_id": "g1", "status": "completed", "result": "done"}]},
        )
        lm.unregister_parent_awaiter.assert_called_once_with("sub_parent")

    def test_duplicate_delivery_triggers_resume_once(self):
        """重复投递（同一 sid：复核补投 + lifecycle 回调竞态重叠）→ 仅恢复一次。"""
        runtime = _make_runtime()
        instance = _make_instance()
        inst = SimpleNamespace(status=SubAgentStatus.FAILED, result_preview="boom")

        _lm, awaiter, _adapter = self._run_suspend(
            runtime, instance, ["g1"], terminal_side_effect=None
        )
        runtime.get_instance = mock.AsyncMock(return_value=inst)

        asyncio.run(awaiter("g1", SubAgentStatus.FAILED))  # 补投
        asyncio.run(awaiter("g1", SubAgentStatus.FAILED))  # lifecycle 回调重复投递
        runtime.resume.assert_awaited_once()

    def test_concurrent_delivery_from_threads_resumes_once(self):
        """两个孙代理在不同线程并发投递终态 → runtime.resume 恰好一次且结果齐全。"""
        runtime = _make_runtime()
        runtime.get_instance = mock.AsyncMock(
            side_effect=lambda sid: SimpleNamespace(
                status=SubAgentStatus.COMPLETED,
                result_preview=f"r-{sid}",
            )
        )
        instance = _make_instance()

        _lm, awaiter, _adapter = self._run_suspend(runtime, instance, ["g1", "g2"])

        errors: list[BaseException] = []
        barrier = threading.Barrier(2, timeout=10)

        def _deliver(sid: str) -> None:
            try:
                barrier.wait()
                asyncio.run(awaiter(sid, SubAgentStatus.COMPLETED))
            except BaseException as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=_deliver, args=("g1",)),
            threading.Thread(target=_deliver, args=("g2",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertFalse(errors)
        runtime.resume.assert_awaited_once()
        payload = runtime.resume.await_args.args[1]
        results = {r["subagent_thread_id"]: r for r in payload["subagent_results"]}
        self.assertEqual(set(results), {"g1", "g2"})
        self.assertEqual(results["g1"]["result"], "r-g1")
        self.assertEqual(results["g2"]["result"], "r-g2")

    def test_partial_terminal_does_not_resume_early(self):
        """部分终态：复核补投一个、另一个仍运行 → 不提前恢复。"""
        runtime = _make_runtime()

        # 复核：g1 已终态、g2 仍在运行
        async def get_instance(sid):
            if sid == "g1":
                return SimpleNamespace(status=SubAgentStatus.FAILED, result_preview="x")
            return SimpleNamespace(status=SubAgentStatus.RUNNING, result_preview="")

        runtime.get_instance = mock.AsyncMock(side_effect=get_instance)
        instance = _make_instance()

        lm, awaiter, _adapter = self._run_suspend(runtime, instance, ["g1", "g2"])

        runtime.resume.assert_not_awaited()
        # g2 之后终态（lifecycle 回调）→ 恢复（需在 lifecycle patch 内执行，
        # 断言 unregister 落在同一 manager mock 上）
        runtime.get_instance = mock.AsyncMock(
            return_value=SimpleNamespace(status=SubAgentStatus.COMPLETED, result_preview="ok")
        )
        with mock.patch(f"{_LIFECYCLE_MODULE}.get_lifecycle_manager") as glm:
            glm.return_value = lm
            asyncio.run(awaiter("g2", SubAgentStatus.COMPLETED))
        runtime.resume.assert_awaited_once()
        payload = runtime.resume.await_args.args[1]
        self.assertEqual(len(payload["subagent_results"]), 2)
        lm.unregister_parent_awaiter.assert_called_once_with("sub_parent")


if __name__ == "__main__":
    unittest.main()
