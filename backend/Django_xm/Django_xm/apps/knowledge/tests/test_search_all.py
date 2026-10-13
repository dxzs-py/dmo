"""跨库聚合检索 SearchAllView 测试（P3 跨库搜索后端聚合）。

覆盖：
- RRF 排序正确性：构造两索引 mock 结果，断言融合序与融合分数学值
  （融合分 = Σ 1/(60 + 库内排名)，跨库绝对 score 不参与比较）
- 部分索引失败不中断：单库异常计入 failed_indexes，正常库结果照常返回
- query embedding 仅计算一次：多索引检索共用同一次 embed_query 与预计算向量
- index_names 过滤：仅检索指定索引，其余不触碰
- 归属校验：请求非本人索引 → ValidationError（400）
- 空索引（num_documents=0）跳过：不计入 searched/failed，不触发 embedding
- 未认证 → 401

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.knowledge.tests.test_search_all --noinput
（单测类 mock 模块绑定无 DB 依赖；401 测试为 Django TestCase 需测试库）
"""

import asyncio
import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from Django_xm.apps.knowledge.views_rag import SearchAllView

SEARCH_ALL_URL = "/api/v1/knowledge/search-all/"
_VIEWS_RAG = "Django_xm.apps.knowledge.views_rag"


def _make_doc(content: str, source: str):
    from langchain_core.documents import Document

    return Document(page_content=content, metadata={"source": source, "original_name": source})


class _FakeUser:
    id = 1


class _FakeRequest:
    """最小请求替身：视图仅访问 request.data 与 request.user（权限由 dispatch 层承担）。"""

    def __init__(self, data: dict):
        self.data = data
        self.user = _FakeUser()


