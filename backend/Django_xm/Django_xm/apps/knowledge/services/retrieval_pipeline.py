"""
统一检索管线

封装所有检索增强能力，供 Strict RAG Chain 和 Agent Tool Call 两条路径共享：
- HyDE 查询改写（可选）
- 向量检索 + DegradableRetriever 降级
- _clean_docs 文档清洗

使用方式：
    pipeline = RetrievalPipeline(retriever, collection_name="kb_xxx", use_hyde=True)
    docs = pipeline.retrieve("用户查询")          # 同步
    docs = await pipeline.aretrieve("用户查询")   # 异步
"""

import asyncio
import json
import re
from typing import List, Optional

from langchain_core.retrievers import BaseRetriever
from langchain_core.documents import Document
from langchain_core.language_models.chat_models import BaseChatModel

from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)


class RetrievalPipeline:
    """统一检索管线

    封装所有检索增强能力：
    - HyDE 查询改写（可选）
    - 向量检索 + DegradableRetriever 降级
    - _clean_docs 文档清洗
    """

    def __init__(
        self,
        retriever: BaseRetriever,
        collection_name: str = "",
        use_hyde: bool = False,
        llm: Optional[BaseChatModel] = None,
    ):
        from Django_xm.apps.knowledge.services.retrieval_service import wrap_with_degradation

        self.retriever = wrap_with_degradation(retriever, collection_name)
        self.collection_name = collection_name
        self.use_hyde = use_hyde
        self.llm = llm

    def retrieve(self, query: str) -> List[Document]:
        """同步检索：HyDE → 向量/降级 → 清洗"""
        retrieval_query = self._maybe_rewrite(query)
        docs = self.retriever.invoke(retrieval_query)
        cleaned = self._clean_docs(docs)
        logger.info(
            f"RetrievalPipeline.retrieve: query='{query[:50]}...', "
            f"raw={len(docs)}, cleaned={len(cleaned)}"
        )
        return cleaned

    async def aretrieve(self, query: str) -> List[Document]:
        """异步检索：HyDE → 向量/降级 → 清洗"""
        retrieval_query = await self._maybe_rewrite_async(query)
        try:
            docs = await self.retriever.ainvoke(retrieval_query)
        except Exception:
            docs = await asyncio.to_thread(self.retriever.invoke, retrieval_query)
        cleaned = self._clean_docs(docs)
        logger.info(
            f"RetrievalPipeline.aretrieve: query='{query[:50]}...', "
            f"raw={len(docs)}, cleaned={len(cleaned)}"
        )
        return cleaned

    # ── HyDE 查询改写 ──────────────────────────────────────────────────────────

    def _maybe_rewrite(self, query: str) -> str:
        """同步 HyDE 查询改写，失败时回退到原始查询"""
        if not self.use_hyde:
            return query
        try:
            from Django_xm.apps.knowledge.services.strict_rag_chain import _hyde_rewrite_query_sync

            llm_for_hyde = self.llm if isinstance(self.llm, BaseChatModel) else None
            rewritten = _hyde_rewrite_query_sync(query, llm=llm_for_hyde)
            if rewritten != query:
                logger.info(f"HyDE 改写: '{query[:50]}...' -> '{rewritten[:50]}...'")
            return rewritten
        except Exception as e:
            logger.warning(f"HyDE 改写失败，使用原始查询: {e}")
            return query

    async def _maybe_rewrite_async(self, query: str) -> str:
        """异步 HyDE 查询改写，失败时回退到原始查询"""
        if not self.use_hyde:
            return query
        try:
            from Django_xm.apps.knowledge.services.strict_rag_chain import _hyde_rewrite_query

            llm_for_hyde = self.llm if isinstance(self.llm, BaseChatModel) else None
            rewritten = await _hyde_rewrite_query(query, llm=llm_for_hyde)
            if rewritten != query:
                logger.info(f"HyDE 改写(async): '{query[:50]}...' -> '{rewritten[:50]}...'")
            return rewritten
        except Exception as e:
            logger.warning(f"HyDE 改写失败(async)，使用原始查询: {e}")
            return query

    # ── 文档清洗 ────────────────────────────────────────────────────────────────

    @staticmethod
    def _clean_docs(docs: List[Document]) -> List[Document]:
        """清理检索结果中的非文档内容，并去重

        过滤两类不应出现在检索结果中的内容：
        1. 意图分类等内部 JSON 数据（辅助 LLM 输出被包装为 Document）
        2. MultiQuery 生成的替代查询（短问题文本被包装为 Document）

        去重策略（按优先级）：
        1. 基于 metadata 中的 source + chunk_index 组合（精确去重）
        2. 回退到 page_content 前缀匹配（近似去重）
        """
        cleaned = []
        seen_ids: set = set()
        seen_prefixes: set = set()
        dedup_prefix_len = 80

        for doc in docs:
            content = doc.page_content
            if not content or not content.strip():
                continue
            # 过滤意图分类等内部 JSON 数据
            if re.match(r'^\s*\{["\'].*["\']\s*:', content) and len(content) < 300:
                try:
                    json.loads(content)
                    continue
                except (json.JSONDecodeError, ValueError):
                    pass
            # 过滤 MultiQuery 生成的替代查询（短问题文本，以问号结尾）
            stripped = content.strip()
            if len(stripped) < 150 and (stripped.endswith('？') or stripped.endswith('?')):
                continue

            # 去重：优先用 source + chunk_index，回退到内容前缀
            source = doc.metadata.get('source', '')
            chunk_index = doc.metadata.get('chunk_index')
            if source and chunk_index is not None:
                doc_id = f"{source}:{chunk_index}"
                if doc_id in seen_ids:
                    continue
                seen_ids.add(doc_id)
            else:
                prefix = content[:dedup_prefix_len]
                if prefix in seen_prefixes:
                    continue
                seen_prefixes.add(prefix)

            cleaned.append(doc)

        total_dedup = len(docs) - len(cleaned)
        if total_dedup > 0:
            logger.debug(
                f"文档清洗+去重: {len(docs)} -> {len(cleaned)} "
                f"（过滤了 {total_dedup} 个非文档/重复内容）"
            )
        elif len(cleaned) != len(docs):
            logger.debug(
                f"文档清洗: {len(docs)} -> {len(cleaned)} "
                f"（过滤了 {len(docs) - len(cleaned)} 个非文档内容）"
            )
        return cleaned
