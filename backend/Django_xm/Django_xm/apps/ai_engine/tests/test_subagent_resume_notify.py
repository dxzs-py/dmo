"""子代理 resume 失败终态通知单元测试（Task 2 C2 回归）。

历史缺陷：resume 失败（``_rebuild_agent_config`` 返回 None / 抛异常，或执行
线程启动失败）仅置 FAILED，未触发生命周期回调（_notify_finished /
lifecycle.on_subagent_finished）——父会话的 fan-in 等待者永不唤醒，
挂到 7200s watchdog 超时。

修复（两处失败路径全覆盖）：
- langgraph_adapter.resume：AgentConfig 重建失败（返回 None 或抛异常）内部
  消化 → FAILED + _notify_finished(instance, "failed")（对齐 _execute_in_thread
  异常路径，langgraph_adapter.py:148）；
- runtime.resume：adapter.resume 未消化异常的兜底路径 → FAILED +
  lifecycle.on_subagent_finished(status="failed")。

全部 mock 级（ORM / adapter / lifecycle 打桩，零 DB 写、零真实图执行）。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.ai_engine.tests.test_subagent_resume_notify
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase

from Django_xm.apps.ai_engine.models import SubAgentStatus
from Django_xm.apps.ai_engine.subagent_runtime.adapters.langgraph_adapter import LangGraphAdapter
from Django_xm.apps.ai_engine.subagent_runtime.runtime import SubAgentRuntime

_LIFECYCLE_MODULE = "Django_xm.apps.ai_engine.subagent_runtime.lifecycle"


def _make_instance(
    thread_id: str = "subagent_x",
    parent_thread_id: str = "parent-1",
    status=SubAgentStatus.INTERRUPTED_PENDING_USER_INPUT,
    pending: dict | None = None,
) -> SimpleNamespace:
    """构造 resume 校验所需的最小 instance（鸭子类型访问面，不触 DB）。"""
    return SimpleNamespace(
        thread_id=thread_id,
        parent_thread_id=parent_thread_id,
        status=status,
        pending_interrupt_info=pending or {},
        metadata={},
    )


class AdapterResumeFailureNotifyTests(SimpleTestCase):
    """adapter.resume 重建失败路径：置 FAILED 并触发生命周期回调。"""

    @staticmethod
    def _make_adapter() -> LangGraphAdapter:
        runtime = SimpleNamespace(_update_status=AsyncMock())
        return LangGraphAdapter(runtime)

    def test_rebuild_none_marks_failed_and_notifies(self):
        """_rebuild_agent_config 返回 None → FAILED + _notify_finished("failed")。"""
        adapter = self._make_adapter()
        instance = _make_instance()
        with (
            patch.object(adapter, "_rebuild_agent_config", new=AsyncMock(return_value=None)),
            patch.object(adapter, "_notify_finished", new=AsyncMock()) as notify_mock,
        ):
            asyncio.run(adapter.resume(instance, {"decision": "approve"}))

        adapter.runtime._update_status.assert_awaited_once_with(instance.thread_id, SubAgentStatus.FAILED)
        notify_mock.assert_awaited_once_with(instance, "failed")

    def test_rebuild_raise_marks_failed_and_notifies(self):
        """_rebuild_agent_config 抛异常（如 config 工厂未注册）→ 内部消化为 FAILED + 通知。"""
        adapter = self._make_adapter()
        instance = _make_instance()
        with (
            patch.object(
                adapter,
                "_rebuild_agent_config",
                new=AsyncMock(side_effect=RuntimeError("子代理 AgentConfig 工厂未注册")),
            ),
            patch.object(adapter, "_notify_finished", new=AsyncMock()) as notify_mock,
        ):
            # 重建异常应被内部消化为终态失败，不向 runtime 传播（否则双重处理）
            asyncio.run(adapter.resume(instance, {"decision": "approve"}))

        adapter.runtime._update_status.assert_awaited_once_with(instance.thread_id, SubAgentStatus.FAILED)
        notify_mock.assert_awaited_once_with(instance, "failed")


class RuntimeResumeFailureNotifyTests(SimpleTestCase):
    """runtime.resume 兜底路径（adapter.resume 抛异常）：FAILED + lifecycle 通知。"""

    def test_adapter_resume_raise_notifies_lifecycle(self):
        """adapter.resume 抛异常（如执行线程启动失败）→ FAILED + 父等待者立即收到 failed。"""
        adapter = SimpleNamespace(resume=AsyncMock(side_effect=RuntimeError("can't start new thread")))
        runtime = SubAgentRuntime(adapter=adapter)
        instance = _make_instance()
        manager = MagicMock()
        manager.on_subagent_finished = AsyncMock()
        with (
            patch.object(SubAgentRuntime, "_get_instance", new=AsyncMock(return_value=instance)),
            patch.object(SubAgentRuntime, "_update_status", new=AsyncMock()) as update_mock,
            patch(f"{_LIFECYCLE_MODULE}.get_lifecycle_manager", return_value=manager),
        ):
            returned = asyncio.run(runtime.resume(instance.thread_id, {"decision": "approve"}))

        self.assertIs(returned, instance)
        update_mock.assert_any_await(instance.thread_id, SubAgentStatus.FAILED)
        manager.on_subagent_finished.assert_awaited_once_with(
            subagent_thread_id=instance.thread_id,
            parent_thread_id=instance.parent_thread_id,
            status=SubAgentStatus.FAILED,
        )

    def test_adapter_resume_success_does_not_notify(self):
        """adapter.resume 正常返回（重建失败已在 adapter 内部消化）时 runtime 不重复通知。"""
        adapter = SimpleNamespace(resume=AsyncMock(return_value=None))
        runtime = SubAgentRuntime(adapter=adapter)
        instance = _make_instance()
        manager = MagicMock()
        manager.on_subagent_finished = AsyncMock()
        with (
            patch.object(SubAgentRuntime, "_get_instance", new=AsyncMock(return_value=instance)),
            patch.object(SubAgentRuntime, "_update_status", new=AsyncMock()),
            patch(f"{_LIFECYCLE_MODULE}.get_lifecycle_manager", return_value=manager),
        ):
            asyncio.run(runtime.resume(instance.thread_id, {"decision": "approve"}))

        manager.on_subagent_finished.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