class SearchAllViewTests(unittest.TestCase):
    """SearchAllView 单元表征：RRF 融合 / 失败隔离 / embedding 单次 / 过滤与归属。"""

    def _post(self, data: dict, stores: dict, records: list, on_stores=None):
        """打桩视图模块绑定后调用 post，返回 (response, store_mocks, embeddings)。

        stores: {索引全名: [(content, source, score), ...]}（映射为
                similarity_search_with_score_by_vector 的返回值）；
        records: [(索引全名, num_documents, embedding_dimension)]（IndexMetadata 查询结果）；
        on_stores: 可选回调，在执行 post 前对 store_mocks 做额外配置（如注入异常）。
        """
        store_mocks = {}
        for full_name, items in stores.items():
            store = mock.MagicMock(name=f"store_{full_name}")
            store.similarity_search_with_score_by_vector.return_value = [
                (_make_doc(content, source), score) for content, source, score in items
            ]
            store_mocks[full_name] = store
        if on_stores is not None:
            on_stores(store_mocks)

        manager = mock.MagicMock(name="IndexManager")
        manager.load_index.side_effect = lambda name, embeddings: store_mocks[name]
        # C-2arch：views_rag 改经 get_score_semantics 取后端语义声明
        #（原 get_store_type MagicMock ≠ "pgvector" → "similarity"，等价保持）
        manager.get_score_semantics.return_value = "similarity"

        metadata_cls = mock.MagicMock(name="IndexMetadata")
        metadata_cls.objects.filter.return_value.values_list.return_value = list(records)

        embeddings = mock.MagicMock(name="embeddings")
        embeddings.embed_query.return_value = [0.1] * 8

        ai_settings = mock.MagicMock(name="ai_settings")
        ai_settings.rag_rrf_constant = 60

        with (
            mock.patch(f"{_VIEWS_RAG}.IndexMetadata", metadata_cls),
            mock.patch(f"{_VIEWS_RAG}.IndexManager", return_value=manager),
            mock.patch(f"{_VIEWS_RAG}.get_embeddings", return_value=embeddings),
            mock.patch(f"{_VIEWS_RAG}.ai_settings", ai_settings),
        ):
            # RP-01：post 已 async 化（重活经 sync_to_async 调度），直调时需事件循环
            response = asyncio.run(SearchAllView().post(_FakeRequest(data)))

        return response, store_mocks, embeddings

    def test_rrf_fusion_order_and_math(self):
        """RRF 融合序与数学值：

        kb1: A(0.9)→rank1, B(0.8)→rank2；kb2: B(0.95)→rank1, C(0.7)→rank2
        融合分：B = 1/61 + 1/62 ≈ 0.032522 > A = 1/61 ≈ 0.016393 > C = 1/62 ≈ 0.016129
        期望序 [B, A, C]；跨库绝对 score（0.95 最高）不决定顺序。
        """
        response, _, _ = self._post(
            {"query": "查询", "k": 4},
            stores={
                "user_1_kb1": [("A 内容", "a.md", 0.9), ("B 内容", "b.md", 0.8)],
                "user_1_kb2": [("B 内容", "b2.md", 0.95), ("C 内容", "c.md", 0.7)],
            },
            records=[("user_1_kb1", 3, 1024), ("user_1_kb2", 3, 1024)],
        )

        data = response.data["data"]
        self.assertEqual(data["searched_indexes"], 2)
        self.assertEqual(data["failed_indexes"], 0)
        self.assertEqual(
            [(item["content"], item["fused_rank"]) for item in data["results"]],
            [("B 内容", 1), ("A 内容", 2), ("C 内容", 3)],
        )
        # 展示字段取首次出现（kb1 内 rank2 的 B），score 为原库 score；
        # metadata 经 C-7 白名单（source 服务器路径键剥离，original_name 保留）
        first = data["results"][0]
        self.assertEqual(first["source_index"], "kb1")
        self.assertEqual(first["score"], 0.8)
        self.assertEqual(first["metadata"], {"original_name": "b.md"})
        self.assertNotIn("source", first["metadata"])

    def test_rrf_global_top_k_slice(self):
        """全局 top-k 截断：k=1 时仅保留融合分最高条目。"""
        response, _, _ = self._post(
            {"query": "查询", "k": 1},
            stores={
                "user_1_kb1": [("A 内容", "a.md", 0.9)],
                "user_1_kb2": [("B 内容", "b.md", 0.8)],
            },
            records=[("user_1_kb1", 2, 1024), ("user_1_kb2", 2, 1024)],
        )

        data = response.data["data"]
        # 两库各自 rank1：融合分同为 1/61，稳定性保证按库遍历序取 A
        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["results"][0]["content"], "A 内容")
        self.assertEqual(data["results"][0]["fused_rank"], 1)

    def test_partial_index_failure_does_not_interrupt(self):
        """单库异常计入 failed_indexes，正常库结果照常返回（200 不抛 500）。"""

        def _inject_failure(stores):
            stores["user_1_kb2"].similarity_search_with_score_by_vector.side_effect = RuntimeError("boom")

        response, _, _ = self._post(
            {"query": "查询", "k": 4},
            stores={
                "user_1_kb1": [("A 内容", "a.md", 0.9)],
                "user_1_kb2": [],
            },
            records=[("user_1_kb1", 2, 1024), ("user_1_kb2", 2, 1024)],
            on_stores=_inject_failure,
        )

        data = response.data["data"]
        self.assertEqual(data["searched_indexes"], 1)
        self.assertEqual(data["failed_indexes"], 1)
        self.assertEqual(
            [(item["content"], item["fused_rank"]) for item in data["results"]],
            [("A 内容", 1)],
        )

    def test_all_indexes_failed_returns_empty_success(self):
        """全部失败：200 + 空数组 + failed_indexes=n，不抛 500。"""
        response, _, _ = self._post(
            {"query": "查询", "k": 4},
            stores={"user_1_kb1": []},
            records=[("user_1_kb1", 2, 1024)],
            on_stores=lambda stores: setattr(
                stores["user_1_kb1"].similarity_search_with_score_by_vector,
                "side_effect",
                RuntimeError("boom"),
            ),
        )

        data = response.data["data"]
        self.assertEqual(data["results"], [])
        self.assertEqual(data["searched_indexes"], 0)
        self.assertEqual(data["failed_indexes"], 1)

    def test_all_indexes_failed_message_reports_failure(self):
        """E5-09：全部检索失败时协议 message 必须是故障文案，不得宣称"操作成功"。"""
        response, _, _ = self._post(
            {"query": "查询", "k": 4},
            stores={"user_1_kb1": []},
            records=[("user_1_kb1", 2, 1024)],
            on_stores=lambda stores: setattr(
                stores["user_1_kb1"].similarity_search_with_score_by_vector,
                "side_effect",
                RuntimeError("boom"),
            ),
        )

        self.assertEqual(response.data["code"], 200)
        self.assertEqual(response.data["message"], "检索服务暂时不可用，请稍后重试")

    def test_embedding_global_failure_message_reports_outage(self):
        """E5-09：query embedding 全局故障（全库检索不可达）时 message 必须是故障文案。"""

        embeddings = mock.MagicMock(name="embeddings")
        embeddings.embed_query.side_effect = RuntimeError("embedding 服务不可用")

        manager = mock.MagicMock(name="IndexManager")
        metadata_cls = mock.MagicMock(name="IndexMetadata")
        metadata_cls.objects.filter.return_value.values_list.return_value = [
            ("user_1_kb1", 2, 1024)
        ]
        ai_settings = mock.MagicMock(name="ai_settings")
        ai_settings.rag_rrf_constant = 60

        with (
            mock.patch(f"{_VIEWS_RAG}.IndexMetadata", metadata_cls),
            mock.patch(f"{_VIEWS_RAG}.IndexManager", return_value=manager),
            mock.patch(f"{_VIEWS_RAG}.get_embeddings", return_value=embeddings),
            mock.patch(f"{_VIEWS_RAG}.ai_settings", ai_settings),
        ):
            # RP-01：post 已 async 化（重活经 sync_to_async 调度），直调时需事件循环
            response = asyncio.run(SearchAllView().post(_FakeRequest({"query": "查询", "k": 4})))

        data = response.data["data"]
        self.assertEqual(data["results"], [])
        self.assertEqual(data["searched_indexes"], 0)
        self.assertEqual(data["failed_indexes"], 1)
        self.assertEqual(response.data["message"], "检索服务暂时不可用，请稍后重试")

    def test_embedding_computed_once_and_reused(self):
        """query embedding 仅计算 1 次，多索引检索共用同一预计算向量（by-vector 路径）。"""
        response, store_mocks, embeddings = self._post(
            {"query": "查询", "k": 4},
            stores={
                "user_1_kb1": [("A 内容", "a.md", 0.9)],
                "user_1_kb2": [("B 内容", "b.md", 0.8)],
            },
            records=[("user_1_kb1", 2, 1024), ("user_1_kb2", 2, 1024)],
        )

        self.assertEqual(response.data["code"], 200)
        embeddings.embed_query.assert_called_once_with("查询")
        expected_embedding = embeddings.embed_query.return_value
        for store in store_mocks.values():
            store.similarity_search_with_score_by_vector.assert_called_once()
            self.assertEqual(
                store.similarity_search_with_score_by_vector.call_args.args[0], expected_embedding
            )
            self.assertEqual(store.similarity_search_with_score_by_vector.call_args.kwargs.get("k"), 4)
            # 不允许回退到 by-query 路径（否则 embedding 被重复计算）
            store.similarity_search_with_score.assert_not_called()

    def test_index_names_filter_limits_targets(self):
        """index_names 过滤：仅检索指定索引，其余索引不触碰。"""
        response, store_mocks, _ = self._post(
            {"query": "查询", "k": 4, "index_names": ["kb2"]},
            stores={
                "user_1_kb1": [("A 内容", "a.md", 0.9)],
                "user_1_kb2": [("B 内容", "b.md", 0.8)],
                "user_1_kb3": [("C 内容", "c.md", 0.7)],
            },
            records=[("user_1_kb1", 2, 1024), ("user_1_kb2", 2, 1024), ("user_1_kb3", 2, 1024)],
        )

        data = response.data["data"]
        self.assertEqual(data["searched_indexes"], 1)
        self.assertEqual(
            [(item["content"], item["source_index"]) for item in data["results"]],
            [("B 内容", "kb2")],
        )
        store_mocks["user_1_kb1"].similarity_search_with_score_by_vector.assert_not_called()
        store_mocks["user_1_kb3"].similarity_search_with_score_by_vector.assert_not_called()

    def test_index_names_outside_ownership_rejected(self):
        """归属校验：请求非本人索引（IndexMetadata 无记录）→ ValidationError。"""
        with self.assertRaises(ValidationError):
            self._post(
                {"query": "查询", "k": 4, "index_names": ["ghost"]},
                stores={},
                records=[("user_1_kb1", 2, 1024)],
            )

    def test_empty_indexes_skipped_without_embedding(self):
        """空索引（num_documents=0）跳过：不计入 searched/failed，不触发 embedding。"""
        response, store_mocks, embeddings = self._post(
            {"query": "查询", "k": 4},
            stores={"user_1_kb1": [("A 内容", "a.md", 0.9)]},
            records=[("user_1_kb1", 0, 1024)],
        )

        data = response.data["data"]
        self.assertEqual(data["results"], [])
        self.assertEqual(data["searched_indexes"], 0)
        self.assertEqual(data["failed_indexes"], 0)
        embeddings.embed_query.assert_not_called()
        store_mocks["user_1_kb1"].similarity_search_with_score_by_vector.assert_not_called()


