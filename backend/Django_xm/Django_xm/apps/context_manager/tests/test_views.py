"""context_manager 视图 API 测试。

覆盖 6 个视图的请求与响应行为，mock 掉 store / context_manager 等外部依赖：
- ContextStatsView GET /api/v1/context/stats/ — 成功 / 未认证
- KnowledgeGraphView DELETE /api/v1/context/knowledge-graph/ — 成功 / 无 confirm 参数 400 / 未认证
- TokenBudgetView GET /api/v1/context/token-budget/ — 成功 / 缺少 session_id 400 / 未认证
- ContextCompressView POST /api/v1/context/compress/ — 成功 / 缺少 session_id 400 / 会话不存在 404
- KnowledgeGraphDetailView GET /api/v1/context/knowledge-graph/detail/ — 成功 / 缺少 session_id 400
- capability_config_view GET /api/v1/context/capability-config/ — 成功（AllowAny）

设计原则：
- 使用 APITestCase（DRF 测试客户端 + DB 事务管理）
- 所有外部依赖通过 patch mock，不依赖真实 Redis / Store / LLM
- 每个测试覆盖成功路径 + 主要错误路径
"""

from unittest.mock import MagicMock, patch

from rest_framework.test import APITestCase

from Django_xm.apps.users.models import User


class ContextManagerViewTestBase(APITestCase):
    """context_manager 视图测试公共 setUp。"""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="ctx_view_tester",
            password="testpass123",
        )

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(user=self.user)

    def _mock_context_manager(self, **overrides):
        """创建一个 mock context_manager，可按需覆盖属性。"""
        mock = MagicMock()
        mock.get_stats.return_value = overrides.get(
            "stats",
            {
                "user_id": 1,
                "compression_enabled": True,
                "knowledge_graph_enabled": True,
                "cross_session_enabled": False,
                "kg_entity_count": 5,
                "kg_relation_count": 3,
            },
        )
        mock._knowledge_graph = overrides.get("_knowledge_graph", MagicMock())
        mock._token_budget_manager = overrides.get(
            "_token_budget_manager", MagicMock(total_budget=100000, total_used=30000)
        )
        mock._compression_engine = overrides.get("_compression_engine", MagicMock())
        return mock


class ContextStatsViewTests(ContextManagerViewTestBase):
    """ContextStatsView GET /api/v1/context/stats/。"""

    @patch("Django_xm.apps.context_manager.views.get_store")
    @patch("Django_xm.apps.context_manager.views.create_context_manager")
    def test_get_stats_success(self, mock_create_ctx, mock_get_store):
        """GET stats 成功：返回上下文统计。"""
        mock_ctx = self._mock_context_manager()
        mock_create_ctx.return_value = mock_ctx
        mock_get_store.return_value = MagicMock()

        resp = self.client.get("/api/v1/context/stats/", {"session_id": "sess-001"})

        self.assertEqual(resp.status_code, 200)
        self.assertIn("code", resp.data)
        mock_ctx.get_stats.assert_called_once()

    @patch("Django_xm.apps.context_manager.views.get_store")
    @patch("Django_xm.apps.context_manager.views.create_context_manager")
    def test_get_stats_server_error(self, mock_create_ctx, mock_get_store):
        """GET stats 内部异常 → 500 error_response。"""
        mock_create_ctx.side_effect = RuntimeError("store 不可用")

        resp = self.client.get("/api/v1/context/stats/", {"session_id": "sess-001"})

        self.assertEqual(resp.status_code, 500)

    def test_get_stats_unauthenticated(self):
        """GET stats 未认证 → 401。"""
        self.client.force_authenticate(user=None)
        resp = self.client.get("/api/v1/context/stats/")
        self.assertIn(resp.status_code, (401, 403))


