"""跨会话记忆写入接线测试（spec D2：chat finalizer 写侧断链补全 + fix-review-round2 1.3/1.4）。

覆盖链路：chat finalizer（_finalize_chat_session，persist_stream_result 完成后）
→ _save_cross_session_memory → ContextManager.save_turn_summary → save_session_context：

1. 终态触发：mock 摘要 LLM（summarize_long_text）与 save_session_context，
   断言被调且参数含 session 维度（session_id）与 user 维度
   （create_context_manager 以 user_id 构建，记忆命名空间 (user_id, "session_contexts")）
2. 静默降级：save_session_context / 摘要 LLM 抛异常时收尾不抛出
3. 配置跳过：cross_session_enabled=False 时摘要与写入均不发生
4. 条件跳过：incomplete（重生成半成品）/ 空内容 / 缺 user_id 不写入
5. finalizer 接线：_finalize_chat_session 终态调用 _save_cross_session_memory
6. KG 入口接线（1.3）：同一链路触发 ContextManager.process_conversation
   （user_id + 本轮 user/assistant 消息），此前该方法零调用、图谱恒空
7. 并发锁（1.4）：写入段在 ChatSession 行锁内（transaction.atomic +
   select_for_update，A17 同款），同会话并发收尾不末写覆盖

save_turn_summary / 摘要通道与配置语义的领域层测试见
apps/context_manager/tests/test_save_turn_summary.py。

运行（backend/Django_xm 目录，conda env langchain_xm，测试库 DB_NAME=langchain_verify）：
    python manage.py test Django_xm.services.fastapi_service.tests.test_cross_session_memory_wiring

注：位于 services 层测试目录——被测对象 chat_executor_core 属执行编排层，
受"apps 业务层禁止依赖 services 执行编排层" importlinter 契约约束
（与 test_await_wait_for_batch_decision 同理由）。
"""

import asyncio
import threading
import time
import unittest
from contextlib import suppress
from types import SimpleNamespace
from unittest import mock

from django.test import TransactionTestCase

from Django_xm.apps.chat import models as chat_models
from Django_xm.apps.context_manager.services import manager as manager_module
from Django_xm.apps.context_manager.services.compression import ContextCompressionEngine
from Django_xm.apps.context_manager.services.manager import ContextManager
from Django_xm.common import realtime_events
from Django_xm.services.fastapi_service import chat_executor_core as core

_FAKE_SUMMARY = "用户询问组合式 API 的优势，助手给出了要点总结。"
_USER_MESSAGE = "帮我总结 Vue3 组合式 API 的优势"
_ASSISTANT_CONTENT = "组合式 API 提升逻辑复用与类型推导能力。"
_SESSION_ID = "sess-d2"

# 写入链测试共用补丁：_write 现包含 process_conversation（1.3 KG 入口），
# 不 mock 会经 get_store_provider 触达真实 Store（测试环境解析为 PostgresStore/
# 开发库），凡走 _write 的测试必须带本补丁。
_KG_ENTRY_TARGET = "Django_xm.apps.context_manager.services.manager.ContextManager.process_conversation"


def _make_chat_service(user_id=42):
    return SimpleNamespace(user_id=user_id)


class SaveCrossSessionMemoryCloseExecutorMixin:
    """sync_to_async(thread_sensitive=True) 在 plain asyncio.run 下落入 asgiref
    **类级共享单线程执行器**（SyncToAsync.single_thread_executor，进程级常驻线程），
    其上创建的 Django 连接不会随测试结束关闭 → 测试库销毁时报
    "being accessed by other users"。tearDownClass 时在该执行器线程内显式关闭连接。"""

    @classmethod
    def tearDownClass(cls):
        with suppress(Exception):
            from asgiref.sync import SyncToAsync
            from django.db import connections as _connections

            SyncToAsync.single_thread_executor.submit(_connections.close_all).result(timeout=10)
        super().tearDownClass()


