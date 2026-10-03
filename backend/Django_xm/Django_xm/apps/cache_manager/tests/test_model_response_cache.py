"""模型响应缓存表征测试（D9：langchain 框架级 LLM 缓存旧轨已下线，单轨化）。

覆盖两组行为锁定：
1. generate_model_cache_key 用户命名空间：
   - 不同 user 同 prompt/model/mode → 键不同（跨用户串缓存修复）
   - user_id=None → "system" 固定命名空间（禁止回退旧的无 user 键结构）
   - tools_hash 提供时进入键；mode/model 变化改变键
2. ModelResponseCacheService 命中/未命中与失效（唯一缓存轨道）：
   - 同 user 写读往返命中；不同 user 读未命中（LocMemCache 真实读写）
   - scope=model 失效清 model_response:* 且仅清该前缀（不波及其他登记前缀）

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.cache_manager.tests.test_model_response_cache \
        --noinput --settings=Django_xm.settings.test
（LocMemCache 真实读写，无 Redis 依赖；locmem 无 delete_pattern，
 功能性失效测试用正则打桩模拟 django-redis 行为）
"""

import os
import re
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.core.cache import cache
from django.test import SimpleTestCase

from Django_xm.apps.cache_manager.services.cache_service import (
    CACHE_PREFIX_MODEL,
    CacheService,
    ModelResponseCacheService,
    generate_model_cache_key,
    invalidate_chat_cache,
)

_MODEL = "openai:gpt-4o-mini"
_PROMPT = "你好，介绍一下你自己"
_RESPONSE = {"message": "你好！", "success": True}


def _locmem_delete_pattern(pattern: str) -> int:
    """LocMemCache 无 delete_pattern，用正则模拟 django-redis 行为（仅测试用）。

    locmem 内部键格式为 ":<version>:<key>"，还原真实键后按 pattern 匹配删除；
    须同步清理 _expire_info，否则残留过期条目会让后续 get 触发 KeyError。
    """
    regex = re.compile("^" + re.escape(pattern).replace(r"\*", ".*") + "$")
    removed = 0
    for full_key in list(cache._cache.keys()):
        real_key = full_key.split(":", 2)[2]
        if regex.match(real_key):
            del cache._cache[full_key]
            cache._expire_info.pop(full_key, None)
            removed += 1
    return removed


class GenerateModelCacheKeyTests(SimpleTestCase):
    """键结构表征：用户命名空间隔离。"""

    def test_different_users_same_prompt_produce_different_keys(self):
        key_a = generate_model_cache_key(_PROMPT, _MODEL, "agent", user_id=1)
        key_b = generate_model_cache_key(_PROMPT, _MODEL, "agent", user_id=2)
        self.assertNotEqual(key_a, key_b)
        self.assertIn(":u1:", key_a)
        self.assertIn(":u2:", key_b)

    def test_system_namespace_when_no_user_context(self):
        """user_id=None → 固定 system 命名空间，不回退旧的无 user 键结构。"""
        key = generate_model_cache_key(_PROMPT, _MODEL, "agent")
        self.assertIn(":system:", key)
        # system 键与任何用户键互不相同
        self.assertNotEqual(key, generate_model_cache_key(_PROMPT, _MODEL, "agent", user_id=1))
        self.assertNotEqual(key, generate_model_cache_key(_PROMPT, _MODEL, "agent", user_id="1"))

    def test_key_deterministic_and_structured(self):
        key1 = generate_model_cache_key(_PROMPT, _MODEL, "agent", user_id=7)
        key2 = generate_model_cache_key(_PROMPT, _MODEL, "agent", user_id=7)
        self.assertEqual(key1, key2)
        self.assertTrue(key1.startswith(f"{CACHE_PREFIX_MODEL}:{_MODEL}:agent:u7:"))

    def test_mode_and_tools_hash_change_key(self):
        base = generate_model_cache_key(_PROMPT, _MODEL, "agent", user_id=1)
        other_mode = generate_model_cache_key(_PROMPT, _MODEL, "chat", user_id=1)
        with_tools = generate_model_cache_key(_PROMPT, _MODEL, "agent", tools_hash="abc", user_id=1)
        self.assertNotEqual(base, other_mode)
        self.assertNotEqual(base, with_tools)
        self.assertIn(":tabc", with_tools)


