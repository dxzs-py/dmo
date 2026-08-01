"""LLM 工厂单元测试（覆盖 ``services/llm_factory.py`` 公开 API 层）

验证目标：
1. ``get_chat_model`` 配置加载与 fallback 包装逻辑
   - ``enable_fallback=False`` 委托给 ``_create_single_chat_model``
   - 主模型成功 + 候选非空 → ``LazyFallbackChatModel`` 包装
   - 主模型失败 + 候选可用 → 提升第一个候选为主模型
   - 主模型 + 所有候选均失败 → 抛 ``RuntimeError``
   - 主模型成功 + 无候选 → 返回主模型（不包装）
2. ``get_chat_model_by_provider`` 路径
   - 未知 provider_id → ``ValueError``
   - API Key 未配置 → ``ValueError``
3. ``get_streaming_model`` 透传 ``streaming=True``
4. ``get_model_by_preset`` 未知 preset → ``ValueError``
5. ``get_model_string`` 优先级解析（显式 > SystemConfig > settings）
6. ``get_model_config`` 预设读取
7. ``model_supports_capability`` 能力查询（含旧格式兼容）
8. ``_apply_special_params`` 解析逻辑（reasoning_effort 在 thinking 未启用时被移除）
9. ``_ensure_groq_bind_tools_field`` 幂等性
10. ``get_helper_model`` 全局缓存
11. ``JsonModeStructuredModel`` JSON 解析（正常 / markdown 剥离 / 空内容 / 无效 JSON / 非 dict / schema 校验失败）

mock 策略：
- ``init_chat_model``：避免创建真实 LLM 客户端
- ``cached_model_creation``：避免污染模块级 LRU 缓存
- ``get_rate_limiter``：返回 None，避免单例污染
- ``get_system_default_chat_model``：返回 None，避免 DB 访问
- ``get_fallback_candidates``：返回受控候选列表
- ``LazyFallbackChatModel``：验证包装调用参数
- ``_get_provider_config`` / ``get_provider_config`` / ``get_model_registry``：返回受控配置
- ``PROVIDER_REGISTRY``：替换为受控映射
- 不依赖真实 Redis / Celery / 网络

运行方式：
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    conda activate langchain_xm
    python -m pytest Django_xm/apps/ai_engine/tests/test_llm_factory.py -v
"""

from __future__ import annotations

import os
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

# Django 环境初始化（兼容 pytest 和 unittest 直接运行）
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django
import django.apps

if not django.apps.apps.ready:
    django.setup()


from Django_xm.apps.ai_engine.services import llm_factory
from Django_xm.apps.ai_engine.services.llm_factory import (
    JsonModeStructuredModel,
    _apply_special_params,
    _ensure_groq_bind_tools_field,
    _get_provider_config,
    get_chat_model,
    get_chat_model_by_provider,
    get_helper_model,
    get_model_by_preset,
    get_model_config,
    get_model_string,
    get_streaming_model,
    model_supports_capability,
)

# check_model_connection（原 test_model_connection，重命名以避免 pytest 误识别）
from Django_xm.apps.ai_engine.services.llm_fallback import (
    LazyFallbackChatModel,
)

try:
    from pydantic import BaseModel
except ImportError:  # pragma: no cover - 仅在极旧环境触发
    BaseModel = None  # type: ignore[misc, assignment]


# ============================================================================
# Mock 工厂
# ============================================================================


def make_mock_model(name: str = "mock-model", provider_id: str = "mock") -> MagicMock:
    """创建模拟的 BaseChatModel 实例。"""
    model = MagicMock(name=name)
    model._llm_type = name
    model._provider_id = provider_id
    return model


# ============================================================================
# get_chat_model（fallback 包装逻辑）
# ============================================================================


