"""SessionExecutor 双域拆分结构守护测试（批E E7）。

锁定拆分后的结构契约，防止后续重构回潮：
- ``SessionExecutor.__new__`` 工厂按 session_type 分派域子类
  （chat → ChatSessionExecutor，其余 → ResearchSessionExecutor，语义与
  拆分前 `!= "chat"` 分派一致），构造签名不变；
- 两子类均继承共享基类（生命周期 / watchdog / 审批信令 / fan-in / 共用清理）；
- 测试类级 patch 面（mock.patch.object(SessionExecutor, M)）锚定的共享方法
  不得被子类覆盖（子类覆盖会 shadow 掉对基类属性的 patch 注入）；
- research Agent 物料装配已下移 apps/research/services/agent_assembly.py，
  executor 仅留薄委托；原 _inherit_parent_files 从 executor 移除。

行为等价回归由既有测试面守护（test_session_executor / test_research_cancel /
test_chat_stop_cleanup / test_subagent_wait / test_await_wait_for_batch_decision /
test_skill_dirs_loading），本文件只做结构断言。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.services.fastapi_service.tests.test_session_executor_split --noinput
"""

import asyncio
import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from Django_xm.services.fastapi_service.session_executor import (
    ChatSessionExecutor,
    ResearchSessionExecutor,
    SessionExecutor,
)

_MODULE = "Django_xm.services.fastapi_service.session_executor"
_ASSEMBLY = "Django_xm.apps.research.services.agent_assembly"


def _make_executor(session_type="research", **kw):
    manager = mock.Mock()
    return SessionExecutor(
        manager,
        thread_id=kw.get("thread_id", "t1"),
        query=kw.get("query", "q"),
        user_id=kw.get("user_id", 1),
        session_id=kw.get("session_id", "s1"),
        message_id=kw.get("message_id", "m1"),
        params=kw.get("params", {}),
        session_type=session_type,
    )


class TestFactoryDispatch(unittest.TestCase):
    """__new__ 工厂：session_type → 域子类分派（构造兼容契约）。"""

    def test_default_dispatches_to_research(self):
        ex = _make_executor()
        self.assertIsInstance(ex, ResearchSessionExecutor)
        self.assertIsInstance(ex, SessionExecutor)
        self.assertEqual(ex.session_type, "research")

    def test_chat_dispatches_to_chat(self):
        ex = _make_executor(session_type="chat")
        self.assertIsInstance(ex, ChatSessionExecutor)
        self.assertIsInstance(ex, SessionExecutor)
        self.assertEqual(ex.session_type, "chat")

    def test_unknown_session_type_dispatches_to_research(self):
        """非 chat 一律 research（与拆分前 `session_type != "chat"` 分派语义一致）。"""
        ex = _make_executor(session_type="other")
        self.assertIsInstance(ex, ResearchSessionExecutor)

    def test_direct_subclass_construction_preserved(self):
        """直接构造域子类（不经工厂）同样可用（域内部调用/新代码路径）。"""
        for cls in (ResearchSessionExecutor, ChatSessionExecutor):
            ex = cls(
                mock.Mock(),
                thread_id="t-direct",
                query="q",
                user_id=1,
                session_id="s1",
                message_id="m1",
                params={},
            )
            self.assertIsInstance(ex, cls)
            self.assertEqual(ex.thread_id, "t-direct")