class KnowledgeGraphViewTests(ContextManagerViewTestBase):
    """KnowledgeGraphView DELETE /api/v1/context/knowledge-graph/。"""

    @patch("Django_xm.apps.context_manager.views.get_store")
    @patch("Django_xm.apps.context_manager.views.create_context_manager")
    def test_delete_kg_success(self, mock_create_ctx, mock_get_store):
        """DELETE knowledge-graph?confirm=true 成功：清除知识图谱。"""
        mock_ctx = self._mock_context_manager()
        mock_create_ctx.return_value = mock_ctx

        resp = self.client.delete("/api/v1/context/knowledge-graph/?confirm=true")

        self.assertEqual(resp.status_code, 200)
        mock_ctx._knowledge_graph.clear_user_graph.assert_called_once()

    @patch("Django_xm.apps.context_manager.views.get_store")
    @patch("Django_xm.apps.context_manager.views.create_context_manager")
    def test_delete_kg_without_confirm_400(self, mock_create_ctx, mock_get_store):
        """DELETE knowledge-graph 无 confirm 参数 → 400。"""
        resp = self.client.delete("/api/v1/context/knowledge-graph/")

        self.assertEqual(resp.status_code, 400)
        mock_create_ctx.assert_not_called()

    @patch("Django_xm.apps.context_manager.views.get_store")
    @patch("Django_xm.apps.context_manager.views.create_context_manager")
    def test_delete_kg_not_enabled(self, mock_create_ctx, mock_get_store):
        """DELETE knowledge-graph 知识图谱未启用 → 200（提示无需清除）。"""
        mock_ctx = self._mock_context_manager(_knowledge_graph=None)
        mock_create_ctx.return_value = mock_ctx

        resp = self.client.delete("/api/v1/context/knowledge-graph/?confirm=true")

        self.assertEqual(resp.status_code, 200)


class TokenBudgetViewTests(ContextManagerViewTestBase):
    """TokenBudgetView GET /api/v1/context/token-budget/。"""

    @patch("Django_xm.apps.context_manager.views.get_store")
    @patch("Django_xm.apps.context_manager.views.create_context_manager")
    def test_get_budget_success(self, mock_create_ctx, mock_get_store):
        """GET token-budget 成功：返回预算使用情况。"""
        mock_ctx = self._mock_context_manager()
        mock_create_ctx.return_value = mock_ctx

        resp = self.client.get("/api/v1/context/token-budget/", {"session_id": "sess-001"})

        self.assertEqual(resp.status_code, 200)
        self.assertIn("data", resp.data)

    def test_get_budget_without_session_id(self):
        """GET token-budget 无 session_id → 200（session_id 为可选参数）。"""
        with (
            patch("Django_xm.apps.context_manager.views.get_store"),
            patch("Django_xm.apps.context_manager.views.create_context_manager") as mock_create_ctx,
        ):
            mock_ctx = self._mock_context_manager()
            mock_create_ctx.return_value = mock_ctx

            resp = self.client.get("/api/v1/context/token-budget/")

            self.assertEqual(resp.status_code, 200)

    @patch("Django_xm.apps.context_manager.views.get_store")
    @patch("Django_xm.apps.context_manager.views.create_context_manager")
    def test_get_budget_server_error(self, mock_create_ctx, mock_get_store):
        """GET token-budget 内部异常 → 500。"""
        mock_create_ctx.side_effect = RuntimeError("store 故障")

        resp = self.client.get("/api/v1/context/token-budget/", {"session_id": "sess-001"})

        self.assertEqual(resp.status_code, 500)


class ContextCompressViewTests(ContextManagerViewTestBase):
    """ContextCompressView POST /api/v1/context/compress/。"""

    @patch("Django_xm.apps.context_manager.views.get_store")
    @patch("Django_xm.apps.context_manager.views.create_context_manager")
    def test_post_compress_success(self, mock_create_ctx, mock_get_store):
        """POST compress 成功：返回压缩结果。"""
        mock_store = MagicMock()
        mock_item = MagicMock()
        mock_item.value = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！"},
        ]
        mock_store.get.return_value = mock_item
        mock_get_store.return_value = mock_store

        mock_ctx = self._mock_context_manager()
        from Django_xm.apps.context_manager.services.compression import CompressionResult

        mock_ctx._compression_engine.compress.return_value = (
            [{"role": "user", "content": "你好"}],
            CompressionResult(
                original_token_estimate=100,
                compressed_token_estimate=50,
                compression_ratio=0.5,
            ),
        )
        mock_create_ctx.return_value = mock_ctx

        resp = self.client.post("/api/v1/context/compress/", {"session_id": "sess-001"}, format="json")

        self.assertEqual(resp.status_code, 200)
        self.assertIn("data", resp.data)

    def test_post_compress_missing_session_id_400(self):
        """POST compress 缺少 session_id → 400。"""
        resp = self.client.post("/api/v1/context/compress/", {}, format="json")
        self.assertEqual(resp.status_code, 400)

    @patch("Django_xm.apps.context_manager.views.get_store")
    @patch("Django_xm.apps.context_manager.views.create_context_manager")
    def test_post_compress_session_not_found_404(self, mock_create_ctx, mock_get_store):
        """POST compress 会话消息不存在 → 404。"""
        mock_store = MagicMock()
        mock_store.get.side_effect = KeyError("not found")
        mock_get_store.return_value = mock_store
        mock_ctx = self._mock_context_manager()
        mock_create_ctx.return_value = mock_ctx

        resp = self.client.post("/api/v1/context/compress/", {"session_id": "nonexistent"}, format="json")

        self.assertEqual(resp.status_code, 404)