class GetChatModelFallbackTestCase(unittest.TestCase):
    """``get_chat_model`` 的 fallback 包装与异常路径测试。"""

    def setUp(self) -> None:
        """统一 patch 外部依赖，每个测试只关注自身关注的调用。"""
        # 缓存与速率限制器：返回 None 避免污染全局单例
        self._patch_cache = patch.object(
            llm_factory, "cached_model_creation", side_effect=lambda key, use_cache, fn, **_: fn()
        )
        self._patch_rate = patch.object(llm_factory, "get_rate_limiter", return_value=None)
        self._patch_default = patch.object(llm_factory, "get_system_default_chat_model", return_value=None)
        self._mock_init = self._patch_cache.start()
        self._patch_rate.start()
        self._patch_default.start()
        # 关闭 _helper_model_cache 防止跨测试污染
        self._orig_helper_cache = llm_factory._helper_model_cache
        llm_factory._helper_model_cache = None
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        patch.stopall()
        llm_factory._helper_model_cache = self._orig_helper_cache

    @patch.object(llm_factory, "init_chat_model")
    def test_no_fallback_delegates_to_single_create(self, mock_init: MagicMock) -> None:
        """enable_fallback=False 时直接走 _create_single_chat_model，不进入 fallback 分支。"""
        mock_model = make_mock_model("primary", "openai")
        mock_init.return_value = mock_model

        result = get_chat_model(enable_fallback=False, use_cache=False)

        self.assertIs(result, mock_model)
        # init_chat_model 被调用一次，未涉及 fallback
        mock_init.assert_called_once()
        # 返回的不是 LazyFallbackChatModel 包装
        self.assertNotIsInstance(result, LazyFallbackChatModel)

    @patch.object(llm_factory, "_create_single_chat_model")
    @patch.object(llm_factory, "get_fallback_candidates")
    @patch.object(llm_factory, "LazyFallbackChatModel")
    def test_with_fallback_wraps_primary_when_candidates_exist(
        self,
        mock_lazy_cls: MagicMock,
        mock_candidates: MagicMock,
        mock_create: MagicMock,
    ) -> None:
        """主模型创建成功 + 候选非空 → 用 LazyFallbackChatModel 包装。"""
        mock_primary = make_mock_model("primary", "openai")
        mock_create.return_value = mock_primary
        mock_candidates.return_value = [("groq", "llama-3.3-70b"), ("deepseek", "deepseek-v4-flash")]
        mock_lazy_instance = MagicMock(name="lazy")
        mock_lazy_cls.return_value = mock_lazy_instance

        result = get_chat_model(model_provider="openai", model_name="gpt-4o-mini")

        self.assertIs(result, mock_lazy_instance)
        mock_lazy_cls.assert_called_once()
        # 验证 primary 被注入
        _, kwargs = mock_lazy_cls.call_args
        self.assertIs(kwargs["primary"], mock_primary)
        self.assertEqual(len(kwargs["fallback_candidates"]), 2)
        self.assertEqual(kwargs["fallback_candidates"][0], ("groq", "llama-3.3-70b"))

    @patch.object(llm_factory, "_create_single_chat_model")
    @patch.object(llm_factory, "get_chat_model_by_provider")
    @patch.object(llm_factory, "get_fallback_candidates")
    @patch.object(llm_factory, "LazyFallbackChatModel")
    def test_fallback_promotes_first_candidate_when_primary_fails(
        self,
        mock_lazy_cls: MagicMock,
        mock_candidates: MagicMock,
        mock_get_by_provider: MagicMock,
        mock_create: MagicMock,
    ) -> None:
        """主模型创建失败 + 候选可用 → 提升第一个候选为主模型，并从候选列表中移除。"""
        # 主模型抛异常
        mock_create.side_effect = Exception("OpenAI 不可用")
        # 第一个候选创建成功
        promoted = make_mock_model("promoted", "groq")
        mock_get_by_provider.return_value = promoted
        # 两个候选，第一个会被提升
        mock_candidates.return_value = [
            ("groq", "llama-3.3-70b"),
            ("deepseek", "deepseek-v4-flash"),
        ]
        mock_lazy_instance = MagicMock(name="lazy")
        mock_lazy_cls.return_value = mock_lazy_instance

        result = get_chat_model(model_provider="openai", model_name="gpt-4o-mini")

        self.assertIs(result, mock_lazy_instance)
        # 验证候选列表中第一个已被移除（只剩第二个）
        _, kwargs = mock_lazy_cls.call_args
        self.assertEqual(kwargs["fallback_candidates"], [("deepseek", "deepseek-v4-flash")])
        # 验证提升的候选被作为 primary
        self.assertIs(kwargs["primary"], promoted)

    @patch.object(llm_factory, "_create_single_chat_model")
    @patch.object(llm_factory, "get_chat_model_by_provider")
    @patch.object(llm_factory, "get_fallback_candidates")
    def test_fallback_raises_runtime_error_when_all_unavailable(
        self,
        mock_candidates: MagicMock,
        mock_get_by_provider: MagicMock,
        mock_create: MagicMock,
    ) -> None:
        """主模型与所有候选均失败 → 抛 RuntimeError。"""
        mock_create.side_effect = Exception("OpenAI 不可用")
        mock_get_by_provider.side_effect = Exception("Groq 不可用")
        mock_candidates.return_value = [("groq", "llama-3.3-70b")]

        with self.assertRaises(RuntimeError) as ctx:
            get_chat_model(model_provider="openai", model_name="gpt-4o-mini")

        self.assertIn("所有已配置的模型均不可用", str(ctx.exception))
        self.assertIn("OpenAI 不可用", str(ctx.exception))
        self.assertIn("Groq 不可用", str(ctx.exception))

    @patch.object(llm_factory, "_create_single_chat_model")
    @patch.object(llm_factory, "get_fallback_candidates")
    @patch.object(llm_factory, "LazyFallbackChatModel")
    def test_fallback_returns_primary_when_no_candidates(
        self,
        mock_lazy_cls: MagicMock,
        mock_candidates: MagicMock,
        mock_create: MagicMock,
    ) -> None:
        """主模型创建成功 + 候选为空 → 直接返回主模型，不调用 LazyFallbackChatModel。"""
        mock_primary = make_mock_model("primary", "openai")
        mock_create.return_value = mock_primary
        mock_candidates.return_value = []  # 无候选

        result = get_chat_model(model_provider="openai", model_name="gpt-4o-mini")

        self.assertIs(result, mock_primary)
        mock_lazy_cls.assert_not_called()


