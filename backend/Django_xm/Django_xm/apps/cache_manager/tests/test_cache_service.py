"""cache_service.py 单元测试。

覆盖范围：
- CacheService：get/set/delete/delete_pattern/get_stats/reset_stats（基于 Django LocMemCache）
- 缓存键生成器：generate_query_cache_key / generate_model_cache_key / generate_embedding_cache_key
  / generate_tool_cache_key / generate_vector_search_cache_key（纯函数）
- CacheTTL：常量一致性
- QueryCacheService：get_cached_query / cache_query_result / invalidate_index_queries
- ModelResponseCacheService：get_cached_response / cache_model_response / invalidate_model_cache
- ToolResultCacheService：get_cached_tool_result / cache_tool_result
- invalidate_chat_cache / invalidate_knowledge_cache：统一失效接口

设计原则：
- 测试 settings 使用 LocMemCache（无 Redis 依赖），CacheService 通过 Django cache 框架工作
- delete_pattern / get_redis_client 等 Redis 专有方法测试其降级返回值（不报错）
- 每个测试独立：reset_stats 后操作，避免统计污染
"""

from __future__ import annotations

import unittest

from django.core.cache import cache as django_cache

from Django_xm.apps.cache_manager.services.cache_service import (
    CACHE_PREFIX_EMBEDDING,
    CACHE_PREFIX_MODEL,
    CACHE_PREFIX_QUERY,
    CACHE_PREFIX_TOOL,
    CACHE_PREFIX_VECTOR,
    CacheService,
    CacheTTL,
    ModelResponseCacheService,
    QueryCacheService,
    ToolResultCacheService,
    generate_embedding_cache_key,
    generate_model_cache_key,
    generate_query_cache_key,
    generate_tool_cache_key,
    generate_vector_search_cache_key,
    invalidate_chat_cache,
    invalidate_knowledge_cache,
)

# ============================================================================
# CacheTTL 常量单测
# ============================================================================


class CacheTTLTests(unittest.TestCase):
    """CacheTTL 常量一致性。"""

    def test_query_ttls_ordered(self):
        self.assertLess(CacheTTL.QUERY_SHORT, CacheTTL.QUERY_MEDIUM)
        self.assertLess(CacheTTL.QUERY_MEDIUM, CacheTTL.QUERY_LONG)

    def test_model_ttls_ordered(self):
        self.assertLess(CacheTTL.MODEL_SHORT, CacheTTL.MODEL_MEDIUM)

    def test_tool_ttls_ordered(self):
        self.assertLess(CacheTTL.TOOL_SHORT, CacheTTL.TOOL_MEDIUM)
        self.assertLess(CacheTTL.TOOL_MEDIUM, CacheTTL.TOOL_LONG)

    def test_embedding_ttl_is_long(self):
        # embedding 缓存 7 天
        self.assertEqual(CacheTTL.EMBEDDING, 604800)

    def test_all_ttls_positive(self):
        for attr in dir(CacheTTL):
            if attr.isupper():
                value = getattr(CacheTTL, attr)
                self.assertIsInstance(value, int)
                self.assertGreater(value, 0)


# ============================================================================
# 缓存键生成器单测
# ============================================================================


class GenerateQueryCacheKeyTests(unittest.TestCase):
    """generate_query_cache_key。"""

    def test_format_contains_prefix_and_index(self):
        key = generate_query_cache_key("测试查询", "my_index", k=4)
        self.assertTrue(key.startswith(f"{CACHE_PREFIX_QUERY}:my_index:"))
        self.assertTrue(key.endswith(":k4"))

    def test_same_input_same_key(self):
        key1 = generate_query_cache_key("查询", "idx", k=3)
        key2 = generate_query_cache_key("查询", "idx", k=3)
        self.assertEqual(key1, key2)

    def test_different_k_different_key(self):
        key1 = generate_query_cache_key("查询", "idx", k=3)
        key2 = generate_query_cache_key("查询", "idx", k=5)
        self.assertNotEqual(key1, key2)

    def test_different_query_different_key(self):
        key1 = generate_query_cache_key("查询A", "idx")
        key2 = generate_query_cache_key("查询B", "idx")
        self.assertNotEqual(key1, key2)

    def test_different_index_different_key(self):
        key1 = generate_query_cache_key("查询", "idx1")
        key2 = generate_query_cache_key("查询", "idx2")
        self.assertNotEqual(key1, key2)