class SaveCrossSessionMemoryTests(SaveCrossSessionMemoryCloseExecutorMixin, unittest.TestCase):
    """_save_cross_session_memory 写入/降级/跳过行为测试。"""

    def _run(self, chat_service, data, content_state, session_id=_SESSION_ID, incomplete=False):
        return asyncio.run(
            core._save_cross_session_memory(chat_service, data, content_state, session_id, incomplete)
        )

    def test_terminal_state_generates_summary_and_saves(self):
        """终态触发：生成摘要并调用 save_session_context（含 session/user 维度）。"""
        with mock.patch.object(
            ContextCompressionEngine, "summarize_long_text", return_value=_FAKE_SUMMARY
        ) as mock_summarize, mock.patch.object(
            ContextManager, "save_session_context", return_value=True
        ) as mock_save, mock.patch.object(
            manager_module,
            "create_context_manager",
            wraps=manager_module.create_context_manager,
        ) as mock_factory, mock.patch(
            _KG_ENTRY_TARGET, return_value=True
        ) as mock_kg:
            self._run(
                _make_chat_service(user_id=42),
                {"message": _USER_MESSAGE},
                {"content": _ASSISTANT_CONTENT},
            )

        # user 维度：ContextManager 以 user_id 构建（记忆命名空间来源）
        mock_factory.assert_called_once()
        self.assertEqual(mock_factory.call_args.kwargs.get("user_id"), 42)

        # session 维度 + 摘要内容 + 实体/决策列表
        mock_save.assert_called_once()
        save_kwargs = mock_save.call_args.kwargs
        self.assertEqual(save_kwargs.get("session_id"), _SESSION_ID)
        self.assertEqual(save_kwargs.get("summary"), _FAKE_SUMMARY)
        self.assertIsInstance(save_kwargs.get("key_entities"), list)
        self.assertIsInstance(save_kwargs.get("key_decisions"), list)

        # 摘要输入包含本轮对话双方内容
        mock_summarize.assert_called_once()
        turn_text = mock_summarize.call_args.args[0]
        self.assertIn(_USER_MESSAGE, turn_text)
        self.assertIn(_ASSISTANT_CONTENT, turn_text)

        # 1.3 KG 入口：同链路触发 process_conversation（user 维度 + 本轮双方消息）
        mock_kg.assert_called_once()
        kg_kwargs = mock_kg.call_args.kwargs
        self.assertEqual(kg_kwargs.get("user_id"), 42)
        self.assertEqual(
            kg_kwargs.get("messages"),
            [
                {"role": "user", "content": _USER_MESSAGE},
                {"role": "assistant", "content": _ASSISTANT_CONTENT},
            ],
        )

    def test_save_failure_degrades_silently(self):
        """降级：save_session_context 抛异常 → 收尾正常完成不抛出。"""
        with mock.patch.object(
            ContextCompressionEngine, "summarize_long_text", return_value=_FAKE_SUMMARY
        ), mock.patch.object(
            ContextManager, "save_session_context", side_effect=Exception("store down")
        ) as mock_save, mock.patch(
            _KG_ENTRY_TARGET, return_value=True
        ):
            self._run(
                _make_chat_service(user_id=42),
                {"message": _USER_MESSAGE},
                {"content": _ASSISTANT_CONTENT},
            )
        mock_save.assert_called_once()

    def test_summary_llm_failure_degrades_silently(self):
        """降级：摘要 LLM 抛异常 → 不写入且不抛出。"""
        with mock.patch.object(
            ContextCompressionEngine, "summarize_long_text", side_effect=Exception("llm down")
        ), mock.patch.object(ContextManager, "save_session_context", return_value=True) as mock_save, mock.patch(
            _KG_ENTRY_TARGET, return_value=True
        ):
            self._run(
                _make_chat_service(user_id=42),
                {"message": _USER_MESSAGE},
                {"content": _ASSISTANT_CONTENT},
            )
        mock_save.assert_not_called()

    def test_incomplete_run_skips_save(self):
        """重生成中断（incomplete 半成品）→ 不写入记忆。"""
        with mock.patch.object(
            ContextCompressionEngine, "summarize_long_text", return_value=_FAKE_SUMMARY
        ) as mock_summarize, mock.patch.object(
            ContextManager, "save_session_context", return_value=True
        ) as mock_save, mock.patch(
            _KG_ENTRY_TARGET, return_value=True
        ) as mock_kg:
            self._run(
                _make_chat_service(user_id=42),
                {"message": _USER_MESSAGE},
                {"content": _ASSISTANT_CONTENT},
                incomplete=True,
            )
        mock_summarize.assert_not_called()
        mock_save.assert_not_called()
        mock_kg.assert_not_called()

    def test_empty_content_or_user_id_skips_save(self):
        """空内容 / 缺 user_id → 不写入记忆。"""
        with mock.patch.object(
            ContextCompressionEngine, "summarize_long_text", return_value=_FAKE_SUMMARY
        ) as mock_summarize, mock.patch.object(
            ContextManager, "save_session_context", return_value=True
        ) as mock_save, mock.patch(
            _KG_ENTRY_TARGET, return_value=True
        ) as mock_kg:
            self._run(_make_chat_service(user_id=42), {"message": _USER_MESSAGE}, {"content": ""})
            self._run(_make_chat_service(user_id=42), {"message": ""}, {"content": _ASSISTANT_CONTENT})
            self._run(_make_chat_service(user_id=None), {"message": _USER_MESSAGE}, {"content": _ASSISTANT_CONTENT})
        mock_summarize.assert_not_called()
        mock_save.assert_not_called()
        mock_kg.assert_not_called()

    def test_llm_outside_db_lock_and_write_inside(self):
        """round3 2.8：摘要 LLM 调用必须在 ChatSession 行锁（atomic）之外，
        最终写回（save_session_context）必须在锁内——锁内无网络调用。"""
        from django.db import transaction

        observed = {"llm": [], "save": []}

        def spy_summarize(text, instruction, **_kwargs):
            observed["llm"].append(transaction.get_connection().in_atomic_block)
            return _FAKE_SUMMARY

        def spy_save(**_kwargs):
            observed["save"].append(transaction.get_connection().in_atomic_block)
            return True

        with mock.patch.object(
            ContextCompressionEngine, "summarize_long_text", side_effect=spy_summarize
        ), mock.patch.object(
            ContextManager, "save_session_context", side_effect=spy_save
        ), mock.patch(
            _KG_ENTRY_TARGET, return_value=True
        ):
            self._run(
                _make_chat_service(user_id=42),
                {"message": _USER_MESSAGE},
                {"content": _ASSISTANT_CONTENT},
            )

        self.assertEqual(observed["llm"], [False], "摘要 LLM 调用必须发生在行锁（atomic）之外")
        self.assertEqual(observed["save"], [True], "最终写回必须发生在行锁（atomic）之内")


