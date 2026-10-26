"""RP-10：FlashRank 重排器每次降级重建 → 按模型名缓存单例。

原实现 UnifiedRagPipeline Level 1 降级与 create_reranker 每次调用都重新构造
FlashRankReranker——构造含 ONNX 模型加载（百 MB 量级磁盘读 + 内存加载），
同进程本可零成本复用。修复：retrieval_service.get_flashrank_reranker 以
lru_cache(maxsize=8) 缓存，键 (model_name, top_n)（top_n 烤在实例上，不能
仅按模型名缓存）；Level 1 降级改经该工厂取实例。

不违反阶段 1 不变量：FlashRank 为纯 CPU ONNX 推理对象，不绑事件循环、
非 async client（3.5 审计 RP-10 已论证）。

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.apps.knowledge.tests.test_reranker_cache --noinput
（纯单元测试，无 DB / Redis / LLM 依赖）
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from Django_xm.apps.knowledge.services import rag_retrieval, retrieval_service

_RETRIEVAL_SERVICE = "Django_xm.apps.knowledge.services.retrieval_service"
_RAG_RETRIEVAL = "Django_xm.apps.knowledge.services.rag_retrieval"


class _FakeReranker:
    """FlashRankReranker 替身：记录构造调用（字段名对齐 langchain-community 0.4.x：model）。"""

    def __init__(self, model: str, top_n: int):
        self.model = model
        self.top_n = top_n


class GetFlashrankRerankerCacheTests(unittest.TestCase):
    """缓存契约：同 (model, top_n) 同实例 / 换模型或 top_n 新实例 / 失败不缓存。"""

    def setUp(self):
        get_flashrank_reranker = retrieval_service.get_flashrank_reranker
        get_flashrank_reranker.cache_clear()
        self.addCleanup(get_flashrank_reranker.cache_clear)

    def _patch_factory_cls(self, **cls_kwargs):
        """patch FlashRankReranker 符号与可用性开关（测试机不依赖 flashrank 包）。"""
        cls_kwargs.setdefault("side_effect", _FakeReranker)
        cls = mock.MagicMock(name="FlashRankReranker", **cls_kwargs)
        patchers = [
            mock.patch.object(retrieval_service, "FlashRankReranker", cls, create=True),
            mock.patch.object(retrieval_service, "FLASHRANK_AVAILABLE", True, create=True),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        return cls

    def test_same_model_and_topn_same_instance(self):
        """同模型同 top_n → 同实例（构造只发生一次）。"""
        cls = self._patch_factory_cls()

        r1 = retrieval_service.get_flashrank_reranker("ms-marco-MiniLM-L-12-v2", 6)
        r2 = retrieval_service.get_flashrank_reranker("ms-marco-MiniLM-L-12-v2", 6)

        self.assertIs(r1, r2)
        # 3.6：0.4.x FlashrankRerank 构造字段为 model（extra="forbid"）
        cls.assert_called_once_with(model="ms-marco-MiniLM-L-12-v2", top_n=6)

    def test_different_model_new_instance(self):
        """换模型 → 新实例（ONNX 模型不同，不可复用）。"""
        cls = self._patch_factory_cls()

        r1 = retrieval_service.get_flashrank_reranker("model-a", 6)
        r2 = retrieval_service.get_flashrank_reranker("model-b", 6)

        self.assertIsNot(r1, r2)
        self.assertEqual(cls.call_count, 2)

    def test_different_topn_new_instance(self):
        """同模型不同 top_n → 新实例（top_n 烤在实例上，键必须含 top_n）。"""
        self._patch_factory_cls()

        r1 = retrieval_service.get_flashrank_reranker("model-a", 5)
        r2 = retrieval_service.get_flashrank_reranker("model-a", 6)

        self.assertIsNot(r1, r2)
        self.assertEqual(r1.top_n, 5)
        self.assertEqual(r2.top_n, 6)

    def test_creation_failure_not_cached_then_recovers(self):
        """构造失败不缓存：首次失败返回 None，修复后再次调用能成功创建。"""
        cls = self._patch_factory_cls(side_effect=[RuntimeError("onnx load failed"), _FakeReranker("m", 5)])

        self.assertIsNone(retrieval_service.create_reranker("m", 5))
        self.assertIsNotNone(retrieval_service.create_reranker("m", 5))
        self.assertEqual(cls.call_count, 2)


class Level1UsesRerankServiceTests(unittest.TestCase):
    """P0-O3：Level 1 精排下沉 rerank_service（模型可配/分数闸门/失败回退单源）。"""

    def test_level1_delegates_to_rerank_service(self):
        """_degradation_level1_from_retriever 把去重后的候选交给 rerank_service。"""
        from langchain_core.documents import Document

        reranked = [Document(page_content="重排后", metadata={})]
        fake_store = mock.MagicMock(name="vector_store")
        fake_store.similarity_search_with_score.return_value = [
            (Document(page_content="向量召回", metadata={}), 0.9)
        ]

        with mock.patch(
            "Django_xm.apps.knowledge.services.rerank_service.rerank_documents",
            return_value=reranked,
        ) as rerank:
            docs = rag_retrieval.UnifiedRagPipeline(
                scenario="knowledge_base"
            )._degradation_level1_from_retriever(fake_store, "查询")

        self.assertEqual([d.page_content for d in docs], ["重排后"])
        rerank.assert_called_once()
        call = rerank.call_args
        self.assertEqual(call.args[1], "查询")
        self.assertEqual(call.args[2], rag_retrieval.settings.rag_rerank_top_n)


class RerankerTopNFromSettingsTests(unittest.TestCase):
    """3.1：create_retriever / create_reranking_retriever 的 top_n 显式接线 settings。"""

    def _patch_env(self):
        """patch 缓存工厂 + 可用性开关 + ContextualCompressionRetriever。"""
        factory = mock.MagicMock(name="get_flashrank_reranker", return_value=mock.MagicMock(name="reranker"))
        compression_retriever = mock.MagicMock(name="compression_retriever")
        patchers = [
            mock.patch.object(retrieval_service, "FLASHRANK_AVAILABLE", True, create=True),
            mock.patch.object(retrieval_service, "ADVANCED_RETRIEVERS_AVAILABLE", True, create=True),
            mock.patch(f"{_RETRIEVAL_SERVICE}.get_flashrank_reranker", factory),
            mock.patch.object(
                retrieval_service,
                "ContextualCompressionRetriever",
                mock.MagicMock(return_value=compression_retriever),
                create=True,
            ),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        return factory

    def test_create_retriever_passes_settings_top_n(self):
        """use_reranker=True 时 top_n = min(k, settings.rag_rerank_top_n)。

        P0（召回窗口解耦）：k 是调用方交付条数契约，精排保留数不再固定为
        settings 值；默认 k=retriever_k=4 → top_n=4。防回归：若回退为无参
        create_reranker()，会落默认 5，配置项成为死配置。
        """
        factory = self._patch_env()

        vector_store = mock.MagicMock(name="vector_store")
        retrieval_service.create_retriever(vector_store, use_reranker=True)

        expected_top_n = min(
            rag_retrieval.settings.retriever_k, rag_retrieval.settings.rag_rerank_top_n
        )
        factory.assert_called_once_with(
            rag_retrieval.settings.rag_rerank_model, expected_top_n
        )

    def test_create_reranking_retriever_default_reranker_passes_settings_top_n(self):
        """create_reranking_retriever 未显式给重排器时，默认构造同样走 settings top_n。"""
        factory = self._patch_env()

        base_retriever = mock.MagicMock(name="base_retriever")
        retrieval_service.create_reranking_retriever(base_retriever, reranker=None)

        factory.assert_called_once_with(
            rag_retrieval.settings.rag_rerank_model, rag_retrieval.settings.rag_rerank_top_n
        )

    def test_settings_top_n_is_not_hardcoded_five(self):
        """配置值锁定：rag_rerank_top_n 存在且接线后不再依赖 create_reranker 默认 5。

        防回归：若有人把两处调用改回无参 create_reranker()，上面两个透传断言
        （factory 收到 settings 值）即失败；本断言另锁配置项本身存在。
        """
        self.assertTrue(hasattr(rag_retrieval.settings, "rag_rerank_top_n"))
        self.assertIsInstance(rag_retrieval.settings.rag_rerank_top_n, int)


class FlashrankAvailabilityTests(unittest.TestCase):
    """3.6：FlashRank 可用性探测锁定——防止顶层导入名漂移导致 reranker 静默禁用。

    缺陷（修复前）：探测用 `from langchain_community.document_compressors import
    FlashRankReranker`，langchain-community 0.4.x 顶层已不导出该名 →
    FLASHRANK_AVAILABLE 恒 False，reranker 全链路（build_user_retriever /
    Level 1 降级）静默禁用，settings.retriever_use_reranker 成死配置。
    flashrank 已是 requirements.txt 声明的硬依赖，本测试锁定真实环境可用。
    """

    def test_flashrank_available_in_declared_env(self):
        self.assertTrue(retrieval_service.FLASHRANK_AVAILABLE, "FlashRank 探测失败：导入路径漂移或 flashrank 未安装")
        self.assertTrue(rag_retrieval.FLASHRANK_AVAILABLE, "rag_retrieval 与 retrieval_service 探测口径不一致")

    def test_real_construction_with_declared_model(self):
        """真实构造（不 mock）：子模块导入 + model 字段下模型可实例化。"""
        retrieval_service.get_flashrank_reranker.cache_clear()
        self.addCleanup(retrieval_service.get_flashrank_reranker.cache_clear)
        reranker = retrieval_service.get_flashrank_reranker("ms-marco-MiniLM-L-12-v2", 6)
        self.assertIsNotNone(reranker)
        self.assertEqual(reranker.top_n, 6)


if __name__ == "__main__":
    unittest.main()