# ============================================================================
# get_chat_model_by_provider
# ============================================================================


class GetChatModelByProviderTestCase(unittest.TestCase):
    """``get_chat_model_by_provider`` 路径校验与配置解析。"""

    def setUp(self) -> None:
        self._patch_cache = patch.object(
            llm_factory, "cached_model_creation", side_effect=lambda key, use_cache, fn, **_: fn()
        )
        self._patch_rate = patch.object(llm_factory, "get_rate_limiter", return_value=None)
        self._patch_cache.start()
        self._patch_rate.start()
        self.addCleanup(patch.stopall)

    def test_raises_value_error_for_unknown_provider(self) -> None:
        """未知 provider_id（既不在 PROVIDER_REGISTRY 也不在 DB 注册表）→ ValueError。"""
        with (
            patch.object(llm_factory, "PROVIDER_REGISTRY", {}),
            patch.object(llm_factory, "get_provider_config", return_value={}),
            patch.object(llm_factory, "get_model_registry", return_value={}),
            self.assertRaises(ValueError) as ctx,
        ):
            get_chat_model_by_provider(provider_id="unknown_provider")

        self.assertIn("unknown_provider", str(ctx.exception))

    @patch.object(llm_factory, "PROVIDER_REGISTRY", {})
    @patch.object(llm_factory, "get_provider_config")
    @patch.object(llm_factory, "get_model_registry")
    def test_raises_value_error_when_api_key_missing(
        self,
        mock_registry: MagicMock,
        mock_provider_cfg: MagicMock,
    ) -> None:
        """provider 注册存在但 API Key 未配置 → ValueError。"""
        mock_registry.return_value = {}
        mock_provider_cfg.return_value = {
            "label": "OpenAI",
            "provider": "openai",
            "default_model": "gpt-4o-mini",
            "api_key_attr": "openai_api_key",
            "base_url_attr": None,
            "special_params": {},
        }
        # settings.openai_api_key 返回空
        mock_settings = MagicMock()
        mock_settings.openai_api_key = ""
        with patch.object(llm_factory, "settings", mock_settings), self.assertRaises(ValueError) as ctx:
            get_chat_model_by_provider(provider_id="openai")

        self.assertIn("API Key 未配置", str(ctx.exception))


