"""C2（KV-1）InMemory 阈值检索回归测试。

契约（remediate-rag-ai-correctness C2）：
1. 版本行为锁定：langchain-core 1.5.4 的 InMemoryVectorStore
   as_retriever(search_type="similarity_score_threshold") invoke 即抛
   NotImplementedError（运行时实测）——若未来版本支持了该 search_type，
   本测试失败，提示重新评估 _ScoreThresholdRetriever 适配器的必要性；
2. _ScoreThresholdRetriever 应用层过滤：阈值语义 = 相似度（越大越相关，
   与 config rag_score_threshold 口径一致），k 为取回上限；
3. 完全同向量文档（相似度 1.0）必通过任意合理阈值。

运行方式：
   python manage.py test Django_xm.apps.knowledge.tests.test_inmemory_threshold_retrieval --noinput
"""

from django.test import SimpleTestCase
from langchain_core.documents import Document
from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.vectorstores import InMemoryVectorStore

from Django_xm.apps.knowledge.services.rag_retrieval import _ScoreThresholdRetriever


def _make_store():
    emb = DeterministicFakeEmbedding(size=8)
    docs = [Document(page_content=f"doc{i}") for i in range(5)]
    return InMemoryVectorStore.from_documents(docs, emb)


class CoreVersionBehaviorTests(SimpleTestCase):
    def test_core_score_threshold_still_unsupported(self):
        """锁定 langchain-core 1.5.4 行为：similarity_score_threshold 不可用。

        若此测试失败（core 已支持），应评估移除 _ScoreThresholdRetriever
        回归原生实现。"""
        store = _make_store()
        retriever = store.as_retriever(
            search_type="similarity_score_threshold",
            search_kwargs={"score_threshold": 0.5, "k": 3},
        )
        with self.assertRaises(NotImplementedError):
            retriever.invoke("doc0")


class ScoreThresholdRetrieverTests(SimpleTestCase):
    """应用层阈值过滤行为（替代 core 未实现的 search_type）。"""

    def test_exact_match_passes_any_reasonable_threshold(self):
        """与 query 完全同文本的文档相似度 = 1.0，必通过阈值。"""
        store = _make_store()
        retriever = _ScoreThresholdRetriever(store, k=5, score_threshold=0.7)
        results = retriever.invoke("doc0")
        contents = [d.page_content for d in results]
        self.assertIn("doc0", contents)

    def test_threshold_filters_low_similarity(self):
        """极高阈值（0.999）只保留完全匹配文档，其余被过滤。"""
        store = _make_store()
        retriever = _ScoreThresholdRetriever(store, k=5, score_threshold=0.999)
        results = retriever.invoke("doc0")
        contents = [d.page_content for d in results]
        self.assertIn("doc0", contents)
        # 其余 4 个随机向量文档与 query 相似度不可能达到 0.999
        self.assertEqual(len(contents), 1)

    def test_k_limits_candidates_before_threshold(self):
        """k 为取回上限（先取 k 个候选再过滤）。"""
        store = _make_store()
        retriever = _ScoreThresholdRetriever(store, k=2, score_threshold=0.0)
        results = retriever.invoke("doc0")
        self.assertLessEqual(len(results), 2)

    def test_threshold_returns_empty_when_all_below(self):
        """全部候选低于阈值 → 空列表（下游据此走降级链）。"""
        store = _make_store()
        retriever = _ScoreThresholdRetriever(store, k=5, score_threshold=1.5)
        self.assertEqual(retriever.invoke("doc0"), [])
