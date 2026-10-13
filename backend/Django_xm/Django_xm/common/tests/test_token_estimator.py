"""TokenEstimator.get_model_limit 三层查询与自定义表 loader 注入行为。

三层顺序：admin 配置（loader 注入，SystemConfig model_context_limits）
→ 内置表（出厂兜底）→ default + warning。纯逻辑测试，无 DB 访问。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.common.tests.test_token_estimator
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")

import django

django.setup()

from django.test import SimpleTestCase

from Django_xm.common.token_estimator import TokenEstimator

LOGGER_NAME = "Django_xm.common.token_estimator"


class GetModelLimitTests(SimpleTestCase):
    def tearDown(self):
        TokenEstimator.set_custom_limits_loader(None)
        super().tearDown()

    def test_builtin_table_prefix_match(self):
        """内置表兜底：gpt-oss / claude 前缀命中（含 provider 前缀的完整模型名）。"""
        self.assertEqual(TokenEstimator.get_model_limit("openai/gpt-oss-120b"), 131072)
        self.assertEqual(TokenEstimator.get_model_limit("gpt-oss-20b"), 131072)
        self.assertEqual(TokenEstimator.get_model_limit("claude-sonnet-4-5-20261001"), 200000)

    def test_builtin_miss_returns_default_with_warning(self):
        """两层都未命中 → default + warning（提示去 admin 配置补档）。"""
        with self.assertLogs(LOGGER_NAME, level="WARNING") as logs:
            self.assertEqual(TokenEstimator.get_model_limit("totally-unknown-model"), 128000)
        self.assertTrue(any("model_context_limits" in m for m in logs.output))

    def test_empty_model_name_returns_default_silently(self):
        """空模型名 → 直接返回 default，不触发 warning。"""
        with self.assertNoLogs(LOGGER_NAME, level="WARNING"):
            self.assertEqual(TokenEstimator.get_model_limit("", default=64000), 64000)

    def test_custom_table_takes_precedence_over_builtin(self):
        """admin 配置优先于内置表；未覆盖的模型仍走内置表。"""
        TokenEstimator.set_custom_limits_loader(lambda: {"my-model": 4096})
        self.assertEqual(TokenEstimator.get_model_limit("provider/my-model-v2"), 4096)
        self.assertEqual(TokenEstimator.get_model_limit("gpt-4o"), 128000)

    def test_custom_table_exact_match_wins_over_prefix(self):
        """自定义表内精确命中优先于前缀命中（更具体的条目不被更宽泛的遮蔽）。"""
        TokenEstimator.set_custom_limits_loader(lambda: {"my-model": 111, "my-model-v2": 222})
        self.assertEqual(TokenEstimator.get_model_limit("my-model-v2"), 222)
        self.assertEqual(TokenEstimator.get_model_limit("provider/my-model-v3"), 111)

    def test_loader_exception_keeps_last_config(self):
        """loader 抛异常（如 DB 抖动）→ 沿用上次成功配置，不影响预算规划。"""
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                return {"stable": 777}
            raise RuntimeError("db down")

        TokenEstimator.set_custom_limits_loader(flaky)
        self.assertEqual(TokenEstimator.get_model_limit("stable-x"), 777)
        # 强制 TTL 过期后再次查询：loader 抛异常，仍返回上次配置
        TokenEstimator._custom_limits_loaded_at = 0.0
        self.assertEqual(TokenEstimator.get_model_limit("stable-x"), 777)
        self.assertEqual(calls["n"], 2)

    def test_ttl_caches_loader_result(self):
        """TTL 内只调用一次 loader（每次 agent 构建多次查询不放大配置读取）。"""
        calls = {"n": 0}

        def counting():
            calls["n"] += 1
            return {"cached-model": 1000}

        TokenEstimator.set_custom_limits_loader(counting)
        TokenEstimator.get_model_limit("cached-model")
        TokenEstimator.get_model_limit("cached-model")
        self.assertEqual(calls["n"], 1)

    def test_unregister_loader_clears_table(self):
        """传 None 注销 loader → 自定义表清空，回退内置表/default 路径。"""
        TokenEstimator.set_custom_limits_loader(lambda: {"x": 1})
        self.assertEqual(TokenEstimator.get_model_limit("x-model"), 1)
        TokenEstimator.set_custom_limits_loader(None)
        with self.assertLogs(LOGGER_NAME, level="WARNING"):
            self.assertEqual(TokenEstimator.get_model_limit("x-model"), 128000)

    def test_loader_returning_empty_dict_clears_table(self):
        """loader 返回空 dict（admin 删光配置）→ 清空自定义表走内置表。"""
        TokenEstimator.set_custom_limits_loader(lambda: {"x": 1})
        self.assertEqual(TokenEstimator.get_model_limit("x-model"), 1)
        TokenEstimator._custom_limits_loaded_at = 0.0
        TokenEstimator._custom_limits_loader = lambda: {}
        self.assertEqual(TokenEstimator.get_model_limit("gpt-4o"), 128000)


class ImprovedRoughEstimateTests(SimpleTestCase):
    """4.5：启发式兜底口径——中文 1.0 token/字（原 1.5 系数系统性高估）。"""

    def test_pure_chinese_one_token_per_char(self):
        """纯中文（无标点/数字/英文）：N 字 ≈ N token。"""
        text = "一二三四五六七八九十"  # 10 个 CJK 汉字
        self.assertEqual(TokenEstimator._improved_rough_estimate(text), 10)
        self.assertEqual(TokenEstimator._improved_rough_estimate("上下文摘要测试"), 7)

    def test_chinese_coefficient_no_longer_inflated(self):
        """口径回归：同一文本估算值不再按 1.5 系数放大。"""
        text = "这是一段用于校准中文系数的文本"  # 15 个汉字
        estimate = TokenEstimator._improved_rough_estimate(text)
        self.assertEqual(estimate, 15)
        self.assertLess(estimate, 23)  # 若误回 1.5 系数则为 22.5 → int 22

    def test_empty_text_returns_zero(self):
        self.assertEqual(TokenEstimator._improved_rough_estimate(""), 0)
        self.assertEqual(TokenEstimator._improved_rough_estimate("   "), 0)