# ============================================================================
# 便捷封装
# ============================================================================


class ConvenienceWrappersTestCase(unittest.TestCase):
    """``get_streaming_model`` / ``get_model_by_preset`` / ``get_model_string`` 等便捷封装。"""

    @patch.object(llm_factory, "get_chat_model")
    def test_get_streaming_model_passes_streaming_true(self, mock_get: MagicMock) -> None:
        """``get_streaming_model`` 透传 streaming=True 与其他参数。"""
        mock_model = make_mock_model("streaming")
        mock_get.return_value = mock_model

        result = get_streaming_model(
            model_name="gpt-4o-mini",
            model_provider="openai",
            temperature=0.5,
        )

        self.assertIs(result, mock_model)
        _, kwargs = mock_get.call_args
        self.assertIs(kwargs["streaming"], True)
        self.assertEqual(kwargs["model_name"], "gpt-4o-mini")
        self.assertEqual(kwargs["model_provider"], "openai")
        self.assertEqual(kwargs["temperature"], 0.5)

    @patch.object(llm_factory, "get_model_presets")
    def test_get_model_by_preset_raises_value_error_for_unknown_preset(
        self,
        mock_presets: MagicMock,
    ) -> None:
        """未知 preset 名称 → ValueError 并提示可用 preset。"""
        mock_presets.return_value = {"default": {"model_provider": "openai"}}

        with self.assertRaises(ValueError) as ctx:
            get_model_by_preset(preset="nonexistent")

        self.assertIn("nonexistent", str(ctx.exception))
        self.assertIn("default", str(ctx.exception))

    @patch.object(llm_factory, "get_chat_model")
    @patch.object(llm_factory, "get_model_presets")
    def test_get_model_by_preset_delegates_to_get_chat_model(
        self,
        mock_presets: MagicMock,
        mock_get: MagicMock,
    ) -> None:
        """合法 preset → 解析配置后委托给 get_chat_model。"""
        mock_presets.return_value = {
            "default": {
                "model_provider": "openai",
                "description": "默认预设",
                "model_name": "gpt-4o-mini",
                "temperature": 0.3,
            }
        }
        mock_model = make_mock_model("preset")
        mock_get.return_value = mock_model

        result = get_model_by_preset(preset="default")

        self.assertIs(result, mock_model)
        # description 应被 pop 掉
        _, kwargs = mock_get.call_args
        self.assertNotIn("description", kwargs)
        self.assertEqual(kwargs["model_provider"], "openai")
        self.assertEqual(kwargs["model_name"], "gpt-4o-mini")

    @patch.object(llm_factory, "get_system_default_chat_model", return_value=None)
    def test_get_model_string_resolves_priority_explicit_over_settings(
        self,
        _mock_default: MagicMock,
    ) -> None:
        """显式传入的 provider / model_name 优先于 SystemConfig 与 settings。"""
        # SystemConfig 返回 None，验证显式参数优先
        result = get_model_string(model_name="claude-3-5-sonnet", provider="anthropic")

        self.assertEqual(result, "anthropic:claude-3-5-sonnet")

    @patch.object(llm_factory, "get_system_default_chat_model")
    def test_get_model_string_uses_system_default_when_explicit_missing(
        self,
        mock_default: MagicMock,
    ) -> None:
        """显式参数缺失时回退到 SystemConfig 配置。"""
        mock_default.return_value = {"provider_id": "deepseek", "model_name": "deepseek-v4-flash"}

        result = get_model_string()

        self.assertEqual(result, "deepseek:deepseek-v4-flash")

    @patch.object(llm_factory, "get_model_presets")
    def test_get_model_config_returns_preset_dict(self, mock_presets: MagicMock) -> None:
        """``get_model_config`` 返回 preset 对应的配置字典。"""
        mock_presets.return_value = {
            "rag": {"model_provider": "openai", "model_name": "gpt-4o-mini"},
            "research": {"model_provider": "deepseek"},
        }

        result = get_model_config("rag")

        self.assertEqual(result["model_provider"], "openai")
        self.assertEqual(result["model_name"], "gpt-4o-mini")
        # 不存在的 preset 返回空 dict
        self.assertEqual(get_model_config("nonexistent"), {})


