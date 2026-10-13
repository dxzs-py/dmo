"""E5-01：FallbackEmbedding 降级事件 append-only 契约。

修复前缺口（rag_audit_error E5-01）：``_call_with_fallback``/``_acall_with_fallback``
每次调用开头清空 ``_fallback_events``——分批 embed（chunks>100 时多次调用）与维度
探测（embed_query("dimension_test")）都会擦除先前的降级事件 → kb_service 上传链
消费点读到空列表 → fallback_info 恒空、SystemConfig 不更新、用户零感知。

修复语义：``_fallback_events`` 改为 append-only（实例级全量历史）。即时构建
（实例缓存退役）下实例生命周期=单请求，无跨请求误报；消费方 kb_service 读到的
是本次上传全过程中发生过的全部降级切换。

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.apps.ai_engine.tests.test_fallback_events_append_only
"""

import os
import unittest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.test import SimpleTestCase

from Django_xm.apps.ai_engine.services.embedding_factory import FallbackEmbedding


class _AlwaysFail:
    """恒失败的 stub provider（模拟主 provider 宕机）。"""

    def embed_query(self, text):
        raise ConnectionError("primary provider down")

    def embed_documents(self, texts):
        raise ConnectionError("primary provider down")

    async def aembed_query(self, text):
        raise ConnectionError("primary provider down")

    async def aembed_documents(self, texts):
        raise ConnectionError("primary provider down")


class _AlwaysOk:
    """恒成功的 stub provider（备用 provider）。"""

    def embed_query(self, text):
        return [0.0] * 4

    def embed_documents(self, texts):
        return [[0.0] * 4 for _ in texts]

    async def aembed_query(self, text):
        return [0.0] * 4

    async def aembed_documents(self, texts):
        return [[0.0] * 4 for _ in texts]


class FallbackEventsAppendOnlyTests(SimpleTestCase):
    """E5-01：降级事件跨调用累积（修复前每次调用头清空）。"""

    def _make(self):
        return FallbackEmbedding(
            [_AlwaysFail(), _AlwaysOk()],
            ["Primary", "Backup"],
            provider_ids=["p1", "p2"],
        )

    def test_events_survive_subsequent_successful_call(self):
        """首次切换后再次成功调用，事件不被擦除（修复前第二次调用读到的 events=[]）。"""
        fb = self._make()
        fb.embed_query("first")  # 触发主→备切换
        self.assertEqual(len(fb.get_fallback_events()), 1)

        fb.embed_query("second")  # 备用成功，无新切换
        events = fb.get_fallback_events()
        self.assertEqual(
            len(events),
            1,
            "后续成功调用不得清空先前的降级事件（E5-01 擦除缺口）",
        )
        self.assertEqual(events[0]["from_provider"], "p1")
        self.assertEqual(events[0]["to_provider"], "p2")
        self.assertIn("primary provider down", events[0]["reason"])

    def test_upload_sequence_simulation_keeps_events(self):
        """复现 E5-01 时序：embed_documents（触发切换）→ 维度探测 embed_query → 事件保留。"""
        fb = self._make()
        fb.embed_documents(["doc1", "doc2"])  # 上传分批 embed（触发切换）
        fb.embed_query("dimension_test")  # index_service 维度探测（修复前此处清空事件）

        events = fb.get_fallback_events()
        self.assertEqual(len(events), 1, "维度探测后事件必须保留（E5-01 消费链断点）")

    def test_multiple_fallbacks_accumulate(self):
        """多次失败-恢复循环下事件累积（append-only 语义）。"""
        fb = self._make()
        fb.embed_query("a")
        fb.embed_query("b")
        fb.embed_query("c")
        # 同一实例后续调用均走活跃备用 provider，不再新增切换事件，
        # 但首次切换事件持续可读
        self.assertEqual(len(fb.get_fallback_events()), 1)

    def test_no_fallback_no_events(self):
        """全程无降级 → 事件列表为空（不误报）。"""
        fb = FallbackEmbedding([_AlwaysOk()], ["Only"], provider_ids=["p1"])
        fb.embed_query("x")
        fb.embed_documents(["y"])

        self.assertEqual(fb.get_fallback_events(), [])

    def test_get_fallback_events_returns_copy(self):
        """消费方拿到的是拷贝（读取不影响内部状态）。"""
        fb = self._make()
        fb.embed_query("x")
        snapshot = fb.get_fallback_events()
        snapshot.clear()

        self.assertEqual(len(fb.get_fallback_events()), 1)


class FallbackEventsAsyncTests(unittest.IsolatedAsyncioTestCase):
    """async 通道同语义。"""

    async def test_async_events_survive_subsequent_call(self):
        fb = FallbackEmbedding(
            [_AlwaysFail(), _AlwaysOk()],
            ["Primary", "Backup"],
            provider_ids=["p1", "p2"],
        )
        await fb.aembed_query("first")
        self.assertEqual(len(fb.get_fallback_events()), 1)

        await fb.aembed_query("second")
        self.assertEqual(len(fb.get_fallback_events()), 1, "async 通道同样不得擦除")


# CachedEmbeddingsPassThroughTests（CachedEmbeddings 透传链用例）已迁
# knowledge/tests/test_embedding_cache_key.py（环 2 破除任务 4.2：
# 消 ai_engine tests → knowledge 测试放置边，被测组件归属对齐）。


if __name__ == "__main__":
    unittest.main()
