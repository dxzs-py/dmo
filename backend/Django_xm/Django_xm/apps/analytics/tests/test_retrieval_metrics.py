"""RAG 检索灰度埋点（P1）回归。

锁定：
1. 无用户（系统内部调用）不投递；
2. payload 形状：rag.query / rag 分类 / 配置指纹（阈值、召回窗口、rerank、hybrid）；
3. broker/序列化失败不得抛出（埋点零阻塞）；
4. 采样率为 0 时完全不投递。

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.apps.analytics.tests.test_retrieval_metrics --noinput
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from Django_xm.apps.analytics.services.retrieval_metrics import record_retrieval
from Django_xm.apps.knowledge.config import settings

_TASKS = "Django_xm.tasks.analytics_tasks"


class RetrievalMetricsTests(unittest.TestCase):
    def test_skips_without_user(self):
        with mock.patch(f"{_TASKS}.track_event") as task:
            record_retrieval(None, kind="strict")
        task.delay.assert_not_called()

    def test_payload_shape_and_config_fingerprint(self):
        with mock.patch(f"{_TASKS}.track_event") as task:
            record_retrieval(
                7,
                kind="strict",
                index_name="user_7_kb",
                k=4,
                hits=3,
                sources=2,
                degraded=True,
                duration_ms=12.7,
            )

        payload = task.delay.call_args.args[0]
        self.assertEqual(payload["user_id"], 7)
        self.assertEqual(payload["event_type"], "rag.query")
        self.assertEqual(payload["event_category"], "rag")
        self.assertEqual(payload["resource_id"], "user_7_kb")
        self.assertEqual(payload["duration_ms"], 12)
        self.assertTrue(payload["is_success"])

        metadata = payload["metadata"]
        self.assertEqual(metadata["kind"], "strict")
        self.assertEqual(metadata["hits"], 3)
        self.assertEqual(metadata["sources"], 2)
        self.assertTrue(metadata["degraded"])
        self.assertFalse(metadata["cached"])
        self.assertEqual(metadata["threshold"], settings.rag_score_threshold)
        self.assertEqual(metadata["recall_k"], settings.rag_recall_k)
        self.assertEqual(metadata["rerank"], settings.retriever_use_reranker)
        self.assertEqual(metadata["hybrid"], settings.rag_hybrid_enabled)

    def test_error_marks_failure(self):
        with mock.patch(f"{_TASKS}.track_event") as task:
            record_retrieval(7, kind="strict", error="boom")

        payload = task.delay.call_args.args[0]
        self.assertFalse(payload["is_success"])
        self.assertEqual(payload["error_message"], "boom")

    def test_broker_failure_is_silent(self):
        with mock.patch(f"{_TASKS}.track_event") as task:
            task.delay.side_effect = RuntimeError("broker down")
            record_retrieval(7, kind="strict")  # 不得抛出

    def test_sampling_zero_skips(self):
        original = settings.analytics_retrieval_sample_rate
        self.addCleanup(setattr, settings, "analytics_retrieval_sample_rate", original)
        settings.analytics_retrieval_sample_rate = 0.0

        with mock.patch(f"{_TASKS}.track_event") as task:
            record_retrieval(7, kind="strict")

        task.delay.assert_not_called()


if __name__ == "__main__":
    unittest.main()
