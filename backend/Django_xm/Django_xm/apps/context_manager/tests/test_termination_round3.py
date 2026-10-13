"""termination_judge / token 预算口径收紧测试（fix-review-round3 2.9 + 2.10）。

2.9 防误杀三项：
- goal_completed 最低轮数配置化（原硬编码 6）；
- 通用词（完成/总结/综上所述）从总结性语言模式移除——仅凭通用词不终止；
- INFO_GAIN_DECAY 需连续 N 轮（termination_info_gain_consecutive_rounds，
  默认 6）低增益才 TERMINATE（原 window=4 且 confidence=0.5 即终止，
  中文长任务易误杀）。

2.10 token 累积口径：
- judge.set_token_usage 全量替换（替代增量累加"仅最后一条 AIMessage"）；
- ContextManagerMiddleware.after_model 以全量消息（system prompt + 输入 +
  工具结果 + 输出）估算设置；
- 预算触发用例。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.context_manager.tests.test_termination_round3
"""

import unittest
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from Django_xm.apps.context_manager.config import context_settings
from Django_xm.apps.context_manager.middleware import ContextManagerMiddleware
from Django_xm.apps.context_manager.services.termination_judge import (
    ContextTerminationJudge,
    TerminationAction,
)

# 终止判定测试环境隔离：冻结 embedding 相似度（测试环境会触达真实 Ollama/
# OpenAI embedding），统一走词重叠启发式，保证判定结果确定性强。
_NO_EMBEDDING = lambda a, b: None  # noqa: E731 - 静态调用点（类调用，无需 self）


def _ai(content: str, with_tool_call: bool = False, call_id: str = "") -> AIMessage:
    if with_tool_call:
        return AIMessage(
            content=content,
            tool_calls=[{"name": "web_search", "args": {}, "id": call_id or "call-x"}],
        )
    return AIMessage(content=content)


class GoalCompletedMinRoundsTests(SimpleTestCase):
    """goal_completed：min_rounds 配置化 + 通用词不再触发。"""

    def test_generic_words_alone_do_not_terminate(self):
        """仅含「总结/完成/综上所述」等通用词的正常推进回复 → 不终止（round3 2.9）。"""
        judge = ContextTerminationJudge()
        # 7 轮 AI 消息（>= 默认 min_rounds=6），末窗口无工具调用，
        # 内容含通用词但无强总结短语；各轮内容互异（信息增益高，
        # 避免误触 INFO_GAIN_DECAY 分支干扰断言目标）。
        messages = [HumanMessage(content="调研任务开始")]
        for i in range(7):
            messages.append(
                _ai(
                    f"第{i}阶段：模块{i}的覆盖率提升至{90 + i}%，"
                    f"接下来完成集成验证{i}，先简单总结要点{i}后再继续"
                )
            )
        with mock.patch.object(ContextTerminationJudge, "_embedding_similarity", _NO_EMBEDDING):
            verdict = judge.judge({"messages": messages})
        self.assertNotEqual(verdict.action, TerminationAction.TERMINATE)

    def test_strong_summary_phrase_after_min_rounds_terminates(self):
        """强总结短语（"以上是"）+ 轮数达标 + 末窗口无工具调用 → GOAL_COMPLETED。"""
        judge = ContextTerminationJudge()
        messages = [HumanMessage(content="任务开始")]
        for i in range(6):
            messages.append(_ai(f"阶段{i}：处理数据分片{i}，产出指标{i}系列结论"))
        messages.append(_ai("以上是全部调研结果，希望这能解决你的问题。"))
        verdict = judge.judge({"messages": messages})
        self.assertEqual(verdict.action, TerminationAction.TERMINATE)
        self.assertEqual(verdict.signal.value, "goal_completed")

    def test_min_rounds_configurable(self):
        """min_rounds 配置化：提高阈值后，轮数不足不再触发 goal_completed。

        注：judge 在构造期读取 context_settings（pydantic 配置实例，
        非 Django settings），故 patch 必须先于 judge 构造生效。
        """
        messages = [HumanMessage(content="任务开始")]
        for i in range(7):
            messages.append(_ai(f"阶段{i}：处理数据分片{i}"))
        messages.append(_ai("以上是全部调研结果，希望这能解决你的问题。"))
        with (
            mock.patch.object(context_settings, "termination_goal_complete_min_rounds", 10),
            mock.patch.object(ContextTerminationJudge, "_embedding_similarity", _NO_EMBEDDING),
        ):
            judge = ContextTerminationJudge()
            verdict = judge.judge({"messages": messages})
        self.assertNotEqual(verdict.action, TerminationAction.TERMINATE)


