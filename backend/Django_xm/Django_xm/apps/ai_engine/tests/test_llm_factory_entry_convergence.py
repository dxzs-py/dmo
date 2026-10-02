"""LLM 工厂双入口收敛（B⑩）表征测试。

收敛方案：provider_id 路径 get_chat_model_by_provider 是唯一核心创建入口；
get_chat_model 经 _create_single_chat_model（薄适配层：名称→provider_id→委托核心）
保持签名不变。本测试锁定：

1. 两入口对同一 provider 产出同一模型实例（缓存命中一致）——
   provider_id 直传与 provider 字符串反查两条适配路径均覆盖；
2. fallback 懒加载接线不变：enable_fallback=True 返回 LazyFallbackChatModel，
   primary 经核心路径创建、candidates 与 factory（get_chat_model_by_provider）正确传递；
3. 未知 provider 在适配层即抛 ValueError（列出可用 provider）；
4. special_params 透传后两入口缓存键一致（薄适配层语义不变）。

mock 边界：registry / SystemConfig / rate_limiter / init_chat_model 全部替换，
不触 DB、不发网络请求；模型实例用 common.testing.create_fake_chat_model。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python -m unittest Django_xm.apps.ai_engine.tests.test_llm_factory_entry_convergence
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from Django_xm.apps.ai_engine.services import llm_factory
from Django_xm.apps.ai_engine.services.llm_fallback import LazyFallbackChatModel
from Django_xm.apps.ai_engine.services.model_cache import _model_instance_cache
from Django_xm.common.testing import create_fake_chat_model

_FACTORY = "Django_xm.apps.ai_engine.services.llm_factory"

# 假注册表：openai 的 provider_id 与 provider 字符串同名（直传命中）；
# qianfan 的 provider_id 与 provider 字符串不同名（provider="openai"，反查命中），
# 用于锁定 _resolve_provider_id 与旧 _get_provider_config 一致的匹配语义。
_FAKE_REGISTRY = {
    "openai": {
        "provider": "openai",
        "label": "OpenAI",
        "models": [{"name": "gpt-fake", "capabilities": ["streaming"]}],
        "model_names": ["gpt-fake"],
        "default_model": "gpt-fake",
        "api_key_attr": "openai_api_key",
        "base_url_attr": None,
        "special_params": {},
    },
    "qianfan": {
        "provider": "openai",
        "label": "百度千帆",
        "models": [{"name": "ernie-fake", "capabilities": []}],
        "model_names": ["ernie-fake"],
        "default_model": "ernie-fake",
        "api_key_attr": "baidu_qianfan_api_key",
        "base_url_attr": None,
        "special_params": {},
    },
}


def _fake_registry_callables():
    registry = {pid: dict(cfg) for pid, cfg in _FAKE_REGISTRY.items()}
    get_registry = mock.MagicMock(return_value=registry)
    get_provider_config = mock.MagicMock(side_effect=lambda pid: registry.get(pid, {}))
    return registry, get_registry, get_provider_config


class LLMFactoryEntryConvergenceTests(unittest.TestCase):
    """两入口收敛表征：同 provider 产出同实例 + fallback 接线不变。"""

    def setUp(self):
        _model_instance_cache.clear()
        # SystemConfig / 速率限制器不参与（纯单元，无 DB / 全局状态）
        self._system_default = mock.patch.object(llm_factory, "get_system_default_chat_model", return_value=None)
        self._rate_limiter = mock.patch.object(llm_factory, "get_rate_limiter", return_value=None)
        self._system_default.start()
        self._rate_limiter.start()
        self.addCleanup(self._system_default.stop)
        self.addCleanup(self._rate_limiter.stop)

    def _patch_registry_and_init(self):
        """替换 registry 与 init_chat_model；init_chat_model 每次调用返回全新替身
        （side_effect 保证不同缓存键对应不同实例，同键命中缓存返回同实例）。"""
        registry, get_registry, get_provider_config = _fake_registry_callables()
        init_chat_model = mock.MagicMock(side_effect=lambda **kwargs: create_fake_chat_model(responses=["ok"])[0])
        patches = [
            mock.patch.object(llm_factory, "get_model_registry", get_registry),
            mock.patch.object(llm_factory, "get_provider_config", get_provider_config),
            mock.patch.object(llm_factory, "PROVIDER_REGISTRY", registry),
            mock.patch.object(llm_factory, "init_chat_model", init_chat_model),
            mock.patch.object(llm_factory.settings, "openai_api_key", "test-openai-key"),
            mock.patch.object(llm_factory.settings, "baidu_qianfan_api_key", "test-qianfan-key"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return init_chat_model

    def test_same_provider_two_entries_yield_same_instance(self):
        """provider_id 直传：get_chat_model(enable_fallback=False) 与 by_provider 同实例。"""
        self._patch_registry_and_init()

        by_provider = llm_factory.get_chat_model_by_provider(
            provider_id="openai", model_name="gpt-fake", temperature=0.5
        )
        via_get_chat_model = llm_factory.get_chat_model(
            model_name="gpt-fake", model_provider="openai", temperature=0.5, enable_fallback=False
        )

        self.assertIs(by_provider, via_get_chat_model)

    def test_provider_string_reverse_lookup_yields_same_instance(self):
        """provider 字符串反查：model_provider="openai"（qianfan 的 provider 字符串场景）经
        _resolve_provider_id 命中 registry 键，与核心路径产出同实例。"""
        self._patch_registry_and_init()

        # provider_id 与 provider 字符串同名：直传与反查命中同一 registry 项
        by_provider = llm_factory.get_chat_model_by_provider(
            provider_id="openai", model_name="gpt-fake", temperature=0.5
        )
        via_get_chat_model = llm_factory.get_chat_model(
            model_name="gpt-fake", model_provider="openai", temperature=0.5, enable_fallback=False
        )
        self.assertIs(by_provider, via_get_chat_model)

        # provider 字符串与 provider_id 不同名（qianfan：provider="openai"）：
        # 适配层按 registry 单次遍历语义解析为 provider_id
        qianfan_by_provider = llm_factory.get_chat_model_by_provider(
            provider_id="qianfan", model_name="ernie-fake", temperature=0.5
        )
        qianfan_via_get_chat_model = llm_factory.get_chat_model(
            model_name="ernie-fake", model_provider="qianfan", temperature=0.5, enable_fallback=False
        )
        self.assertIs(qianfan_by_provider, qianfan_via_get_chat_model)
        # 不同 api_key（openai vs qianfan）→ 缓存键不同 → 实例不同
        self.assertIsNot(by_provider, qianfan_by_provider)

    def test_special_params_keep_cache_key_consistent_across_entries(self):
        """special_params 透传：两入口（同 provider 同参数）缓存键一致 → 同实例。"""
        self._patch_registry_and_init()
        special = {"top_k": 40}

        by_provider = llm_factory.get_chat_model_by_provider(
            provider_id="openai", model_name="gpt-fake", temperature=0.5, special_params=dict(special)
        )
        via_get_chat_model = llm_factory.get_chat_model(
            model_name="gpt-fake",
            model_provider="openai",
            temperature=0.5,
            enable_fallback=False,
            special_params=dict(special),
        )

        self.assertIs(by_provider, via_get_chat_model)

    def test_fallback_wiring_unchanged(self):
        """enable_fallback=True：返回 LazyFallbackChatModel，primary/candidates/factory 接线正确。"""
        self._patch_registry_and_init()
        candidates = [("qianfan", "ernie-fake")]
        with mock.patch.object(llm_factory, "get_fallback_candidates", return_value=list(candidates)):
            model = llm_factory.get_chat_model(
                model_name="gpt-fake", model_provider="openai", temperature=0.5, enable_fallback=True
            )

        self.assertIsInstance(model, LazyFallbackChatModel)
        self.assertEqual(model.fallback_candidates, candidates)
        self.assertIs(model.factory, llm_factory.get_chat_model_by_provider)
        # primary 为核心路径创建的实例（缓存中同键实例）
        self.assertIs(model.primary, llm_factory.get_chat_model_by_provider(
            provider_id="openai", model_name="gpt-fake", temperature=0.5
        ))

    def test_unknown_provider_raises_value_error(self):
        """未知 provider：适配层解析失败即抛 ValueError（列出可用 provider）。"""
        self._patch_registry_and_init()

        with self.assertRaises(ValueError) as ctx:
            llm_factory.get_chat_model(
                model_name="gpt-fake", model_provider="no-such-provider", enable_fallback=False
            )
        self.assertIn("no-such-provider", str(ctx.exception))
        self.assertIn("openai", str(ctx.exception))

    def test_unknown_provider_id_by_provider_raises_value_error(self):
        """核心路径自身：未注册 provider_id 抛 ValueError（原有语义保持）。"""
        self._patch_registry_and_init()

        with self.assertRaises(ValueError):
            llm_factory.get_chat_model_by_provider(provider_id="no-such-provider", model_name="gpt-fake")


if __name__ == "__main__":
    unittest.main()