class FinalizerWiringTests(
    SaveCrossSessionMemoryCloseExecutorMixin, unittest.TestCase
):
    """_finalize_chat_session 终态接线测试：persist 完成后调用记忆写入。"""

    def test_finalize_chat_session_invokes_memory_write(self):
        """终态收尾：persist + 广播完成后调用 _save_cross_session_memory。"""
        chat_models_mock = mock.MagicMock()
        fake_msg = SimpleNamespace(is_streaming=True, save=mock.MagicMock())
        chat_models_mock.filter.return_value.first.return_value = fake_msg

        with mock.patch.object(core, "persist_chat_tool_calls", new=mock.AsyncMock()) as persist_mock, \
                mock.patch.object(core, "_save_cross_session_memory", new=mock.AsyncMock()) as memory_mock, \
                mock.patch.object(chat_models.ChatMessage, "objects", chat_models_mock), \
                mock.patch.object(realtime_events, "publish_event", new=mock.AsyncMock()):
            asyncio.run(
                core._finalize_chat_session(
                    _make_chat_service(user_id=42),
                    {"message": _USER_MESSAGE},
                    {"content": _ASSISTANT_CONTENT},
                    _SESSION_ID,
                    "123",
                    stopped=False,
                    incomplete=False,
                )
            )

        persist_mock.assert_awaited_once()
        memory_mock.assert_awaited_once()
        args = memory_mock.await_args.args
        self.assertEqual(args[2], {"content": _ASSISTANT_CONTENT})
        self.assertEqual(args[3], _SESSION_ID)
        self.assertFalse(args[4])


