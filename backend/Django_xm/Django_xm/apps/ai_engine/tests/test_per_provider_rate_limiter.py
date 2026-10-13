"""按 provider 独立限速器（llm_cache.get_rate_limiter）行为。

多 Agent 并行（主 agent + N 子代理）共享 LLM 出口：限速按 provider 独立计数，
本地 provider（ollama）不限速，外部 API 各自一桶——避免全局单桶让并行子代理排队。
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")

import django

django.setup()

from django.test import SimpleTestCase

from Django_xm.apps.ai_engine.services import llm_cache
from Django_xm.apps.ai_engine.services.llm_cache import get_rate_limiter


class PerProviderRateLimiterTests(SimpleTestCase):
    def setUp(self):
        llm_cache._rate_limiters.clear()
        self._orig = (
            llm_cache.settings.rate_limit_unlimited_providers,
            llm_cache.settings.rate_limit_rps_overrides,
            llm_cache.settings.rate_limit_rps,
        )

    def tearDown(self):
        llm_cache.settings.rate_limit_unlimited_providers, llm_cache.settings.rate_limit_rps_overrides, (
            llm_cache.settings.rate_limit_rps
        ) = self._orig
        llm_cache._rate_limiters.clear()
        super().tearDown()

    def test_local_provider_unlimited(self):
        """ollama 等本地 provider 无 API 限额 → 不限速（None）。"""
        self.assertIsNone(get_rate_limiter("ollama"))

    def test_external_provider_gets_default_bucket(self):
        """外部 API provider → 默认档限速实例。"""
        limiter = get_rate_limiter("groq")
        self.assertIsNotNone(limiter)
        self.assertEqual(limiter.requests_per_second, llm_cache.settings.rate_limit_rps)

    def test_providers_have_independent_limiters(self):
        """不同 provider 各自独立限速实例（多 Agent 并行互不排队的前提）。"""
        a = get_rate_limiter("groq")
        b = get_rate_limiter("deepseek")
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        self.assertIsNot(a, b)

    def test_same_provider_reuses_instance(self):
        """同 provider 复用同一限速实例（令牌桶状态连续）。"""
        self.assertIs(get_rate_limiter("groq"), get_rate_limiter("groq"))

    def test_override_takes_precedence(self):
        """overrides 精确覆盖默认档（如 Groq 免费档限流）。"""
        llm_cache.settings.rate_limit_rps_overrides = {"groq": 0.5}
        limiter = get_rate_limiter("groq")
        self.assertIsNotNone(limiter)
        self.assertAlmostEqual(limiter.requests_per_second, 0.5)

    def test_unlimited_list_is_configurable(self):
        """不限速清单可配置（新增本地 provider 时无需改代码）。"""
        llm_cache.settings.rate_limit_unlimited_providers = ["ollama", "my-local"]
        self.assertIsNone(get_rate_limiter("my-local"))