class GenerateModelCacheKeyTests(unittest.TestCase):
    """generate_model_cache_key。"""

    def test_format_contains_prefix_and_model(self):
        key = generate_model_cache_key("prompt", "gpt-4o", mode="default")
        self.assertTrue(key.startswith(f"{CACHE_PREFIX_MODEL}:gpt-4o:default:"))

    def test_tools_hash_appended(self):
        key = generate_model_cache_key("prompt", "model", tools_hash="abc123")
        self.assertIn(":tabc123", key)

    def test_no_tools_hash_when_none(self):
        key = generate_model_cache_key("prompt", "model")
        self.assertNotIn(":t", key)

    def test_same_input_same_key(self):
        key1 = generate_model_cache_key("prompt", "model", "mode", "hash123")
        key2 = generate_model_cache_key("prompt", "model", "mode", "hash123")
        self.assertEqual(key1, key2)


class GenerateEmbeddingCacheKeyTests(unittest.TestCase):
    """generate_embedding_cache_key。"""

    def test_format(self):
        key = generate_embedding_cache_key("文本", "text-embedding-3")
        self.assertTrue(key.startswith(f"{CACHE_PREFIX_EMBEDDING}:text-embedding-3:"))

    def test_same_text_same_key(self):
        self.assertEqual(
            generate_embedding_cache_key("文本", "model"),
            generate_embedding_cache_key("文本", "model"),
        )

    def test_default_model(self):
        key = generate_embedding_cache_key("文本")
        self.assertTrue(key.startswith(f"{CACHE_PREFIX_EMBEDDING}:default:"))


class GenerateToolCacheKeyTests(unittest.TestCase):
    """generate_tool_cache_key。"""

    def test_format_contains_tool_name(self):
        key = generate_tool_cache_key("shell_exec", {"cmd": "ls"}, user_id=1)
        self.assertTrue(key.startswith(f"{CACHE_PREFIX_TOOL}:shell_exec:"))
        self.assertIn(":u1", key)

    def test_no_user_part_when_none(self):
        key = generate_tool_cache_key("calc", {"x": 1})
        self.assertNotIn(":u", key)

    def test_same_params_same_key(self):
        key1 = generate_tool_cache_key("tool", {"a": 1, "b": 2})
        key2 = generate_tool_cache_key("tool", {"b": 2, "a": 1})  # 顺序不同
        # sort_keys=True 保证顺序无关
        self.assertEqual(key1, key2)

    def test_different_params_different_key(self):
        key1 = generate_tool_cache_key("tool", {"a": 1})
        key2 = generate_tool_cache_key("tool", {"a": 2})
        self.assertNotEqual(key1, key2)


class GenerateVectorSearchCacheKeyTests(unittest.TestCase):
    """generate_vector_search_cache_key。"""

    def test_format(self):
        key = generate_vector_search_cache_key("查询", "idx", k=5)
        self.assertTrue(key.startswith(f"{CACHE_PREFIX_VECTOR}:idx:"))
        self.assertIn(":k5", key)

    def test_threshold_appended(self):
        key = generate_vector_search_cache_key("查询", "idx", k=4, score_threshold=0.5)
        self.assertIn(":th0.5", key)

    def test_no_threshold_when_none(self):
        key = generate_vector_search_cache_key("查询", "idx", k=4)
        self.assertNotIn(":th", key)


# ============================================================================
# CacheService 单测（基于 Django LocMemCache）
# ============================================================================


class CacheServiceGetSetTests(unittest.TestCase):
    """CacheService.get / set / delete 基本操作。"""

    def setUp(self):
        CacheService.reset_stats()
        django_cache.clear()

    def tearDown(self):
        django_cache.clear()

    def test_set_and_get(self):
        self.assertTrue(CacheService.set("test:key1", {"data": 123}))
        result = CacheService.get("test:key1")
        self.assertEqual(result, {"data": 123})

    def test_get_missing_returns_default(self):
        result = CacheService.get("nonexistent:key", default="fallback")
        self.assertEqual(result, "fallback")

    def test_get_missing_returns_none_default(self):
        result = CacheService.get("nonexistent:key")
        self.assertIsNone(result)

    def test_delete_existing(self):
        CacheService.set("test:delete", "value")
        self.assertTrue(CacheService.delete("test:delete"))
        self.assertIsNone(CacheService.get("test:delete"))

    def test_delete_nonexistent_returns_true(self):
        # delete 不存在的 key 不报错，返回 True
        self.assertTrue(CacheService.delete("nonexistent:delete"))

    def test_set_with_ttl(self):
        self.assertTrue(CacheService.set("test:ttl", "value", ttl=60))
        self.assertEqual(CacheService.get("test:ttl"), "value")

    def test_set_complex_value(self):
        complex_value = {"list": [1, 2, 3], "nested": {"a": "b"}, "none": None}
        CacheService.set("test:complex", complex_value)
        self.assertEqual(CacheService.get("test:complex"), complex_value)


