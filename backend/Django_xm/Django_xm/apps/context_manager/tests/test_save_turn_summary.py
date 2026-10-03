"""ContextManager.save_turn_summary 领域层测试（spec D2 写入链 + P2 增量合并）。

覆盖：
- 终态摘要写入：摘要复用统一引擎轻量摘要通道（summarize_long_text），
  实体/决策复用引擎规则抽取唯一实现（extract_decisions 公开别名），
  写入委托 save_session_context（session/user 维度参数完整）；
- P2 增量合并：写入前读取旧摘要 → 摘要 LLM 输入 =「旧摘要 + 本轮摘要」→
  合并输出落库；实体/决策新旧合并去重（旧值在前，截断至现有抽取上限）；
- 降级链：无旧摘要 → 纯本轮摘要（现状逻辑）；合并 LLM 失败 → 保存本轮摘要
  （本轮数据不丢）；本轮摘要 LLM 失败 → 跳过写入；全程异常不向外抛（P2 兜底）；
- 配置语义：cross_session_enabled=False（ContextManagementConfig）跳过写入；
- 条件跳过：空输入 / 空摘要。

与执行编排层接线的集成测试见
services/fastapi_service/tests/test_cross_session_memory_wiring.py。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.context_manager.tests.test_save_turn_summary --settings=Django_xm.settings.test
"""

import unittest
from unittest import mock

from Django_xm.apps.context_manager.services.compression import ContextCompressionEngine
from Django_xm.apps.context_manager.services.manager import (
    ContextManagementConfig,
    ContextManager,
)

_OLD_SUMMARY = "旧摘要：用户在搭建 Vue3 项目，偏好组合式 API 与 Pinia。"
_OLD_ENTITIES = ["Vue3", "Pinia"]
_OLD_DECISIONS = ["使用 Pinia 状态管理"]
_TURN_SUMMARY = "本轮摘要：用户询问路由守卫的实现方式。"
_MERGED_SUMMARY = "合并累积摘要：涵盖 Vue3 项目搭建与路由守卫要点。"


def _make_manager(cross_session_enabled=True, user_id=42):
    config = ContextManagementConfig(cross_session_enabled=cross_session_enabled)
    return ContextManager(user_id=user_id, config=config)


def _old_context_data():
    """模拟 save_session_context 已写入的旧条目结构（spec P2 读侧输入）。"""
    return {
        "session_id": "sess-p2",
        "summary": _OLD_SUMMARY,
        "key_entities": list(_OLD_ENTITIES),
        "key_decisions": list(_OLD_DECISIONS),
        "saved_at": 1.0,
    }


