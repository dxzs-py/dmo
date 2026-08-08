"""
统一 RAG 检索管线（Unified RAG Retrieval Pipeline）

提供两个核心能力：
1. load_attachment_documents - 从附件 ID 列表加载 Document 分块并估算 token 数
2. UnifiedRagPipeline - 5 步检索管线（建索引 → 相似度搜索 → 3 级降级 → 格式化输出）

Pipeline 流程:
    Step 1-2: 构建 InMemoryVectorStore 索引
    Step 3: 初始相似度搜索 (threshold=0.7, k=6)
    Step 4: 3 级降级链
        Level 1: 扩展向量检索 + 关键词召回 + FlashRank 重排序
        Level 2: 附件全文注入 (token < 5000) / 结构化提示 (token >= 5000)
    Step 5: 格式化输出
"""

from __future__ import annotations

import logging
import re
from typing import Any

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.retrievers import BaseRetriever
from langchain_core.vectorstores import InMemoryVectorStore

from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)

# ── FlashRank 条件导入 ─────────────────────────────────────────────────────────
try:
    from langchain_community.document_compressors import FlashRankReranker

    FLASHRANK_AVAILABLE = True
except ImportError:
    FLASHRANK_AVAILABLE = False

try:
    from langchain_classic.retrievers import ContextualCompressionRetriever

    ADVANCED_RETRIEVERS_AVAILABLE = True
except ImportError:
    ADVANCED_RETRIEVERS_AVAILABLE = False

# ── 常量 ───────────────────────────────────────────────────────────────────────

# 中文字符 Unicode 范围
_CJK_RANGE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")

# 关键词提取分隔符
_KEYWORD_SPLIT_PATTERN = re.compile(r"[，。！？；：、\s,\.!\?;:\-\(\)\[\]【】《》""''｜|/\\@#$%^&*+=]+")

# 停用词（中文常见虚词）
_STOP_WORDS = {
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一", "一个",
    "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看", "好",
    "自己", "这", "他", "她", "它", "们", "那", "些", "什么", "怎么", "哪", "吗",
    "啊", "吧", "呢", "哦", "嗯", "哈", "嘛", "呀", "哇", "啦", "噢", "嘿", "哎",
    "与", "或", "且", "但", "而", "所", "以", "之", "其", "从", "对", "被", "把",
    "向", "让", "给", "用", "能", "将", "该", "可", "已", "还", "又", "再", "才",
    "刚", "正", "只", "没", "非", "更", "最", "太", "多", "少", "大", "小", "新",
    "旧", "前", "后", "里", "外", "中", "内", "间", "旁", "边", "上", "下", "左",
    "右", "东", "西", "南", "北", "年", "月", "日", "时", "分", "秒", "个", "次",
    "位", "种", "类", "样", "件", "条", "张", "些", "点", "来", "去", "进", "出",
    "过", "回", "开", "关", "起", "做", "做", "进行", "使用", "通过", "可以",
    "需要", "能够", "应该", "可能", "已经", "没有", "不是", "因为", "所以",
    "如果", "虽然", "但是", "而且", "或者", "以及", "然后", "接着", "首先",
    "最后", "同时", "此外", "另外", "例如", "比如", "包括", "关于", "对于",
    "根据", "按照", "除了", "除了", "为了", "由于", "因此", "因而", "于是",
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "through", "during",
    "before", "after", "above", "below", "between", "under", "over",
    "such", "each", "every", "both", "few", "more", "most", "other",
    "some", "only", "own", "same", "so", "than", "too", "very", "just",
    "about", "up", "out", "then", "now", "also", "it", "its", "this",
    "that", "these", "those", "all", "any", "no", "not", "or", "and",
    "but", "if", "when", "where", "how", "what", "which", "who", "whom",
    "whose", "why", "whether", "while", "because", "though", "although",
}


# ── Token 估算 ──────────────────────────────────────────────────────────────────