class TestPatchSurfaceStability(unittest.TestCase):
    """测试类级 patch 面锚定的共享方法不得被子类覆盖（shadow 会破坏 patch 注入）。"""

    # 既有测试 mock.patch.object(SessionExecutor, M) 的全部方法（E7 盘点）
    CLASS_PATCHED_METHODS = (
        "_publish_status_change",
        "_build_agent",
        "_release_checkpointer",
        "_handle_result",
        "_handle_failure",
        "_wait_for_initial_batch",
        "_inject_research_resume_baseline",
        "_make_parent_awaiter",
        "_execute_and_handle",
        "_run_research",
        "_persist_research_progress",
        "_schedule_sandbox_cleanup",
    )

    def test_class_patched_methods_not_overridden(self):
        for name in self.CLASS_PATCHED_METHODS:
            for cls in (ResearchSessionExecutor, ChatSessionExecutor):
                self.assertNotIn(
                    name,
                    cls.__dict__,
                    f"{cls.__name__} 覆盖了 {name}：会 shadow 掉 "
                    f"mock.patch.object(SessionExecutor, '{name}') 的注入，破坏既有测试面",
                )

    def test_static_call_surface_on_base(self):
        """静态调用面：SessionExecutor._list_chat_batch_ids / _load_research_resume_baseline 可达。"""
        self.assertTrue(callable(SessionExecutor._list_chat_batch_ids))
        self.assertTrue(callable(SessionExecutor._load_research_resume_baseline))


class TestDomainMethodPlacement(unittest.TestCase):
    """域方法归位：chat 域方法仅在 Chat 子类、research 取消终态化仅在 Research 子类。"""

    CHAT_ONLY = (
        "_run_chat",
        "_wait_for_chat_initial_batch",
        "_inject_chat_resume_baseline",
        "_load_chat_resume_baseline",
        "_handle_chat_wait_suspend",
        "_schedule_chat_stop_cleanup",
        "_finalize_chat_stop_cleanup",
    )
    RESEARCH_ONLY = (
        "_finalize_research_cancelled",
        "_schedule_research_cancel_finalization",
        "_handle_suspend",
        "_on_interrupt",
        "_wait_for_batch_decision",
    )
    # 多态钩子：基类声明（NotImplementedError），两子类各自实现
    DOMAIN_HOOKS = (
        "_run_domain",
        "_handle_run_exception",
        "_schedule_stop_finalization",
        "_finalize_suspended_failure",
        "_resume_execution",
        "_handle_resume_failure",
    )

    def test_chat_methods_live_on_chat_subclass(self):
        for name in self.CHAT_ONLY:
            self.assertIn(name, ChatSessionExecutor.__dict__)
            self.assertNotIn(name, ResearchSessionExecutor.__dict__)

    def test_research_methods_live_on_research_subclass(self):
        for name in self.RESEARCH_ONLY:
            self.assertIn(name, ResearchSessionExecutor.__dict__)
            self.assertNotIn(name, ChatSessionExecutor.__dict__)

    def test_domain_hooks_implemented_by_both_subclasses(self):
        for name in self.DOMAIN_HOOKS:
            self.assertIn(name, SessionExecutor.__dict__)
            self.assertIn(name, ResearchSessionExecutor.__dict__)
            self.assertIn(name, ChatSessionExecutor.__dict__)


class TestAgentAssemblyDelegation(unittest.TestCase):
    """Agent 物料装配下移：_build_agent/_load_skill_dirs 薄委托 agent_assembly。"""

    def test_inherit_parent_files_removed_from_executor(self):
        """_inherit_parent_files 已随装配主体下移（executor 不再持有副本）。"""
        self.assertFalse(hasattr(SessionExecutor, "_inherit_parent_files"))

    def test_build_agent_delegates_to_assembly(self):
        async def _case():
            ex = _make_executor(params={"model_name": "m1"})
            agent = mock.Mock()
            with mock.patch(
                f"{_ASSEMBLY}.build_research_agent", new_callable=mock.AsyncMock, return_value=agent
            ) as build:
                result = await ex._build_agent()
            self.assertIs(result, agent)
            build.assert_awaited_once_with(
                ex.params, thread_id="t1", user_id=1
            )

        asyncio.run(_case())

    def test_load_skill_dirs_delegates_to_assembly(self):
        ex = _make_executor(user_id=7)
        with mock.patch(f"{_ASSEMBLY}.load_skill_dirs", return_value=["/dir"]) as load:
            dirs = ex._load_skill_dirs(["pkg_a"])
        self.assertEqual(dirs, ["/dir"])
        load.assert_called_once_with(["pkg_a"], user_id=7)


if __name__ == "__main__":
    unittest.main()