class KnowledgeGraphDetailViewTests(ContextManagerViewTestBase):
    """KnowledgeGraphDetailView GET /api/v1/context/knowledge-graph/detail/。"""

    @patch("Django_xm.apps.context_manager.views.get_store")
    @patch("Django_xm.apps.context_manager.views.create_context_manager")
    def test_get_detail_success(self, mock_create_ctx, mock_get_store):
        """GET knowledge-graph/detail 成功：返回实体和关系。"""
        mock_kg = MagicMock()
        entity = MagicMock()
        entity.to_dict.return_value = {
            "name": "实体1",
            "entity_type": "concept",
            "properties": {},
            "confidence": 0.9,
            "first_seen": 1.0,
            "last_seen": 2.0,
            "mention_count": 3,
        }
        relation = MagicMock()
        relation.to_dict.return_value = {
            "source": "e1",
            "target": "e2",
            "relation_type": "related",
            "properties": {},
            "confidence": 0.8,
            "created_at": 1.5,
        }
        mock_kg.get_full_graph.return_value = ([entity], [relation])

        mock_ctx = self._mock_context_manager(_knowledge_graph=mock_kg)
        mock_create_ctx.return_value = mock_ctx

        resp = self.client.get("/api/v1/context/knowledge-graph/detail/", {"session_id": "sess-001"})

        self.assertEqual(resp.status_code, 200)
        self.assertIn("data", resp.data)

    @patch("Django_xm.apps.context_manager.views.get_store")
    @patch("Django_xm.apps.context_manager.views.create_context_manager")
    def test_get_detail_missing_session_id_ok(self, mock_create_ctx, mock_get_store):
        """GET knowledge-graph/detail 无 session_id → 200（session_id 为可选参数）。"""
        mock_kg = MagicMock()
        mock_kg.get_full_graph.return_value = ([], [])
        mock_ctx = self._mock_context_manager(_knowledge_graph=mock_kg)
        mock_create_ctx.return_value = mock_ctx

        resp = self.client.get("/api/v1/context/knowledge-graph/detail/")

        self.assertEqual(resp.status_code, 200)

    @patch("Django_xm.apps.context_manager.views.get_store")
    @patch("Django_xm.apps.context_manager.views.create_context_manager")
    def test_get_detail_kg_not_enabled_503(self, mock_create_ctx, mock_get_store):
        """GET knowledge-graph/detail 知识图谱未启用 → 503。"""
        mock_ctx = self._mock_context_manager(_knowledge_graph=None)
        mock_create_ctx.return_value = mock_ctx

        resp = self.client.get("/api/v1/context/knowledge-graph/detail/", {"session_id": "sess-001"})

        self.assertEqual(resp.status_code, 503)


class CapabilityConfigViewTests(ContextManagerViewTestBase):
    """capability_config_view GET /api/v1/context/capability-config/。"""

    def test_get_capability_config_success(self):
        """GET capability-config 成功（AllowAny，无需认证）。"""
        self.client.force_authenticate(user=None)
        resp = self.client.get("/api/v1/context/capability-config/")

        self.assertEqual(resp.status_code, 200)
        self.assertIn("data", resp.data)
        self.assertIn("available_capabilities", resp.data["data"])

    def test_get_capability_config_with_agent_type(self):
        """GET capability-config?agent_type=base 成功：返回默认能力列表。"""
        resp = self.client.get("/api/v1/context/capability-config/", {"agent_type": "base"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["data"]["agent_type"], "base")
