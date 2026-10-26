"""意图分类 / 检索结果缓存 / 附件索引 TTL 清理的单元测试。

覆盖：
- ``query_intent``：关键词判定、显式意图优先、开关关闭回退 precise
- ``retrieval_cache``：缓存键含 intent、读写往返、按 collection 失效
- ``attachment_index_cleanup``：只清理前缀匹配且超期的索引；dry_run 不删除
"""

import os
import unittest
from datetime import timedelta
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.test import TestCase
from django.utils import timezone
from langchain_core.documents import Document

from Django_xm.apps.knowledge.services import retrieval_cache
from Django_xm.apps.knowledge.services.query_intent import (
    COMPREHENSIVE,
    PRECISE,
    classify_query_intent,
    resolve_intent,
)

_CACHE_SVC = "Django_xm.apps.knowledge.services.retrieval_cache.CacheService"


class QueryIntentTests(unittest.TestCase):
    """意图分类规则。"""

    def test_summary_query_is_comprehensive(self):
        for query in (
            "这个知识库主要讲了什么",
            "帮我总结一下这份附件",
            "整篇文档的结构是什么",
            "Redis 持久化有哪些方式",
            "对比 RDB 和 AOF 的差异",
        ):
            self.assertEqual(classify_query_intent(query), COMPREHENSIVE, query)

    def test_factual_query_is_precise(self):
        for query in (
            "redis.conf 里 appendonly 默认值是多少",
            "怎么启动 redis-stack 容器",
            "L1 学习路径包含哪一步",  # 无总结类信号词
        ):
            self.assertEqual(classify_query_intent(query), PRECISE, query)

    def test_empty_query_falls_back_to_precise(self):
        self.assertEqual(classify_query_intent(""), PRECISE)
        self.assertEqual(classify_query_intent("   "), PRECISE)

    def test_explicit_intent_wins(self):
        # 显式意图不改写（即使与关键词判定相反）
        self.assertEqual(resolve_intent("precise", "总结一下全文"), PRECISE)
        self.assertEqual(resolve_intent("comprehensive", "appendonly 默认值"), COMPREHENSIVE)

    def test_auto_uses_rule_classification(self):
        self.assertEqual(resolve_intent("auto", "总结一下全文"), COMPREHENSIVE)
        self.assertEqual(resolve_intent(None, "appendonly 默认值"), PRECISE)

    def test_disabled_switch_forces_precise(self):
        runtime = {
            "intent_classification_enabled": False,
        }
        with mock.patch(
            "Django_xm.apps.knowledge.rag_settings.get_rag_runtime_settings", return_value=runtime
        ):
            self.assertEqual(resolve_intent("auto", "总结一下全文"), PRECISE)


class RetrievalCacheTests(unittest.TestCase):
    """检索结果缓存键/读写/失效。"""

    DOCS = (
        Document(page_content="正文", metadata={"source": "a.md", "chunk_index": 0}),
    )

    def test_key_contains_intent_and_k(self):
        precise = retrieval_cache.generate_agent_docs_key("user_1_kb", "q", 5, "precise")
        comprehensive = retrieval_cache.generate_agent_docs_key("user_1_kb", "q", 12, "comprehensive")
        self.assertNotEqual(precise, comprehensive)
        self.assertIn(":k5:precise", precise)
        self.assertIn(":k12:comprehensive", comprehensive)
        self.assertTrue(precise.startswith("rag_agent_docs:user_1_kb:"))

    def test_cache_roundtrip_rebuilds_documents(self):
        with mock.patch.object(retrieval_cache.CacheService, "set", return_value=True) as set_mock, \
             mock.patch.object(
                 retrieval_cache.CacheService,
                 "get",
                 side_effect=lambda key, default=None: set_mock.call_args.args[1],
             ):
            retrieval_cache.cache_documents(
                collection_name="user_1_kb",
                query="q",
                k=5,
                intent="precise",
                ttl=60,
                docs=self.DOCS,
            )
            loaded = retrieval_cache.get_cached_documents(
                collection_name="user_1_kb", query="q", k=5, intent="precise"
            )

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].page_content, "正文")
        self.assertEqual(loaded[0].metadata["source"], "a.md")

    def test_empty_docs_not_cached(self):
        with mock.patch.object(retrieval_cache.CacheService, "set") as set_mock:
            retrieval_cache.cache_documents(
                collection_name="user_1_kb", query="q", k=5, intent="precise", ttl=60, docs=[]
            )
        set_mock.assert_not_called()

    def test_ttl_zero_disables_cache(self):
        with mock.patch.object(retrieval_cache.CacheService, "set") as set_mock:
            retrieval_cache.cache_documents(
                collection_name="user_1_kb",
                query="q",
                k=5,
                intent="precise",
                ttl=0,
                docs=self.DOCS,
            )
        set_mock.assert_not_called()
        self.assertIsNone(
            retrieval_cache.get_cached_documents(
                collection_name="", query="q", k=5, intent="precise"
            )
        )

    def test_invalidate_collection_deletes_by_prefix(self):
        with mock.patch.object(
            retrieval_cache.CacheService, "delete_pattern", return_value=3
        ) as delete_mock:
            count = retrieval_cache.invalidate_collection("user_1_kb")
        self.assertEqual(count, 3)
        self.assertEqual(delete_mock.call_args.args[0], "rag_agent_docs:user_1_kb:*")