# ============================================================================
# model_supports_capability
# ============================================================================


class ModelSupportsCapabilityTestCase(unittest.TestCase):
    """``model_supports_capability`` 能力查询。"""

    @patch.object(llm_factory, "get_provider_config")
    def test_returns_true_when_capability_listed(self, mock_cfg: MagicMock) -> None:
        """模型配置中显式声明了对应 capability → 返回 True。"""
        mock_cfg.return_value = {
            "models": [
                {"name": "deepseek-v4-flash", "capabilities": ["deep_thinking", "tool_calling"]},
            ],
        }

        self.assertIs(
            model_supports_capability("deepseek", "deepseek-v4-flash", "deep_thinking"),
            True,
        )
        self.assertIs(
            model_supports_capability("deepseek", "deepseek-v4-flash", "vision"),
            False,
        )

    @patch.object(llm_factory, "get_provider_config")
    def test_returns_false_for_old_string_format(self, mock_cfg: MagicMock) -> None:
        """旧格式（model_cfg 为 str）→ 始终返回 False（兼容旧注册表）。"""
        mock_cfg.return_value = {"models": ["gpt-4o-mini"]}

        self.assertIs(
            model_supports_capability("openai", "gpt-4o-mini", "tool_calling"),
            False,
        )

    @patch.object(llm_factory, "get_provider_config")
    def test_returns_false_when_model_not_found(self, mock_cfg: MagicMock) -> None:
        """模型名在 provider 配置中不存在 → 返回 False。"""
        mock_cfg.return_value = {"models": [{"name": "gpt-4o", "capabilities": ["vision"]}]}

        self.assertIs(
            model_supports_capability("openai", "gpt-3.5-turbo", "vision"),
            False,
        )


# ============================================================================
# _apply_special_params
# ============================================================================


class ApplySpecialParamsTestCase(unittest.TestCase):
    """``_apply_special_params`` 解析 special_params 并应用至 init_kwargs。"""

    def test_drops_reasoning_effort_when_thinking_not_enabled(self) -> None:
        """reasoning_effort 已设置但 thinking 未启用 → 移除 reasoning_effort 注入。

        覆盖源码 113-120 行的兼容逻辑：DeepSeek 在 thinking 未启用时
        reasoning_effort 无意义，需要从 init_kwargs / extra_body / model_kwargs
        三处移除。
        """
        # 构造一个含 reasoning_effort 和 thinking 配置的 registry
        registry = {
            "label": "DeepSeek",
            "provider": "deepseek",
            "special_params": {
                "reasoning_effort": {
                    "model_kwarg": "reasoning_effort",
                    "pass_mode": "extra_body",
                },
                "thinking": {
                    "model_kwarg": "thinking",
                    "pass_mode": "top_level",
                },
            },
        }
        init_kwargs: dict[str, Any] = {}
        special_params = {"reasoning_effort": "high"}  # 仅 reasoning_effort，无 thinking

        with (
            patch.object(llm_factory, "PROVIDER_REGISTRY", {"deepseek": registry}),
            patch.object(llm_factory, "model_supports_capability", return_value=False),
        ):
            _apply_special_params(
                init_kwargs,
                special_params,
                provider_id="deepseek",
                provider="deepseek",
                model_name="deepseek-v4-flash",
            )

        # reasoning_effort 应被移除（thinking 未启用）
        self.assertNotIn("reasoning_effort", init_kwargs)
        # extra_body 不应包含 reasoning_effort
        self.assertNotIn("extra_body", init_kwargs)  # extra_body 为空时不应注入

    def test_pass_mode_top_level_injects_into_init_kwargs(self) -> None:
        """pass_mode=top_level → 直接注入 init_kwargs。"""
        registry = {
            "label": "OpenAI",
            "provider": "openai",
            "special_params": {
                "verbosity": {
                    "model_kwarg": "verbosity",
                    "pass_mode": "top_level",
                },
            },
        }
        init_kwargs: dict[str, Any] = {}

        with (
            patch.object(llm_factory, "PROVIDER_REGISTRY", {"openai": registry}),
            patch.object(llm_factory, "model_supports_capability", return_value=False),
        ):
            _apply_special_params(
                init_kwargs,
                {"verbosity": "high"},
                provider_id="openai",
                provider="openai",
                model_name="gpt-4o-mini",
            )

        self.assertEqual(init_kwargs["verbosity"], "high")


