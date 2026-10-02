"""strict_rag_chain 空结果处理 sync/async 薄壳收敛（B⑪）表征测试。

收敛方案：_ahandle_empty_result 改为薄壳——整体经 asyncio.to_thread 委托
同步核心 _handle_empty_result（UnifiedRagPipeline 无状态且线程安全），
输出与原实现（两处 IO 分别 to_thread）完全一致。

本测试锁定薄壳行为：三分支（无 collection / 有文档 / 空知识库）输出与
同步核心一致、关键调用参数一致。保留对（_aretrieve_with_fallback、
_amaybe_rewrite_query、stream/astream 双生成器）为真差异双实现，另行注释。

mock 边界（与 test_strict_rag_convergence.py 同风格，patch 源模块属性，
函数内延迟导入在调用时解析）：
- rag_retrieval.UnifiedRagPipeline / rag_retrieval.load_kb_documents

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python -m unittest Django_xm.apps.knowledge.tests.test_strict_rag_sync_async_shell
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langchain_core.documents import Document

from Django_xm.apps.knowledge.services.strict_rag_chain import (
    NO_RESULT_ANSWER,
    _ahandle_empty_result,
    _handle_empty_result,
)

_STRICT_RAG = "Django_xm.apps.knowledge.services.strict_rag_chain"
_RAG_RETRIEVAL = "Django_xm.apps.knowledge.services.rag_retrieval"

_QUERY = "什么是 LangChain"
_COLLECTION = "test_collection"


def _kb_doc():
    return Document(page_content="知识库全量内容", metadata={"source": "kb.md"})


class HandleEmptyResultShellTests(unittest.IsolatedAsyncioTestCase):
    """空结果处理薄壳表征：async 输出与 sync 核心一致。"""

    def _patch_pipeline(self, kb_docs, kb_tokens, search_return="管道检索回答"):
        pipeline_cls = mock.MagicMock()
        pipeline_cls.return_value.search.return_value = search_return
        load_kb = mock.MagicMock(return_value=(list(kb_docs), kb_tokens))
        return (
            mock.patch(f"{_RAG_RETRIEVAL}.UnifiedRagPipeline", pipeline_cls),
            mock.patch(f"{_RAG_RETRIEVAL}.load_kb_documents", load_kb),
            pipeline_cls,
            load_kb,
        )

    async def test_no_collection_both_return_default_without_load(self):
        """collection_name 为空：两版本都返回默认回答且不触 load_kb_documents。"""
        p1, p2, _, load_kb = self._patch_pipeline([], 0)
        with p1, p2:
            sync_result = _handle_empty_result(_QUERY, "")
            async_result = await _ahandle_empty_result(_QUERY, "")

        self.assertEqual(sync_result, (NO_RESULT_ANSWER, False))
        self.assertEqual(async_result, (NO_RESULT_ANSWER, False))
        load_kb.assert_not_called()

    async def test_kb_documents_branch_both_use_pipeline_search(self):
        """知识库有文档：两版本都走 pipeline.search 且输出一致（answer, True）。"""
        kb_docs = [_kb_doc()]
        p1, p2, pipeline_cls, load_kb = self._patch_pipeline(kb_docs, 42)
        with p1, p2:
            sync_result = _handle_empty_result(_QUERY, _COLLECTION)
            async_result = await _ahandle_empty_result(_QUERY, _COLLECTION)

        self.assertEqual(sync_result, ("管道检索回答", True))
        self.assertEqual(async_result, ("管道检索回答", True))
        # 薄壳委托同步核心 → load_kb / search 各被调用两次（sync 一次 + async 一次），
        # 且 search 参数与同步核心逐字一致（位置传参 query, kb_docs, kb_tokens）
        load_kb.assert_called_with([_COLLECTION])
        pipeline_cls.return_value.search.assert_called_with(_QUERY, kb_docs, 42)

    async def test_empty_kb_both_return_default(self):
        """collection 非空但知识库无文档：两版本都返回默认回答且 used_kb=False。"""
        p1, p2, pipeline_cls, load_kb = self._patch_pipeline([], 0)
        with p1, p2:
            sync_result = _handle_empty_result(_QUERY, _COLLECTION)
            async_result = await _ahandle_empty_result(_QUERY, _COLLECTION)

        self.assertEqual(sync_result, (NO_RESULT_ANSWER, False))
        self.assertEqual(async_result, (NO_RESULT_ANSWER, False))
        load_kb.assert_called_with([_COLLECTION])
        pipeline_cls.return_value.search.assert_not_called()

    async def test_async_shell_runs_sync_core_off_event_loop(self):
        """薄壳确经 to_thread 在工作线程执行同步核心（线程标识非主线程）。"""
        import threading

        main_thread = threading.current_thread()
        observed = {}

        original = _handle_empty_result

        def _probe(query, collection_name):
            observed["thread"] = threading.current_thread()
            return original(query, collection_name)

        p1, p2, _, _ = self._patch_pipeline([], 0)
        with p1, p2, mock.patch(f"{_STRICT_RAG}._handle_empty_result", side_effect=_probe):
            await _ahandle_empty_result(_QUERY, "")

        self.assertIsNot(observed["thread"], main_thread)


if __name__ == "__main__":
    unittest.main()
