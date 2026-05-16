"""
LLM factory 和 model cache 单元测试

测试模型实例缓存的 LRU 行为和 key 生成逻辑。
"""
from unittest import TestCase
from unittest.mock import patch, MagicMock

from Django_xm.apps.ai_engine.services.model_cache import (
    make_cache_key,
    get_cached_model,
    set_cached_model,
)


class MakeCacheKeyTest(TestCase):
    """测试缓存键生成"""

    def test_basic_key_format(self):
        key = make_cache_key("gpt-4o", "openai", 0.7, True, 4096)
        self.assertEqual(key, "openai:gpt-4o:t0.7:sTrue:mt4096")

    def test_key_without_max_tokens(self):
        key = make_cache_key("gpt-4o-mini", "openai", 0.5, False, None)
        self.assertEqual(key, "openai:gpt-4o-mini:t0.5:sFalse:mtNone")

    def test_key_with_special_suffix(self):
        key = make_cache_key("deepseek-v4-flash", "deepseek", 0.7, True, None, ":sp_thinking")
        self.assertEqual(key, "deepseek:deepseek-v4-flash:t0.7:sTrue:mtNone:sp_thinking")

    def test_float_temperature_no_trailing_issues(self):
        key = make_cache_key("gpt-4o", "openai", 1.0, True, 2048)
        self.assertIn("t1.0", key)


class ModelCacheTest(TestCase):
    """测试模型实例缓存的读写和淘汰"""

    def setUp(self):
        # 清空缓存状态（每个测试独立）
        self._clear_cache()

    def tearDown(self):
        self._clear_cache()

    def _clear_cache(self):
        from Django_xm.apps.ai_engine.services.model_cache import _model_instance_cache
        _model_instance_cache.clear()

    def test_cache_miss_returns_none(self):
        result = get_cached_model("nonexistent:key")
        self.assertIsNone(result)

    def test_cache_hit_returns_model(self):
        model = MagicMock()
        set_cached_model("test:key", model)
        result = get_cached_model("test:key")
        self.assertIs(result, model)

    def test_cache_miss_after_eviction(self):
        from Django_xm.apps.ai_engine.services.model_cache import _MODEL_CACHE_MAXSIZE

        # 填充超过最大容量
        models = []
        for i in range(_MODEL_CACHE_MAXSIZE + 1):
            m = MagicMock()
            models.append(m)
            set_cached_model(f"key:{i}", m)

        # 最老的 key:0 应该被淘汰
        self.assertIsNone(get_cached_model("key:0"))
        # 最新的 key:{MAXSIZE} 应该在
        self.assertIsNotNone(get_cached_model(f"key:{_MODEL_CACHE_MAXSIZE}"))

    def test_lru_recently_used_is_kept(self):
        from Django_xm.apps.ai_engine.services.model_cache import _MODEL_CACHE_MAXSIZE

        # 插入若干条目
        for i in range(_MODEL_CACHE_MAXSIZE):
            set_cached_model(f"key:{i}", MagicMock())

        # 访问 key:0 使其变热
        hot = get_cached_model("key:0")
        self.assertIsNotNone(hot)

        # 再插入一个触发淘汰
        set_cached_model("key:new", MagicMock())

        # key:0 因为是最近访问过的，不应该被淘汰
        self.assertIsNotNone(get_cached_model("key:0"))