# ============================================================================
# _get_provider_config（硬编码 fallback）
# ============================================================================


class GetProviderConfigTestCase(unittest.TestCase):
    """``_get_provider_config`` 硬编码兜底逻辑。"""

    @patch.object(llm_factory, "get_model_registry", return_value={})
    def test_returns_hardcoded_openai_config_when_db_empty(self, _mock: MagicMock) -> None:
        """数据库无数据时回退到 settings.openai_api_key / settings.openai_api_base。"""
        mock_settings = MagicMock()
        mock_settings.openai_api_key = "sk-test-key"
        mock_settings.openai_api_base = "https://api.openai.com/v1"

        with patch.object(llm_factory, "settings", mock_settings):
            result = _get_provider_config("openai")

        self.assertEqual(result["api_key"], "sk-test-key")
        self.assertEqual(result["base_url"], "https://api.openai.com/v1")

    @patch.object(llm_factory, "get_model_registry", return_value={})
    def test_returns_empty_dict_for_unknown_provider(self, _mock: MagicMock) -> None:
        """未知 provider 且不在硬编码兜底中 → 返回空 dict。"""
        mock_settings = MagicMock()

        with patch.object(llm_factory, "settings", mock_settings):
            result = _get_provider_config("totally_unknown_provider")

        self.assertEqual(result, {})


# ============================================================================
# _ensure_groq_bind_tools_field
# ============================================================================


class EnsureGroqBindToolsFieldTestCase(unittest.TestCase):
    """``_ensure_groq_bind_tools_field`` 幂等性测试。"""

    def setUp(self) -> None:
        # 重置模块级 _groq_field_patched 标志，确保每次测试都重新执行补丁逻辑
        self._orig = llm_factory._groq_field_patched
        llm_factory._groq_field_patched = False
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        llm_factory._groq_field_patched = self._orig

    def test_returns_early_when_import_fails(self) -> None:
        """langchain-groq 未安装时 → 直接置位 _groq_field_patched=True 并返回。"""
        # 模拟 import 失败：patch import 触发 ImportError
        with patch("builtins.__import__", side_effect=ImportError("no langchain_groq")):
            _ensure_groq_bind_tools_field()

        # 标志位被置位，后续调用应直接返回
        self.assertIs(llm_factory._groq_field_patched, True)

    def test_idempotent_when_already_patched(self) -> None:
        """已补丁过 → 再次调用直接返回，不重复执行补丁逻辑。"""
        llm_factory._groq_field_patched = True

        # 即便 langchain_groq 不可导入也不应触发
        with patch("builtins.__import__", side_effect=AssertionError("不应被调用")):
            _ensure_groq_bind_tools_field()


# ============================================================================
# get_helper_model（缓存）
# ============================================================================