class CacheServiceStatsTests(unittest.TestCase):
    """CacheService.get_stats / reset_stats 命中统计。"""

    def setUp(self):
        CacheService.reset_stats()
        django_cache.clear()

    def tearDown(self):
        CacheService.reset_stats()
        django_cache.clear()

    def test_initial_stats_zero(self):
        stats = CacheService.get_stats()
        self.assertEqual(stats["hit_count"], 0)
        self.assertEqual(stats["miss_count"], 0)
        self.assertEqual(stats["total_requests"], 0)
        self.assertEqual(stats["hit_rate"], 0)

    def test_hit_increments(self):
        CacheService.set("test:hit", "value")
        CacheService.get("test:hit")
        stats = CacheService.get_stats()
        self.assertEqual(stats["hit_count"], 1)

    def test_miss_increments(self):
        CacheService.get("nonexistent:key")
        stats = CacheService.get_stats()
        self.assertEqual(stats["miss_count"], 1)

    def test_hit_rate_calculation(self):
        CacheService.set("test:rate", "value")
        CacheService.get("test:rate")  # hit
        CacheService.get("nonexistent")  # miss
        stats = CacheService.get_stats()
        self.assertEqual(stats["total_requests"], 2)
        self.assertEqual(stats["hit_rate"], 50.0)

    def test_reset_stats(self):
        CacheService.set("test:reset", "value")
        CacheService.get("test:reset")
        CacheService.reset_stats()
        stats = CacheService.get_stats()
        self.assertEqual(stats["hit_count"], 0)
        self.assertEqual(stats["miss_count"], 0)


class CacheServiceDeletePatternTests(unittest.TestCase):
    """CacheService.delete_pattern 降级行为。"""

    def setUp(self):
        CacheService.reset_stats()
        django_cache.clear()

    def tearDown(self):
        django_cache.clear()

    def test_returns_int(self):
        # LocMemCache 无 delete_pattern，返回 0；不报错
        result = CacheService.delete_pattern("rag_query:*")
        self.assertIsInstance(result, int)

    def test_does_not_raise_on_invalid_pattern(self):
        # 任何 pattern 都不应抛异常
        CacheService.delete_pattern("*")
        CacheService.delete_pattern("")


# ============================================================================
# QueryCacheService 单测
# ============================================================================


class QueryCacheServiceTests(unittest.TestCase):
    """QueryCacheService 缓存查询结果。"""

    def setUp(self):
        CacheService.reset_stats()
        django_cache.clear()

    def tearDown(self):
        django_cache.clear()

    def test_cache_and_get(self):
        result = {"docs": [{"text": "内容"}]}
        self.assertTrue(QueryCacheService.cache_query_result("查询", result, "idx", k=4))
        cached = QueryCacheService.get_cached_query("查询", "idx", k=4)
        self.assertIsNotNone(cached)
        self.assertEqual(cached["result"], result)
        self.assertEqual(cached["query"], "查询")

    def test_get_missing_returns_none(self):
        self.assertIsNone(QueryCacheService.get_cached_query("不存在", "idx"))

    def test_different_k_separate_cache(self):
        QueryCacheService.cache_query_result("查询", {"r": 1}, "idx", k=3)
        QueryCacheService.cache_query_result("查询", {"r": 2}, "idx", k=5)
        self.assertEqual(QueryCacheService.get_cached_query("查询", "idx", k=3)["result"], {"r": 1})
        self.assertEqual(QueryCacheService.get_cached_query("查询", "idx", k=5)["result"], {"r": 2})

    def test_invalidate_index_queries_returns_true(self):
        # 不报错，返回 True
        self.assertTrue(QueryCacheService.invalidate_index_queries("idx"))


# ============================================================================
# ModelResponseCacheService 单测
# ============================================================================


class ModelResponseCacheServiceTests(unittest.TestCase):
    """ModelResponseCacheService 缓存模型响应。"""

    def setUp(self):
        CacheService.reset_stats()
        django_cache.clear()

    def tearDown(self):
        django_cache.clear()

    def test_cache_and_get(self):
        response = {"content": "AI 回复"}
        self.assertTrue(ModelResponseCacheService.cache_model_response("prompt", response, "gpt-4o", mode="default"))
        cached = ModelResponseCacheService.get_cached_response("prompt", "gpt-4o", "default")
        self.assertIsNotNone(cached)
        self.assertEqual(cached["response"], response)

    def test_get_missing_returns_none(self):
        self.assertIsNone(ModelResponseCacheService.get_cached_response("不存在", "model"))

    def test_different_model_separate_cache(self):
        ModelResponseCacheService.cache_model_response("prompt", {"r": 1}, "gpt-4o")
        ModelResponseCacheService.cache_model_response("prompt", {"r": 2}, "claude")
        self.assertEqual(ModelResponseCacheService.get_cached_response("prompt", "gpt-4o")["response"], {"r": 1})

    def test_invalidate_model_cache_returns_true(self):
        self.assertTrue(ModelResponseCacheService.invalidate_model_cache("gpt-4o"))


