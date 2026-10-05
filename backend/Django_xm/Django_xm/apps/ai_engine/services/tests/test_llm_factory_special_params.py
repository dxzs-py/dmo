"""批次 E（E1d/E1e）special_params 判定与 provider 传参回归测试。

- E1d：is_thinking_enabled 对 reasoning_effort-only 不再判为 thinking 启用。
  修复前 reasoning_effort 单独存在 → is_thinking_enabled 返回 True（thinking.py
  旧 L64-66 分支），而 _apply_special_params（llm_factory.py:134-141）却把
  reasoning_effort 剥离——两处判定自相矛盾，导致 DeepSeek 结构化输出在
  get_structured_model_with_fallback 中误走 JsonMode 分支（幽灵分支）。
- E1e：get_chat_model_by_provider 的 ollama 分支将 max_tokens 映射为
  num_predict 显式传给 provider（修复前被过滤列表静默丢弃，
  ChatOllama 无 max_tokens 字段，直接透传会丢失）。

运行（backend/Django_xm，conda env langchain_xm）：
    python manage.py test Django_xm.apps.ai_engine.services.tests.test_llm_factory_special_params --noinput
"""

from unittest.mock import patch

import unittest

from Django_xm.apps.ai_engine.services.thinking import is_thinking_enabled


class ThinkingEnabledJudgementTests(unittest.TestCase):
    """E1d：is_thinking_enabled 判定契约（thinking.py）。"""

    def test_reasoning_effort_only_is_not_thinking(self):
        """reasoning_effort 单独存在（无 thinking）→ 不视为 thinking 启用。"""
        self.assertIs(is_thinking_enabled({"reasoning_effort": "high"}, "deepseek"), False)
        self.assertIs(is_thinking_enabled({"reasoning_effort": "max"}, "deepseek"), False)

    def test_explicit_thinking_enabled_dict(self):
        """显式 thinking={"type": "enabled"} → True（DeepSeek/Anthropic 格式）。"""
        self.assertIs(is_thinking_enabled({"thinking": {"type": "enabled"}}, "deepseek"), True)

    def test_thinking_disabled_dict(self):
        """显式 thinking={"type": "disabled"} → False。"""
        self.assertIs(is_thinking_enabled({"thinking": {"type": "disabled"}}, "deepseek"), False)

    def test_thinking_bool_true_ollama_format(self):
        """Ollama 格式 thinking=True → True。"""
        self.assertIs(is_thinking_enabled({"thinking": True}, "ollama"), True)

    def test_ollama_reasoning_param(self):
        """Ollama reasoning=True → True。"""
        self.assertIs(is_thinking_enabled({"reasoning": True}, "ollama"), True)

    def test_thinking_enabled_with_reasoning_effort_combined(self):
        """显式 thinking 启用 + reasoning_effort → 仍判为 thinking（JsonMode 路径保留）。"""
        self.assertIs(
            is_thinking_enabled({"thinking": {"type": "enabled"}, "reasoning_effort": "high"}, "deepseek"),
            True,
        )

    def test_empty_or_none_params(self):
        """空参数 → False。"""
        self.assertIs(is_thinking_enabled(None, "deepseek"), False)
        self.assertIs(is_thinking_enabled({}, "deepseek"), False)


class _FakeChatModel:
    """替身聊天模型：with_structured_output / bind 返回可断言的哨兵元组。"""

    def with_structured_output(self, schema, **kwargs):
        return ("structured_output", schema)

    def bind(self, **kwargs):
        return ("bound", kwargs)


class DeepSeekStructuredRouteTests(unittest.TestCase):
    """E1d：get_structured_model_with_fallback 的 DeepSeek 结构化输出路由。"""

    _FACTORY_TARGET = "Django_xm.apps.ai_engine.services.llm_factory.get_chat_model_by_provider"
    _JSON_MODE_TARGET = "Django_xm.apps.ai_engine.services.llm_factory.JsonModeStructuredModel"

    def _run(self, special_params):
        from Django_xm.apps.ai_engine.services import llm_factory

        with (
            patch(
                "Django_xm.apps.ai_engine.services.llm_factory.get_system_default_chat_model",
                return_value=None,
            ),
            patch(
                "Django_xm.apps.ai_engine.services.llm_factory.get_fallback_candidates",
                return_value=[],
            ),
            patch(self._FACTORY_TARGET, return_value=_FakeChatModel()) as mock_create,
        ):
            result = llm_factory.get_structured_model_with_fallback(
                schema=dict,
                model_name="deepseek-v4-flash",
                model_provider="deepseek",
                special_params=special_params,
            )
        return result, mock_create

    def test_reasoning_effort_only_uses_tool_choice_path(self):
        """reasoning_effort-only：走 with_structured_output 路径（thinking 显式禁用），不误走 JsonMode。"""
        from Django_xm.apps.ai_engine.services.llm_fallback import StructuredModelWithFallback

        with patch(self._JSON_MODE_TARGET) as mock_json_mode:
            result, mock_create = self._run({"reasoning_effort": "high"})

        # 幽灵分支修复点：不得进入 DeepSeek thinking 的 JsonMode 分支
        mock_json_mode.assert_not_called()
        # 原 elif 分支契约：以 thinking 显式禁用创建主模型
        self.assertEqual(
            mock_create.call_args.kwargs.get("special_params"), {"thinking": {"type": "disabled"}}
        )
        self.assertIsInstance(result, StructuredModelWithFallback)
        self.assertEqual(result._models[0][2], ("structured_output", dict))

    def test_explicit_thinking_still_uses_json_mode(self):
        """显式 thinking 启用：保留 JsonMode 路径（tool_choice 冲突的原有语义不变）。"""
        with patch(self._JSON_MODE_TARGET) as mock_json_mode:
            _result, mock_create = self._run({"thinking": {"type": "enabled"}})

        mock_json_mode.assert_called_once()
        self.assertEqual(
            mock_create.call_args.kwargs.get("special_params"), {"thinking": {"type": "enabled"}}
        )


class OllamaMaxTokensTests(unittest.TestCase):
    """E1e：get_chat_model_by_provider 的 ollama 分支 max_tokens → num_predict。"""

    def _call(self, max_tokens):
        from Django_xm.apps.ai_engine.services import llm_factory

        with (
            patch("Django_xm.apps.ai_engine.providers.ollama.create_chat_model") as mock_create,
            patch("Django_xm.apps.ai_engine.services.llm_factory.get_rate_limiter", return_value=None),
            patch("Django_xm.apps.ai_engine.config.settings.openai_max_tokens", None),
        ):
            llm_factory.get_chat_model_by_provider(
                provider_id="ollama",
                model_name="test-model",
                temperature=0.3,
                max_tokens=max_tokens,
                streaming=False,
                use_cache=False,
            )
        return mock_create

    def test_max_tokens_mapped_to_num_predict(self):
        """max_tokens=512 → 以 num_predict=512 显式传入 provider，不再静默丢弃。"""
        mock_create = self._call(512)

        mock_create.assert_called_once()
        kwargs = mock_create.call_args.kwargs
        self.assertEqual(kwargs.get("num_predict"), 512)
        # ChatOllama 无 max_tokens 字段，不得以原名透传
        self.assertNotIn("max_tokens", kwargs)

    def test_no_max_tokens_does_not_pass_num_predict(self):
        """未设置 max_tokens（且 settings.openai_max_tokens=None）→ 不传 num_predict，行为不变。"""
        mock_create = self._call(None)

        mock_create.assert_called_once()
        self.assertNotIn("num_predict", mock_create.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