class SaveTurnSummaryConcurrencyTests(TransactionTestCase):
    """1.4 并发锁测试：同会话并发收尾写记忆，后写者基于已提交最新摘要合并。

    用 TransactionTestCase 的理由：并发写在两个独立线程的独立事件循环中真实
    交叠（模拟多进程 worker），行锁（select_for_update）与事务提交语义必须
    在真实数据库上验证；TestCase 的测试事务会令并行线程看不到已写数据。

    判定逻辑：锁生效 ⇔ 两个线程的最终落库摘要同时包含两轮对话内容
    （后写者读到了先写者已提交的摘要并合并）。若去掉行锁，两线程在竞态窗口内
    都读到空旧摘要 → 末写覆盖 → 先写者内容丢失，断言失败。
    """

    databases = "__all__"
    _LOCK_SESSION_ID = "sess-turn-summary-lock"
    _LOCK_USER_ID = 424242

    @classmethod
    def tearDownClass(cls):
        # 本测试类（TransactionTestCase）在整个运行序中最后执行：兜底清扫
        # asgiref 共享单线程执行器线程上的残留连接（前序任何测试经
        # sync_to_async(thread_sensitive=True) 触库的连接都落在该常驻线程），
        # 避免测试库销毁时报 "being accessed by other users"。
        with suppress(Exception):
            from asgiref.sync import SyncToAsync
            from django.db import connections as _connections

            SyncToAsync.single_thread_executor.submit(_connections.close_all).result(timeout=10)
        super().tearDownClass()

    def test_concurrent_turn_summary_merges_not_overwrites(self):
        from concurrent.futures import ThreadPoolExecutor

        from asgiref.sync import AsyncToSync
        from langgraph.store.memory import InMemoryStore

        shared_store = InMemoryStore()

        # 会话行（锁目标）：生产链路收尾时该行必已存在
        chat_models.ChatSession.objects.create(session_id=self._LOCK_SESSION_ID)

        def fake_summarize(text, instruction, **_kwargs):
            # 模拟摘要 LLM 延迟，拉大「读旧 → 写入」竞态窗口
            time.sleep(0.05)
            if "【历史累积摘要】" in text:
                return f"合并<{text}>"
            return f"单轮<{text}>"

        barrier = threading.Barrier(2, timeout=10)
        errors: list[BaseException] = []

        # 补丁必须在主线程一次性施加并覆盖两个 worker 全生命周期：
        # 各 worker 各自 patch 同一批类属性会在 barrier 交叠期互相
        # restore 覆盖（patch 退出恢复原值，令另一 worker 的 mock 失效），
        # 第二个 worker 会打出真实 LLM 调用 → 异常被兜底吞没 → 假失败。
        real_load = ContextManager._load_session_context_data

        def slow_load(self, session_id):
            """读旧后延迟 200ms 再写：无锁时两 worker 读旧必然都早于对方写入
            （读→写间隔被拉大），末写覆盖确定发生；有锁时后写者阻塞在行锁上，
            进入读旧前先写者已提交 → 读到对方摘要并合并。"""
            result = real_load(self, session_id)
            time.sleep(0.2)
            return result

        with mock.patch.object(
            ContextCompressionEngine, "summarize_long_text", side_effect=fake_summarize
        ), mock.patch.object(
            ContextManager, "_load_session_context_data", slow_load
        ), mock.patch(
            # Store 收敛到共享 InMemoryStore（避免触达真实 PostgresStore）
            "Django_xm.apps.context_manager.services.runtime_providers.get_store_provider",
            return_value=lambda: shared_store,
        ), mock.patch(
            _KG_ENTRY_TARGET, return_value=True
        ):

            def _worker(user_message: str, assistant_content: str) -> None:
                from django.db import connections

                loop = asyncio.new_event_loop()
                # 关键：为每个 worker 的 loop 注册独立单线程执行器。否则
                # sync_to_async(thread_sensitive=True) 落入 asgiref 类级共享单线程
                # 执行器，两个 _write 被执行器本身串行化——锁测试沦为假通过。
                executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lock-probe")
                AsyncToSync.loop_thread_executors[loop] = executor
                try:
                    barrier.wait()
                    loop.run_until_complete(
                        core._save_cross_session_memory(
                            SimpleNamespace(user_id=self._LOCK_USER_ID),
                            {"message": user_message},
                            {"content": assistant_content},
                            self._LOCK_SESSION_ID,
                            False,
                        )
                    )
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    AsyncToSync.loop_thread_executors.pop(loop, None)
                    with suppress(Exception):
                        # 在 worker 执行器线程内关闭其 Django 连接（确定性强于依赖 GC）
                        executor.submit(connections.close_all).result(timeout=10)
                    executor.shutdown(wait=True)
                    loop.close()
                    connections.close_all()

            threads = [
                threading.Thread(target=_worker, args=("问题A 关于路由守卫", "回答A 使用 beforeEach")),
                threading.Thread(target=_worker, args=("问题B 关于 Pinia 状态管理", "回答B 建议组合式 API")),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)
        self.assertFalse(errors)

        # 最终累积摘要：后写者必须基于先写者已提交的摘要合并（两轮内容都保留）
        item = shared_store.get((str(self._LOCK_USER_ID), "session_contexts"), self._LOCK_SESSION_ID)
        self.assertIsNotNone(item)
        summary = str(item.value.get("summary", ""))
        self.assertIn("问题A", summary)
        self.assertIn("问题B", summary)