def _estimate_token_count(text: str) -> int:
    """估算文本的 token 数量。

    启发式算法:
    - 中文字符: 约 1 token / 字符（实际约 1.2-1.5，保守取 1）
    - 英文/数字: 约 1 token / 4 字符
    - 混合文本按比例加权
    """
    if not text:
        return 0

    cjk_chars = len(_CJK_RANGE.findall(text))
    other_chars = len(text) - cjk_chars

    # 中文约 1 token/char，英文约 0.25 token/char
    return cjk_chars + max(1, other_chars // 4)


def _extract_keywords(query: str, max_keywords: int = 5) -> list[str]:
    """从查询文本中提取关键词。

    策略:
    1. 按标点/空格/特殊字符切分
    2. 过滤停用词和过短词（<=1 字符）
    3. 去重，返回不超过 max_keywords 个
    """
    raw_tokens = _KEYWORD_SPLIT_PATTERN.split(query)
    keywords: list[str] = []
    seen: set[str] = set()

    for token in raw_tokens:
        token = token.strip().lower()
        if not token or len(token) <= 1:
            continue
        if token in _STOP_WORDS:
            continue
        if token in seen:
            continue
        seen.add(token)
        keywords.append(token)
        if len(keywords) >= max_keywords:
            break

    return keywords


def _deduplicate_documents(docs: list[Document], prefix_length: int = 80) -> list[Document]:
    """按内容前缀去重文档列表，保留首次出现的文档。"""
    seen: set[str] = set()
    unique: list[Document] = []

    for doc in docs:
        key = doc.page_content[:prefix_length].strip()
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        unique.append(doc)

    if len(docs) != len(unique):
        logger.debug(f"文档去重: {len(docs)} -> {len(unique)}")

    return unique


# ── 文档加载 ────────────────────────────────────────────────────────────────────


def load_attachment_documents(
    attachment_ids: list[int],
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
) -> tuple[list[Document], int]:
    """从附件 ID 列表加载 Document 分块并估算总 token 数。

    对应 build_rag_enhanced_message 中 L120-134 的逻辑抽取与增强。

    Args:
        attachment_ids: 附件 ID 列表
        chunk_size: 分块大小
        chunk_overlap: 分块重叠量

    Returns:
        (documents 列表, total_token_count)
    """
    from Django_xm.apps.tools.langchain.file_reader import read_attachment_as_documents

    all_docs: list[Document] = []
    total_tokens = 0

    for att_id in attachment_ids:
        try:
            docs = read_attachment_as_documents(att_id, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
            for doc in docs:
                token_count = _estimate_token_count(doc.page_content)
                doc.metadata["token_count"] = token_count
                total_tokens += token_count
            all_docs.extend(docs)
            logger.info(f"附件 {att_id}: {len(docs)} 个片段, ~{total_tokens} tokens")
        except Exception:
            logger.exception(f"读取附件文档失败 (id={att_id})")

    logger.info(
        f"load_attachment_documents: {len(attachment_ids)} 个附件 -> "
        f"{len(all_docs)} 个片段, ~{total_tokens} tokens"
    )
    return all_docs, total_tokens


# ── 统一 RAG 检索管线 ───────────────────────────────────────────────────────────


class UnifiedRagPipeline:
    """统一 RAG 检索管线（无状态、5 步流程）。

    设计为无状态：每次 search() 调用时根据传入的 documents 建索引。
    不持有任何跨请求状态，线程安全。

    Pipeline:
        1-2. 构建 InMemoryVectorStore 索引
        3. 初始相似度搜索 (threshold=0.7, k=6)
        4. 3 级降级链（Level 1: 扩展检索 + 重排序, Level 2: 全文/结构化提示）
        5. 格式化输出
    """

    def __init__(self, embeddings: Embeddings | None = None):
        """初始化管线。

        Args:
            embeddings: Embeddings 实例，为 None 时自动从 embedding_service 获取
        """
        if embeddings is None:
            from Django_xm.apps.knowledge.services.embedding_service import get_embeddings

            embeddings = get_embeddings()

        self._embeddings = embeddings

    def search(
        self,
        query: str,
        documents: list[Document],
        total_token_count: int = 0,
    ) -> str:
        """执行完整检索管线，返回格式化结果字符串。

        此方法不会抛出异常，所有错误均转为用户可读字符串。

        Args:
            query: 用户查询文本
            documents: Document 列表（通常来自 load_attachment_documents）
            total_token_count: 文档总 token 数（用于 Level 2 降级判断）

        Returns:
            格式化后的检索结果字符串
        """
        try:
            return self._search_impl(query, documents, total_token_count)
        except Exception:
            logger.exception("UnifiedRagPipeline.search 异常")
            return "检索过程中发生错误，请稍后重试。"

    # ── 内部实现 ────────────────────────────────────────────────────────────────

    def _search_impl(
        self,
        query: str,
        documents: list[Document],
        total_token_count: int,
    ) -> str:
        if not documents:
            return "附件中未包含可读取的文本内容，请检查附件格式。"

        # Step 1-2: 构建 InMemoryVectorStore 索引
        logger.info(f"RAG Pipeline: 构建索引, {len(documents)} 个文档")
        try:
            vector_store = InMemoryVectorStore.from_documents(documents, self._embeddings)
        except Exception as e:
            logger.warning(f"构建向量索引失败: {e}，回退到全文注入")
            return self._fallback_full_text(documents, total_token_count)

        # Step 3: 初始相似度搜索
        try:
            retriever = vector_store.as_retriever(
                search_type="similarity_score_threshold",
                search_kwargs={"score_threshold": 0.7, "k": 6},
            )
            docs = retriever.invoke(query)
        except Exception as e:
            logger.warning(f"初始向量搜索失败: {e}")
            docs = []

        if docs:
            logger.info(f"RAG Pipeline: 初始搜索命中 {len(docs)} 个文档")
            return self._format_output(docs, degradation_level=0)

        # Step 4: 降级链
        logger.info("RAG Pipeline: 初始搜索无结果，进入降级 Level 1")

        # Level 1: 扩展检索
        level1_docs = self._degradation_level1(vector_store, query)
        if level1_docs:
            logger.info(f"RAG Pipeline: Level 1 降级命中 {len(level1_docs)} 个文档")
            return self._format_output(level1_docs, degradation_level=1)

        # Level 2: 全文/结构化提示
        logger.info("RAG Pipeline: Level 1 无结果，进入降级 Level 2")
        return self._degradation_level2(documents, total_token_count)

    # ── Level 1: 扩展向量检索 + 关键词召回 + 重排序 ─────────────────────────────

    def _degradation_level1(self, vector_store: InMemoryVectorStore, query: str) -> list[Document]:
        """Level 1 降级: 扩展向量检索 + 关键词召回 + FlashRank 重排序。"""
        all_docs: list[Document] = []

        # 1) 扩展向量检索: threshold=0.4, k=12
        try:
            expanded_retriever = vector_store.as_retriever(
                search_type="similarity_score_threshold",
                search_kwargs={"score_threshold": 0.4, "k": 12},
            )
            vector_docs = expanded_retriever.invoke(query)
            all_docs.extend(vector_docs)
            logger.debug(f"Level 1 扩展向量检索: {len(vector_docs)} 个文档")
        except Exception as e:
            logger.warning(f"Level 1 扩展向量检索失败: {e}")

        # 2) 关键词召回
        keywords = _extract_keywords(query, max_keywords=5)
        if keywords:
            logger.debug(f"Level 1 关键词: {keywords}")
            for kw in keywords:
                try:
                    kw_docs = vector_store.similarity_search(kw, k=3)
                    all_docs.extend(kw_docs)
                except Exception as e:
                    logger.warning(f"Level 1 关键词检索失败 (kw='{kw}'): {e}")

        # 去重
        all_docs = _deduplicate_documents(all_docs, prefix_length=80)

        if not all_docs:
            return []

        # 3) FlashRank 重排序（如果可用）
        if FLASHRANK_AVAILABLE and ADVANCED_RETRIEVERS_AVAILABLE:
            try:
                reranker = FlashRankReranker(model_name="ms-marco-MiniLM-L-12-v2", top_n=6)
                # 构造简单 retriever 包装 all_docs
                simple_retriever = _StaticDocRetriever(all_docs)
                compression_retriever = ContextualCompressionRetriever(
                    base_compressor=reranker,
                    base_retriever=simple_retriever,
                )
                reranked = compression_retriever.invoke(query)
                logger.debug(f"Level 1 FlashRank 重排序: {len(all_docs)} -> {len(reranked)}")
                return reranked
            except Exception as e:
                logger.warning(f"Level 1 FlashRank 重排序失败: {e}，回退到原始结果")

        # 无 FlashRank：返回去重后的前 6 个
        return all_docs[:6]

    # ── Level 2: 全文 / 结构化提示 ───────────────────────────────────────────────

    def _degradation_level2(self, documents: list[Document], total_token_count: int) -> str:
        """Level 2 降级: 根据 token 规模返回全文或结构化提示。"""
        if 0 < total_token_count < 5000:
            # 小文档: 返回全文
            full_text = "\n\n".join(doc.page_content for doc in documents)
            return f"未匹配到相关片段，以下基于附件全文整理：\n\n{full_text}"

        # 大文档或未知 token: 返回结构化提示
        return self._build_structured_hint(documents, total_token_count)

    # ── 格式化输出 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _format_output(docs: list[Document], degradation_level: int = 0) -> str:
        """格式化检索结果为字符串。"""
        if not docs:
            return "未找到相关文档。"

        parts: list[str] = []

        if degradation_level == 1:
            parts.append("未匹配到高度相关片段，以下为扩展检索结果：\n")

        for doc in docs:
            source = doc.metadata.get("original_name", doc.metadata.get("source", "未知"))
            parts.append(f"[来源: {source}]\n{doc.page_content}")

        return "\n\n".join(parts)

    @staticmethod
    def _build_structured_hint(documents: list[Document], total_token_count: int) -> str:
        """构建结构化提示（文档过大时）。"""
        # 收集文件信息
        file_map: dict[int, str] = {}
        for doc in documents:
            att_id = doc.metadata.get("attachment_id")
            name = doc.metadata.get("original_name", "未知")
            if att_id is not None and att_id not in file_map:
                file_map[att_id] = name
            elif att_id is None and "未知" not in file_map.values():
                file_map[-1] = name

        file_list = "\n".join(
            f"  - [{att_id}] {name}" for att_id, name in file_map.items() if att_id != -1
        )
        if not file_list:
            file_list = "\n".join(f"  - {name}" for name in file_map.values())

        token_display = f"约 {total_token_count}" if total_token_count > 0 else "未知"

        return (
            f"未检索到与您问题相关的片段。\n"
            f"附件信息：\n"
            f"{file_list}\n"
            f"总规模约 {token_display} token。\n"
            f"如需基于全文分析，请调用 attachment_reader 工具读取指定附件。"
        )

    @staticmethod
    def _fallback_full_text(documents: list[Document], total_token_count: int) -> str:
        """索引构建失败时的全文回退。"""
        if 0 < total_token_count < 5000:
            full_text = "\n\n".join(doc.page_content for doc in documents)
            return f"向量索引构建失败，以下基于附件全文整理：\n\n{full_text}"
        return UnifiedRagPipeline._build_structured_hint(documents, total_token_count)


# ── 内部辅助: 静态文档检索器 ────────────────────────────────────────────────────


class _StaticDocRetriever(BaseRetriever):
    """将固定的 Document 列表包装为 BaseRetriever。

    用于 FlashRank ContextualCompressionRetriever 的 base_retriever 参数，
    直接返回预先准备好的文档列表，不执行向量检索。
    """

    def __init__(self, documents: list[Document]):
        super().__init__()
        self._docs = documents

    def _get_relevant_documents(self, query: str) -> list[Document]:
        return self._docs

    async def _aget_relevant_documents(self, query: str) -> list[Document]:
        return self._docs