class SaveTurnSummaryTests(unittest.TestCase):
    """save_turn_summary 摘要生成与委托写入测试。"""

    def test_generates_summary_and_delegates_save(self):
        """摘要经统一引擎通道生成，实体/决策规则抽取后委托 save_session_context。"""
        manager = _make_manager()
        with mock.patch.object(
            ContextCompressionEngine, "summarize_long_text", return_value="  会话摘要  "
        ) as mock_summarize, mock.patch.object(
            ContextManager, "_load_session_context_data", return_value=None
        ) as mock_load, mock.patch.object(
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

        mock_load.assert_called_once_with("sess-d2")
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
        """摘要为空/空白 → 跳过写入（空摘要在读取旧摘要之前短路）。"""
        manager = _make_manager()
        with mock.patch.object(
            ContextCompressionEngine, "summarize_long_text", return_value="   "
        ), mock.patch.object(ContextManager, "save_session_context", return_value=True) as mock_save:
            result = manager.save_turn_summary("sess-d2", "问题", "回答")
        self.assertFalse(result)
        mock_save.assert_not_called()

    def test_save_failure_swallowed_returns_false(self):
        """P2 兜底语义：save_session_context 抛出 → 吞没返回 False，不向外抛
        （调用方 chat finalizer 已有降级包裹，此处再兜底）。"""
        manager = _make_manager()
        with mock.patch.object(
            ContextCompressionEngine, "summarize_long_text", return_value="摘要"
        ), mock.patch.object(
            ContextManager, "_load_session_context_data", return_value=None
        ), mock.patch.object(
            ContextManager, "save_session_context", side_effect=RuntimeError("store down")
        ):
            result = manager.save_turn_summary("sess-d2", "问题", "回答")
        self.assertFalse(result)

    def test_extract_decisions_public_alias(self):
        """extract_decisions 公开别名与 _extract_decisions 同源（spec D2 消除私有访问）。"""
        messages = [{"role": "assistant", "content": "结论是使用 Chroma 向量库"}]
        self.assertEqual(
            ContextCompressionEngine.extract_decisions(messages),
            ["使用 Chroma 向量库"],
        )


class SaveTurnSummaryIncrementalMergeTests(unittest.TestCase):
    """save_turn_summary 增量合并测试（spec P2）：读旧 → 合并 → 降级链。"""

    def test_first_turn_without_old_summary_saves_turn_summary(self):
        """①首轮（无旧摘要）= 本轮摘要（现状逻辑，不触发合并调用）。"""
        manager = _make_manager()
        with mock.patch.object(
            ContextCompressionEngine, "summarize_long_text", return_value=_TURN_SUMMARY
        ) as mock_summarize, mock.patch.object(
            ContextManager, "_load_session_context_data", return_value=None
        ) as mock_load, mock.patch.object(
            ContextManager, "save_session_context", return_value=True
        ) as mock_save:
            result = manager.save_turn_summary("sess-p2", "路由守卫怎么写", "建议：使用 beforeEach。")

        self.assertTrue(result)
        mock_summarize.assert_called_once()  # 仅本轮摘要一次调用，无合并调用
        mock_load.assert_called_once_with("sess-p2")
        save_kwargs = mock_save.call_args.kwargs
        self.assertEqual(save_kwargs.get("summary"), _TURN_SUMMARY)

    def test_second_turn_merges_old_and_turn_summary(self):
        """②第二轮：合并 LLM 输入含旧摘要 + 本轮摘要，保存合并输出；实体/决策新旧合并。"""
        manager = _make_manager()
        with mock.patch.object(
            ContextCompressionEngine,
            "summarize_long_text",
            side_effect=[_TURN_SUMMARY, _MERGED_SUMMARY],
        ) as mock_summarize, mock.patch.object(
            ContextManager, "_load_session_context_data", return_value=_old_context_data()
        ), mock.patch.object(
            ContextManager, "save_session_context", return_value=True
        ) as mock_save:
            result = manager.save_turn_summary(
                "sess-p2",
                "我决定使用 Vue Router 的导航守卫",
                "好的。建议：使用 beforeEach 注册全局守卫。结论是路由守卫能统一鉴权",
            )

        self.assertTrue(result)
        self.assertEqual(mock_summarize.call_count, 2)
        # 第 1 次调用 = 本轮摘要；第 2 次调用 = 增量合并（输入含旧摘要与本轮摘要）
        first_text = mock_summarize.call_args_list[0].args[0]
        self.assertIn("我决定使用 Vue Router 的导航守卫", first_text)
        merge_text = mock_summarize.call_args_list[1].args[0]
        self.assertIn(_OLD_SUMMARY, merge_text)
        self.assertIn(_TURN_SUMMARY, merge_text)
        merge_instruction = mock_summarize.call_args_list[1].args[1]
        self.assertIn("不超过5句话", merge_instruction)
        self.assertIn("累积摘要", merge_instruction)
        self.assertIn("保留历史关键决策与实体", merge_instruction)

        mock_save.assert_called_once()
        save_kwargs = mock_save.call_args.kwargs
        self.assertEqual(save_kwargs.get("summary"), _MERGED_SUMMARY)
        # 实体/决策：旧值在前 + 本轮规则抽取追加，去重
        entities = save_kwargs.get("key_entities")
        decisions = save_kwargs.get("key_decisions")
        self.assertEqual(entities[:2], _OLD_ENTITIES)
        self.assertIn("Vue3", entities)
        self.assertIn("Pinia", entities)
        self.assertEqual(decisions[0], _OLD_DECISIONS[0])
        self.assertTrue(any("beforeEach" in d for d in decisions[1:]))

    def test_merge_llm_failure_degrades_to_turn_summary(self):
        """③合并 LLM 失败 → 降级保存本轮摘要（本轮数据不丢），不抛异常。"""
        manager = _make_manager()
        with mock.patch.object(
            ContextCompressionEngine,
            "summarize_long_text",
            side_effect=[_TURN_SUMMARY, RuntimeError("merge llm down")],
        ) as mock_summarize, mock.patch.object(
            ContextManager, "_load_session_context_data", return_value=_old_context_data()
        ), mock.patch.object(
            ContextManager, "save_session_context", return_value=True
        ) as mock_save:
            result = manager.save_turn_summary("sess-p2", "路由守卫怎么写", "建议：使用 beforeEach。")

        self.assertTrue(result)
        self.assertEqual(mock_summarize.call_count, 2)
        save_kwargs = mock_save.call_args.kwargs
        self.assertEqual(save_kwargs.get("summary"), _TURN_SUMMARY)

    def test_merge_empty_output_degrades_to_turn_summary(self):
        """合并 LLM 返回空白 → 同样降级保存本轮摘要（不写入空摘要覆盖旧记忆）。"""
        manager = _make_manager()
        with mock.patch.object(
            ContextCompressionEngine,
            "summarize_long_text",
            side_effect=[_TURN_SUMMARY, "   "],
        ), mock.patch.object(
            ContextManager, "_load_session_context_data", return_value=_old_context_data()
        ), mock.patch.object(
            ContextManager, "save_session_context", return_value=True
        ) as mock_save:
            result = manager.save_turn_summary("sess-p2", "问题", "回答")

        self.assertTrue(result)
        self.assertEqual(mock_save.call_args.kwargs.get("summary"), _TURN_SUMMARY)

    def test_turn_summary_llm_failure_skips_write_silently(self):
        """本轮摘要 LLM 失败 → 跳过写入且不抛异常（短路于读取旧摘要之前）。"""
        manager = _make_manager()
        with mock.patch.object(
            ContextCompressionEngine, "summarize_long_text", side_effect=RuntimeError("llm down")
        ), mock.patch.object(
            ContextManager, "_load_session_context_data", return_value=_old_context_data()
        ) as mock_load, mock.patch.object(
            ContextManager, "save_session_context", return_value=True
        ) as mock_save:
            result = manager.save_turn_summary("sess-p2", "问题", "回答")
        self.assertFalse(result)
        mock_load.assert_not_called()
        mock_save.assert_not_called()

    def test_no_exception_ever_propagates(self):
        """④全程不抛异常：读取旧摘要等任意环节异常均吞没为 False。"""
        manager = _make_manager()
        with mock.patch.object(
            ContextCompressionEngine, "summarize_long_text", return_value=_TURN_SUMMARY
        ), mock.patch.object(
            ContextManager, "_load_session_context_data", side_effect=RuntimeError("read boom")
        ):
            self.assertFalse(manager.save_turn_summary("sess-p2", "问题", "回答"))


class SaveTurnSummaryRealStoreEndToEndTests(unittest.TestCase):
    """真实 InMemoryStore 链路：读旧 → 合并 → 覆盖写同键的两轮累积验证。

    注入独立 InMemoryStore 实例（不依赖全局 store 单例与其后端回退行为），
    真实走 save_session_context（容量淘汰）与 _load_session_context_data（store.get）。
    """

    def test_second_turn_merges_real_saved_summary(self):
        from langgraph.store.memory import InMemoryStore

        store = InMemoryStore()
        user_id = 987654
        namespace = (str(user_id), "session_contexts")
        session_id = "sess-p2-e2e"
        manager = _make_manager(user_id=user_id)
        with mock.patch.object(ContextManager, "_ensure_store", return_value=store), mock.patch.object(
            ContextCompressionEngine,
            "summarize_long_text",
            side_effect=[
                "第一轮摘要：搭建 Vue3 项目。",
                "第二轮摘要：增加路由守卫。",
                "合并累积摘要：两轮要点齐全。",
            ],
        ):
            self.assertTrue(manager.save_turn_summary(session_id, "用户问题A", "助手回答A"))
            self.assertTrue(manager.save_turn_summary(session_id, "用户问题B", "助手回答B"))

        item = store.get(namespace, session_id)
        self.assertIsNotNone(item)
        self.assertEqual(item.value.get("summary"), "合并累积摘要：两轮要点齐全。")


if __name__ == "__main__":
    unittest.main()