class ModelResponseCacheHitMissTests(SimpleTestCase):
    """命中/未命中行为（LocMemCache 真实读写）。"""

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_roundtrip_hit_for_same_user(self):
        self.assertTrue(
            ModelResponseCacheService.cache_model_response(_PROMPT, _RESPONSE, _MODEL, "agent", user_id=1)
        )
        cached = ModelResponseCacheService.get_cached_response(_PROMPT, _MODEL, "agent", user_id=1)
        self.assertEqual(cached["response"], _RESPONSE)

    def test_other_user_misses(self):
        """跨用户串缓存修复表征：user 1 写入后 user 2 读未命中。"""
        ModelResponseCacheService.cache_model_response(_PROMPT, _RESPONSE, _MODEL, "agent", user_id=1)
        self.assertIsNone(ModelResponseCacheService.get_cached_response(_PROMPT, _MODEL, "agent", user_id=2))

    def test_system_namespace_isolated_from_users(self):
        """无 user 上下文（system 段）与用户键互不命中。"""
        ModelResponseCacheService.cache_model_response(_PROMPT, _RESPONSE, _MODEL, "agent", user_id=1)
        self.assertIsNone(ModelResponseCacheService.get_cached_response(_PROMPT, _MODEL, "agent", user_id=None))
        cache.clear()
        ModelResponseCacheService.cache_model_response(_PROMPT, _RESPONSE, _MODEL, "agent", user_id=None)
        self.assertIsNone(ModelResponseCacheService.get_cached_response(_PROMPT, _MODEL, "agent", user_id=1))

    def test_miss_before_write(self):
        self.assertIsNone(ModelResponseCacheService.get_cached_response(_PROMPT, _MODEL, "agent", user_id=1))


class ModelInvalidationTests(SimpleTestCase):
    """scope=model 失效（单轨）：清 model_response:* 且不波及其他登记前缀。"""

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_invalidate_calls_model_track_pattern_once(self):
        with mock.patch.object(CacheService, "delete_pattern", return_value=2) as mock_dp:
            count = ModelResponseCacheService.invalidate_model_cache(_MODEL)
        mock_dp.assert_called_once_with(f"{CACHE_PREFIX_MODEL}:{_MODEL}:*")
        self.assertEqual(count, 2)

    def test_scope_model_functionally_clears_model_response_keys(self):
        """功能性表征：scope=model 失效后业务层键被清（LocMemCache 真实读写）。"""
        ModelResponseCacheService.cache_model_response(_PROMPT, _RESPONSE, _MODEL, "agent", user_id=1)
        self.assertIsNotNone(
            ModelResponseCacheService.get_cached_response(_PROMPT, _MODEL, "agent", user_id=1)
        )

        with mock.patch.object(CacheService, "delete_pattern", side_effect=_locmem_delete_pattern):
            ModelResponseCacheService.invalidate_model_cache(_MODEL)

        self.assertIsNone(ModelResponseCacheService.get_cached_response(_PROMPT, _MODEL, "agent", user_id=1))

    def test_scope_model_invalidation_spares_other_prefixes(self):
        """失效仅清 model_response 前缀，不波及其他登记前缀（以 rag_query 为样本）。"""
        rag_key = "rag_query:user_1_kb:abcdefabcdefabcdefabcdef:k4"
        CacheService.set(rag_key, {"q": 1}, 60)
        ModelResponseCacheService.cache_model_response(_PROMPT, _RESPONSE, _MODEL, "agent", user_id=1)

        with mock.patch.object(CacheService, "delete_pattern", side_effect=_locmem_delete_pattern):
            ModelResponseCacheService.invalidate_model_cache(_MODEL)

        self.assertIsNotNone(CacheService.get(rag_key))

    def test_chat_cache_invalidation_only_clears_dashboard_key(self):
        with mock.patch.object(CacheService, "delete", return_value=True) as mock_del:
            invalidate_chat_cache(user_id=1)
        mock_del.assert_called_once_with("analytics:dashboard:user_1")