class InfoGainDecayConsecutiveTests(SimpleTestCase):
    """INFO_GAIN_DECAY：连续 N 轮低增益才 TERMINATE。"""

    def test_short_low_gain_streak_does_not_terminate(self):
        """5 轮低增益（< 默认连续要求 6）→ 不终止（旧实现 window=4 即终止）。"""
        judge = ContextTerminationJudge()
        messages = [HumanMessage(content="任务开始")]
        for _ in range(6):  # len(ai)=6 < n_required(6)+1=7 → 不判定
            messages.append(_ai("当前进展汇报：情况与此前相同。"))
        with mock.patch.object(ContextTerminationJudge, "_embedding_similarity", _NO_EMBEDDING):
            verdict = judge.judge({"messages": messages})
        self.assertNotEqual(verdict.action, TerminationAction.TERMINATE)

    def test_long_low_gain_streak_terminates(self):
        """7 轮低增益（>= 连续要求 6）→ TERMINATE，metadata 记录连续轮数要求。"""
        judge = ContextTerminationJudge()
        messages = [HumanMessage(content="任务开始")]
        for _ in range(8):
            messages.append(_ai("当前进展汇报：情况与此前相同。"))
        with mock.patch.object(ContextTerminationJudge, "_embedding_similarity", _NO_EMBEDDING):
            verdict = judge.judge({"messages": messages})
        self.assertEqual(verdict.action, TerminationAction.TERMINATE)
        self.assertEqual(verdict.signal.value, "info_gain_decay")
        self.assertEqual(verdict.metadata.get("consecutive_rounds_required"), 6)

    def test_novel_progress_amid_long_task_not_killed(self):
        """中文长任务不误杀：持续有新信息（高新颖度）的汇报轮不触发终止。"""
        judge = ContextTerminationJudge()
        messages = [HumanMessage(content="调研任务开始")]
        for i in range(12):
            messages.append(
                _ai(
                    f"第{i}阶段突破：发现机制{i}，量化指标{i}为{50 + i}单位，"
                    f"下一步验证假设{i}"
                )
            )
        with mock.patch.object(ContextTerminationJudge, "_embedding_similarity", _NO_EMBEDDING):
            verdict = judge.judge({"messages": messages})
        self.assertNotEqual(verdict.action, TerminationAction.TERMINATE)


class TokenUsageAccountingTests(SimpleTestCase):
    """round3 2.10：全量 token 口径（set_token_usage 替换式 + middleware 接线）。"""

    def test_set_token_usage_replaces_not_accumulates(self):
        judge = ContextTerminationJudge(token_budget_limit=10**9)
        judge.set_token_usage(100)
        judge.set_token_usage(250)
        self.assertEqual(judge._cumulative_tokens, 250, "全量口径应为替换而非累加")

    def test_budget_exhausted_triggers_terminate(self):
        """预算触发：全量口径超限 → BUDGET_EXHAUSTED TERMINATE。"""
        judge = ContextTerminationJudge(token_budget_limit=200)
        judge.set_token_usage(201)
        verdict = judge.judge({"messages": [_ai("done")]})
        self.assertEqual(verdict.action, TerminationAction.TERMINATE)
        self.assertEqual(verdict.signal.value, "budget_exhausted")

    def test_after_model_sets_full_message_estimate(self):
        """after_model 以全量消息估算（+ system prompt）设置 judge token 口径。"""
        mw = ContextManagerMiddleware(model_name="gpt-4o")
        mw._capture_system_prompt_tokens(SimpleNamespace(system_prompt="你是严谨的助手"))
        self.assertGreater(mw._system_prompt_tokens, 0)

        messages = [
            HumanMessage(content="帮我分析这段配置的作用并给出建议"),
            _ai("我先读取配置文件。", with_tool_call=True, call_id="call-1"),
            ToolMessage(content="配置内容：" + "细节" * 50, tool_call_id="call-1"),
            _ai("分析结论如下，包含多项建议与说明。"),
        ]
        expected = mw._system_prompt_tokens + mw._estimate_messages_tokens(messages)

        judge_mock = mock.Mock()
        from Django_xm.apps.context_manager.services.termination_judge import TerminationVerdict

        judge_mock.judge.return_value = TerminationVerdict()
        mw._termination_judge = judge_mock

        state = {"messages": messages}
        mw.after_model(state, runtime=None)

        judge_mock.set_token_usage.assert_called_once_with(expected)

    def test_after_model_no_messages_skips_accounting(self):
        """空消息列表：不设置 token 口径。"""
        mw = ContextManagerMiddleware(model_name="gpt-4o")
        judge_mock = mock.Mock()
        from Django_xm.apps.context_manager.services.termination_judge import TerminationVerdict

        judge_mock.judge.return_value = TerminationVerdict()
        mw._termination_judge = judge_mock

        mw.after_model({"messages": []}, runtime=None)
        judge_mock.set_token_usage.assert_not_called()


if __name__ == "__main__":
    unittest.main()