class GetHelperModelTestCase(unittest.TestCase):
    """``get_helper_model`` 全局缓存行为。"""

    def setUp(self) -> None:
        self._orig = llm_factory._helper_model_cache
        llm_factory._helper_model_cache = None
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        llm_factory._helper_model_cache = self._orig

    def test_caches_helper_model_instance(self) -> None:
        """第二次调用 get_helper_model → 复用缓存的实例，不重新创建。"""
        mock_model = make_mock_model("helper", "openai")

        # SystemConfig.get_value 返回有效 helper 配置 → 不回退到 django_settings
        # get_chat_model_by_provider 返回 mock 模型
        # get_fallback_candidates 返回空 → 不进入 LazyFallbackChatModel 包装
        with (
            patch(
                "Django_xm.apps.ai_engine.models.SystemConfig.get_value",
                return_value={"provider_id": "openai", "model_name": "gpt-4o-mini"},
            ),
            patch.object(llm_factory, "get_chat_model_by_provider", return_value=mock_model) as mock_get_by_provider,
            patch.object(llm_factory, "get_fallback_candidates", return_value=[]),
        ):
            first = get_helper_model()
            # 第二次应命中缓存，不再调用 get_chat_model_by_provider
            mock_get_by_provider.reset_mock()
            second = get_helper_model()

        self.assertIs(first, mock_model)
        self.assertIs(first, second)
        # 第二次调用应直接返回缓存，不再创建
        mock_get_by_provider.assert_not_called()

    def test_returns_none_when_no_provider_available(self) -> None:
        """无可用辅助模型（SystemConfig 无配置 + HELPER_MODEL_PRIORITY 全部不可用）→ 返回 None。"""
        # SystemConfig 返回空 → 进入 HELPER_MODEL_PRIORITY 循环
        # registry_is_provider_available 全部返回 False → 跳过所有候选
        with (
            patch(
                "Django_xm.apps.ai_engine.models.SystemConfig.get_value",
                return_value={},
            ),
            patch.object(llm_factory, "registry_is_provider_available", return_value=False),
        ):
            result = get_helper_model()

        self.assertIsNone(result)


# ============================================================================
# JsonModeStructuredModel
# ============================================================================


@unittest.skipIf(BaseModel is None, "pydantic 未安装，跳过 JsonModeStructuredModel 测试")
class JsonModeStructuredModelTestCase(unittest.TestCase):
    """``JsonModeStructuredModel._parse_result`` / ``invoke`` JSON 解析逻辑。"""

    class _Schema(BaseModel):
        name: str
        age: int

    def _make_model(self, response_content: str = "") -> tuple[MagicMock, JsonModeStructuredModel]:
        """构造一个 mock model 与对应的 JsonModeStructuredModel 实例。"""
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.content = response_content
        mock_model.invoke.return_value = mock_response
        instance = JsonModeStructuredModel(
            model=mock_model,
            schema=self._Schema,
            provider="deepseek",
            model_name="deepseek-v4-flash",
        )
        return mock_model, instance

    def test_parses_valid_json_object(self) -> None:
        """有效 JSON 字符串 → 返回 Pydantic 实例。"""
        _, instance = self._make_model()
        result = instance._parse_result('{"name": "Alice", "age": 30}')

        self.assertIsInstance(result, self._Schema)
        assert result is not None  # type narrowing for mypy
        self.assertEqual(result.name, "Alice")
        self.assertEqual(result.age, 30)

    def test_parses_markdown_wrapped_json(self) -> None:
        """```json ... ``` 代码块包裹 → 正确剥离后解析。"""
        _, instance = self._make_model()
        result = instance._parse_result('```json\n{"name": "Bob", "age": 25}\n```')

        self.assertIsInstance(result, self._Schema)
        assert result is not None  # type narrowing for mypy
        self.assertEqual(result.name, "Bob")
        self.assertEqual(result.age, 25)

    def test_parses_plain_code_block_without_lang(self) -> None:
        """``` ... ``` 不带语言标识 → 同样剥离。"""
        _, instance = self._make_model()
        result = instance._parse_result('```\n{"name": "Carol", "age": 40}\n```')

        self.assertIsInstance(result, self._Schema)
        assert result is not None  # type narrowing for mypy
        self.assertEqual(result.name, "Carol")

    def test_returns_none_for_empty_content(self) -> None:
        """空字符串 / 仅空白 → None。"""
        _, instance = self._make_model()
        self.assertIsNone(instance._parse_result(""))
        self.assertIsNone(instance._parse_result("   \n  \t  "))

    def test_returns_none_for_invalid_json(self) -> None:
        """非 JSON 文本 → None。"""
        _, instance = self._make_model()
        self.assertIsNone(instance._parse_result("not a json string"))

    def test_returns_none_for_json_array(self) -> None:
        """JSON 数组（非 dict）→ None。"""
        _, instance = self._make_model()
        self.assertIsNone(instance._parse_result("[1, 2, 3]"))

    def test_returns_none_when_schema_validation_fails(self) -> None:
        """JSON dict 但缺少必填字段 → schema 校验失败 → None。"""
        _, instance = self._make_model()
        # 缺少 age 字段
        self.assertIsNone(instance._parse_result('{"name": "Dave"}'))

    def test_invoke_calls_model_and_returns_parsed_result(self) -> None:
        """invoke 完整路径：构造 messages → 调用模型 → 解析 JSON。"""
        mock_model, instance = self._make_model('{"name": "Eve", "age": 28}')
        # invoke 入参可以是 list 或单条消息
        from langchain_core.messages import HumanMessage

        result = instance.invoke([HumanMessage(content="请生成 JSON")])

        self.assertIsInstance(result, self._Schema)
        assert result is not None  # type narrowing for mypy
        self.assertEqual(result.name, "Eve")
        self.assertEqual(result.age, 28)
        # 验证模型被调用，且首位注入了 SystemMessage（JSON Schema 提示词）
        mock_model.invoke.assert_called_once()
        args, _ = mock_model.invoke.call_args
        messages = args[0]
        self.assertGreaterEqual(len(messages), 2)
        # 首位应为 SystemMessage，包含 "JSON Schema"
        from langchain_core.messages import SystemMessage

        self.assertIsInstance(messages[0], SystemMessage)
        self.assertIn("JSON Schema", messages[0].content)


