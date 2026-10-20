"""
统一 RAG 检索管线（Unified RAG Retrieval Pipeline）

提供两个核心能力：
1. load_attachment_documents - 从附件 ID 列表加载 Document 分块并估算 token 数
2. UnifiedRagPipeline - 4 步检索管线（检索 → 3 级降级 → 格式化输出）

Pipeline 流程:
    Step 1: 通过 BaseRetriever 执行初始搜索 (threshold 来自 config, k 来自 config)
    Step 2: 3 级降级链
        Level 0: 初始搜索命中，直接使用结果
        Level 1: 扩展向量检索 + FlashRank 重排序（需要 InMemoryVectorStore）
        Level 2: 附件全文注入 (token < threshold) / 结构化提示 (token >= threshold)
    Step 3: 格式化输出

核心入口:
    - search_with_retriever(): 接受任意 BaseRetriever，执行完整管线
    - search(): 兼容旧接口，内部构建 InMemoryVectorStore 后委托给 search_with_retriever()
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.retrievers import BaseRetriever
from langchain_core.vectorstores import InMemoryVectorStore, VectorStore

from Django_xm.apps.context_manager.services.compression import TokenEstimator
from Django_xm.apps.core.logging_utils import get_logger

# A-5：ai_engine 配置统一经 knowledge.config 门面（settings 为其 re-export）
from Django_xm.apps.knowledge.config import settings

logger = get_logger(__name__)

# ── FlashRank 条件导入 ─────────────────────────────────────────────────────────
try:
    # 3.6 实测：langchain-community 0.4.x 顶层不再导出 FlashRank 重排器，
    # 须从子模块导入（类名 FlashrankRerank）——与 retrieval_service 探测口径
    # 一致；重排器实例统一经 retrieval_service.get_flashrank_reranker 缓存
    # 创建（RP-10），本模块不再直接构造
    from langchain_community.document_compressors.flashrank_rerank import FlashrankRerank  # noqa: F401

    FLASHRANK_AVAILABLE = True
except ImportError:
    FLASHRANK_AVAILABLE = False

try:
    from langchain_classic.retrievers import ContextualCompressionRetriever

    ADVANCED_RETRIEVERS_AVAILABLE = True
except ImportError:
    ADVANCED_RETRIEVERS_AVAILABLE = False

# ── 常量 ───────────────────────────────────────────────────────────────────────

# B11：多库并行检索单进程线程上限（对齐 views_rag.SearchAllView 的 SEARCH_ALL_MAX_WORKERS）
MULTI_KB_MAX_WORKERS = 8
# B11：multi-KB 检索工具输出单文档内容截断上限（对齐 strict_rag_chain 单文档 800 字符截断口径）
_MULTI_KB_DOC_MAX_CHARS = 800
_MULTI_KB_TRUNCATED_TAG = "...[内容已截断]"


# ── Token 估算 ──────────────────────────────────────────────────────────────────


def _estimate_token_count(text: str) -> int:
    """估算文本的 token 数量（收敛：委托 context_manager 权威实现，5-1.6）

    行为变化：原自造启发式（CJK 1/char + 其他 /4）已删除，改为委托
    TokenEstimator.estimate_tokens（tiktoken → transformers → 改进启发式），
    与上下文管理侧估算同源。cl100k 对 CJK 约 1~1.5 token/字，估算值通常
    略高于旧公式（旧公式保守取 1），仅用于预算而非计费，可接受。
    """
    return TokenEstimator.estimate_tokens(text)


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


def load_kb_documents(collection_names: list[str]) -> tuple[list[Document], int]:
    """从知识库索引（PGVector 集合）加载全部 Document 分块并估算总 token 数。

    镜像 load_attachment_documents：逐集合读取全部 chunk，
    为每个 doc 写入 metadata["token_count"]，累加 total_tokens。
    不存在的集合跳过（read_all_documents 返回空列表）。

    Args:
        collection_names: 知识库索引全名列表（如 user_{id}_{kb}）

    Returns:
        (documents 列表, total_token_count)
    """
    from Django_xm.apps.knowledge.services.index_service import IndexManager

    all_docs: list[Document] = []
    total_tokens = 0
    manager = IndexManager()

    for name in collection_names:
        try:
            docs = manager.read_all_documents(name)
        except Exception:
            logger.exception(f"读取知识库索引失败 (collection={name})")
            docs = []
        for doc in docs:
            token_count = _estimate_token_count(doc.page_content)
            doc.metadata["token_count"] = token_count
            total_tokens += token_count
        all_docs.extend(docs)
        logger.info(f"知识库 {name}: {len(docs)} 个片段, ~{total_tokens} tokens")

    logger.info(
        f"load_kb_documents: {len(collection_names)} 个集合 -> "
        f"{len(all_docs)} 个片段, ~{total_tokens} tokens"
    )
    return all_docs, total_tokens


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


class _ScoreThresholdRetriever:
    """InMemory 阈值检索适配器（C2/KV-1）。

    langchain-core 1.5.4 的 InMemoryVectorStore 不支持
    search_type="similarity_score_threshold"（invoke 即抛 NotImplementedError，
    运行时实测），且异常被调用方吞掉 → 阈值检索从未生效，RAG 恒走关键词
    召回降级。本适配器用等价实现替代：similarity 检索取回候选后按相似度
    阈值在应用层过滤（similarity_search_with_score 返回 cosine similarity，
    越大越相关，与 config 阈值口径一致）。

    只实现 invoke(query)——消费方（_initial_search_from_retriever 等）
    仅使用该接口。
    """

    def __init__(self, vector_store: InMemoryVectorStore, k: int, score_threshold: float):
        self._store = vector_store
        self._k = k
        self._threshold = score_threshold

    def invoke(self, query: str) -> list[Document]:
        pairs = self._store.similarity_search_with_score(query, k=self._k)
        return [
            doc
            for doc, score in pairs
            if score is not None and score >= self._threshold
        ]


# ── RRF 融合纯函数（B-2 单源） ─────────────────────────────────────────────────


def _rrf_doc_key(doc: Document) -> str:
    """RRF 去重身份键（B12）：(source, chunk_index) 组合键优先。

    旧实现以整段正文为键：不同知识库收录的相同 chunk（如同源文件上传到
    多个库，metadata.source 为各自库目录路径）会被错误合并，RRF 分数跨库
    累加导致结果身份歧义。组合键按"来源 + 块序"判定文档身份。
    metadata 缺 chunk_index 或 source（关键词降级检索、外部构造的 Document）
    时回退整段正文哈希，保持旧行为（同文合并）。
    """
    metadata = doc.metadata or {}
    chunk_index = metadata.get("chunk_index")
    source = metadata.get("source") or metadata.get("file_name") or metadata.get("source_filename")
    if source and chunk_index is not None:
        return f"{source}#{chunk_index}"
    # 缺组合键字段：回退正文哈希（仅进程内去重用途，非安全场景）
    return "content#" + hashlib.sha256(doc.page_content.encode("utf-8")).hexdigest()


def rrf_fuse(
    ranked_lists: list[list[tuple[Document, float | None]]],
    weights: list[float] | None = None,
    rrf_k: int | None = None,
) -> list[tuple[Document, float, float | None, int]]:
    """Reciprocal Rank Fusion 融合（跨库检索唯一算法源，B-2）。

    此前同一 RRF 算法两处内联（views_rag.SearchAllView 等权保留 score /
    UnifiedRagPipeline.search_multi_kb 带权重无 score），能力不同步；本函数取
    两版能力并集统一：支持库权重 + 保留首现库原生 score + 来源列表索引。

    Args:
        ranked_lists: 各库 [(Document, score|None)]，**已按库内 score 降序**
            （调用方负责排序；无打分能力的库 score 传 None，顺序即相关性序）
        weights: 与 ranked_lists 一一对应的库权重，None 时等权
        rrf_k: RRF 常数，None 时取 settings.rag_rrf_constant

    Returns:
        [(Document, fused_score, first_score, first_list_idx)] 按 fused_score
        降序（稳定排序，同分保持首现序）；first_score 为该文档首次出现库的
        原生 score（可能 None）；first_list_idx 为首次出现列表的下标（0 起，
        调用方可据此映射回库标识）。
    """
    if weights is None:
        weights = [1.0] * len(ranked_lists)
    if rrf_k is None:
        rrf_k = settings.rag_rrf_constant

    fused: dict[str, dict] = {}
    for list_idx, entries in enumerate(ranked_lists):
        weight = weights[list_idx] if list_idx < len(weights) else 1.0
        for rank, (doc, score) in enumerate(entries, 1):
            key = _rrf_doc_key(doc)
            entry = fused.get(key)
            if entry is None:
                fused[key] = {
                    "doc": doc,
                    "fused_score": weight / (rrf_k + rank),
                    "first_score": score,
                    "first_list_idx": list_idx,
                }
            else:
                entry["fused_score"] += weight / (rrf_k + rank)

    ordered = sorted(fused.values(), key=lambda e: e["fused_score"], reverse=True)
    return [
        (e["doc"], e["fused_score"], e["first_score"], e["first_list_idx"])
        for e in ordered
    ]


# ── 统一 RAG 检索管线 ───────────────────────────────────────────────────────────


@dataclass
class PipelineSearchResult:
    """UnifiedRagPipeline 检索结构化结果（Task 4/B2）。

    此前 search_with_retriever 以格式化字符串承载全部语义，调用方只能靠
    正文子串（"未找到" 等）猜状态——文档正文含"未找到"即误判，且异常文案
    "检索过程中发生错误"会被当知识内容加前缀回灌 LLM。结构化后状态判定
    只读 status，禁止再依赖 text 内容。

    status 语义：
    - "ok"：检索命中（含 Level 1/2 降级命中），text 为可交给 LLM 的检索内容，
      docs 携带原始文档，degradation_level 标明降级深度（0/1/2）
    - "empty"：检索正常执行但无命中，text 为受控空结果文案（不得作为知识内容包装）
    - "error"：检索过程异常，error 携带原始错误摘要，text 为受控错误文案
    """

    status: str
    text: str
    docs: list[Document] = field(default_factory=list)
    degradation_level: int = 0
    error: str | None = None

    @property
    def is_ok(self) -> bool:
        return self.status == "ok"


class UnifiedRagPipeline:
    """统一 RAG 检索管线（无状态、4 步流程）。

    设计为无状态：每次调用时根据传入的 retriever 或 documents 执行检索。
    不持有任何跨请求状态，线程安全。

    核心入口:
        - search_with_retriever(): 接受任意 BaseRetriever，执行完整管线
        - search(): 兼容旧接口，内部构建 InMemoryVectorStore 后委托

    Pipeline:
        1. 通过 BaseRetriever 执行初始搜索
        2. 3 级降级链
            Level 0: 初始命中 → 直接格式化
            Level 1: 扩展向量检索 + FlashRank 重排序
            Level 2: 全文注入 / 结构化提示
        3. 格式化输出
    """

    def __init__(self, embeddings: Embeddings | None = None, scenario: str = "attachment"):
        """初始化管线。

        Args:
            embeddings: Embeddings 实例，为 None 时按需从 embedding_service 获取。
                        仅在兼容模式（search/retrieve_documents 构建 InMemoryVectorStore）时需要。
            scenario: 检索场景，"attachment"（附件）或 "knowledge_base"（知识库），
                      决定空结果与降级文案
        """
        if scenario not in ("attachment", "knowledge_base"):
            raise ValueError(f"不支持的场景: {scenario}")

        self._embeddings = embeddings
        self._scenario = scenario

    # ── 懒加载 embeddings ────────────────────────────────────────────────────────

    def _ensure_embeddings(self) -> Embeddings:
        """确保 embeddings 实例可用，为 None 时从 embedding_service 懒加载。"""
        if self._embeddings is None:
            from Django_xm.apps.knowledge.services.embedding_service import get_embeddings

            self._embeddings = get_embeddings()
        return self._embeddings

    # ═══════════════════════════════════════════════════════════════════════════════
    # 新核心入口: 基于 BaseRetriever
    # ═══════════════════════════════════════════════════════════════════════════════

    def search_with_retriever(
        self,
        retriever: BaseRetriever,
        query: str,
        vector_store: InMemoryVectorStore | None = None,
        documents: list[Document] | None = None,
        total_token_count: int = 0,
    ) -> PipelineSearchResult:
        """执行完整 4 步检索管线，返回结构化结果。

        适用于已有 BaseRetriever（如 PGVector retriever）的调用方，
        不需要构建 InMemoryVectorStore。

        Pipeline:
            Step 1: retriever.invoke(query) 初始搜索
            Step 2: 无结果 → Level 1 降级（需 vector_store 做扩展向量检索）
            Step 3: 仍无结果 → Level 2 降级（需 documents 做全文注入）
            Step 4: 结构化封装（status: ok/empty/error）

        此方法不会抛出异常；所有错误以 status="error" 结构化返回
        （Task 4/B2：禁止再以文案字符串承载错误语义）。

        Args:
            retriever: 任意 BaseRetriever 实例
            query: 用户查询文本
            vector_store: InMemoryVectorStore（Level 1 扩展向量检索需要）
            documents: 原始 Document 列表（Level 2 全文注入需要）
            total_token_count: 文档总 token 数（用于 Level 2 降级判断）

        Returns:
            PipelineSearchResult：status 为 ok/empty/error，调用方只读 status
            判定结果，不得解析 text 内容
        """
        query_short = query[:50] + "..." if len(query) > 50 else query

        try:
            # Step 1: 初始搜索
            # Task 4/B2：raise_on_error=True 使检索故障（embedding/向量库不可达）
            # 穿透到本方法 except → status="error"，与"正常检索但无命中"（empty）
            # 区分——否则工具侧会把故障误报为"知识库中未找到"
            docs = self._initial_search_from_retriever(retriever, query, raise_on_error=True)

            if docs:
                logger.info(
                    f"pipeline=UnifiedRagPipeline degradation_level=0 "
                    f"query_short={query_short} doc_count={len(docs)} scenario={self._scenario}"
                )
                return PipelineSearchResult(
                    status="ok", text=self._format_output(docs, degradation_level=0),
                    docs=list(docs), degradation_level=0,
                )

            # Step 2: Level 1 降级
            if vector_store is not None:
                docs = self._degradation_level1_from_retriever(vector_store, query)
                if docs:
                    logger.info(
                        f"pipeline=UnifiedRagPipeline degradation_level=1 "
                        f"query_short={query_short} doc_count={len(docs)} scenario={self._scenario}"
                    )
                    return PipelineSearchResult(
                        status="ok", text=self._format_output(docs, degradation_level=1),
                        docs=list(docs), degradation_level=1,
                    )

            # Step 3: Level 2 降级
            logger.info(
                f"pipeline=UnifiedRagPipeline degradation_level=2 "
                f"query_short={query_short} doc_count=0 scenario={self._scenario}"
            )
            if documents:
                return PipelineSearchResult(
                    status="ok", text=self._degradation_level2(documents, total_token_count),
                    docs=list(documents), degradation_level=2,
                )

            # 无文档可用：受控空结果应答
            if self._scenario == "knowledge_base":
                return PipelineSearchResult(status="empty", text="知识库中未找到与您问题相关的信息。")
            return PipelineSearchResult(status="empty", text="当前会话无可用附件，且未检索到相关内容。")

        except Exception as e:
            logger.exception("search_with_retriever 异常")
            return PipelineSearchResult(
                status="error", text="检索过程中发生错误，请稍后重试。", error=str(e)
            )

    def retrieve_documents_from_retriever(
        self,
        retriever: BaseRetriever,
        query: str,
        vector_store: InMemoryVectorStore | None = None,
        raise_on_error: bool = False,
        with_scores: bool = False,
    ) -> list[Document]:
        """基于 BaseRetriever 检索文档，含 Level 1 降级。

        供学习工作流等需要原始文档对象（非格式化文本）的调用方使用。
        与 retrieve_documents 镜像，但接受 retriever 而非 documents 列表。

        Pipeline:
            1. retriever.invoke(query) 初始搜索
            2. 无结果 → Level 1 降级（需 vector_store）

        Args:
            retriever: 任意 BaseRetriever 实例
            query: 用户查询文本
            vector_store: InMemoryVectorStore（Level 1 扩展向量检索需要）
            raise_on_error: 异常上抛开关（C-3）。默认 False 吞一切异常返回
                []（学习工作流等既有调用方语义保持）；True 时异常原样上抛——
                strict_rag_chain 的降级编排依赖该开关感知 embedding 故障，
                否则"embedding 故障→关键词降级"分支生产不可达。
            with_scores: 相关度分数透出开关（R-UX-04）。True 且 retriever 为
                纯 similarity 的 VectorStoreRetriever 时，改走 search_scored
                统一打分内核（检索次数不变），相似度分数写入 doc.metadata["score"]；
                其余检索器（MMR/MultiQuery/重排等）保持 invoke 原路径不带分数。

        Returns:
            按相关性排序的 Document 列表，无结果时返回空列表
        """
        try:
            docs = self._initial_search_from_retriever(
                retriever, query, raise_on_error=raise_on_error, with_scores=with_scores
            )
            if docs:
                return docs

            if vector_store is not None:
                return self._degradation_level1_from_retriever(vector_store, query)

            return []
        except Exception:
            if raise_on_error:
                raise
            logger.exception("retrieve_documents_from_retriever 异常")
            return []

    # ═══════════════════════════════════════════════════════════════════════════════
    # 多知识库 RRF 融合检索
    # ═══════════════════════════════════════════════════════════════════════════════

    def search_multi_kb(
        self,
        kb_names: list[str],
        query: str,
        k: int | None = None,
        weights: list[float] | None = None,
        user_id: int | None = None,
    ) -> str:
        """多知识库 RRF 联合检索。

        为每个知识库创建 retriever → 并行独立检索（B11：ThreadPoolExecutor，
        对齐 views_rag.SearchAllView 的 worker 模型——单库异常计入失败不中断、
        线程内 Django 连接显式释放）→ RRF 融合（rrf_fuse 单源）→ Level 2 降级
        判断。命中路径输出加 KB_RESULT_INSTRUCTION 规则前缀与单文档 800 字符
        截断（对齐单库检索工具行为）。

        Args:
            kb_names: 知识库全名列表（如 user_{id}_{kb}）
            query: 用户查询文本
            k: 每个知识库检索返回数，默认从 settings.rag_initial_k 读取
            weights: RRF 权重列表，默认等权
            user_id: 检索发起用户（3.5 越权守卫）。提供时索引名前缀必须匹配
                ``user_{user_id}_``，不匹配的库直接剔除（名字内嵌 user_id 是
                第一道防线；DB 归属校验在工具构建期由 build_retriever_tool_for_research
                完成）。None 为系统内部可信路径，行为与旧版一致

        Returns:
            格式化后的检索结果字符串
        """
        from django.db import connections

        from Django_xm.apps.knowledge.services.embedding_service import get_embeddings
        from Django_xm.apps.knowledge.services.index_service import IndexManager
        from Django_xm.apps.knowledge.services.retrieval_service import create_retriever
        from Django_xm.apps.knowledge.services.retriever_tool import KB_RESULT_INSTRUCTION

        if not kb_names:
            return "知识库中暂无文档，请先上传文档。"

        # 3.5 越权硬守卫：user_id 提供时仅保留本用户前缀的库（他人库剔除并
        # 留痕，不泄露存在性——剔除后等同"该库不存在"）
        if user_id is not None:
            denied = [n for n in kb_names if not n.startswith(f"user_{user_id}_")]
            if denied:
                logger.warning(
                    f"search_multi_kb: 拒绝非归属知识库检索: user_id={user_id}, denied={denied}"
                )
                kb_names = [n for n in kb_names if n not in denied]
            if not kb_names:
                return "知识库中暂无文档，请先上传文档。"

        if k is None:
            k = settings.rag_initial_k

        manager = IndexManager()
        embeddings = get_embeddings()
        ranked_lists: list[list[tuple[Document, float | None]]] = []
        used_weights: list[float] = []
        if weights is None:
            weights = [1.0 / len(kb_names)] * len(kb_names)

        def _search_one_kb(name: str, weight: float):
            """单库检索 worker（B11）：加载/检索异常计入失败返回 None，不中断整体。

            worker 内打开的 Django 连接随线程结束显式释放（对齐
            SearchAllView._search_one，防止线程池连接泄漏）。
            """
            try:
                vector_store = manager.load_index(name, embeddings)
                retriever = create_retriever(vector_store, k=k)
                docs = retriever.invoke(query)
                return [(doc, None) for doc in docs], weight
            except Exception as e:
                logger.warning(f"search_multi_kb: 知识库 {name} 检索失败: {e}")
                return None
            finally:
                connections.close_all()

        # 逐库串行 invoke → 线程池并行（B11）；executor.map 按提交序返回，
        # used_weights 与 ranked_lists 顺序保持与 kb_names 一致（zip strict=False
        # 语义：weights 长度不足时截断，与原实现一致）
        max_workers = min(len(kb_names), MULTI_KB_MAX_WORKERS)
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="multi-kb-search") as executor:
            for result in executor.map(_search_one_kb, kb_names, weights):
                if result is None:
                    continue
                ranked, weight = result
                ranked_lists.append(ranked)
                used_weights.append(weight)

        if not ranked_lists:
            return "知识库加载失败，请稍后重试。"

        # RRF 融合（B-2 单源：与 views_rag.SearchAllView 共用 rrf_fuse 纯函数）
        fused = rrf_fuse(ranked_lists, weights=used_weights)
        merged_docs = [doc for doc, _fused_score, _first_score, _first_list in fused]
        merged_docs = merged_docs[: settings.rag_initial_k]

        query_short = query[:50] + "..." if len(query) > 50 else query

        if merged_docs:
            logger.info(
                f"pipeline=UnifiedRagPipeline degradation_level=0 "
                f"query_short={query_short} doc_count={len(merged_docs)} scenario=multi_kb_rrf"
            )
            # B11：对齐单库检索工具——真实检索内容加 KB_RESULT_INSTRUCTION 规则前缀，
            # 单文档内容截断（保头 + 注明）防止全文注入撑爆下游 LLM 上下文
            truncated = [self._truncate_doc_content(doc) for doc in merged_docs]
            return KB_RESULT_INSTRUCTION + self._format_output(truncated, degradation_level=0)

        # RRF 无结果 → Level 2: 加载全库文档全文注入
        logger.info(
            f"pipeline=UnifiedRagPipeline degradation_level=2 "
            f"query_short={query_short} doc_count=0 scenario=multi_kb_rrf"
        )
        kb_docs, total_tokens = load_kb_documents(kb_names)
        if kb_docs:
            return self._degradation_level2(kb_docs, total_tokens)

        return "知识库中未找到与您问题相关的信息。"

    # ═══════════════════════════════════════════════════════════════════════════════
    # 统一原始打分检索入口（Level 0）：kb_service / SearchAllView 共用
    # ═══════════════════════════════════════════════════════════════════════════════

    @staticmethod
    def search_scored(
        vector_store: VectorStore,
        query: str,
        k: int = 4,
        score_threshold: float | None = None,
        reload_vector_store: Callable[[], VectorStore] | None = None,
        required_dim: int | None = None,
        query_embedding: list[float] | None = None,
        *,
        score_semantics: str = "similarity",
    ) -> list[tuple[Document, float | None]]:
        """原始打分检索（Level 0）：向量库相似度打分检索 + 维度不匹配自愈重试。

        kb_service.search_knowledge_base 与 views_rag.SearchAllView 的统一检索内核
        （ARCH-08 入口收敛）：此前两处各自内联 search_vector_store 调用，
        且仅 kb_service 持有 pgvector 维度自愈逻辑，口径不一。

        Args:
            vector_store: 已加载的向量库实例
            query: 用户查询文本
            k: 返回文档数量
            score_threshold: 相似度阈值，检索后过滤低于该值的结果
            reload_vector_store: 自愈重建向量库的回调；不提供时维度不匹配异常原样上抛
            required_dim: 索引期望的向量维度（自愈重置 pgvector 运行时缓存用）
            query_embedding: 预计算的 query 向量；提供时走 by-vector 检索（跨库
                聚合场景 embedding 仅算一次），None 时保持现行为由向量库自行 embed
            score_semantics: 后端原生分数语义（C1/KV-2）。pgvector 返回 cosine
                distance，须传 "distance"（归一 similarity = 1 - distance 后再
                过滤/返回）；"similarity"（默认）行为不变。语义契约见
                search_vector_store docstring。

        Returns:
            (Document, score) 元组列表；score 为相似度语义（越大越相关），
            可能为 None（取决于后端打分能力）

        Raises:
            Exception: 非维度不匹配异常原样上抛
        """
        from Django_xm.apps.knowledge.vector_store import search_vector_store

        try:
            return search_vector_store(
                vector_store,
                query,
                k=k,
                score_threshold=score_threshold,
                query_embedding=query_embedding,
                score_semantics=score_semantics,
            )
        except Exception as e:
            # B-4附：维度不匹配识别收口到 pgvector 层（as_dimension_mismatch_error
            # 返回类型化 DimensionMismatchError），本通用入口不再做异常消息子串匹配；
            # pgvector 专属自愈按后端能力门控——仅 SQLAlchemy engine 型后端
            # （langchain_postgres PGVector，pgvector 专属知识）执行自愈重试，
            # 其他后端（faiss/chroma/milvus/inmemory）原样上抛
            if reload_vector_store is None:
                raise
            from Django_xm.apps.knowledge.vector_store.pgvector_runtime import as_dimension_mismatch_error

            mismatch = as_dimension_mismatch_error(e)
            if mismatch is None or not hasattr(vector_store, "_engine"):
                raise
            logger.warning(f"检测到维度不匹配，清理缓存后重试: {e}")
            # 释放旧 VectorStore 的 SQLAlchemy engine 连接池
            if hasattr(vector_store, "_engine") and vector_store._engine:
                vector_store._engine.dispose()
            # 关闭 Django 数据库连接
            from django.db import connections

            connections.close_all()
            # 统一重置 PGVector 缓存（clear metadata + 重置 _classes + 重建）
            from Django_xm.apps.knowledge.vector_store.pgvector_runtime import reset_pgvector_cache

            reset_pgvector_cache(dimension=required_dim)
            # 清理 IndexManager 缓存，并释放快照中的旧 engine 连接池
            # （C3/KV-3：此前直接丢弃快照，泄漏连接）
            from Django_xm.apps.knowledge.services.index_service import IndexManager
            from Django_xm.apps.knowledge.vector_store import dispose_vector_stores

            dispose_vector_stores(IndexManager.clear_cache())
            rebuilt = reload_vector_store()
            return search_vector_store(
                rebuilt,
                query,
                k=k,
                score_threshold=score_threshold,
                query_embedding=query_embedding,
                score_semantics=score_semantics,
            )

    # ═══════════════════════════════════════════════════════════════════════════════
    # 兼容入口: 基于 documents 列表（构建 InMemoryVectorStore 后委托）
    # ═══════════════════════════════════════════════════════════════════════════════

    def search(
        self,
        query: str,
        documents: list[Document],
        total_token_count: int = 0,
    ) -> str:
        """兼容旧接口：从 documents 构建 InMemoryVectorStore 后执行完整检索管线。

        此方法不会抛出异常，所有错误均转为用户可读字符串（附件链路既有
        字符串契约，attachment_rag 等调用方不变；结构化消费方应改用
        search_with_retriever 读 PipelineSearchResult.status）。

        Args:
            query: 用户查询文本
            documents: Document 列表（通常来自 load_attachment_documents）
            total_token_count: 文档总 token 数（用于 Level 2 降级判断）

        Returns:
            格式化后的检索结果字符串
        """
        if not documents:
            if self._scenario == "knowledge_base":
                return "知识库中暂无文档，请先上传文档。"
            return "当前会话无可用附件。"

        try:
            # 构建 InMemoryVectorStore
            try:
                embeddings = self._ensure_embeddings()
                vector_store = InMemoryVectorStore.from_documents(documents, embeddings)
            except Exception as e:
                logger.warning(f"构建向量索引失败: {e}，回退到全文注入")
                return self._fallback_full_text(documents, total_token_count)

            # 创建 retriever 并委托
            # C2/KV-1：core 1.5.4 的 similarity_score_threshold 抛 NotImplementedError，
            # 改用应用层阈值过滤适配器（阈值检索自本修复起才真实生效）
            retriever = _ScoreThresholdRetriever(
                vector_store,
                k=settings.rag_initial_k,
                score_threshold=settings.rag_score_threshold,
            )
            return self.search_with_retriever(
                retriever=retriever,
                query=query,
                vector_store=vector_store,
                documents=documents,
                total_token_count=total_token_count,
            ).text
        except Exception:
            logger.exception("UnifiedRagPipeline.search 异常")
            return "检索过程中发生错误，请稍后重试。"

    def retrieve_documents(
        self,
        query: str,
        documents: list[Document],
        total_token_count: int = 0,
    ) -> list[Document]:
        """兼容旧接口：从 documents 构建 InMemoryVectorStore 后检索文档。

        供学习工作流等需要原始文档对象（非格式化文本）的调用方使用。

        Args:
            query: 用户查询文本
            documents: Document 列表
            total_token_count: 文档总 token 数（保留参数，保持签名兼容）

        Returns:
            按相关性排序的 Document 列表，无结果时返回空列表
        """
        if not documents:
            return []

        try:
            # 构建 InMemoryVectorStore
            try:
                embeddings = self._ensure_embeddings()
                vector_store = InMemoryVectorStore.from_documents(documents, embeddings)
            except Exception as e:
                logger.warning(f"构建向量索引失败: {e}")
                return []

            # 创建 retriever 并委托
            # C2/KV-1：同 search，改用应用层阈值过滤适配器
            retriever = _ScoreThresholdRetriever(
                vector_store,
                k=settings.rag_initial_k,
                score_threshold=settings.rag_score_threshold,
            )
            return self.retrieve_documents_from_retriever(
                retriever=retriever,
                query=query,
                vector_store=vector_store,
            )
        except Exception:
            logger.exception("UnifiedRagPipeline.retrieve_documents 异常")
            return []

    # ═══════════════════════════════════════════════════════════════════════════════
    # 内部: retriever 基础操作
    # ═══════════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _initial_search_from_retriever(
        retriever: BaseRetriever,
        query: str,
        raise_on_error: bool = False,
        with_scores: bool = False,
    ) -> list[Document]:
        """通过 retriever 执行初始搜索，异常时返回空列表（raise_on_error=True 时上抛）。

        with_scores=True（R-UX-04）：retriever 为纯 similarity 的 VectorStoreRetriever
        （鸭子判定：持有 vector_store 且 search_type=="similarity"）时改走
        search_scored 统一打分内核——检索行为等价（同为单次向量相似度检索），
        相似度分数经 doc.metadata["score"] 透出；score_semantics 取后端
        native_score_semantics 自描述（C-2arch），pgvector distance 自动归一相似度。
        异常语义不变（search_scored 不吞异常，仍由 raise_on_error 决定上抛或返回空）。
        """
        try:
            if with_scores:
                # langchain_core≥1.0 的 VectorStoreRetriever 字段名为 vectorstore（无下划线），
                # 旧版为 vector_store——双拼写兼容（冒烟实测：单拼写判定在 1.5.4 落空，
                # 静默回退 invoke，sources 无 score）
                vector_store = getattr(retriever, "vector_store", None) or getattr(
                    retriever, "vectorstore", None
                )
                search_type = getattr(retriever, "search_type", "similarity")
                if vector_store is not None and search_type == "similarity":
                    k = (getattr(retriever, "search_kwargs", None) or {}).get("k", 4)
                    scored = UnifiedRagPipeline.search_scored(
                        vector_store,
                        query,
                        k=k,
                        # 3.2：KB 主链路相似度阈值——默认 retriever_search_type=similarity
                        # 的纯相似度检索走统一打分内核后按 rag_score_threshold 过滤
                        #（pgvector distance 已归一 0~1 similarity，与附件路径
                        # _ScoreThresholdRetriever 同口径）；MMR/重排等包装检索器
                        # 不经过本分支，行为不变
                        score_threshold=settings.rag_score_threshold,
                        score_semantics=getattr(vector_store, "native_score_semantics", "similarity"),
                    )
                    docs = []
                    for doc, score in scored:
                        if score is not None:
                            doc.metadata["score"] = float(score)
                        docs.append(doc)
                    return docs
            return retriever.invoke(query)
        except Exception as e:
            if raise_on_error:
                raise
            logger.warning(f"初始检索失败: {e}")
            return []

    # ═══════════════════════════════════════════════════════════════════════════════
    # Level 1: 扩展向量检索 + 重排序
    # ═══════════════════════════════════════════════════════════════════════════════

    def _degradation_level1_from_retriever(
        self, vector_store: InMemoryVectorStore, query: str
    ) -> list[Document]:
        """Level 1 降级: 扩展向量检索 + FlashRank 重排序。

        阈值从 settings 读取（rag_degraded_threshold, rag_degraded_k 等）。
        3.1：伪"关键词召回"（逐关键词向量检索，分数丢弃不参与融合、每关键词
        多一次 embed）已删除——它不是关键词检索路径；真正的关键词降级在
        keyword_fallback（CJK 2-gram ILIKE，embedding 故障时的 PG 子串检索）。

        Args:
            vector_store: InMemoryVectorStore 实例（已有 embeddings）
            query: 用户查询文本

        Returns:
            重排序后的 Document 列表
        """
        all_docs: list[Document] = []

        # 1) 扩展向量检索: 降低阈值 + 增加 k
        try:
            # C2/KV-1：同上，core 1.5.4 不支持 similarity_score_threshold，改应用层过滤
            expanded_retriever = _ScoreThresholdRetriever(
                vector_store,
                k=settings.rag_degraded_k,
                score_threshold=settings.rag_degraded_threshold,
            )
            vector_docs = expanded_retriever.invoke(query)
            all_docs.extend(vector_docs)
            logger.debug(f"Level 1 扩展向量检索: {len(vector_docs)} 个文档")
        except Exception as e:
            logger.warning(f"Level 1 扩展向量检索失败: {e}")

        # 去重
        all_docs = _deduplicate_documents(all_docs, prefix_length=80)

        if not all_docs:
            return []

        # 3) FlashRank 重排序（如果可用）
        if FLASHRANK_AVAILABLE and ADVANCED_RETRIEVERS_AVAILABLE:
            try:
                # RP-10：经检索器工厂的缓存单例取重排器（同模型+top_n 进程级
                # 复用，不再每次降级重建百 MB ONNX）；构造失败仍回退原始结果
                from Django_xm.apps.knowledge.services.retrieval_service import get_flashrank_reranker

                reranker = get_flashrank_reranker(
                    "ms-marco-MiniLM-L-12-v2", settings.rag_rerank_top_n
                )
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

        # 无 FlashRank：返回去重后的前 N 个
        return all_docs[: settings.rag_rerank_top_n]

    # ═══════════════════════════════════════════════════════════════════════════════
    # Level 2: 全文 / 结构化提示
    # ═══════════════════════════════════════════════════════════════════════════════

    def _degradation_level2(self, documents: list[Document], total_token_count: int) -> str:
        """Level 2 降级: 根据 token 规模返回全文或结构化提示。"""
        source_label = "知识库" if self._scenario == "knowledge_base" else "附件"

        if 0 < total_token_count < settings.rag_fulltext_token_threshold:
            # 小文档: 返回全文
            full_text = "\n\n".join(doc.page_content for doc in documents)
            return f"未匹配到相关片段，以下基于{source_label}全文整理：\n\n{full_text}"

        # 大文档或未知 token: 返回结构化提示
        return self._build_structured_hint(documents, total_token_count)

    # ═══════════════════════════════════════════════════════════════════════════════
    # 格式化输出
    # ═══════════════════════════════════════════════════════════════════════════════

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
    def _truncate_doc_content(doc: Document, max_chars: int = _MULTI_KB_DOC_MAX_CHARS) -> Document:
        """单文档内容截断（B11，保头 + 注明截断），不改动入参 Document。

        multi-KB 工具输出侧防全文注入撑爆下游 LLM 上下文；截断上限 800 字符，
        与 strict_rag_chain._FORMAT_DOC_MAX_CHARS 同一口径。
        """
        content = doc.page_content
        if len(content) <= max_chars:
            return doc
        return Document(
            page_content=content[:max_chars].rstrip() + _MULTI_KB_TRUNCATED_TAG,
            metadata=doc.metadata,
        )

    def _build_structured_hint(self, documents: list[Document], total_token_count: int) -> str:
        """构建结构化提示（文档过大时）。

        按场景区分：
        - attachment: 附件 ID + 文件列表 + attachment_reader 引导
        - knowledge_base: 文档来源列表，无额外工具引导
        """
        token_display = f"约 {total_token_count}" if total_token_count > 0 else "未知"

        if self._scenario == "knowledge_base":
            source_names: list[str] = []
            seen: set[str] = set()
            for doc in documents:
                name = doc.metadata.get("source") or "未知"
                if name not in seen:
                    seen.add(name)
                    source_names.append(name)
            file_list = "\n".join(f"  - {name}" for name in source_names)
            return (
                f"未检索到与您问题相关的片段。\n"
                f"知识库包含以下文档：\n"
                f"{file_list}\n"
                f"总规模约 {token_display} token。\n"
                f"知识库中未找到与您问题相关的信息。"
            )

        # 附件场景：收集附件信息
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

        return (
            f"未检索到与您问题相关的片段。\n"
            f"附件信息：\n"
            f"{file_list}\n"
            f"总规模约 {token_display} token。\n"
            f"如需基于全文分析，请调用 attachment_reader 工具读取指定附件。"
        )

    def _fallback_full_text(self, documents: list[Document], total_token_count: int) -> str:
        """索引构建失败时的全文回退。"""
        source_label = "知识库" if self._scenario == "knowledge_base" else "附件"

        if 0 < total_token_count < settings.rag_fulltext_token_threshold:
            full_text = "\n\n".join(doc.page_content for doc in documents)
            return f"向量索引构建失败，以下基于{source_label}全文整理：\n\n{full_text}"
        return self._build_structured_hint(documents, total_token_count)
