"""RAG 运行时设置单元测试（SystemConfig 覆盖 + 归一 + 保存合并）。

锁定：
1. 无 SystemConfig 时，默认值动态取 settings（patch settings 立即生效）；
2. SystemConfig 合法值覆盖默认值；
3. 非法值（越界/错类型/未知枚举）回退当前值或夹取；
4. DB 异常 → 返回默认值，不抛；
5. 保存只落白名单字段，未知字段忽略。

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.apps.knowledge.tests.test_rag_settings --noinput
"""

import os
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.test import SimpleTestCase

from Django_xm.apps.knowledge import rag_settings
from Django_xm.apps.knowledge.config import settings

_GET = "Django_xm.apps.ai_engine.models.SystemConfig.get_value"
_SET = "Django_xm.apps.ai_engine.models.SystemConfig.set_value"
_VIEWS = "Django_xm.apps.knowledge.views_settings"


class GetRagSettingsTests(unittest.TestCase):
    def test_defaults_from_settings(self):
        with mock.patch(_GET, return_value={}):
            runtime = rag_settings.get_rag_runtime_settings()

        self.assertEqual(runtime["use_reranker"], settings.retriever_use_reranker)
        self.assertEqual(runtime["score_threshold"], settings.rag_score_threshold)
        self.assertEqual(runtime["recall_k"], settings.rag_recall_k)
        self.assertEqual(runtime["dedupe_mode"], settings.rag_dedupe_mode)
        self.assertEqual(runtime["citation_verify_model_mode"], settings.rag_citation_verify_model_mode)

    def test_system_config_overrides(self):
        stored = {
            "use_reranker": True,
            "score_threshold": 0.5,
            "recall_k": 50,
            "hybrid_enabled": True,
            "dedupe_mode": "embedding",
        }
        with mock.patch(_GET, return_value=stored):
            runtime = rag_settings.get_rag_runtime_settings()

        self.assertTrue(runtime["use_reranker"])
        self.assertEqual(runtime["score_threshold"], 0.5)
        self.assertEqual(runtime["recall_k"], 50)
        self.assertTrue(runtime["hybrid_enabled"])
        self.assertEqual(runtime["dedupe_mode"], "embedding")

    def test_invalid_values_fallback_or_clamp(self):
        stored = {
            "score_threshold": 2.5,       # 夹到 1.0
            "recall_k": 0,                # 夹到下限 1
            "rerank_top_n": 999,          # 夹到上限 50
            "dedupe_mode": "bad",         # 回退默认
            "rerank_model": "bad",        # 回退默认
            "use_reranker": "maybe",      # 回退默认
        }
        with mock.patch(_GET, return_value=stored):
            runtime = rag_settings.get_rag_runtime_settings()

        self.assertEqual(runtime["score_threshold"], 1.0)
        self.assertEqual(runtime["recall_k"], 1)
        self.assertEqual(runtime["rerank_top_n"], 50)
        self.assertEqual(runtime["dedupe_mode"], settings.rag_dedupe_mode)
        self.assertEqual(runtime["rerank_model"], settings.rag_rerank_model)
        self.assertEqual(runtime["use_reranker"], settings.retriever_use_reranker)

    def test_db_failure_returns_defaults(self):
        with mock.patch(_GET, side_effect=RuntimeError("db down")):
            runtime = rag_settings.get_rag_runtime_settings()

        self.assertEqual(runtime["score_threshold"], settings.rag_score_threshold)
        self.assertIn("use_reranker", runtime)


class SaveRagSettingsTests(unittest.TestCase):
    def test_merge_and_persist(self):
        with (
            mock.patch(_GET, return_value={}),
            mock.patch(_SET) as setter,
        ):
            result = rag_settings.save_rag_runtime_settings(
                {"use_reranker": True, "recall_k": 999}
            )

        self.assertTrue(result["use_reranker"])
        self.assertEqual(result["recall_k"], 200)  # 夹到上限
        setter.assert_called_once()
        key, payload = setter.call_args.args
        self.assertEqual(key, "rag_retrieval")
        self.assertEqual(payload["use_reranker"], True)
        self.assertEqual(payload["recall_k"], 200)

    def test_unknown_fields_ignored(self):
        with (
            mock.patch(_GET, return_value={}),
            mock.patch(_SET) as setter,
        ):
            rag_settings.save_rag_runtime_settings({"unknown": 1, "score_threshold": 0.4})

        _, payload = setter.call_args.args
        self.assertNotIn("unknown", payload)
        self.assertEqual(payload["score_threshold"], 0.4)


class AISettingsRagApiTests(SimpleTestCase):
    """AISettingsView：GET 下发 rag_retrieval，PUT 保存并回读。"""

    def _request(self, payload=None):
        from rest_framework.parsers import JSONParser
        from rest_framework.request import Request
        from rest_framework.test import APIRequestFactory

        factory = APIRequestFactory()
        django_request = factory.post("/api/v1/ai-engine/settings/", payload or {}, format="json")
        request = Request(django_request, parsers=[JSONParser()])
        request.user = SimpleNamespace(is_authenticated=True, id=1, pk=1, is_staff=True)
        return request

    def test_get_returns_rag_retrieval(self):
        from Django_xm.apps.knowledge.views_settings import AISettingsView

        with (
            mock.patch(f"{_VIEWS}.get_available_providers", return_value=[]),
            mock.patch(f"{_VIEWS}._get_embedding_providers", return_value=[]),
            mock.patch.object(AISettingsView, "_get_index_dimensions", return_value=[]),
            mock.patch(_GET, return_value={}),
        ):
            response = AISettingsView().get(self._request())

        self.assertEqual(response.status_code, 200)
        payload = response.data["data"]["rag_retrieval"]
        self.assertIn("use_reranker", payload)
        self.assertIn("score_threshold", payload)

    def test_put_saves_rag_retrieval(self):
        from Django_xm.apps.knowledge.views_settings import AISettingsView

        with mock.patch(_GET, return_value={}), mock.patch(_SET) as setter:
            response = AISettingsView().put(
                self._request({"rag_retrieval": {"use_reranker": True, "recall_k": 50}})
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["data"]["rag_retrieval"]["use_reranker"])
        self.assertEqual(response.data["data"]["rag_retrieval"]["recall_k"], 50)
        setter.assert_called_once()


if __name__ == "__main__":
    unittest.main()