# ============================================================================
# check_model_connection
# ============================================================================


class TestModelConnectionTestCase(unittest.TestCase):
    """``check_model_connection`` 返回 success/failure 字典。"""

    @patch.object(llm_factory, "get_chat_model_by_provider")
    def test_returns_success_dict_when_invoke_succeeds(self, mock_get: MagicMock) -> None:
        """模型创建 + invoke 成功 → 返回 success=True 的字典。"""
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "Hello back"
        mock_model.invoke.return_value = mock_response
        mock_get.return_value = mock_model

        # PROVIDER_REGISTRY / get_provider_config 提供默认 model 名
        with (
            patch.object(llm_factory, "PROVIDER_REGISTRY", {}),
            patch.object(
                llm_factory,
                "get_provider_config",
                return_value={"default_model": "gpt-4o-mini"},
            ),
        ):
            result = llm_factory.check_model_connection("openai", model_name="gpt-4o-mini")

        self.assertTrue(result["success"])
        self.assertEqual(result["message"], "模型连接成功")
        self.assertEqual(result["model_info"]["provider_id"], "openai")
        self.assertEqual(result["model_info"]["model_name"], "gpt-4o-mini")
        self.assertEqual(result["model_info"]["response_preview"], "Hello back")

    @patch.object(llm_factory, "get_chat_model_by_provider")
    def test_returns_failure_dict_when_invoke_raises(self, mock_get: MagicMock) -> None:
        """模型创建或 invoke 失败 → 返回 success=False 的字典，包含异常信息。"""
        mock_get.side_effect = Exception("API key invalid")

        with (
            patch.object(llm_factory, "PROVIDER_REGISTRY", {}),
            patch.object(
                llm_factory,
                "get_provider_config",
                return_value={"default_model": "gpt-4o-mini"},
            ),
        ):
            result = llm_factory.check_model_connection("openai")

        self.assertFalse(result["success"])
        self.assertIn("API key invalid", result["message"])
        self.assertEqual(result["model_info"]["model_name"], "gpt-4o-mini")


if __name__ == "__main__":
    unittest.main()
