"""
检索器模块
提供统一的检索器接口，支持多种检索策略
包含向量检索降级策略：向量检索失败 → PostgreSQL 全文关键词检索 → 空结果
"""

import asyncio
import json
import re
import time
from typing import Any, ClassVar, Literal

from langchain_core.documents import Document
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.retrievers import BaseRetriever
from langchain_core.tools import BaseTool
from langchain_core.vectorstores import VectorStore
from pydantic import BaseModel, Field

from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.apps.knowledge.config import settings

logger = get_logger(__name__)

SearchType = Literal["similarity", "mmr", "similarity_score_threshold"]

# ── 降级检索相关 ──────────────────────────────────────────────────────────────

_EMBEDDING_ERROR_PATTERNS = (
    "ConnectionError",
    "connect",
    "timeout",
    "refused",
    "embedding",
    "ollama",
    "insufficient_balance",
    "403",
    "503",
    "ConnectionRefusedError",
    "MaxRetriesError",
)


def _is_embedding_error(exc: Exception) -> bool:
    """判断异常是否为 Embedding 服务不可用导致"""
    exc_name = type(exc).__name__
    exc_msg = str(exc).lower()
    return any(pat.lower() in exc_name.lower() or pat.lower() in exc_msg for pat in _EMBEDDING_ERROR_PATTERNS)


# ── 关键词降级检索（CJK 友好） ────────────────────────────────────────────────

# 连续 CJK 字符片段（含扩展 A 区）或 ASCII 词
_GRAM_TOKEN_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]+|[A-Za-z0-9_]+")

# CJK 2-gram：中文无空格，单字召回噪声过大，3-gram 又会显著漏召回
_CJK_GRAM_SIZE = 2
_MAX_GRAMS = 16


def _build_retrieval_grams(query: str, max_grams: int = _MAX_GRAMS) -> list[str]:
    """把查询拆成适合 PostgreSQL 子串匹配的检索单元。

    - 连续 CJK 片段切为 2-gram：无需分词词典即可覆盖中文，兼顾精度与召回
    - ASCII 词整词保留（长度 >= 2）：避免 "SQL" 被切成 "SQ"/"QL" 造成误召回
    - 按 gram 长度降序（长 gram 区分度更高）去重后截断

    Args:
        query: 用户查询文本
        max_grams: 最多保留的 gram 数量，防止超长查询导致 SQL 开销失控

    Returns:
        gram 列表；查询不含有效字符时返回空列表
    """
    grams: list[str] = []

    for token in _GRAM_TOKEN_RE.findall(query or ""):
        if token.isascii():
            if len(token) >= 2:
                grams.append(token.lower())
        elif len(token) < _CJK_GRAM_SIZE:
            grams.append(token)
        else:
            grams.extend(token[i : i + _CJK_GRAM_SIZE] for i in range(len(token) - _CJK_GRAM_SIZE + 1))

    seen: set[str] = set()
    ordered: list[str] = []
    for gram in sorted(grams, key=len, reverse=True):
        if gram not in seen:
            seen.add(gram)
            ordered.append(gram)

    return ordered[:max_grams]


# 中文全文检索降级 SQL。
# 关键点：不能使用 to_tsvector/to_tsquery('simple', ...) —— PostgreSQL 的 simple
# 配置不做中文分词，整段连续中文会被解析成**单一词元**，导致任何中文子串查询都无法命中
# （实测 30 题中文评测集召回率为 0）。此处改为 unnest gram 数组后做 ILIKE 子串计数，
# 以命中 gram 数排序，命中数相同者优先短文档（主题更集中）。
_KEYWORD_FALLBACK_SQL = """
WITH grams AS (
    SELECT unnest(%s::text[]) AS gram
)
SELECT e.document,
       e.cmetadata,
       m.hit_count
  FROM {embedding_table} e
  JOIN {collection_table} c ON e.collection_id = c.uuid
 CROSS JOIN LATERAL (
       SELECT count(*) AS hit_count
         FROM grams g
        WHERE e.document ILIKE '%%' || g.gram || '%%'
 ) m
 WHERE c.name = %s
   AND m.hit_count > 0
 ORDER BY m.hit_count DESC, length(e.document) ASC
 LIMIT %s
"""


def _keyword_search_fallback(query: str, collection_name: str, k: int = 4) -> list[Document]:
    """PostgreSQL 关键词降级检索（Embedding 服务不可用时的兜底路径）

    以字符 2-gram 子串匹配替代 PostgreSQL 全文检索，从而支持中文查询。
    代价是失去 GIN 索引支持、退化为顺序扫描；作为兜底路径可接受。
    如需恢复索引加速，可在 PostgreSQL 侧引入 zhparser / pg_jieba 分词扩展或
    pg_bigm / pg_trgm 三元组索引（见 README Roadmap）。

    Args:
        query: 用户查询文本
        collection_name: PGVector collection 名称
        k: 返回文档数量

    Returns:
        Document 列表，metadata 含 degraded=True、degraded_score（∈[0,1] 的 gram 命中率）
        与 degraded_hits（命中 gram 数）；无可检索内容或出错时返回空列表
    """
    from django.db import connections

    from Django_xm.apps.knowledge.vector_store.pgvector_backend import (
        _check_table_exists,
        _get_collection_table_name,
        _get_embedding_table_name,
        _quote_identifier,
    )

    grams = _build_retrieval_grams(query)
    if not grams:
        logger.warning("降级关键词检索: 查询未提取到有效检索单元，返回空结果")
        return []

    collection_table = _get_collection_table_name()
    embedding_table = _get_embedding_table_name()
    # 使用 _quote_identifier 包装表名，防止 SQL 注入与保留字冲突
    collection_table_quoted = _quote_identifier(collection_table)
    embedding_table_quoted = _quote_identifier(embedding_table)

    if not _check_table_exists(collection_table) or not _check_table_exists(embedding_table):
        logger.warning("降级关键词检索: PGVector 表不存在，返回空结果")
        return []

    sql = _KEYWORD_FALLBACK_SQL.format(
        embedding_table=embedding_table_quoted,
        collection_table=collection_table_quoted,
    )

    try:
        with connections["default"].cursor() as cursor:
            # 表名来自固定内部函数并经 _quote_identifier 白名单包装（非用户输入），
            # 查询值（grams/collection_name/k）已全部 %s 参数化
            cursor.execute(sql, [grams, collection_name, k])
            rows = cursor.fetchall()

        results: list[Document] = []
        for document, cmetadata, hit_count in rows:
            # cmetadata 可能已是 dict，也可能是未反序列化的 JSON 字符串
            # （取决于驱动对 json/jsonb 列的解析行为），两种形态都要兼容。
            # 历史实现因 tsquery 从不命中、循环体从未执行，此处一直未被覆盖。
            if isinstance(cmetadata, str):
                try:
                    cmetadata = json.loads(cmetadata)
                except ValueError:
                    logger.warning("降级关键词检索: cmetadata 反序列化失败，按空元数据处理")
                    cmetadata = {}
            metadata = dict(cmetadata) if isinstance(cmetadata, dict) else {}
            metadata["degraded"] = True
            metadata["degraded_hits"] = int(hit_count)
            metadata["degraded_score"] = round(int(hit_count) / len(grams), 4)
            results.append(Document(page_content=document, metadata=metadata))

        logger.info(
            f"降级关键词检索: query='{query[:50]}', collection={collection_name}, "
            f"grams={len(grams)}, 返回 {len(results)} 个文档"
        )
        return results

    except Exception as e:
        logger.warning(f"降级关键词检索也失败，返回空结果: {e}")
        return []