class MergeContextTests(unittest.TestCase):
    """跨 scope 合并阶段不得再次按源收口（曾把邻块砍到 2 块）。"""

    def test_merge_keeps_all_chunks_of_single_source(self):
        from Django_xm.apps.knowledge.services.rag_context_pipeline import merge_context

        docs = [
            Document(page_content=f"c{i}", metadata={"source": "a.md", "chunk_index": i})
            for i in range(6)
        ]
        out = merge_context(docs, apply_budget=False)

        self.assertEqual(len(out.docs), 6)
        self.assertEqual(out.sources[0]["chunk_indexes"], [0, 1, 2, 3, 4, 5])

    def test_retrieve_scopes_does_not_recap_per_source(self):
        from Django_xm.apps.knowledge.services.rag_service import (
            RagResult,
            RagScope,
            RagService,
        )

        docs = [
            Document(page_content=f"c{i}", metadata={"source": "a.md", "chunk_index": i})
            for i in range(6)
        ]
        runtime = {
            "context_max_tokens": 3000,
            "tool_max_context_tokens": 2000,
            "intent_classification_enabled": True,
        }
        bundles = [(RagScope("persistent_kb", "user_1_kb"), mock.MagicMock(), None)]
        with mock.patch.object(
            RagService, "retrieve", return_value=RagResult(docs=docs, metrics={"cached": False})
        ), mock.patch(
            "Django_xm.apps.knowledge.rag_settings.get_rag_runtime_settings",
            return_value=runtime,
        ):
            result = RagService().retrieve_scopes(
                "这个知识库主要讲了什么", bundles=bundles, k=4, apply_budget=False
            )

        # 旧实现会被 build_context 的 max_chunks_per_source=2 砍到 2 块
        self.assertEqual(len(result.docs), 6)
        self.assertEqual(result.metrics["intent"], COMPREHENSIVE)


class AttachmentIndexCleanupTests(TestCase):
    """附件索引 TTL 清理：前缀 + 超期筛选，dry_run 不删除。"""

    def setUp(self):
        from Django_xm.apps.knowledge.models import IndexMetadata

        self.IndexMetadata = IndexMetadata
        self.old_name = "attach_old-session"
        self.fresh_name = "attach_fresh-session"
        self.kb_name = "user_1_kb"

        for name in (self.old_name, self.fresh_name, self.kb_name):
            IndexMetadata.objects.filter(name=name).delete()
        self.old = IndexMetadata.objects.create(name=self.old_name, description="attachment_ids:1")
        self.fresh = IndexMetadata.objects.create(name=self.fresh_name, description="attachment_ids:2")
        self.kb = IndexMetadata.objects.create(name=self.kb_name, description="kb")
        # 只让 old 超期
        self.IndexMetadata.objects.filter(pk=self.old.pk).update(
            updated_at=timezone.now() - timedelta(days=30)
        )
        self.addCleanup(lambda: IndexMetadata.objects.filter(
            name__in=[self.old_name, self.fresh_name, self.kb_name]
        ).delete())

    def _patch(self):
        delete_scope = mock.MagicMock()
        runtime = {
            "attachment_ttl_days": 7,
            "attachment_collection_prefix": "attach_",
        }
        return (
            mock.patch(
                "Django_xm.apps.knowledge.services.rag_service.RagService.delete_scope",
                delete_scope,
            ),
            mock.patch(
                "Django_xm.apps.knowledge.rag_settings.get_rag_runtime_settings",
                return_value=runtime,
            ),
            delete_scope,
        )

    def test_dry_run_lists_without_deleting(self):
        from Django_xm.apps.knowledge.services.attachment_index_cleanup import (
            cleanup_expired_attachment_indexes,
        )

        delete_patch, runtime_patch, delete_scope = self._patch()
        with delete_patch, runtime_patch:
            result = cleanup_expired_attachment_indexes(dry_run=True)

        self.assertEqual(result["scanned"], 1)
        self.assertEqual(result["names"], [self.old_name])
        self.assertEqual(result["deleted"], 0)
        delete_scope.assert_not_called()

    def test_cleanup_deletes_only_expired_attachment_indexes(self):
        from Django_xm.apps.knowledge.services.attachment_index_cleanup import (
            cleanup_expired_attachment_indexes,
        )

        delete_patch, runtime_patch, delete_scope = self._patch()
        with delete_patch, runtime_patch:
            result = cleanup_expired_attachment_indexes()

        self.assertEqual(result["scanned"], 1)
        self.assertEqual(result["deleted"], 1)
        self.assertEqual(result["failed"], 0)
        called_scopes = [c.args[0] for c in delete_scope.call_args_list]
        self.assertEqual([scope.name for scope in called_scopes], [self.old_name])
        self.assertEqual(called_scopes[0].kind, "session_attachment")

    def test_empty_prefix_falls_back_to_default(self):
        from Django_xm.apps.knowledge.services.attachment_index_cleanup import (
            cleanup_expired_attachment_indexes,
        )

        delete_patch, _runtime_patch, _delete_scope = self._patch()
        with delete_patch, mock.patch(
            "Django_xm.apps.knowledge.rag_settings.get_rag_runtime_settings",
            return_value={"attachment_ttl_days": 7, "attachment_collection_prefix": ""},
        ):
            result = cleanup_expired_attachment_indexes(dry_run=True)

        # 空前缀不能匹配到知识库索引（user_1_kb）
        self.assertEqual(result["prefix"], "attach_")
        self.assertEqual(result["names"], [self.old_name])


if __name__ == "__main__":
    unittest.main()