# ============================================================================
# ToolResultCacheService 单测
# ============================================================================


class ToolResultCacheServiceTests(unittest.TestCase):
    """ToolResultCacheService 缓存工具结果。"""

    def setUp(self):
        CacheService.reset_stats()
        django_cache.clear()

    def tearDown(self):
        django_cache.clear()

    def test_cache_and_get(self):
        result = {"output": "command result"}
        self.assertTrue(ToolResultCacheService.cache_tool_result("shell_exec", {"cmd": "ls"}, result))
        cached = ToolResultCacheService.get_cached_tool_result("shell_exec", {"cmd": "ls"})
        self.assertEqual(cached, result)

    def test_get_missing_returns_none(self):
        self.assertIsNone(ToolResultCacheService.get_cached_tool_result("tool", {"x": 1}))

    def test_user_isolation(self):
        # 不同 user_id 生成不同 key
        ToolResultCacheService.cache_tool_result("tool", {"x": 1}, "r1", user_id=1)
        ToolResultCacheService.cache_tool_result("tool", {"x": 1}, "r2", user_id=2)
        self.assertEqual(ToolResultCacheService.get_cached_tool_result("tool", {"x": 1}, user_id=1), "r1")
        self.assertEqual(ToolResultCacheService.get_cached_tool_result("tool", {"x": 1}, user_id=2), "r2")

    def test_different_params_separate_cache(self):
        ToolResultCacheService.cache_tool_result("tool", {"a": 1}, "r1")
        ToolResultCacheService.cache_tool_result("tool", {"a": 2}, "r2")
        self.assertEqual(ToolResultCacheService.get_cached_tool_result("tool", {"a": 1}), "r1")


# ============================================================================
# invalidate_chat_cache / invalidate_knowledge_cache 单测
# ============================================================================


class InvalidateChatCacheTests(unittest.TestCase):
    """invalidate_chat_cache 统一失效接口。"""

    def setUp(self):
        CacheService.reset_stats()
        django_cache.clear()

    def tearDown(self):
        django_cache.clear()

    def test_deletes_dashboard_cache(self):
        CacheService.set("dashboard:user_1", {"sessions": []})
        invalidate_chat_cache(user_id=1)
        self.assertIsNone(CacheService.get("dashboard:user_1"))

    def test_none_user_id_is_noop(self):
        # user_id=None 时不报错
        invalidate_chat_cache(user_id=None)

    def test_does_not_delete_other_users(self):
        CacheService.set("dashboard:user_1", {"a": 1})
        CacheService.set("dashboard:user_2", {"b": 2})
        invalidate_chat_cache(user_id=1)
        self.assertIsNone(CacheService.get("dashboard:user_1"))
        self.assertEqual(CacheService.get("dashboard:user_2"), {"b": 2})


class InvalidateKnowledgeCacheTests(unittest.TestCase):
    """invalidate_knowledge_cache 统一失效接口。"""

    def setUp(self):
        CacheService.reset_stats()
        django_cache.clear()

    def tearDown(self):
        django_cache.clear()

    def test_deletes_kb_list_cache(self):
        CacheService.set("kb_list:user_1", {"kbs": []})
        invalidate_knowledge_cache(user_id=1)
        self.assertIsNone(CacheService.get("kb_list:user_1"))

    def test_deletes_doc_list_cache(self):
        CacheService.set("doc_list:user_1_my_kb", {"docs": []})
        invalidate_knowledge_cache(user_index_name="user_1_my_kb")
        self.assertIsNone(CacheService.get("doc_list:user_1_my_kb"))

    def test_both_params(self):
        CacheService.set("kb_list:user_1", {"x": 1})
        CacheService.set("doc_list:idx_1", {"y": 2})
        invalidate_knowledge_cache(user_id=1, user_index_name="idx_1")
        self.assertIsNone(CacheService.get("kb_list:user_1"))
        self.assertIsNone(CacheService.get("doc_list:idx_1"))

    def test_none_params_is_noop(self):
        invalidate_knowledge_cache()
        invalidate_knowledge_cache(user_id=None, user_index_name=None)


if __name__ == "__main__":
    unittest.main()
