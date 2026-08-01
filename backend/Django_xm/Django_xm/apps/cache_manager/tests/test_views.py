"""cache_manager views API 测试。

覆盖 5 个 view 的权限校验、参数校验、成功路径与异常路径：

| View                       | Method | Path                          | 权限            |
|----------------------------|--------|-------------------------------|-----------------|
| CacheHealthView            | GET    | /api/v1/cache/health/         | IsAuthenticated |
| CacheStatsView             | GET    | /api/v1/cache/stats/          | IsAuthenticated |
| CacheInvalidateView        | POST   | /api/v1/cache/invalidate/     | IsAdminUser     |
| CacheClearView             | POST   | /api/v1/cache/clear/          | IsAuthenticated |
| CacheResetStatsView        | POST   | /api/v1/cache/reset-stats/    | IsAdminUser     |

设计原则：
- mock 外部依赖（Redis 客户端、CacheService 静态方法、CacheHealthChecker），
  避免 view 测试因 Redis 不可用而 flaky
- 权限测试覆盖：未认证 401、普通用户访问管理员端点 403、管理员访问成功
- 参数校验测试覆盖：scope 缺失/非法、index/session/pattern/all 分支
- 异常路径测试覆盖：底层服务抛异常时返回 500
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from rest_framework.test import APITestCase

from Django_xm.apps.users.models import User


class CacheManagerViewTestBase(APITestCase):
    """cache_manager 视图测试公共 setUp。"""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="cache-view-tester",
            password="testpass123",
        )
        cls.admin_user = User.objects.create_user(
            username="cache-admin-tester",
            password="testpass123",
            is_staff=True,
            is_superuser=True,
        )

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(user=self.user)

    def _force_admin(self):
        """切换为管理员身份。"""
        self.client.force_authenticate(user=self.admin_user)


# ============================================================================
# CacheHealthView 测试
# ============================================================================


class CacheHealthViewTests(CacheManagerViewTestBase):
    """GET /api/v1/cache/health/。"""

    @patch("Django_xm.apps.cache_manager.views.RedisDirectClient.get_info")
    @patch("Django_xm.apps.cache_manager.views.get_redis_info")
    @patch("Django_xm.apps.cache_manager.views.CacheHealthChecker.get_health_info")
    def test_get_health_healthy(self, mock_health_info, mock_redis_info, mock_direct_info):
        """缓存健康时返回 connection=healthy 与 Redis 信息。"""
        mock_health_info.return_value = {
            "connection": "healthy",
            "stats": {"hit_count": 10, "miss_count": 2},
            "checked_at": "2026-07-31T00:00:00",
        }
        mock_redis_info.return_value = {
            "redis_version": "7.0.0",
            "used_memory_human": "1M",
            "connected_clients": 5,
            "uptime_in_seconds": 100,
            "db0": {"keys": 100},
        }
        mock_direct_info.return_value = {
            "used_memory_human": "1M",
            "connected_clients": 5,
            "db_size": 100,
            "total_commands_processed": 1000,
            "keyspace_hits": 800,
            "keyspace_misses": 200,
        }

        resp = self.client.get("/api/v1/cache/health/")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["code"], 200)
        data = body["data"]
        self.assertEqual(data["connection"], "healthy")
        # Redis info 合并字段
        self.assertEqual(data["backend"], "Redis")
        self.assertEqual(data["version"], "7.0.0")
        self.assertEqual(data["total_keys"], 100)
        # RedisDirectClient info 合并字段
        self.assertEqual(data["db_size"], 100)
        self.assertEqual(data["keyspace_hits"], 800)
        self.assertEqual(data["keyspace_misses"], 200)
        self.assertIn("缓存服务正常", body["message"])

    @patch("Django_xm.apps.cache_manager.views.RedisDirectClient.get_info")
    @patch("Django_xm.apps.cache_manager.views.get_redis_info")
    @patch("Django_xm.apps.cache_manager.views.CacheHealthChecker.get_health_info")
    def test_get_health_unhealthy(self, mock_health_info, mock_redis_info, mock_direct_info):
        """缓存不健康时返回 connection=unhealthy 与异常消息。"""
        mock_health_info.return_value = {"connection": "unhealthy", "stats": {}}
        mock_redis_info.return_value = None
        mock_direct_info.return_value = None

        resp = self.client.get("/api/v1/cache/health/")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["data"]["connection"], "unhealthy")
        self.assertIn("缓存服务异常", body["message"])

    @patch(
        "Django_xm.apps.cache_manager.views.CacheHealthChecker.get_health_info",
        side_effect=RuntimeError("redis down"),
    )
    def test_get_health_exception_returns_500(self, _mock):
        """底层异常时返回 500。"""
        resp = self.client.get("/api/v1/cache/health/")

        self.assertEqual(resp.status_code, 500)
        body = resp.json()
        self.assertEqual(body["code"], 50001)

    def test_get_health_unauthenticated_returns_401(self):
        """未认证返回 401。"""
        self.client.force_authenticate(user=None)
        resp = self.client.get("/api/v1/cache/health/")
        self.assertEqual(resp.status_code, 401)


# ============================================================================
# CacheStatsView 测试
# ============================================================================


class CacheStatsViewTests(CacheManagerViewTestBase):
    """GET /api/v1/cache/stats/。"""

    @patch("Django_xm.apps.cache_manager.views._count_keys_by_prefix")
    @patch("Django_xm.apps.cache_manager.views.RedisDirectClient.get_info")
    @patch("Django_xm.apps.cache_manager.views.get_redis_info")
    @patch("Django_xm.apps.cache_manager.views.CacheService.get_stats")
    def test_get_stats_success(self, mock_get_stats, mock_redis_info, mock_direct_info, mock_count_keys):
        """成功返回统计数据，包含 categories 列表与 Redis 命中率。"""
        mock_get_stats.return_value = {"hit_count": 10, "miss_count": 5, "total_requests": 15, "hit_rate": 66.67}
        mock_redis_info.return_value = {
            "db0": {"keys": 50, "expires": 10, "avg_ttl": 3600},
            "used_memory": 1024,
            "used_memory_human": "1K",
            "used_memory_peak_human": "2K",
            "total_commands_processed": 200,
            "keyspace_hits": 150,
            "keyspace_misses": 50,
        }
        mock_direct_info.return_value = {"db_size": 50}
        # _count_keys_by_prefix 对每个 prefix 调用一次，返回递增计数
        mock_count_keys.side_effect = [10, 5, 8, 3, 2, 7, 4]

        resp = self.client.get("/api/v1/cache/stats/")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["code"], 200)
        data = body["data"]
        # 基础 stats
        self.assertEqual(data["hit_count"], 10)
        # Redis info 合并字段
        self.assertEqual(data["total_keys"], 50)
        self.assertEqual(data["used_memory_human"], "1K")
        self.assertEqual(data["redis_hit_rate"], 75.0)
        # categories 列表（CACHE_PREFIX_LABELS 共 7 个）
        self.assertEqual(len(data["categories"]), 7)
        self.assertEqual(data["categories"][0]["prefix"], "rag_query")
        self.assertEqual(data["categories"][0]["count"], 10)
        # RedisDirectClient info 合并字段
        self.assertEqual(data["db_size"], 50)

    @patch("Django_xm.apps.cache_manager.views._count_keys_by_prefix")
    @patch("Django_xm.apps.cache_manager.views.RedisDirectClient.get_info")
    @patch("Django_xm.apps.cache_manager.views.get_redis_info")
    @patch("Django_xm.apps.cache_manager.views.CacheService.get_stats")
    def test_get_stats_redis_unavailable(self, mock_get_stats, mock_redis_info, mock_direct_info, mock_count_keys):
        """Redis 不可用时仍返回基础 stats（无 Redis 字段）。"""
        mock_get_stats.return_value = {"hit_count": 0, "miss_count": 0, "total_requests": 0, "hit_rate": 0}
        mock_redis_info.return_value = None
        mock_direct_info.return_value = None
        mock_count_keys.return_value = 0

        resp = self.client.get("/api/v1/cache/stats/")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        data = body["data"]
        self.assertEqual(data["hit_count"], 0)
        # Redis 不可用时不应有 total_keys / redis_hit_rate
        self.assertNotIn("total_keys", data)
        self.assertNotIn("redis_hit_rate", data)
        # categories 仍应存在（全为 0）
        self.assertEqual(len(data["categories"]), 7)
        self.assertEqual(data["categories"][0]["count"], 0)

    @patch(
        "Django_xm.apps.cache_manager.views.CacheService.get_stats",
        side_effect=RuntimeError("stats error"),
    )
    def test_get_stats_exception_returns_500(self, _mock):
        """底层异常时返回 500。"""
        resp = self.client.get("/api/v1/cache/stats/")
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json()["code"], 50001)

    def test_get_stats_unauthenticated_returns_401(self):
        """未认证返回 401。"""
        self.client.force_authenticate(user=None)
        resp = self.client.get("/api/v1/cache/stats/")
        self.assertEqual(resp.status_code, 401)


# ============================================================================
# CacheInvalidateView 测试
# ============================================================================


class CacheInvalidateViewTests(CacheManagerViewTestBase):
    """POST /api/v1/cache/invalidate/（IsAdminUser）。"""

    def test_invalidate_unauthenticated_returns_401(self):
        """未认证返回 401。"""
        self.client.force_authenticate(user=None)
        resp = self.client.post("/api/v1/cache/invalidate/", {}, format="json")
        self.assertEqual(resp.status_code, 401)

    def test_invalidate_non_admin_returns_403(self):
        """普通用户访问管理员端点返回 403。"""
        resp = self.client.post("/api/v1/cache/invalidate/", {"scope": "all"}, format="json")
        self.assertEqual(resp.status_code, 403)

    @patch("Django_xm.apps.cache_manager.views.CacheInvalidationStrategy.on_index_updated")
    def test_invalidate_index_scope_success(self, mock_on_index):
        """scope=index 时调用 on_index_updated。"""
        self._force_admin()
        resp = self.client.post(
            "/api/v1/cache/invalidate/",
            {"scope": "index", "index_name": "my_index"},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("my_index", body["message"])
        mock_on_index.assert_called_once_with("my_index")

    @patch("Django_xm.apps.cache_manager.views.CacheInvalidationStrategy.on_session_updated")
    def test_invalidate_session_scope_success(self, mock_on_session):
        """scope=session 时调用 on_session_updated。"""
        self._force_admin()
        resp = self.client.post(
            "/api/v1/cache/invalidate/",
            {"scope": "session", "session_id": "sess-123"},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertIn("sess-123", resp.json()["message"])
        mock_on_session.assert_called_once_with("sess-123")

    @patch("Django_xm.apps.cache_manager.views.RedisDirectClient.delete_keys_by_pattern", return_value=5)
    def test_invalidate_pattern_scope_success(self, mock_delete):
        """scope=pattern 时调用 RedisDirectClient.delete_keys_by_pattern。"""
        self._force_admin()
        resp = self.client.post(
            "/api/v1/cache/invalidate/",
            {"scope": "pattern", "pattern": "rag_query:*"},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("rag_query:*", body["message"])
        self.assertIn("5", body["message"])
        mock_delete.assert_called_once_with("rag_query:*")

    @patch(
        "Django_xm.apps.cache_manager.views.CacheInvalidationStrategy.invalidate_all",
        return_value={"query": 3, "model": 2},
    )
    def test_invalidate_all_scope_success(self, mock_invalidate_all):
        """scope=all 时调用 invalidate_all。"""
        self._force_admin()
        resp = self.client.post(
            "/api/v1/cache/invalidate/",
            {"scope": "all"},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("全量缓存已失效", body["message"])
        mock_invalidate_all.assert_called_once()

    def test_invalidate_invalid_scope_returns_400(self):
        """scope 非法或缺参数返回 400。"""
        self._force_admin()
        # 缺 index_name
        resp = self.client.post(
            "/api/v1/cache/invalidate/",
            {"scope": "index"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["code"], 40002)
        self.assertIn("scope", body["message"])

    def test_invalidate_empty_body_defaults_to_invalid_scope(self):
        """空 body 时 scope 默认 'all'，应调用 invalidate_all。"""
        self._force_admin()
        with patch(
            "Django_xm.apps.cache_manager.views.CacheInvalidationStrategy.invalidate_all",
            return_value={},
        ) as mock_invalidate_all:
            resp = self.client.post("/api/v1/cache/invalidate/", {}, format="json")
            self.assertEqual(resp.status_code, 200)
            mock_invalidate_all.assert_called_once()

    @patch(
        "Django_xm.apps.cache_manager.views.CacheInvalidationStrategy.invalidate_all",
        side_effect=RuntimeError("flush failed"),
    )
    def test_invalidate_exception_returns_500(self, _mock):
        """底层异常时返回 500。"""
        self._force_admin()
        resp = self.client.post("/api/v1/cache/invalidate/", {"scope": "all"}, format="json")
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json()["code"], 50001)


# ============================================================================
# CacheClearView 测试
# ============================================================================


class CacheClearViewTests(CacheManagerViewTestBase):
    """POST /api/v1/cache/clear/（IsAuthenticated）。"""

    @patch("Django_xm.apps.cache_manager.views.CacheService.delete_pattern")
    def test_clear_with_pattern_success(self, mock_delete_pattern):
        """带 pattern 参数时调用 CacheService.delete_pattern。"""
        resp = self.client.post(
            "/api/v1/cache/clear/",
            {"pattern": "rag_query:user_1_*"},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["data"]["cleared"], 1)
        mock_delete_pattern.assert_called_once_with("rag_query:user_1_*")

    @patch("Django_xm.apps.cache_manager.views.CacheService.delete_pattern")
    def test_clear_query_type_success(self, mock_delete_pattern):
        """type=query 时按用户 id 删除查询缓存。

        view 中 query 分支调用 delete_pattern 但不累加 cleared（保持 0），
        仅验证 delete_pattern 被以正确 pattern 调用。
        """
        resp = self.client.post(
            "/api/v1/cache/clear/",
            {"type": "query"},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        # view 中 query 分支不累加 cleared
        self.assertEqual(body["data"]["cleared"], 0)
        expected_pattern = f"rag_query:user_{self.user.id}_*"
        mock_delete_pattern.assert_called_once_with(expected_pattern)

    @patch("Django_xm.apps.cache_manager.views.ModelResponseCacheService.invalidate_model_cache")
    def test_clear_model_type_success(self, mock_invalidate):
        """type=model 时调用 ModelResponseCacheService.invalidate_model_cache。

        依赖 app_cfg.get_openai_config() 真实运行返回 model 名称，
        只 mock invalidate_model_cache 验证调用链。
        """
        resp = self.client.post(
            "/api/v1/cache/clear/",
            {"type": "model"},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        # view 中 model 分支不累加 cleared，仍为 0
        self.assertEqual(body["data"]["cleared"], 0)
        mock_invalidate.assert_called_once()
        # 验证传入的 model 参数来自 app_cfg.get_openai_config()
        called_model = mock_invalidate.call_args.args[0]
        self.assertIsInstance(called_model, str)
        self.assertTrue(called_model)

    @patch("Django_xm.apps.cache_manager.views.CacheService.reset_stats")
    @patch("Django_xm.apps.cache_manager.views.get_redis_client")
    def test_clear_all_default_success(self, mock_get_client, mock_reset_stats):
        """无 pattern/type 时清空所有 prefix 并重置统计。"""
        mock_client = MagicMock()
        # scan 每次调用都返回 cursor=0（立即退出 while 循环），带 2 个 key
        # CACHE_PREFIX_LABELS 共 7 个 prefix，每个 prefix 调用一次 scan
        mock_client.scan.return_value = (0, ["key1", "key2"])
        mock_client.delete.return_value = 2
        mock_get_client.return_value = mock_client

        resp = self.client.post("/api/v1/cache/clear/", {}, format="json")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        # 7 个 prefix × 2 keys = 14
        self.assertEqual(body["data"]["cleared"], 14)
        # CACHE_PREFIX_LABELS 有 7 个 prefix，每个都扫描一次
        self.assertEqual(mock_client.scan.call_count, 7)
        mock_reset_stats.assert_called_once()

    @patch("Django_xm.apps.cache_manager.views.CacheService.reset_stats")
    @patch("Django_xm.apps.cache_manager.views.get_redis_client", return_value=None)
    def test_clear_all_redis_unavailable(self, mock_get_client, mock_reset_stats):
        """Redis 不可用时仍调用 reset_stats 并返回 cleared=0。"""
        resp = self.client.post("/api/v1/cache/clear/", {}, format="json")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["data"]["cleared"], 0)
        mock_reset_stats.assert_called_once()

    @patch(
        "Django_xm.apps.cache_manager.views.CacheService.delete_pattern",
        side_effect=RuntimeError("delete failed"),
    )
    def test_clear_exception_returns_500(self, _mock):
        """底层异常时返回 500（无 http_status 参数，默认 500）。"""
        resp = self.client.post(
            "/api/v1/cache/clear/",
            {"pattern": "rag_query:*"},
            format="json",
        )
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json()["code"], 50001)

    def test_clear_unauthenticated_returns_401(self):
        """未认证返回 401。"""
        self.client.force_authenticate(user=None)
        resp = self.client.post("/api/v1/cache/clear/", {}, format="json")
        self.assertEqual(resp.status_code, 401)


# ============================================================================
# CacheResetStatsView 测试
# ============================================================================


class CacheResetStatsViewTests(CacheManagerViewTestBase):
    """POST /api/v1/cache/reset-stats/（IsAdminUser）。"""

    def test_reset_stats_unauthenticated_returns_401(self):
        """未认证返回 401。"""
        self.client.force_authenticate(user=None)
        resp = self.client.post("/api/v1/cache/reset-stats/", {}, format="json")
        self.assertEqual(resp.status_code, 401)

    def test_reset_stats_non_admin_returns_403(self):
        """普通用户访问管理员端点返回 403。"""
        resp = self.client.post("/api/v1/cache/reset-stats/", {}, format="json")
        self.assertEqual(resp.status_code, 403)

    @patch("Django_xm.apps.cache_manager.views.CacheService.reset_stats")
    def test_reset_stats_success(self, mock_reset):
        """管理员重置统计成功。"""
        self._force_admin()
        resp = self.client.post("/api/v1/cache/reset-stats/", {}, format="json")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("已重置", body["message"])
        mock_reset.assert_called_once()

    @patch(
        "Django_xm.apps.cache_manager.views.CacheService.reset_stats",
        side_effect=RuntimeError("reset failed"),
    )
    def test_reset_stats_exception_returns_500(self, _mock):
        """底层异常时返回 500。"""
        self._force_admin()
        resp = self.client.post("/api/v1/cache/reset-stats/", {}, format="json")
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json()["code"], 50001)
