"""跨会话记忆写入接线测试（spec D2：chat finalizer 写侧断链补全）。

覆盖链路：chat finalizer（_finalize_chat_session，persist_stream_result 完成后）
→ _save_cross_session_memory → ContextManager.save_turn_summary → save_session_context：

1. 终态触发：mock 摘要 LLM（summarize_long_text）与 save_session_context，
   断言被调且参数含 session 维度（session_id）与 user 维度
   （create_context_manager 以 user_id 构建，记忆命名空间 (user_id, "session_contexts")）
2. 静默降级：save_session_context / 摘要 LLM 抛异常时收尾不抛出
3. 配置跳过：cross_session_enabled=False 时摘要与写入均不发生
4. 条件跳过：incomplete（重生成半成品）/ 空内容 / 缺 user_id 不写入
5. finalizer 接线：_finalize_chat_session 终态调用 _save_cross_session_memory

save_turn_summary / 摘要通道与配置语义的领域层测试见
apps/context_manager/tests/test_save_turn_summary.py。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.services.fastapi_service.tests.test_cross_session_memory_wiring

注：位于 services 层测试目录——被测对象 chat_executor_core 属执行编排层，
受"apps 业务层禁止依赖 services 执行编排层" importlinter 契约约束
（与 test_await_wait_for_batch_decision 同理由）。
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

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


def _make_chat_service(user_id=42):
    return SimpleNamespace(user_id=user_id)


class SaveCrossSessionMemoryTests(unittest.TestCase):
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
        ) as mock_factory:
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

    def test_save_failure_degrades_silently(self):
        """降级：save_session_context 抛异常 → 收尾正常完成不抛出。"""
        with mock.patch.object(
            ContextCompressionEngine, "summarize_long_text", return_value=_FAKE_SUMMARY
        ), mock.patch.object(
            ContextManager, "save_session_context", side_effect=Exception("store down")
        ) as mock_save:
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
        ), mock.patch.object(ContextManager, "save_session_context", return_value=True) as mock_save:
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
        ) as mock_save:
            self._run(
                _make_chat_service(user_id=42),
                {"message": _USER_MESSAGE},
                {"content": _ASSISTANT_CONTENT},
                incomplete=True,
            )
        mock_summarize.assert_not_called()
        mock_save.assert_not_called()

    def test_empty_content_or_user_id_skips_save(self):
        """空内容 / 缺 user_id → 不写入记忆。"""
        with mock.patch.object(
            ContextCompressionEngine, "summarize_long_text", return_value=_FAKE_SUMMARY
        ) as mock_summarize, mock.patch.object(
            ContextManager, "save_session_context", return_value=True
        ) as mock_save:
            self._run(_make_chat_service(user_id=42), {"message": _USER_MESSAGE}, {"content": ""})
            self._run(_make_chat_service(user_id=42), {"message": ""}, {"content": _ASSISTANT_CONTENT})
            self._run(_make_chat_service(user_id=None), {"message": _USER_MESSAGE}, {"content": _ASSISTANT_CONTENT})
        mock_summarize.assert_not_called()
        mock_save.assert_not_called()


class FinalizerWiringTests(unittest.TestCase):
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
