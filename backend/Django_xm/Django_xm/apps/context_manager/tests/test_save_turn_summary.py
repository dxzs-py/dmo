"""ContextManager.save_turn_summary 领域层测试（spec D2：会话摘要记忆写入链）。

覆盖：
- 终态摘要写入：摘要复用统一引擎轻量摘要通道（summarize_long_text），
  实体/决策复用引擎规则抽取唯一实现（extract_decisions 公开别名），
  写入委托 save_session_context（session/user 维度参数完整）；
- 配置语义：cross_session_enabled=False（ContextManagementConfig）跳过写入；
- 条件跳过：空输入 / 空摘要；
- 异常语义：save_session_context 异常向上抛出（由 chat finalizer 静默降级）。

与执行编排层接线的集成测试见
services/fastapi_service/tests/test_cross_session_memory_wiring.py。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.context_manager.tests.test_save_turn_summary
"""

import unittest
from unittest import mock

from Django_xm.apps.context_manager.services.compression import ContextCompressionEngine
from Django_xm.apps.context_manager.services.manager import (
    ContextManagementConfig,
    ContextManager,
)


def _make_manager(cross_session_enabled=True, user_id=42):
    config = ContextManagementConfig(cross_session_enabled=cross_session_enabled)
    return ContextManager(user_id=user_id, config=config)


class SaveTurnSummaryTests(unittest.TestCase):
    """save_turn_summary 摘要生成与委托写入测试。"""

    def test_generates_summary_and_delegates_save(self):
        """摘要经统一引擎通道生成，实体/决策规则抽取后委托 save_session_context。"""
        manager = _make_manager()
        with mock.patch.object(
            ContextCompressionEngine, "summarize_long_text", return_value="  会话摘要  "
        ) as mock_summarize, mock.patch.object(
            ContextManager, "save_session_context", return_value=True
        ) as mock_save:
            result = manager.save_turn_summary(
                session_id="sess-d2",
                user_message="我决定使用 Vue3 组合式 API",
                assistant_content="好的。建议：优先使用 setup 语法。结论是组合式 API 更利于复用",
            )

        self.assertTrue(result)
        mock_summarize.assert_called_once()
        turn_text = mock_summarize.call_args.args[0]
        self.assertIn("我决定使用 Vue3 组合式 API", turn_text)
        self.assertIn("好的。建议：优先使用 setup 语法。", turn_text)

        mock_save.assert_called_once()
        save_kwargs = mock_save.call_args.kwargs
        self.assertEqual(save_kwargs.get("session_id"), "sess-d2")
        self.assertEqual(save_kwargs.get("summary"), "会话摘要")  # 摘要 strip 后落库
        self.assertIsInstance(save_kwargs.get("key_entities"), list)
        self.assertIsInstance(save_kwargs.get("key_decisions"), list)
        # 决策抽取唯一实现：assistant 内容中的建议句被规则命中
        self.assertTrue(any("优先使用 setup 语法" in d for d in save_kwargs.get("key_decisions")))

    def test_disabled_config_returns_false_without_save(self):
        """cross_session_enabled=False → 跳过（默认值 True 维持现状，此处仅验证关闭语义）。"""
        manager = _make_manager(cross_session_enabled=False)
        with mock.patch.object(
            ContextCompressionEngine, "summarize_long_text", return_value="摘要"
        ) as mock_summarize, mock.patch.object(
            ContextManager, "save_session_context", return_value=True
        ) as mock_save:
            result = manager.save_turn_summary(
                session_id="sess-d2", user_message="问题", assistant_content="回答"
            )
        self.assertFalse(result)
        mock_summarize.assert_not_called()
        mock_save.assert_not_called()

    def test_empty_input_returns_false(self):
        """空输入（用户消息或回答为空）→ 跳过写入且不调 LLM。"""
        manager = _make_manager()
        with mock.patch.object(
            ContextCompressionEngine, "summarize_long_text", return_value="摘要"
        ) as mock_summarize, mock.patch.object(
            ContextManager, "save_session_context", return_value=True
        ) as mock_save:
            self.assertFalse(manager.save_turn_summary("sess-d2", "", "回答"))
            self.assertFalse(manager.save_turn_summary("sess-d2", "   ", "回答"))
            self.assertFalse(manager.save_turn_summary("sess-d2", "问题", ""))
            self.assertFalse(manager.save_turn_summary("sess-d2", "问题", None))
            self.assertFalse(manager.save_turn_summary("sess-d2", None, "回答"))
        mock_summarize.assert_not_called()
        mock_save.assert_not_called()

    def test_empty_summary_returns_false(self):
        """摘要为空/空白 → 跳过写入。"""
        manager = _make_manager()
        with mock.patch.object(
            ContextCompressionEngine, "summarize_long_text", return_value="   "
        ), mock.patch.object(ContextManager, "save_session_context", return_value=True) as mock_save:
            result = manager.save_turn_summary("sess-d2", "问题", "回答")
        self.assertFalse(result)
        mock_save.assert_not_called()

    def test_save_failure_propagates_to_caller(self):
        """异常语义：save_session_context 抛出向上传播（finalizer 侧静默降级兜底）。"""
        manager = _make_manager()
        with mock.patch.object(
            ContextCompressionEngine, "summarize_long_text", return_value="摘要"
        ), mock.patch.object(
            ContextManager, "save_session_context", side_effect=RuntimeError("store down")
        ), self.assertRaises(RuntimeError):
            manager.save_turn_summary("sess-d2", "问题", "回答")

    def test_extract_decisions_public_alias(self):
        """extract_decisions 公开别名与 _extract_decisions 同源（spec D2 消除私有访问）。"""
        messages = [{"role": "assistant", "content": "结论是使用 Chroma 向量库"}]
        self.assertEqual(
            ContextCompressionEngine.extract_decisions(messages),
            ["使用 Chroma 向量库"],
        )


if __name__ == "__main__":
    unittest.main()