def _record_degradation_metric(collection_name: str, error: Exception) -> None:
    """在 IndexMetadata.metadata_json 中记录降级次数"""
    try:
        from Django_xm.apps.knowledge.models import IndexMetadata

        meta = IndexMetadata.objects.filter(name=collection_name).first()
        if meta is None:
            return

        mj = meta.metadata_json or {}
        degradation = mj.get("degradation", {})
        degradation["total_count"] = degradation.get("total_count", 0) + 1
        degradation["last_error"] = str(error)[:500]
        degradation["last_time"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        mj["degradation"] = degradation
        meta.metadata_json = mj
        meta.save(update_fields=["metadata_json", "updated_at"])
    except Exception as e:
        logger.debug(f"记录降级指标失败（可忽略）: {e}")


# ── 检索器创建 ────────────────────────────────────────────────────────────────

try:
    from langchain_community.document_compressors import FlashRankReranker

    FLASHRANK_AVAILABLE = True
except ImportError:
    FLASHRANK_AVAILABLE = False

try:
    from langchain_classic.retrievers import ContextualCompressionRetriever, MultiQueryRetriever

    ADVANCED_RETRIEVERS_AVAILABLE = True
except ImportError:
    ADVANCED_RETRIEVERS_AVAILABLE = False


def create_retriever(
    vector_store: VectorStore,
    search_type: SearchType | None = None,
    k: int | None = None,
    score_threshold: float | None = None,
    fetch_k: int | None = None,
    use_reranker: bool = False,
    **kwargs,
) -> BaseRetriever:
    search_type: str = search_type or settings.retriever_search_type
    k = k or settings.retriever_k
    score_threshold = score_threshold or settings.retriever_score_threshold
    fetch_k = fetch_k or settings.retriever_fetch_k

    logger.info(f"创建检索器: search_type={search_type}, k={k}, use_reranker={use_reranker}")

    search_kwargs: dict[str, Any] = {"k": k}

    if search_type == "mmr":
        search_kwargs["fetch_k"] = fetch_k
    elif search_type == "similarity_score_threshold":
        search_kwargs["score_threshold"] = score_threshold

    search_kwargs.update(kwargs)

    try:
        retriever: BaseRetriever = vector_store.as_retriever(
            search_type=search_type,
            search_kwargs=search_kwargs,
        )
        logger.info("检索器创建成功")

        if use_reranker:
            reranker = create_reranker()
            if reranker is not None:
                retriever = create_reranking_retriever(retriever, reranker)
                logger.info("检索器已添加 Reranker 增强")

        return retriever
    except Exception:
        logger.exception("创建检索器失败")
        raise


def create_reranker(model_name: str = "ms-marco-MiniLM-L-12-v2", top_n: int = 5):
    if not FLASHRANK_AVAILABLE:
        logger.warning("FlashRankReranker 不可用，请安装 flashrank 包")
        return None

    try:
        reranker = FlashRankReranker(model_name=model_name, top_n=top_n)
        logger.info(f"FlashRankReranker 创建成功: model={model_name}, top_n={top_n}")
        return reranker
    except Exception as e:
        logger.warning(f"FlashRankReranker 创建失败: {e}")
        return None


def create_reranking_retriever(
    base_retriever: BaseRetriever,
    reranker=None,
) -> BaseRetriever:
    if reranker is None:
        reranker = create_reranker()
        if reranker is None:
            logger.warning("Reranker 不可用，返回基础检索器")
            return base_retriever

    if not ADVANCED_RETRIEVERS_AVAILABLE:
        logger.warning("ContextualCompressionRetriever 不可用，返回基础检索器")
        return base_retriever

    try:
        compression_retriever = ContextualCompressionRetriever(
            base_compressor=reranker,
            base_retriever=base_retriever,
        )
        logger.info("Reranking 检索器创建成功")
        return compression_retriever
    except Exception as e:
        logger.warning(f"创建 Reranking 检索器失败: {e}，返回基础检索器")
        return base_retriever


MULTI_QUERY_PROMPT = None
try:
    from langchain_core.prompts import PromptTemplate

    MULTI_QUERY_PROMPT = PromptTemplate.from_template(
        "你是一个AI语言模型助手。你的任务是生成3个不同版本的用户问题，"
        "用于从向量数据库中检索相关文档。通过生成多个视角的问题，"
        "你可以帮助用户克服基于距离相似度检索的一些局限性。"
        "请提供这些用换行符分隔的替代问题。\n\n"
        "原始问题: {question}\n\n替代问题:"
    )
except ImportError:
    pass


def create_multi_query_retriever(
    base_retriever: BaseRetriever,
    llm: BaseChatModel | None = None,
    include_original: bool = True,
) -> BaseRetriever:
    if not ADVANCED_RETRIEVERS_AVAILABLE:
        logger.warning("MultiQueryRetriever 不可用，返回基础检索器")
        return base_retriever

    if llm is None:
        logger.warning("LLM 未提供，MultiQueryRetriever 不可用，返回基础检索器")
        return base_retriever

    try:
        # 使用 RunnableLambda 包装 LLM，强制将 callbacks 设为空列表，
        # 防止 MultiQuery 内部 LLM 调用的输出被父级 callback 捕获
        # 并流式传输到前端（表现为 reasoning_content 或 content 泄漏）
        from langchain_core.output_parsers import BaseOutputParser
        from langchain_core.runnables import RunnableLambda

        class _LineListParser(BaseOutputParser[list[str]]):
            """将 LLM 输出按换行拆分为字符串列表，替代 langchain_classic 的 LineListOutputParser"""

            def parse(self, text: str) -> list[str]:
                return [line.strip() for line in text.strip().split("\n") if line.strip()]

        def _quiet_invoke(input, config=None, **kwargs):
            config = config or {}
            config["callbacks"] = []
            config["tags"] = [*config.get("tags", []), "nostream"]
            return llm.invoke(input, config=config, **kwargs)

        async def _quiet_ainvoke(input, config=None, **kwargs):
            config = config or {}
            config["callbacks"] = []
            config["tags"] = [*config.get("tags", []), "nostream"]
            return await llm.ainvoke(input, config=config, **kwargs)

        quiet_llm = RunnableLambda(func=_quiet_invoke, afunc=_quiet_ainvoke)
        prompt = MULTI_QUERY_PROMPT
        if prompt is None:
            from langchain.retrievers.multi_query import DEFAULT_QUERY_PROMPT

            prompt = DEFAULT_QUERY_PROMPT

        output_parser = _LineListParser()
        llm_chain = prompt | quiet_llm | output_parser

        multi_query_retriever = MultiQueryRetriever(
            retriever=base_retriever,
            llm_chain=llm_chain,
            include_original=include_original,
        )
        logger.info(f"MultiQueryRetriever 创建成功 (include_original={include_original}, callback隔离已启用)")
        return multi_query_retriever
    except Exception as e:
        logger.warning(f"MultiQueryRetriever 创建失败: {e}，返回基础检索器")
        return base_retriever


def create_advanced_retriever(
    base_retriever: BaseRetriever,
    llm: BaseChatModel | None = None,
    use_reranker: bool = False,
    use_multi_query: bool = False,
    include_original: bool = True,
) -> BaseRetriever:
    retriever = base_retriever

    if use_multi_query and llm is not None:
        retriever = create_multi_query_retriever(retriever, llm=llm, include_original=include_original)
        logger.info("高级检索器: 已启用 MultiQuery")

    if use_reranker:
        reranker = create_reranker()
        if reranker is not None:
            retriever = create_reranking_retriever(retriever, reranker)
            logger.info("高级检索器: 已启用 Reranker")

    logger.info("高级检索器创建完成")
    return retriever


def create_parent_document_retriever(
    vector_store: VectorStore,
    docstore=None,
    child_splitter=None,
    parent_splitter=None,
    child_k: int = 20,
    **kwargs,
) -> BaseRetriever:
    """
    创建 ParentDocumentRetriever

    核心思路：小块嵌入检索，返回包含该小块的完整父文档。
    解决长文档分块后上下文碎片化问题。

    Args:
        vector_store: 用于存储子文档嵌入的向量库
        docstore: 存储父文档的 docstore（默认 InMemoryDocstore）
        child_splitter: 子文档分块器（小块，用于检索）
        parent_splitter: 父文档分块器（大块，用于返回），None 表示不分块
        child_k: 检索子文档数量

    Returns:
        ParentDocumentRetriever 实例
    """
    try:
        from langchain_classic.retrievers import ParentDocumentRetriever
    except ImportError:
        logger.exception("ParentDocumentRetriever 不可用，请升级 langchain")
        raise

    if docstore is None:
        from langchain_classic.storage import InMemoryStore

        docstore = InMemoryStore()

    if child_splitter is None:
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        child_splitter = RecursiveCharacterTextSplitter(chunk_size=400, chunk_overlap=50)

    retriever = ParentDocumentRetriever(
        vectorstore=vector_store,
        docstore=docstore,
        child_splitter=child_splitter,
        parent_splitter=parent_splitter,
        search_kwargs={"k": child_k},
        **kwargs,
    )

    logger.info(f"ParentDocumentRetriever 创建成功 (child_k={child_k})")
    return retriever


class _RetrieverToolInput(BaseModel):
    query: str = Field(description="搜索查询")
    retrieval_mode: str | None = Field(default=None, description="检索模式: auto/precise/comprehensive")


# 模块级常量：避免 Pydantic BaseModel 将 _ 前缀属性转为 ModelPrivateAttr
# 导致 @classmethod 中 cls._XXX 返回 ModelPrivateAttr 对象而非实际值
KB_RESULT_INSTRUCTION = (
    "【知识库检索结果 - 以下为真实检索到的知识库内容，你已成功访问知识库】\n"
    "【回答规则 - 你必须遵守：】\n"
    "1. 以下「检索内容」部分就是知识库的真实数据，绝不允许声称无法访问知识库或知识库为空\n"
    "2. 你必须基于「检索内容」回答用户问题，忠实于检索内容，绝不允许编造或推断\n"
    "3. 提取与用户问题相关的核心要点，每个要点必须保留其领域上下文\n"
    "4. 如果用户要求总结知识库内容，应给出完整的主题概览\n"
    "5. 绝对不要输出：意图分类JSON、检索查询语句、替代问题、中间处理过程、元数据\n"
    "6. 如果检索内容确实与问题无关，直接告知用户\n"
    "7. 回答末尾标注「如需了解特定主题的详细信息，请告诉我」\n"
    "---检索内容开始---\n"
)
MAX_DOCS_IN_RESULT = 10
MAX_DOC_CONTENT_LENGTH = 800


class SyncSafeRetrieverTool(BaseTool):
    name: str = "knowledge_base"
    description: str = "搜索知识库中的相关信息。当需要回答关于文档内容的问题时使用此工具。输入应该是一个搜索查询。"
    args_schema: type[BaseModel] = _RetrieverToolInput
    retriever: BaseRetriever | None = None
    comprehensive_retriever: BaseRetriever | None = None
    retrieval_mode: str = "auto"
    llm: Any | None = None
    kb_name: str = ""
    kb_description: str = ""
    collection_names: list[str] = Field(default_factory=list)

    def _build_kb_context(self) -> str:
        """构建知识库上下文前缀，注入到工具返回结果中"""
        parts = []
        if self.kb_name:
            parts.append(f"知识库名称: {self.kb_name}")
        if self.kb_description:
            parts.append(f"知识库描述: {self.kb_description}")
        if parts:
            return "【知识库信息】\n" + "\n".join(parts) + "\n\n"
        return ""

    def _degrade_knowledge_search(self, query: str) -> str:
        """检索无结果时的知识库降级：加载全库文档走 UnifiedRagPipeline。

        与 attachment_rag_search 共享同一 5 步检索管道，
        降级仅在初始检索无结果时触发。
        """
        if not self.collection_names:
            return "知识库中未找到与您问题相关的信息。"

        from Django_xm.apps.knowledge.services.rag_retrieval import UnifiedRagPipeline, load_kb_documents

        documents, total_tokens = load_kb_documents(self.collection_names)
        pipeline = UnifiedRagPipeline(scenario="knowledge_base")
        return pipeline.search(query, documents, total_tokens)

    def _run(self, query: str, retrieval_mode: str | None = None) -> str:
        mode = retrieval_mode or self.retrieval_mode

        if mode == "auto":
            mode = self._classify_intent(query)

        if mode == "comprehensive":
            return self._run_comprehensive(query)
        return self._run_precise(query)

    async def _arun(self, query: str, retrieval_mode: str | None = None) -> str:
        mode = retrieval_mode or self.retrieval_mode

        if mode == "auto":
            mode = await self._classify_intent_async(query)

        if mode == "comprehensive":
            return await self._arun_comprehensive(query)
        return await asyncio.to_thread(self._run_precise, query)

    def _classify_intent(self, query: str) -> str:
        try:
            classifier = QueryIntentClassifier(llm=self.llm)
            return classifier.classify_sync(query)
        except Exception as e:
            logger.warning(f"意图分类异常，回退到 precise: {e}")
            return "precise"

    async def _classify_intent_async(self, query: str) -> str:
        try:
            classifier = QueryIntentClassifier(llm=self.llm)
            return await classifier.classify(query)
        except Exception as e:
            logger.warning(f"意图分类异常，回退到 precise: {e}")
            return "precise"

    def _run_precise(self, query: str) -> str:
        from Django_xm.apps.knowledge.services.rag_retrieval import UnifiedRagPipeline

        pipeline = UnifiedRagPipeline(scenario="knowledge_base")
        result = pipeline.search_with_retriever(
            retriever=self.retriever,
            query=query,
            vector_store=None,  # PGVector can't do InMemoryVectorStore keyword search
            documents=None,
            total_token_count=0,
        )
        # search_with_retriever returns formatted output; if it's the "no results" message, try degradation
        if "未找到" in result or "未检索到" in result or "暂无文档" in result:
            # Try to get docs from retriever for cleaning
            try:
                docs = self.retriever.invoke(query)
                cleaned = self._clean_docs(docs)
                if cleaned:
                    logger.info(f"precise 检索: query='{query[:50]}...', 返回 {len(cleaned)} 个文档 (Pipeline level=0)")
                    return self._build_kb_context() + KB_RESULT_INSTRUCTION + self._format_docs(cleaned)
            except Exception as e:
                logger.debug("precise 检索回退清理失败，进入降级检索: %s", e)
            return self._degrade_knowledge_search(query)
        logger.info(f"precise 检索 Pipeline: query='{query[:50]}...'")
        return self._build_kb_context() + KB_RESULT_INSTRUCTION + result

    def _run_comprehensive(self, query: str) -> str:
        from Django_xm.apps.knowledge.services.rag_retrieval import UnifiedRagPipeline

        retriever = self.comprehensive_retriever or self.retriever
        pipeline = UnifiedRagPipeline(scenario="knowledge_base")
        result = pipeline.search_with_retriever(
            retriever=retriever,
            query=query,
            vector_store=None,
            documents=None,
            total_token_count=0,
        )
        if "未找到" in result or "未检索到" in result or "暂无文档" in result:
            try:
                docs = retriever.invoke(query)
                cleaned = self._clean_docs(docs)
                if cleaned:
                    logger.info(
                        f"comprehensive 检索: query='{query[:50]}...', "
                        f"返回 {len(cleaned)} 个文档 (Pipeline level=0)"
                    )
                    combiner = MapReduceDocCombiner(llm=self.llm)
                    combined = combiner.combine_sync(cleaned, query, llm=self.llm)
                    return self._build_kb_context() + KB_RESULT_INSTRUCTION + combined
            except Exception as e:
                logger.debug("comprehensive 检索回退清理失败，进入降级检索: %s", e)
            return self._degrade_knowledge_search(query)
        logger.info(f"comprehensive 检索 Pipeline: query='{query[:50]}...'")
        return self._build_kb_context() + KB_RESULT_INSTRUCTION + result

    async def _arun_comprehensive(self, query: str) -> str:
        import asyncio

        from Django_xm.apps.knowledge.services.rag_retrieval import UnifiedRagPipeline

        retriever = self.comprehensive_retriever or self.retriever
        pipeline = UnifiedRagPipeline(scenario="knowledge_base")
        result = pipeline.search_with_retriever(
            retriever=retriever,
            query=query,
            vector_store=None,
            documents=None,
            total_token_count=0,
        )
        if "未找到" in result or "未检索到" in result or "暂无文档" in result:
            try:
                try:
                    docs = await retriever.ainvoke(query)
                except Exception:
                    docs = await asyncio.to_thread(retriever.invoke, query)
                cleaned = self._clean_docs(docs)
                if cleaned:
                    logger.info(
                        f"comprehensive 异步检索: query='{query[:50]}...', "
                        f"返回 {len(cleaned)} 个文档 (Pipeline level=0)"
                    )
                    combiner = MapReduceDocCombiner(llm=self.llm)
                    combined = await combiner.combine(cleaned, query, llm=self.llm)
                    return self._build_kb_context() + KB_RESULT_INSTRUCTION + combined
            except Exception as e:
                logger.debug("comprehensive 异步检索回退清理失败，进入降级检索: %s", e)
            return self._degrade_knowledge_search(query)
        logger.info(f"comprehensive 异步检索 Pipeline: query='{query[:50]}...'")
        return self._build_kb_context() + KB_RESULT_INSTRUCTION + result

    @staticmethod
    def _clean_docs(docs: list[Document]) -> list[Document]:
        """清理检索结果中的非文档内容

        过滤两类不应出现在检索结果中的内容：
        1. 意图分类等内部 JSON 数据（辅助 LLM 输出被包装为 Document）
        2. MultiQuery 生成的替代查询（短问题文本被包装为 Document）

        不需要在此兜底。
        """
        cleaned = []
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
            if len(stripped) < 150 and (stripped.endswith(("？", "?"))):
                continue
            cleaned.append(doc)
        if len(cleaned) != len(docs):
            logger.debug(f"文档清理: {len(docs)} -> {len(cleaned)}（过滤了 {len(docs) - len(cleaned)} 个非文档内容）")
        return cleaned

    @classmethod
    def _format_docs(cls, docs: list[Document]) -> str:
        if not docs:
            return "未找到相关文档。"
        # 限制文档数量和单文档长度，避免上下文过长导致 LLM 原文复述
        limited_docs = docs[:MAX_DOCS_IN_RESULT]
        formatted = []
        for i, doc in enumerate(limited_docs, 1):
            source = doc.metadata.get("source", "未知来源")
            content = doc.page_content
            if len(content) > MAX_DOC_CONTENT_LENGTH:
                content = content[:MAX_DOC_CONTENT_LENGTH].rstrip() + "...[内容已截断]"
            formatted.append(f"[{i}] (来源: {source})\n{content}")
        if len(docs) > MAX_DOCS_IN_RESULT:
            formatted.append(f"\n[注: 共检索到 {len(docs)} 个文档，已展示前 {MAX_DOCS_IN_RESULT} 个最相关文档]")
        return "\n\n".join(formatted)


def create_retriever_tool(
    retriever: BaseRetriever,
    name: str = "knowledge_base",
    description: str | None = None,
    retrieval_mode: str = "auto",
    comprehensive_retriever: BaseRetriever | None = None,
    llm: Any | None = None,
    kb_name: str = "",
    kb_description: str = "",
    collection_names: list[str] | None = None,
) -> BaseTool:
    if description is None:
        description = (
            f"搜索 {name} 知识库中的相关信息。当需要回答关于文档内容的问题时使用此工具。输入应该是一个搜索查询。"
        )

    logger.info(f"创建检索器工具: {name}, retrieval_mode={retrieval_mode}")

    try:
        tool = SyncSafeRetrieverTool(
            retriever=retriever,
            name=name,
            description=description,
            retrieval_mode=retrieval_mode,
            comprehensive_retriever=comprehensive_retriever,
            llm=llm,
            kb_name=kb_name,
            kb_description=kb_description,
            collection_names=collection_names or [],
        )
        logger.info(f"检索器工具创建成功（SyncSafe 模式, retrieval_mode={retrieval_mode}）")
        return tool
    except Exception:
        logger.exception("创建检索器工具失败")
        raise


def test_retriever(
    retriever: BaseRetriever,
    query: str = "测试查询",
    show_results: bool = True,
) -> bool:
    """测试检索器是否正常工作"""
    try:
        logger.info(f"🧪 测试检索器: query='{query}'")

        docs = retriever.invoke(query)

        logger.info(f"✅ 检索成功: 找到 {len(docs)} 个文档")

        if show_results and docs:
            logger.info("📄 检索结果:")
            for i, doc in enumerate(docs, 1):
                logger.info(f"   [{i}] {doc.page_content[:100]}...")
                if doc.metadata:
                    logger.info(f"       元数据: {doc.metadata}")

        return True

    except Exception:
        logger.exception("❌ 检索器测试失败")
        return False


_INTENT_CLASSIFICATION_PROMPT = """分析用户查询的意图类型：

1. precise - 精准查找：需要定位特定文档/数据/事实，答案通常在1-2个文档中。特征：包含具体名称、编号、日期等
2. comprehensive - 全局分析：需要广泛收集信息，答案分布在多个文档中。特征：包含"所有"、"全部"、"分析"、"总结"、\
"对比"、"概述"等词

用户查询: {query}

仅返回 JSON: {{"intent": "precise", "confidence": 0.9}} 或 {{"intent": "comprehensive", "confidence": 0.9}}"""


class QueryIntentClassifier:
    _cache: ClassVar[dict] = {}
    _cache_ttl: int = 60

    def __init__(self, llm: BaseChatModel | None = None):
        self._llm = llm

    def _get_llm(self) -> BaseChatModel | None:
        if self._llm is not None:
            return self._llm
        try:
            from Django_xm.apps.ai_engine.services.llm_factory import get_helper_model

            llm = get_helper_model()
            if llm is not None:
                return llm
            from Django_xm.apps.knowledge.config import get_chat_model

            return get_chat_model(streaming=False)
        except Exception:
            return None

    def _parse_intent(self, response_text: Any) -> str:
        response_text = response_text if isinstance(response_text, str) else str(response_text)
        match = re.search(r"\{[^}]+\}", response_text)
        if match:
            try:
                data = json.loads(match.group())
                intent = data.get("intent", "precise")
                if intent in ("precise", "comprehensive"):
                    return intent
            except (json.JSONDecodeError, KeyError):
                pass
        return "precise"

    def _check_cache(self, query: str) -> str | None:
        now = time.time()
        cached = self._cache.get(query)
        if cached and (now - cached["ts"]) < self._cache_ttl:
            return cached["intent"]
        return None

    def _set_cache(self, query: str, intent: str) -> None:
        self._cache[query] = {"intent": intent, "ts": time.time()}

    def classify_sync(self, query: str) -> str:
        from Django_xm.apps.knowledge.config import settings as app_cfg

        if not app_cfg.retriever_intent_classification_enabled:
            return "precise"

        cached = self._check_cache(query)
        if cached is not None:
            logger.debug(f"意图分类缓存命中: query='{query[:30]}...' -> {cached}")
            return cached

        llm = self._get_llm()
        if llm is None:
            logger.warning("意图分类: LLM 不可用，回退到 precise")
            return "precise"

        try:
            from langchain_core.messages import HumanMessage

            prompt = _INTENT_CLASSIFICATION_PROMPT.format(query=query)
            response = llm.invoke(
                [HumanMessage(content=prompt)],
                config={"timeout": 3, "callbacks": [], "tags": ["nostream"]},  # type: ignore[arg-type]  # RunnableConfig accepts arbitrary keys
            )
            intent = self._parse_intent(str(response.content) if hasattr(response, "content") else str(response))
            self._set_cache(query, intent)
            logger.info(f"意图分类: query='{query[:50]}...' -> {intent}")
            return intent
        except Exception as e:
            logger.warning(f"意图分类失败，回退到 precise: {e}")
            return "precise"

    async def classify(self, query: str) -> str:
        from Django_xm.apps.knowledge.config import settings as app_cfg

        if not app_cfg.retriever_intent_classification_enabled:
            return "precise"

        cached = self._check_cache(query)
        if cached is not None:
            logger.debug(f"意图分类缓存命中: query='{query[:30]}...' -> {cached}")
            return cached

        llm = self._get_llm()
        if llm is None:
            logger.warning("意图分类: LLM 不可用，回退到 precise")
            return "precise"

        try:
            import asyncio

            from langchain_core.messages import HumanMessage

            prompt = _INTENT_CLASSIFICATION_PROMPT.format(query=query)
            response = await asyncio.wait_for(
                llm.ainvoke([HumanMessage(content=prompt)], config={"callbacks": [], "tags": ["nostream"]}),  # type: ignore[arg-type]  # RunnableConfig accepts arbitrary keys
                timeout=3.0,
            )
            intent = self._parse_intent(str(response.content) if hasattr(response, "content") else str(response))
            self._set_cache(query, intent)
            logger.info(f"意图分类: query='{query[:50]}...' -> {intent}")
            return intent
        except TimeoutError:
            logger.warning("意图分类超时(3s)，回退到 precise")
            return "precise"
        except Exception as e:
            logger.warning(f"意图分类失败，回退到 precise: {e}")
            return "precise"


_MAP_PROMPT_TEMPLATE = (
    """从以下文档内容中提取与问题相关的核心要点。

严格规则（必须遵守）：
- 必须忠实于文档原文，只提取文档中实际出现的信息，绝不允许编造或推断
- 提取的每个要点必须保留其领域上下文（如文档讲的是 Redis，要点中必须体现 Redis 而非泛化为"管理"或"技术"）
- 只提取关键信息和数据，不要保留详细示例、代码片段或配置细节
- 如果问题是概览性的（如"写了什么"、"有哪些内容"、"总结一下"等），应提取文档的核心主题和要点，"""
    "不要返回 [NO_RELEVANT_INFO]\n"
    "- 只有当文档内容与问题明确无关（如问的是 Redis，文档讲的是完全不同的领域）时，"
    "才回复 [NO_RELEVANT_INFO]\n"
    "\n"
    "文档内容: {doc}\n"
    "问题: {question}\n"
    "核心要点:"
)

_REDUCE_PROMPT_TEMPLATE = """以下是多个文档片段中提取的相关信息，请合并去重，整理为简洁的内容摘要。

重要规则：
- 必须忠实于提供的摘要内容，绝不允许编造、推断或泛化
- 每个主题必须保留其领域上下文（如原文提到 Redis 缓存，不能泛化为"缓存"或"技术"）
- 只提取核心要点和关键数据，不要保留详细示例、代码片段或配置细节
- 每个主题用2-3句话概括即可
- 如果多个文档提到相同内容，只保留一次
- 输出应该是精炼的摘要，而非原文复述
- 摘要总长度控制在800字以内，只保留最关键的信息

{summaries}

问题: {question}
内容摘要:"""

_STUFF_PROMPT_TEMPLATE = """以下是从知识库中检索到的参考资料。请提取其中与问题相关的核心要点，整理为简洁的结构化摘要。

重要规则：
- 必须忠实于参考资料原文，只提取文档中实际出现的信息，绝不允许编造或推断
- 提取的每个要点必须保留其领域上下文（如资料讲的是 Redis，要点中必须体现 Redis 而非泛化为"管理"或"技术"）
- 只提取核心要点和关键数据，不要保留详细示例、代码片段或配置细节
- 每个主题用2-3句话概括即可
- 如果资料确实与问题完全无关，仅回复 [NO_RELEVANT_INFO]
- 输出应该是精炼的摘要，而非原文复述

{context}

问题: {question}
相关信息摘要:"""


class MapReduceDocCombiner:
    def __init__(self, llm: BaseChatModel | None = None, batch_size: int | None = None):
        self._llm = llm
        self._batch_size = batch_size

    def _get_llm(self) -> BaseChatModel | None:
        if self._llm is not None:
            return self._llm
        try:
            from Django_xm.apps.ai_engine.services.llm_factory import get_helper_model

            llm = get_helper_model()
            if llm is not None:
                return llm
            from Django_xm.apps.knowledge.config import get_chat_model

            return get_chat_model(streaming=False)
        except Exception:
            return None

    def _get_batch_size(self) -> int:
        if self._batch_size is not None:
            return self._batch_size
        from Django_xm.apps.knowledge.config import settings as app_cfg

        return app_cfg.retriever_map_reduce_batch_size

    def _should_use_map_reduce(self, doc_count: int) -> bool:
        return doc_count > self._get_batch_size() * 1.5

    def _map_batch_sync(self, docs: list[Document], query: str, llm: BaseChatModel) -> list[str]:
        from langchain_core.messages import HumanMessage

        results = []
        for doc in docs:
            prompt = _MAP_PROMPT_TEMPLATE.format(doc=doc.page_content, question=query)
            try:
                response = llm.invoke([HumanMessage(content=prompt)], config={"callbacks": [], "tags": ["nostream"]})  # type: ignore[arg-type]  # RunnableConfig accepts arbitrary keys
                content = str(response.content) if hasattr(response, "content") else str(response)
                results.append(content)
            except Exception as e:
                logger.warning(f"Map 阶段文档处理失败: {e}")
                results.append("")
        return results

    async def _map_batch_async(self, docs: list[Document], query: str, llm: BaseChatModel) -> list[str]:
        import asyncio

        from langchain_core.messages import HumanMessage

        tasks = []
        for doc in docs:
            prompt = _MAP_PROMPT_TEMPLATE.format(doc=doc.page_content, question=query)
            tasks.append(llm.ainvoke([HumanMessage(content=prompt)], config={"callbacks": [], "tags": ["nostream"]}))  # type: ignore[arg-type]  # RunnableConfig accepts arbitrary keys
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        results = []
        for resp in responses:
            if isinstance(resp, Exception):
                logger.warning(f"Map 阶段文档处理失败: {resp}")
                results.append("")
            else:
                content = str(resp.content) if hasattr(resp, "content") else str(resp)
                results.append(content)
        return results

    def combine_sync(self, docs: list[Document], query: str, llm: BaseChatModel | None = None) -> str:
        from langchain_core.messages import HumanMessage

        if not docs:
            return "未检索到相关文档"

        llm = llm or self._get_llm()
        if llm is None:
            return SyncSafeRetrieverTool._format_docs(docs)

        if not self._should_use_map_reduce(len(docs)):
            context = SyncSafeRetrieverTool._format_docs(docs)
            prompt = _STUFF_PROMPT_TEMPLATE.format(context=context, question=query)
            try:
                response = llm.invoke([HumanMessage(content=prompt)], config={"callbacks": [], "tags": ["nostream"]})  # type: ignore[arg-type]  # RunnableConfig accepts arbitrary keys
                result = str(response.content) if hasattr(response, "content") else str(response)
                if "[NO_RELEVANT_INFO]" in result:
                    logger.warning("Stuff 策略 LLM 判定无相关信息，回退到原始文档")
                    return context
                if len(result) > 3000:
                    result = result[:3000].rstrip() + "\n\n[内容已截断，以上为最相关的核心要点摘要]"
                return result
            except Exception as e:
                logger.warning(f"Stuff 策略失败，回退到原始文档: {e}")
                return context

        batch_size = self._get_batch_size()
        batches = [docs[i : i + batch_size] for i in range(0, len(docs), batch_size)]
        logger.info(f"Map-Reduce: {len(docs)} 个文档分为 {len(batches)} 批处理")

        all_summaries = []
        for i, batch in enumerate(batches):
            summaries = self._map_batch_sync(batch, query, llm)
            valid = [s for s in summaries if s and "[NO_RELEVANT_INFO]" not in s and len(s.strip()) > 10]
            all_summaries.extend(valid)
            logger.debug(f"Map 批次 {i + 1}/{len(batches)}: {len(valid)} 个有效摘要")

        if not all_summaries:
            logger.warning(f"Map-Reduce 全部摘要被过滤，回退到原始文档 (docs={len(docs)})")
            context = SyncSafeRetrieverTool._format_docs(docs)
            return context[:3000]

        combined = "\n\n---\n\n".join(all_summaries)
        reduce_prompt = _REDUCE_PROMPT_TEMPLATE.format(summaries=combined, question=query)
        try:
            response = llm.invoke([HumanMessage(content=reduce_prompt)], config={"callbacks": [], "tags": ["nostream"]})  # type: ignore[arg-type]  # RunnableConfig accepts arbitrary keys
            result = str(response.content) if hasattr(response, "content") else str(response)
            # 限制 Reduce 结果长度，避免返回过多内容给 LLM
            if len(result) > 3000:
                result = result[:3000].rstrip() + "\n\n[内容已截断，以上为最相关的核心要点摘要]"
            logger.info(f"Map-Reduce 完成: {len(docs)} 文档 -> {len(all_summaries)} 摘要 -> 最终回答")
            return result
        except Exception as e:
            logger.warning(f"Reduce 阶段失败，返回合并摘要: {e}")
            return combined

    async def combine(self, docs: list[Document], query: str, llm: BaseChatModel | None = None) -> str:
        from langchain_core.messages import HumanMessage

        if not docs:
            return "未检索到相关文档"

        llm = llm or self._get_llm()
        if llm is None:
            return SyncSafeRetrieverTool._format_docs(docs)

        if not self._should_use_map_reduce(len(docs)):
            context = SyncSafeRetrieverTool._format_docs(docs)
            prompt = _STUFF_PROMPT_TEMPLATE.format(context=context, question=query)
            try:
                response = await llm.ainvoke(
                    [HumanMessage(content=prompt)], config={"callbacks": [], "tags": ["nostream"]}  # type: ignore[arg-type]  # RunnableConfig accepts arbitrary keys
                )
                result = str(response.content) if hasattr(response, "content") else str(response)
                if "[NO_RELEVANT_INFO]" in result:
                    logger.warning("Stuff 策略 LLM 判定无相关信息，回退到原始文档")
                    return context
                if len(result) > 3000:
                    result = result[:3000].rstrip() + "\n\n[内容已截断，以上为最相关的核心要点摘要]"
                return result
            except Exception as e:
                logger.warning(f"Stuff 策略失败，回退到原始文档: {e}")
                return context

        batch_size = self._get_batch_size()
        batches = [docs[i : i + batch_size] for i in range(0, len(docs), batch_size)]
        logger.info(f"Map-Reduce: {len(docs)} 个文档分为 {len(batches)} 批处理")

        all_summaries = []
        for i, batch in enumerate(batches):
            summaries = await self._map_batch_async(batch, query, llm)
            valid = [s for s in summaries if s and "[NO_RELEVANT_INFO]" not in s and len(s.strip()) > 10]
            all_summaries.extend(valid)
            logger.debug(f"Map 批次 {i + 1}/{len(batches)}: {len(valid)} 个有效摘要")

        if not all_summaries:
            logger.warning(f"Map-Reduce 全部摘要被过滤，回退到原始文档 (docs={len(docs)})")
            context = SyncSafeRetrieverTool._format_docs(docs)
            return context[:1000]

        combined = "\n\n---\n\n".join(all_summaries)
        reduce_prompt = _REDUCE_PROMPT_TEMPLATE.format(summaries=combined, question=query)
        try:
            response = await llm.ainvoke(
                [HumanMessage(content=reduce_prompt)], config={"callbacks": [], "tags": ["nostream"]}  # type: ignore[arg-type]  # RunnableConfig accepts arbitrary keys
            )
            result = str(response.content) if hasattr(response, "content") else str(response)
            # 限制 Reduce 结果长度，避免返回过多内容给 LLM
            if len(result) > 3000:
                result = result[:3000].rstrip() + "\n\n[内容已截断，以上为最相关的核心要点摘要]"
            logger.info(f"Map-Reduce 完成: {len(docs)} 文档 -> {len(all_summaries)} 摘要 -> 最终回答")
            return result
        except Exception as e:
            logger.warning(f"Reduce 阶段失败，返回合并摘要: {e}")
            return combined


class _RRFEnsembleRetriever(BaseRetriever):
    """基于 Reciprocal Rank Fusion 的组合检索器

    替代已从 langchain_community 0.4+ 移除的 EnsembleRetriever。
    使用加权 RRF 算法合并多个检索器的结果。
    """

    retrievers: list[BaseRetriever] = Field(default_factory=list)
    weights: list[float] = Field(default_factory=list)
    c: int = Field(default=60, description="RRF 常数，通常为 60")
    k: int = Field(default=4, description="最终返回的文档数")

    class Config:
        arbitrary_types_allowed = True

    def _get_relevant_documents(self, query: str) -> list[Document]:
        doc_scores: dict = {}
        doc_map: dict = {}

        for retriever, weight in zip(self.retrievers, self.weights, strict=False):
            try:
                docs = retriever.invoke(query)
            except Exception as e:
                logger.warning(f"检索器 {retriever} 调用失败: {e}")
                continue

            for rank, doc in enumerate(docs, 1):
                content_key = doc.page_content
                if content_key not in doc_scores:
                    doc_scores[content_key] = 0.0
                    doc_map[content_key] = doc
                doc_scores[content_key] += weight / (self.c + rank)

        sorted_items = sorted(doc_scores.items(), key=lambda x: x[1], reverse=True)
        return [doc_map[k] for k, _ in sorted_items[: self.k]]

    async def _aget_relevant_documents(self, query: str) -> list[Document]:
        doc_scores: dict = {}
        doc_map: dict = {}

        for retriever, weight in zip(self.retrievers, self.weights, strict=False):
            try:
                docs = await retriever.ainvoke(query)
            except Exception:
                try:
                    docs = await asyncio.to_thread(retriever.invoke, query)
                except Exception as e:
                    logger.warning(f"检索器 {retriever} 调用失败: {e}")
                    continue

            for rank, doc in enumerate(docs, 1):
                content_key = doc.page_content
                if content_key not in doc_scores:
                    doc_scores[content_key] = 0.0
                    doc_map[content_key] = doc
                doc_scores[content_key] += weight / (self.c + rank)

        sorted_items = sorted(doc_scores.items(), key=lambda x: x[1], reverse=True)
        return [doc_map[k] for k, _ in sorted_items[: self.k]]


def create_multi_retriever(
    retrievers: list,
    weights: list | None = None,
    **kwargs,
) -> BaseRetriever:
    """创建多检索器（基于 RRF 的组合检索器）"""
    try:
        logger.info(f"🔗 创建组合检索器: {len(retrievers)} 个检索器")

        if weights is None:
            weights = [1.0 / len(retrievers)] * len(retrievers)

        k = kwargs.pop("k", 4)
        c = kwargs.pop("c", 60)

        ensemble = _RRFEnsembleRetriever(
            retrievers=retrievers,
            weights=weights,
            k=k,
            c=c,
        )

        logger.info("✅ 组合检索器创建成功")
        return ensemble

    except Exception:
        logger.exception("❌ 创建组合检索器失败")
        raise


def get_retriever_config(search_type: str = "similarity") -> dict:
    """获取推荐的检索器配置"""
    configs = {
        "similarity": {
            "search_type": "similarity",
            "k": 4,
            "description": "基本相似度检索，速度快",
        },
        "mmr": {
            "search_type": "mmr",
            "k": 4,
            "fetch_k": 20,
            "description": "最大边际相关性检索，结果更多样化",
        },
        "threshold": {
            "search_type": "similarity_score_threshold",
            "score_threshold": 0.7,
            "k": 10,
            "description": "相似度阈值过滤，只返回高质量结果",
        },
    }

    if search_type not in configs:
        logger.warning(f"未知的检索类型: {search_type}，使用默认配置")
        return configs["similarity"]

    config = configs[search_type].copy()
    logger.info(f"📋 推荐的检索器配置 ({search_type}):")
    logger.info(f"   {config.get('description', '')}")

    config.pop("description", None)

    return config
