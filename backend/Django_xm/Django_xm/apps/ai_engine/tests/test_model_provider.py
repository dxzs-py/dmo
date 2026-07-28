"""ModelProvider 单元测试

验证:
1. get_default_model() 返回 ResilientModel 实例（主模型 + 降级模型）
2. get_default_model(enable_fallback=False) 返回原始 BaseChatModel（不包装）
3. get_helper_model() 返回 ResilientModel 实例
4. get_helper_model() 在辅助模型未配置时抛出 RuntimeError
5. get_structured_model() 返回 ResilientModel 实例
6. get_streaming_model() 返回 ResilientModel 实例
7. 主模型创建失败时直接抛 RuntimeError（快速失败，不提升 fallback）
8. 辅助模型创建失败时直接抛 RuntimeError
9. 降级模型未配置时返回主模型（无降级链）
10. _read_model_config 从 SystemConfig 读取 (provider_id, model_name)

行为契约:
- 创建时降级链构建（本模块职责）：主模型 + 降级模型 → ResilientModel 包装
- 运行时降级（ResilientInvoker 职责）：主模型重试3次失败后切换到降级模型
- 主模型创建失败 → 直接抛 RuntimeError（配置错误应快速失败）
- 降级模型创建失败 → 仅记日志，返回主模型（降级是保险，不是必需品）

运行方式:
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    conda activate langchain_xm
    python -m pytest Django_xm/apps/ai_engine/tests/test_model_provider.py -v

    # 或使用 unittest
    set DJANGO_SETTINGS_MODULE=Django_xm.settings.dev
    python -m unittest Django_xm.apps.ai_engine.tests.test_model_provider -v
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

# Django 环境初始化（兼容 pytest 和 unittest 直接运行）
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django
import django.apps

if not django.apps.apps.ready:
    django.setup()

from Django_xm.apps.agent_hub.services.resilient_invoker import (
    ResilientModel,
)
from Django_xm.apps.ai_engine.services.model_provider import (
    _create_model_from_candidate,
    _read_model_config,
    get_default_model,
    get_helper_model,
    get_streaming_model,
    get_structured_model,
)

# ============================================================================
# Mock 工厂
# ============================================================================


def make_mock_model(name: str = "mock-model", provider_id: str = "mock") -> MagicMock:
    """创建模拟的 BaseChatModel

    Args:
        name: 模型名称（用于日志和识别）
        provider_id: 提供商 ID

    Returns:
        MagicMock 实例，模拟 BaseChatModel 接口
    """
    model = MagicMock(name=name)
    model._llm_type = name
    model._provider_id = provider_id
    # with_structured_output 返回一个新的 mock Runnable
    model.with_structured_output.return_value = MagicMock(name=f"{name}-structured")
    return model


# ============================================================================
# get_default_model 测试
# ============================================================================


class GetDefaultModelTestCase(unittest.TestCase):
    """get_default_model 测试"""

    @patch("Django_xm.apps.ai_engine.services.model_provider._create_model_from_candidate")
    @patch("Django_xm.apps.ai_engine.services.model_provider._read_model_config")
    def test_returns_resilient_model_when_fallback_enabled(
        self,
        mock_read_config,
        mock_create_from_candidate,
    ):
        """get_default_model() 返回 ResilientModel 实例（主模型 + 降级模型）"""
        mock_default = make_mock_model("default", "openai")
        mock_fallback = make_mock_model("fallback", "groq")

        # _read_model_config 被调用两次：主模型 + 降级模型
        mock_read_config.side_effect = [
            ("openai", "gpt-4o-mini"),
            ("groq", "llama-3.3-70b-versatile"),
        ]
        mock_create_from_candidate.side_effect = [mock_default, mock_fallback]

        result = get_default_model()

        self.assertIsInstance(result, ResilientModel)
        # 降级链：主模型 + 降级模型 = 2 个
        self.assertEqual(len(result.models), 2)
        self.assertIs(result.models[0], mock_default)
        self.assertIs(result.models[1], mock_fallback)
        # 验证 _read_model_config 调用了 2 次
        self.assertEqual(mock_read_config.call_count, 2)

    @patch("Django_xm.apps.ai_engine.services.model_provider._create_model_from_candidate")
    @patch("Django_xm.apps.ai_engine.services.model_provider._read_model_config")
    def test_returns_base_model_when_fallback_disabled(
        self,
        mock_read_config,
        mock_create_from_candidate,
    ):
        """get_default_model(enable_fallback=False) 返回原始 BaseChatModel"""
        mock_default = make_mock_model("default", "openai")
        mock_read_config.return_value = ("openai", "gpt-4o-mini")
        mock_create_from_candidate.return_value = mock_default

        result = get_default_model(enable_fallback=False)

        self.assertNotIsInstance(result, ResilientModel)
        self.assertIs(result, mock_default)
        # enable_fallback=False 时只读取主模型配置，不读取降级模型配置
        self.assertEqual(mock_read_config.call_count, 1)

    @patch("Django_xm.apps.ai_engine.services.model_provider._create_model_from_candidate")
    @patch("Django_xm.apps.ai_engine.services.model_provider._read_model_config")
    def test_raises_runtime_error_when_default_not_configured(
        self,
        mock_read_config,
        mock_create_from_candidate,
    ):
        """主模型未配置时抛出 RuntimeError"""
        mock_read_config.return_value = ("", "")

        with self.assertRaises(RuntimeError) as ctx:
            get_default_model()

        self.assertIn("未配置默认模型", str(ctx.exception))
        self.assertIn("default_chat_model", str(ctx.exception))
        # 创建函数不应被调用
        mock_create_from_candidate.assert_not_called()

    @patch("Django_xm.apps.ai_engine.services.model_provider._create_model_from_candidate")
    @patch("Django_xm.apps.ai_engine.services.model_provider._read_model_config")
    def test_raises_runtime_error_when_default_creation_fails(
        self,
        mock_read_config,
        mock_create_from_candidate,
    ):
        """主模型创建失败时直接抛 RuntimeError（快速失败，不提升 fallback）"""
        mock_read_config.return_value = ("openai", "gpt-4o-mini")
        mock_create_from_candidate.side_effect = Exception("OpenAI 不可用")

        with self.assertRaises(RuntimeError) as ctx:
            get_default_model()

        self.assertIn("OpenAI 不可用", str(ctx.exception))
        # 主模型创建失败后不应再读取降级模型配置
        self.assertEqual(mock_read_config.call_count, 1)

    @patch("Django_xm.apps.ai_engine.services.model_provider._create_model_from_candidate")
    @patch("Django_xm.apps.ai_engine.services.model_provider._read_model_config")
    def test_returns_default_only_when_fallback_not_configured(
        self,
        mock_read_config,
        mock_create_from_candidate,
    ):
        """降级模型未配置时返回主模型（不包装 ResilientModel）"""
        mock_default = make_mock_model("default", "openai")

        # 主模型配置存在，降级模型配置为空
        mock_read_config.side_effect = [
            ("openai", "gpt-4o-mini"),
            ("", ""),
        ]
        mock_create_from_candidate.return_value = mock_default

        result = get_default_model()

        # 无降级链时返回原始主模型
        self.assertNotIsInstance(result, ResilientModel)
        self.assertIs(result, mock_default)

    @patch("Django_xm.apps.ai_engine.services.model_provider._create_model_from_candidate")
    @patch("Django_xm.apps.ai_engine.services.model_provider._read_model_config")
    def test_returns_default_only_when_fallback_creation_fails(
        self,
        mock_read_config,
        mock_create_from_candidate,
    ):
        """降级模型创建失败时仅记日志，返回主模型（不包装）"""
        mock_default = make_mock_model("default", "openai")

        mock_read_config.side_effect = [
            ("openai", "gpt-4o-mini"),
            ("groq", "llama-3.3-70b-versatile"),
        ]
        # 主模型创建成功，降级模型创建失败
        mock_create_from_candidate.side_effect = [mock_default, Exception("Groq 不可用")]

        result = get_default_model()

        # 降级模型创建失败时返回主模型（不包装）
        self.assertNotIsInstance(result, ResilientModel)
        self.assertIs(result, mock_default)


# ============================================================================
# get_helper_model 测试
# ============================================================================


class GetHelperModelTestCase(unittest.TestCase):
    """get_helper_model 测试"""

    @patch("Django_xm.apps.ai_engine.services.model_provider._create_model_from_candidate")
    @patch("Django_xm.apps.ai_engine.services.model_provider._read_model_config")
    def test_returns_resilient_model_when_helper_configured(
        self,
        mock_read_config,
        mock_create_from_candidate,
    ):
        """get_helper_model() 返回 ResilientModel 实例（辅助模型 + 降级模型）"""
        mock_helper = make_mock_model("helper", "deepseek")
        mock_fallback = make_mock_model("fallback", "groq")

        # _read_model_config 被调用两次：辅助模型 + 降级模型
        mock_read_config.side_effect = [
            ("deepseek", "deepseek-v4-flash"),
            ("groq", "llama-3.3-70b-versatile"),
        ]
        mock_create_from_candidate.side_effect = [mock_helper, mock_fallback]

        result = get_helper_model()

        self.assertIsInstance(result, ResilientModel)
        self.assertEqual(len(result.models), 2)
        self.assertIs(result.models[0], mock_helper)
        self.assertIs(result.models[1], mock_fallback)
        self.assertEqual(mock_read_config.call_count, 2)

    @patch("Django_xm.apps.ai_engine.services.model_provider._read_model_config")
    def test_raises_runtime_error_when_helper_not_configured(self, mock_read_config):
        """辅助模型未配置时抛出 RuntimeError"""
        mock_read_config.return_value = ("", "")

        with self.assertRaises(RuntimeError) as ctx:
            get_helper_model()

        self.assertIn("未配置辅助模型", str(ctx.exception))
        self.assertIn("helper_model", str(ctx.exception))

    @patch("Django_xm.apps.ai_engine.services.model_provider._create_model_from_candidate")
    @patch("Django_xm.apps.ai_engine.services.model_provider._read_model_config")
    def test_raises_runtime_error_when_helper_creation_fails(
        self,
        mock_read_config,
        mock_create_from_candidate,
    ):
        """辅助模型创建失败时直接抛 RuntimeError（快速失败，不提升 fallback）"""
        mock_read_config.return_value = ("deepseek", "deepseek-v4-flash")
        mock_create_from_candidate.side_effect = Exception("DeepSeek 不可用")

        with self.assertRaises(RuntimeError) as ctx:
            get_helper_model()

        self.assertIn("DeepSeek 不可用", str(ctx.exception))
        # 辅助模型创建失败后不应再读取降级模型配置
        self.assertEqual(mock_read_config.call_count, 1)

    @patch("Django_xm.apps.ai_engine.services.model_provider._create_model_from_candidate")
    @patch("Django_xm.apps.ai_engine.services.model_provider._read_model_config")
    def test_returns_helper_only_when_fallback_not_configured(
        self,
        mock_read_config,
        mock_create_from_candidate,
    ):
        """降级模型未配置时返回辅助模型（不包装 ResilientModel）"""
        mock_helper = make_mock_model("helper", "deepseek")

        # 辅助模型配置存在，降级模型配置为空
        mock_read_config.side_effect = [
            ("deepseek", "deepseek-v4-flash"),
            ("", ""),
        ]
        mock_create_from_candidate.return_value = mock_helper

        result = get_helper_model()

        self.assertNotIsInstance(result, ResilientModel)
        self.assertIs(result, mock_helper)

    @patch("Django_xm.apps.ai_engine.services.model_provider._create_model_from_candidate")
    @patch("Django_xm.apps.ai_engine.services.model_provider._read_model_config")
    def test_returns_helper_only_when_fallback_creation_fails(
        self,
        mock_read_config,
        mock_create_from_candidate,
    ):
        """降级模型创建失败时仅记日志，返回辅助模型（不包装）"""
        mock_helper = make_mock_model("helper", "deepseek")

        mock_read_config.side_effect = [
            ("deepseek", "deepseek-v4-flash"),
            ("groq", "llama-3.3-70b-versatile"),
        ]
        # 辅助模型创建成功，降级模型创建失败
        mock_create_from_candidate.side_effect = [mock_helper, Exception("Groq 不可用")]

        result = get_helper_model()

        self.assertNotIsInstance(result, ResilientModel)
        self.assertIs(result, mock_helper)


# ============================================================================
# get_structured_model 测试
# ============================================================================


class GetStructuredModelTestCase(unittest.TestCase):
    """get_structured_model 测试"""

    @patch("Django_xm.apps.ai_engine.services.model_provider.get_default_model")
    def test_returns_resilient_model(
        self,
        mock_get_default,
    ):
        """get_structured_model() 返回 ResilientModel 实例"""
        mock_model1 = make_mock_model("model1", "openai")
        mock_model2 = make_mock_model("model2", "deepseek")
        # get_default_model 返回 ResilientModel
        base_resilient = ResilientModel(models=[mock_model1, mock_model2])
        mock_get_default.return_value = base_resilient

        # mock_model.with_structured_output 已经在 make_mock_model 中设置为返回 MagicMock
        result = get_structured_model(response_format=MagicMock())

        self.assertIsInstance(result, ResilientModel)
        # 内部模型是 with_structured_output 的返回值（MagicMock）
        self.assertEqual(len(result.models), 2)

    @patch("Django_xm.apps.ai_engine.services.model_provider.get_default_model")
    def test_non_resilient_base_returns_structured_directly(
        self,
        mock_get_default,
    ):
        """base_model 不是 ResilientModel 时直接应用 with_structured_output"""
        mock_model = make_mock_model("model", "openai")
        mock_get_default.return_value = mock_model

        result = get_structured_model(response_format=MagicMock())

        # 应该返回 mock_model.with_structured_output() 的结果
        mock_model.with_structured_output.assert_called_once()
        self.assertIs(result, mock_model.with_structured_output.return_value)

    @patch("Django_xm.apps.ai_engine.services.model_provider.get_default_model")
    def test_raises_runtime_error_when_all_structured_output_fail(
        self,
        mock_get_default,
    ):
        """所有模型应用 with_structured_output 均失败时抛出 RuntimeError"""
        mock_model1 = make_mock_model("model1", "openai")
        mock_model2 = make_mock_model("model2", "groq")  # 非 deepseek，走 with_structured_output
        # with_structured_output 都失败
        mock_model1.with_structured_output.side_effect = Exception("不支持")
        mock_model2.with_structured_output.side_effect = Exception("不支持")

        base_resilient = ResilientModel(models=[mock_model1, mock_model2])
        mock_get_default.return_value = base_resilient

        with self.assertRaises(RuntimeError) as ctx:
            get_structured_model(response_format=MagicMock())

        self.assertIn("结构化输出", str(ctx.exception))


# ============================================================================
# get_streaming_model 测试
# ============================================================================


class GetStreamingModelTestCase(unittest.TestCase):
    """get_streaming_model 测试"""

    @patch("Django_xm.apps.ai_engine.services.model_provider.get_default_model")
    def test_returns_resilient_model_with_streaming_true(
        self,
        mock_get_default,
    ):
        """get_streaming_model() 返回 ResilientModel 并传递 streaming=True"""
        mock_resilient = ResilientModel(
            models=[make_mock_model("model", "openai")]
        )
        mock_get_default.return_value = mock_resilient

        result = get_streaming_model(
            model_name="gpt-4o-mini",
            model_provider="openai",
            temperature=0.7,
        )

        self.assertIs(result, mock_resilient)
        # 验证 streaming=True 被传递
        _, kwargs = mock_get_default.call_args
        self.assertEqual(kwargs["streaming"], True)
        self.assertEqual(kwargs["model_name"], "gpt-4o-mini")
        self.assertEqual(kwargs["model_provider"], "openai")
        self.assertEqual(kwargs["temperature"], 0.7)


# ============================================================================
# _read_model_config 测试
# ============================================================================


class ReadModelConfigTestCase(unittest.TestCase):
    """_read_model_config 测试"""

    @patch("Django_xm.apps.ai_engine.models.SystemConfig.get_value")
    def test_returns_provider_and_model_when_configured(self, mock_get_value):
        """SystemConfig 配置了指定 key 时返回 (provider_id, model_name)"""
        mock_get_value.return_value = {
            "provider_id": "deepseek",
            "model_name": "deepseek-v4-flash",
        }

        result = _read_model_config("helper_model")

        self.assertEqual(result, ("deepseek", "deepseek-v4-flash"))

    @patch("Django_xm.apps.ai_engine.models.SystemConfig.get_value")
    def test_returns_empty_tuple_when_not_configured(self, mock_get_value):
        """SystemConfig 未配置时返回 ("", "")"""
        mock_get_value.return_value = {}

        result = _read_model_config("helper_model")

        self.assertEqual(result, ("", ""))

    @patch("Django_xm.apps.ai_engine.models.SystemConfig.get_value")
    def test_returns_empty_tuple_when_config_invalid(self, mock_get_value):
        """配置非 dict 时返回 ("", "")"""
        mock_get_value.return_value = "invalid"

        result = _read_model_config("helper_model")

        self.assertEqual(result, ("", ""))

    @patch("Django_xm.apps.ai_engine.models.SystemConfig.get_value")
    def test_returns_empty_tuple_when_exception_raised(self, mock_get_value):
        """SystemConfig.get_value 抛异常时返回 ("", "")"""
        mock_get_value.side_effect = Exception("DB error")

        result = _read_model_config("helper_model")

        self.assertEqual(result, ("", ""))


# ============================================================================
# _create_model_from_candidate 测试
# ============================================================================


class CreateModelFromCandidateTestCase(unittest.TestCase):
    """_create_model_from_candidate 测试"""

    @patch("Django_xm.apps.ai_engine.services.model_provider.get_chat_model_by_provider")
    def test_delegates_to_get_chat_model_by_provider(
        self,
        mock_get_by_provider,
    ):
        """_create_model_from_candidate 委托给 get_chat_model_by_provider"""
        mock_model = make_mock_model("model", "openai")
        mock_get_by_provider.return_value = mock_model

        result = _create_model_from_candidate(
            provider_id="openai",
            model_name="gpt-4o-mini",
            temperature=0.5,
            max_tokens=1024,
            streaming=True,
            use_cache=False,
        )

        self.assertIs(result, mock_model)
        mock_get_by_provider.assert_called_once_with(
            provider_id="openai",
            model_name="gpt-4o-mini",
            temperature=0.5,
            max_tokens=1024,
            streaming=True,
            use_cache=False,
        )


if __name__ == "__main__":
    unittest.main()