class SearchAllViewAuthTests(TestCase):
    """鉴权表征：未认证请求 401。"""

    def test_unauthenticated_returns_401(self):
        resp = APIClient().post(SEARCH_ALL_URL, {"query": "查询"}, format="json")
        self.assertEqual(resp.status_code, 401)


class RagQueryStreamAuthTests(TestCase):
    """C-6：rag_query_stream 挂 IsAuthenticated（token 不再允许走 URL 查询参数）。

    前端 fetchSSE 已注入 Authorization 头（utils/sse.js buildHeaders），
    query-param 认证仅剩 <a href> 下载场景，SSE 端点属权限残留。
    """

    def test_unauthenticated_returns_401(self):
        resp = APIClient().post(
            "/api/v1/knowledge/query/stream/", {"query": "查询", "index_name": "kb1"}, format="json"
        )
        self.assertEqual(resp.status_code, 401)

    def test_valid_query_param_token_no_longer_grants_access(self):
        """判别用例（修复前红）：合法 JWT 放查询串，query-param 认证放行（非 401）、
        修复后统一 401（token 不应出现在 URL，避免进访问日志/浏览器历史）。"""
        from rest_framework_simplejwt.tokens import AccessToken

        user = get_user_model().objects.create_user(username="qpstream", password="pw123456")
        token = str(AccessToken.for_user(user))
        resp = APIClient().post(
            f"/api/v1/knowledge/query/stream/?token={token}",
            {"query": "查询", "index_name": "kb1"},
            format="json",
        )
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
