"""
严格 RAG Chain 模块

标准 RAG 架构：检索 -> 上下文构建 -> 生成
与 Agent 模式的区别：
- Agent 模式：模型自主决定是否调用检索工具，可能绕过知识库直接回答
- Chain 模式：强制先检索，将检索结果注入 prompt 上下文，模型仅基于上下文生成

这是行业主流 RAG 架构，确保智能体严格依据知识库内容输出。
支持检索降级：向量检索失败时自动降级到 PostgreSQL 全文关键词检索。

对外入口（sync/async 双版本共享同一检索与降级内核）：
- query_strict_rag：同步字典结果
- stream_strict_rag：同步生成器（SSE 事件流）
- astream_strict_rag：异步生成器（SSE 事件流）

引用体系（Task 4/B1）：上下文编号与 sources 同源生成——按来源文件分组后
一个文件一个编号，答案正文 [n] 与 sources[n-1] 一一对应；生成后校验剔除
越界引用（同步答案整体替换，流式路径经 _StreamingCitationFilter 增量过滤）。
"""

import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Any

from langchain_core.documents import Document
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.retrievers import BaseRetriever

from Django_xm.apps.context_manager.services.compression import TokenEstimator
from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.apps.knowledge.config import settings
from Django_xm.apps.knowledge.rag_settings import get_rag_runtime_settings
from Django_xm.apps.knowledge.services.context_format import (
    DATA_BOUNDARY_NOTICE,
    RAG_CONTEXT_MAX_TOTAL_CHARS,
    RAG_DOC_MAX_CHARS,
    TRUNCATED_TAG,
    truncate_content,
    truncate_content_by_tokens,
)

logger = get_logger(__name__)

# 空结果受控应答与降级提示（Task 4/B3：空结果不再走全库重嵌入降级管道，
# 直接返回受控文案，degraded 置位；结果不写查询缓存，见 views_rag）
NO_RESULT_ANSWER = "根据知识库中的资料，未找到与您问题相关的信息。"
NO_RESULT_NOTICE = "本次检索未命中知识库内容"
KEYWORD_DEGRADATION_NOTICE = "向量检索服务暂时不可用，结果由关键词检索提供，相关性可能低于正常水平"

STRICT_RAG_QA_PROMPT = (
    "基于以下参考资料回答用户问题。如果参考资料中没有相关信息，请明确说明。\n\n"
    "参考资料：\n{context}\n\n"
    "用户问题：{question}\n\n"
    "回答要求：\n"
    "1. 仅基于上述参考资料回答，不得使用自身知识。\n"
    "2. 引用参考资料内容时，在对应表述末尾标注其编号（如 [1]、[2]），编号与参考资料标记一致。\n"
    f"3. {DATA_BOUNDARY_NOTICE}\n\n"
    "回答："
)

# ── 引用校验（Task 4/B1）──────────────────────────────────────────────────────
# 答案中的引用编号形如 [1]、[2]；有效范围 1..len(sources)，越界一律剔除
_CITATION_PATTERN = re.compile(r"\[(\d+)\]")
# 末尾可能是未完成引用的前缀："[12"（流式分片可能把 [n] 切开）
_CITATION_PARTIAL_TAIL = re.compile(r"\[\d*\Z")
# 引用 token 最长持有长度（含 "["）：超过按普通文本发出，防止异常长数字串阻塞流
_MAX_CITATION_TOKEN_LEN = 12

# B9/P0-O18：上下文预算单源（context_format）——保留模块级别名以兼容既有导入/测试
_FORMAT_DOC_MAX_CHARS = RAG_DOC_MAX_CHARS
_FORMAT_DOCS_MAX_TOTAL_CHARS = RAG_CONTEXT_MAX_TOTAL_CHARS
_FORMAT_TRUNCATED_TAG = TRUNCATED_TAG


def _resolve_chat_model(model, streaming: bool = False) -> BaseChatModel:
    """从模型字符串或实例解析出 ChatModel

    get_chat_model() 已内置 fallback，主模型失败时自动切换。

    Args:
        model: 模型字符串（如 "openai:gpt-4o"）或 BaseChatModel 实例或 None
        streaming: 是否流式输出

    Returns:
        BaseChatModel 实例（可能带 fallback）
    """
    from Django_xm.apps.knowledge.config import get_chat_model

    if model is None:
        return get_chat_model(streaming=streaming)
    if isinstance(model, BaseChatModel):
        return model
    # model 是字符串，如 "openai:gpt-4o"
    if ":" in model:
        provider, model_name = model.split(":", 1)
        return get_chat_model(model_name=model_name, model_provider=provider, streaming=streaming)
    return get_chat_model(model_name=model, streaming=streaming)


def _group_docs_by_source(docs: list[Document]) -> list[list[Document]]:
    """按来源显示名分组（保持首现顺序）——引用编号与 sources 的同源权威（Task 4/B1）。

    此前 _format_docs 按 chunk 序编号、_extract_sources 按来源文件去重，同一
    文件贡献多个 chunk 时答案中的 [n] 与 sources 错位。分组后一个来源文件一个
    编号：第 i 组在 prompt 中标记 [i]，对应 sources[i-1]。
    """
    groups: list[list[Document]] = []
    index_by_source: dict[str, int] = {}
    for doc in docs:
        source_name = _display_source_name(doc)
        idx = index_by_source.get(source_name)
        if idx is None:
            index_by_source[source_name] = len(groups)
            groups.append([doc])
        else:
            groups[idx].append(doc)
    return groups


def _format_docs(docs: list[Document]) -> str:
    """将检索到的文档格式化为上下文字符串（编号按来源文件，与 sources 同源）

    同一文件的多个 chunk 共用同一编号 [i]，续块标注" (续)"，
    保证答案正文 [n] 与 sources[n-1] 一一对应。

    B9 上下文预算：单个 chunk 内容截断至 _FORMAT_DOC_MAX_CHARS（保头 + 注明）；
    整体按
    _FORMAT_DOCS_MAX_TOTAL_CHARS 预算依次装填，放不下的整块丢弃并注明，
    防止 prompt 无界膨胀（此前整段拼接无任何预算）。
    """
    if not docs:
        return "（未检索到任何相关文档）"

    # 检测是否存在降级标记
    has_degraded = any(doc.metadata.get("degraded") for doc in docs)

    formatted_parts: list[str] = []
    total_tokens = 0
    runtime = get_rag_runtime_settings()
    max_doc_tokens = runtime["doc_max_tokens"]
    max_total_tokens = runtime["context_max_tokens"]
    budget_exceeded = False
    for i, group in enumerate(_group_docs_by_source(docs), 1):
        if budget_exceeded:
            break
        source = _display_source_name(group[0])
        for j, doc in enumerate(group):
            content, _truncated = truncate_content(doc.page_content, _FORMAT_DOC_MAX_CHARS)
            content, _truncated_tokens = truncate_content_by_tokens(content, max_doc_tokens)
            continue_tag = " (续)" if j > 0 else ""
            degraded_tag = " [关键词检索-降级模式]" if doc.metadata.get("degraded") else ""
            part = f"--- 参考资料 [{i}] 来源: {source}{continue_tag}{degraded_tag} ---\n{content}\n"
            part_tokens = TokenEstimator.estimate_tokens(part)
            if total_tokens + part_tokens > max_total_tokens:
                budget_exceeded = True
                break
            formatted_parts.append(part)
            total_tokens += part_tokens

    result = "\n".join(formatted_parts)
    if budget_exceeded:
        result += "\n[注: 参考资料总长度超出上下文预算，其余内容已省略]"

    if has_degraded:
        result = "【注意：向量检索服务暂时不可用，以下结果由关键词检索提供，相关性可能低于正常水平。】\n\n" + result

    return result


# C-7（信息泄漏）：sources 出口 metadata 白名单——chunk metadata 含服务器绝对路径
# （document_service.load_document 注入 source=<data_uploads 绝对路径>），
# 原样透传会向客户端暴露服务器目录结构；按白名单剥离，file_name 等展示字段保留。
_SOURCE_METADATA_KEYS = frozenset(
    {
        "file_name",
        "file_type",
        "doc_type",
        "degraded",
        "page",
        "page_label",
        "total_pages",
        "original_name",
    }
)


def _display_source_name(doc: Document) -> str:
    """来源显示名：file_name 优先 → source 路径 basename 回退 → filename → "未知来源"。

    任何情况下不得把服务器绝对路径透给客户端（C-7）。
    """
    metadata = doc.metadata
    file_name = metadata.get("file_name")
    if file_name:
        return file_name
    source = metadata.get("source")
    if source:
        return Path(source).name
    return metadata.get("filename", "未知来源")


def sanitize_source_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """按白名单过滤来源 metadata（C-7 统一口径，sources 各出口共用）。"""
    return {k: v for k, v in metadata.items() if k in _SOURCE_METADATA_KEYS}


def _extract_sources(docs: list[Document]) -> list[dict[str, Any]]:
    """从检索文档中提取来源信息（metadata 白名单化，剥离服务器路径）

    Task 4/B1：按 _group_docs_by_source 同源分组生成——sources 列表顺序即
    答案引用编号序（sources[i] 对应正文 [i+1]），每组取首个（最相关）chunk
    生成预览与 metadata。

    分数透出（R-UX-04 + 3.6 复核修复）：
    - metadata["score"]：检索内核 with_scores=True 路径写入的归一相似度
      （C1/KV-2，越大越相关）；
    - metadata["relevance_score"]：rerank 开启时检索器为
      ContextualCompressionRetriever，with_scores 鸭子判定不命中，分数改由
      FlashRank 重排器以 relevance_score 键写入 metadata（rerank 后相关性，
      0-1）——3.6 实测 rerank 开启时 sources 无分数，按"rerank 后透传"修复：
      score 优先、relevance_score 兜底。两键均无（MMR/MultiQuery/关键词降级）
      时不带分数键，前端不显示。
    """
    sources = []
    for group in _group_docs_by_source(docs):
        doc = group[0]
        source_info = {
            "content": doc.page_content[:200] + "..." if len(doc.page_content) > 200 else doc.page_content,
            "source": _display_source_name(doc),
            "metadata": sanitize_source_metadata(doc.metadata),
        }
        # 3.6 实测：flashrank 的 relevance_score 为 numpy.float32，
        # isinstance((int, float)) 判定为 False——统一 float() 归一后判空，
        # 非数值（None/字符串等）转换失败即不带分数键
        score = doc.metadata.get("score")
        if score is None:
            score = doc.metadata.get("relevance_score")
        try:
            normalized_score = float(score) if score is not None else None
        except (TypeError, ValueError):
            normalized_score = None
        if normalized_score is not None:
            source_info["score"] = normalized_score
        if doc.metadata.get("degraded"):
            source_info["degraded"] = True
        sources.append(source_info)
    return sources


def _strip_invalid_citations(answer: str, source_count: int) -> str:
    """剔除答案中越界的 [n] 引用（Task 4/B1：模型可能编造编号），逐条记 debug 日志。

    有效编号范围 1..source_count（与 sources 列表一一对应）；
    [0] 与超范围编号剔除，剔除处留空（不改写其余正文）。
    """
    def _replace(match: re.Match) -> str:
        n = int(match.group(1))
        if 1 <= n <= source_count:
            return match.group(0)
        logger.debug(f"剔除越界引用 [{n}]（有效范围 1-{source_count}）")
        return ""

    return _CITATION_PATTERN.sub(_replace, answer)


class _StreamingCitationFilter:
    """流式引用过滤器（Task 4/B1）：越界 [n] 在发出前剔除。

    流式分片可能把 [n] 切开（如 "[", "9", "]"），无法对单片段独立判定，
    故用增量状态机：末尾疑似未完成引用（"[" + 数字前缀）时暂存 pending
    待下一片段，完整 token 判定有效放行/越界丢弃，flush 冲刷残余。
    """

    def __init__(self, source_count: int):
        self._source_count = source_count
        self._pending = ""

    def feed(self, chunk: str) -> str:
        """输入一个流片段，返回可安全发出的文本（不含暂存的疑似引用尾部）。"""
        text = self._pending + chunk
        self._pending = ""
        pieces: list[str] = []
        pos = 0
        while pos < len(text):
            bracket = text.find("[", pos)
            if bracket == -1:
                pieces.append(text[pos:])
                break
            pieces.append(text[pos:bracket])
            pos = bracket
            complete = _CITATION_PATTERN.match(text, pos)
            if complete:
                n = int(complete.group(1))
                if 1 <= n <= self._source_count:
                    pieces.append(complete.group(0))
                else:
                    logger.debug(f"流式剔除越界引用 [{n}]（有效范围 1-{self._source_count}）")
                pos = complete.end()
                continue
            partial = _CITATION_PARTIAL_TAIL.match(text, pos)
            if partial and (len(text) - pos) <= _MAX_CITATION_TOKEN_LEN:
                # 末尾疑似未完成引用（如 "[12"），留待下一片段
                self._pending = text[pos:]
                break
            # 普通方括号文本，原样发出 "["
            pieces.append("[")
            pos += 1
        return "".join(pieces)

    def flush(self) -> str:
        """流结束时冲刷暂存尾部（未完成的疑似引用按普通文本发出）。"""
        tail, self._pending = self._pending, ""
        return tail


def _resolve_hyde_llm() -> BaseChatModel | None:
    """解析 HyDE 改写用 LLM：优先辅助轻量模型，失败回退主模型链，均不可用返回 None

    HyDE 唯一实现位于 context_manager.services.retrieval_augmenter.RetrievalAugmenter
    （改写提示词与改写内核，context_manager/manager.py 共用同一实现）；
    本模块仅负责 LLM 解析与失败回退原始查询，sync/async 两版本共享此解析，
    不再各自内联（ARCH-08 收敛）。
    """
    try:
        # A-5：统一走 knowledge.config 门面（原直连 ai_engine.llm_factory）
        from Django_xm.apps.knowledge.config import get_helper_model

        llm = get_helper_model()
        if llm is not None:
            return llm
    except Exception:
        # 辅助模型获取失败时回退到 _resolve_chat_model
        logger.debug("获取辅助模型失败，回退到 _resolve_chat_model")
    try:
        return _resolve_chat_model(None, streaming=False)
    except Exception as e:
        logger.warning(f"HyDE: 获取 LLM 失败，使用原始查询 - {e}")
        return None


async def _hyde_rewrite_query(query: str, llm: BaseChatModel | None = None) -> str:
    """HyDE 查询改写，失败时回退到原始查询

    Args:
        query: 原始查询
        llm: 可选的 LLM 实例，不提供时经 _resolve_hyde_llm 解析（辅助模型优先）

    Returns:
        改写后的查询或原始查询
    """
    from Django_xm.apps.context_manager.services.retrieval_augmenter import RetrievalAugmenter

    if llm is None:
        llm = _resolve_hyde_llm()
        if llm is None:
            return query

    try:
        rewritten = await RetrievalAugmenter.hyde_rewrite(query, llm)
        return rewritten
    except Exception as e:
        logger.warning(f"HyDE: 查询改写失败，回退到原始查询 - {e}")
        return query


def _hyde_rewrite_query_sync(query: str, llm: BaseChatModel | None = None) -> str:
    """HyDE 查询改写（同步版本），失败时回退到原始查询

    Args:
        query: 原始查询
        llm: 可选的 LLM 实例，不提供时经 _resolve_hyde_llm 解析（辅助模型优先）

    Returns:
        改写后的查询或原始查询
    """
    from Django_xm.apps.context_manager.services.retrieval_augmenter import RetrievalAugmenter

    if llm is None:
        llm = _resolve_hyde_llm()
        if llm is None:
            return query

    try:
        rewritten = RetrievalAugmenter.hyde_rewrite_sync(query, llm)
        return rewritten
    except Exception as e:
        logger.warning(f"HyDE: 查询改写失败，回退到原始查询 - {e}")
        return query


def _log_hyde_outcome(query: str, retrieval_query: str) -> None:
    """HyDE 改写结果日志（改写生效 / 未生效两分支）"""
    if retrieval_query != query:
        logger.info(f"HyDE 改写: '{query[:50]}...' -> '{retrieval_query[:50]}...'")
    else:
        logger.info("HyDE 改写未生效，使用原始查询")


def _maybe_rewrite_query(query: str, model: Any | None, use_hyde: bool) -> str:
    """入口级 HyDE 查询改写（同步）：未启用时原样返回

    调用方传入 BaseChatModel 实例时直接复用；否则传 None 交由
    _hyde_rewrite_query_sync 内部解析 helper/主模型。

    Args:
        query: 原始查询
        model: 入口 model 参数（字符串 / BaseChatModel 实例 / None）
        use_hyde: 是否启用 HyDE 改写

    Returns:
        用于检索的改写后查询（或原始查询）
    """
    if not use_hyde:
        return query
    llm_for_hyde = model if isinstance(model, BaseChatModel) else None
    retrieval_query = _hyde_rewrite_query_sync(query, llm=llm_for_hyde)
    _log_hyde_outcome(query, retrieval_query)
    return retrieval_query


async def _amaybe_rewrite_query(query: str, model: Any | None, use_hyde: bool) -> str:
    """入口级 HyDE 查询改写（异步）：未启用时原样返回

    真差异判定（B⑪，与 _maybe_rewrite_query 不可合并）：
    二者分别调用 RetrievalAugmenter 的 async / sync 改写内核
    （hyde_rewrite vs hyde_rewrite_sync），属底层双协议 API 的分叉，
    共同骨架（use_hyde 判断 / llm 解析 / 日志收尾）已由
    _log_hyde_outcome 等辅助函数共享。

    Args:
        query: 原始查询
        model: 入口 model 参数（字符串 / BaseChatModel 实例 / None）
        use_hyde: 是否启用 HyDE 改写

    Returns:
        用于检索的改写后查询（或原始查询）
    """
    if not use_hyde:
        return query
    llm_for_hyde = model if isinstance(model, BaseChatModel) else None
    retrieval_query = await _hyde_rewrite_query(query, llm=llm_for_hyde)
    _log_hyde_outcome(query, retrieval_query)
    return retrieval_query


def _retrieve_with_fallback(
    retriever: BaseRetriever,
    queries: list[str],
    query: str,
    k: int,
    collection_name: str,
    vector_store: Any | None = None,
) -> tuple[list[Document], bool]:
    """多路初始检索（同步）：统一走 RagService.retrieve_many（多路 + RRF + 上下文）。

    embedding 服务故障时降级 BM25 关键词检索；降级使用原始 query。
    """
    from Django_xm.apps.knowledge.services.keyword_fallback import (
        is_embedding_error,
        keyword_search_fallback,
        record_degradation_metric,
    )
    from Django_xm.apps.knowledge.services.rag_service import RagScope, RagService

    try:
        result = RagService().retrieve_many(
            queries,
            retriever=retriever,
            scope=RagScope(kind="persistent_kb", name=collection_name or ""),
            vector_store=vector_store,
            k=k,
            apply_budget=False,
        )
        return result.docs, False
    except Exception as e:
        if is_embedding_error(e):
            logger.warning(f"向量检索失败，降级到全文关键词检索: {e}")
            if collection_name:
                record_degradation_metric(collection_name, e)
            return keyword_search_fallback(query, collection_name, k=k), True
        raise


async def _aretrieve_with_fallback(
    retriever: BaseRetriever,
    queries: list[str],
    query: str,
    k: int,
    collection_name: str,
    vector_store: Any | None = None,
) -> tuple[list[Document], bool]:
    """多路初始检索（异步）：RagService.retrieve_many 经 to_thread 离开事件循环。"""
    from Django_xm.apps.knowledge.services.keyword_fallback import (
        is_embedding_error,
        keyword_search_fallback,
        record_degradation_metric,
    )
    from Django_xm.apps.knowledge.services.rag_service import RagScope, RagService

    try:
        result = await asyncio.to_thread(
            RagService().retrieve_many,
            queries,
            retriever=retriever,
            scope=RagScope(kind="persistent_kb", name=collection_name or ""),
            vector_store=vector_store,
            k=k,
            apply_budget=False,
        )
        return result.docs, False
    except Exception as e:
        if is_embedding_error(e):
            logger.warning(f"向量检索失败，降级到全文关键词检索: {e}")
            if collection_name:
                record_degradation_metric(collection_name, e)
            docs = await asyncio.to_thread(keyword_search_fallback, query, collection_name, k)
            return docs, True
        raise


def _build_queries_sync(query: str, model: Any | None, use_hyde: bool) -> list[str]:
    """构建多路检索查询（原 query + HyDE + MQ）；LLM 不可用时退化为原 query。"""
    from Django_xm.apps.knowledge.services.query_expansion import build_retrieval_queries_sync

    llm = model if isinstance(model, BaseChatModel) else _resolve_hyde_llm()
    return build_retrieval_queries_sync(query, llm=llm, use_hyde=use_hyde)


async def _abuild_queries(query: str, model: Any | None, use_hyde: bool) -> list[str]:
    """异步构建多路检索查询。"""
    from Django_xm.apps.knowledge.services.query_expansion import build_retrieval_queries_async

    llm = model if isinstance(model, BaseChatModel) else _resolve_hyde_llm()
    return await build_retrieval_queries_async(query, llm=llm, use_hyde=use_hyde)


def _prepare_and_retrieve(
    retriever: BaseRetriever,
    query: str,
    model: Any | None,
    use_hyde: bool,
    k: int,
    collection_name: str,
    vector_store: Any | None,
) -> tuple[list[Document], bool]:
    """查询扩展 + 多路检索（流式链路在同一线程步骤内完成，不额外增加心跳）。"""
    queries = _build_queries_sync(query, model, use_hyde)
    return _retrieve_with_fallback(retriever, queries, query, k, collection_name, vector_store)


def _is_degraded_result(degraded: bool, docs: list[Document]) -> bool:
    """降级判定：检索阶段已降级，或任一检索文档自带降级标记"""
    return degraded or any(doc.metadata.get("degraded") for doc in docs)


def query_strict_rag(
    retriever: BaseRetriever,
    query: str,
    model: Any | None = None,
    k: int = 4,
    use_hyde: bool = True,
    collection_name: str = "",
    vector_store: Any | None = None,
) -> dict[str, Any]:
    """
    严格 RAG 查询（同步）

    Args:
        retriever: 向量检索器
        query: 用户查询
        model: LLM 模型
        k: 返回文档数量
        use_hyde: 是否启用 HyDE 查询改写，默认 True
        collection_name: 知识库 collection 名称，用于检索降级
        vector_store: 底层向量库实例（P0 Level 1 降级需要）

    Returns:
        包含 answer、sources、retrieved_docs 的字典
    """
    logger.info(f"严格 RAG 查询: {query[:50]}...")

    try:
        # 0. 多路查询扩展（原 query + HyDE + MQ；失败/超时只少一路）
        queries = _build_queries_sync(query, model, use_hyde)

        # 1. 多路检索 + RRF 融合 + 去冗 + 邻块扩展
        docs, degraded = _retrieve_with_fallback(
            retriever, queries, query, k, collection_name, vector_store
        )
        logger.info(f"检索到 {len(docs)} 个文档{' (降级模式)' if degraded else ''}")

        # 2. 无检索结果：受控空应答（Task 4/B3：不再全库重嵌入降级），
        #    degraded 置位，views 侧不写查询缓存
        if not docs:
            result: dict[str, Any] = {
                "answer": NO_RESULT_ANSWER,
                "sources": [],
                "retrieved_docs": [],
                "success": True,
                "degraded": True,
                "degradation_notice": NO_RESULT_NOTICE,
            }
            logger.info("严格 RAG 查询完成（无检索结果）")
            return result

        # 3. 提取来源（与上下文编号同源：sources 序即引用编号序）并构建 prompt
        sources = _extract_sources(docs)
        prompt_text = STRICT_RAG_QA_PROMPT.format(context=_format_docs(docs), question=query)

        # 4. 调用 LLM（_resolve_chat_model 已内置 fallback，model=None 时自动读 SystemConfig）
        llm = _resolve_chat_model(model, streaming=False)
        response = llm.invoke([HumanMessage(content=prompt_text)])
        answer = response.content if hasattr(response, "content") else str(response)

        # 5. 引用校验：剔除越界 [n]（Task 4/B1）
        answer = _strip_invalid_citations(answer, len(sources))

        # 5.5 引用真实性校验（Phase 2）：只移除不被支持的 [n]，不改写正文
        citation_check: dict[str, Any] = {}
        if get_rag_runtime_settings()["citation_verify_enabled"] and sources:
            try:
                from Django_xm.apps.knowledge.services.citation_verifier import (
                    verify_answer_citations,
                )

                answer, citation_check = verify_answer_citations(
                    answer,
                    sources,
                    max_sentences=settings.rag_citation_verify_max_sentences,
                )
            except Exception as e:
                logger.warning(f"引用真实性校验失败，保留原答案: {e}")

        result = {
            "answer": answer,
            "sources": sources,
            "retrieved_docs": docs,
            "success": True,
            # 内部字段（serializer 丢弃）：供埋点记录多路处理规模
            "retrieval_meta": {"routes": len(queries), "docs": len(docs)},
        }
        if citation_check:
            result["citation_check"] = citation_check

        # 6. 降级标记
        if _is_degraded_result(degraded, docs):
            result["degraded"] = True
            result["degradation_notice"] = KEYWORD_DEGRADATION_NOTICE

        logger.info("严格 RAG 查询完成")
        return result

    except Exception:
        logger.exception("严格 RAG 查询失败")
        raise


async def astream_strict_rag(
    retriever: BaseRetriever,
    query: str,
    model: Any | None = None,
    k: int = 4,
    use_hyde: bool = True,
    collection_name: str = "",
    vector_store: Any | None = None,
):
    """
    严格 RAG 流式查询（异步生成器）

    流程：
    1. HyDE 查询改写（可选）
    2. 先完成检索（非流式）
    3. 将检索结果注入 prompt
    4. 流式生成回答

    Args:
        retriever: 向量检索器
        query: 用户查询
        model: LLM 模型
        k: 返回文档数量
        use_hyde: 是否启用 HyDE 查询改写，默认 True
        collection_name: 知识库 collection 名称，用于检索降级
        vector_store: 底层向量库实例（P0 Level 1 降级需要）

    Yields:
        事件字典，type 为 "chunk"（内容片段）、"sources"（来源信息）、"degradation"（降级提示）、"error"（错误）
    """
    logger.info(f"严格 RAG 流式查询: {query[:50]}...")

    try:
        # 0. 多路查询扩展（原 query + HyDE + MQ）：与检索合并进同一任务，
        #    此处仅提示；LLM 等待由检索任务的周期 heartbeat 覆盖
        if use_hyde:
            yield {"type": "heartbeat", "message": "正在扩展查询..."}

        # 1. 查询扩展 + 多路检索（to_thread 离开事件循环 + 周期 heartbeat）
        yield {"type": "heartbeat", "message": "正在检索文档..."}
        retrieve_task = asyncio.ensure_future(
            asyncio.to_thread(
                _prepare_and_retrieve,
                retriever,
                query,
                model,
                use_hyde,
                k,
                collection_name,
                vector_store,
            )
        )
        try:
            while True:
                try:
                    docs, degraded = await asyncio.wait_for(asyncio.shield(retrieve_task), timeout=15.0)
                    break
                except TimeoutError:
                    yield {"type": "heartbeat", "message": "正在检索文档..."}
        finally:
            if not retrieve_task.done():
                retrieve_task.cancel()
        logger.info(f"检索到 {len(docs)} 个文档{' (降级模式)' if degraded else ''}")

        # 2. 无检索结果：受控空应答 + degradation 事件（Task 4/B3：不再全库
        #    重嵌入降级，不产生假正文；views 侧不写查询缓存）。
        #    B10：degradation 前置到正文 chunk 之前（检索完成即知状态）
        if not docs:
            yield {"type": "degradation", "message": NO_RESULT_NOTICE}
            yield {"type": "chunk", "content": NO_RESULT_ANSWER}
            logger.info("严格 RAG 流式查询完成（无检索结果）")
            return

        # B10：降级状态检索完成即知，degradation 事件前置到首个正文 chunk 之前
        # 发出（此前在全部正文 chunk 之后才发，用户看完低质正文才见到提示）
        if _is_degraded_result(degraded, docs):
            yield {"type": "degradation", "message": KEYWORD_DEGRADATION_NOTICE}

        # 3. 提取来源（与上下文编号同源）并构建 prompt（使用原始查询）
        sources = _extract_sources(docs)
        prompt_text = STRICT_RAG_QA_PROMPT.format(context=_format_docs(docs), question=query)

        # 4. 流式调用 LLM（_resolve_chat_model 已内置 fallback，model=None 时自动读 SystemConfig）
        yield {"type": "heartbeat", "message": "正在生成回答..."}
        llm = _resolve_chat_model(model, streaming=True)

        # 引用过滤：越界 [n] 在发出前剔除（可能跨 chunk 分片，见过滤器 docstring）
        citation_filter = _StreamingCitationFilter(source_count=len(sources))
        full_response = ""
        async for chunk in llm.astream([HumanMessage(content=prompt_text)]):
            if hasattr(chunk, "content") and chunk.content:
                filtered = citation_filter.feed(chunk.content)
                if filtered:
                    full_response += filtered
                    yield {"type": "chunk", "content": filtered}
        tail = citation_filter.flush()
        if tail:
            full_response += tail
            yield {"type": "chunk", "content": tail}

        # 5. 发送来源信息
        if sources:
            yield {"type": "sources", "data": sources}

        logger.info(f"严格 RAG 流式查询完成, total_len={len(full_response)}")

    except Exception:
        logger.exception("严格 RAG 流式查询失败")
        yield {"type": "error", "message": "严格 RAG 查询失败，请稍后重试"}


def _blocking_step_with_heartbeat(fn, args, stage_message, interval: float = 15.0):
    """阻塞步骤移入单工作线程执行，等待期间周期性 yield heartbeat 事件。

    背景：HyDE 改写 / 向量检索为同步阻塞调用，辅助模型走本地推理时单次
    可能耗时数分钟，期间生成器无法产出事件——前端 SSE 空闲超时（180s）
    会静默断开流，后端不知情继续跑完，结果写进已断开的连接而丢失。
    """
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(fn, *args)
        while True:
            try:
                return future.result(timeout=interval)
            except FuturesTimeoutError:
                yield {"type": "heartbeat", "message": stage_message}
    finally:
        # 消费方中断（前端断开触发 GeneratorExit）时不等待工作线程：
        # 任务自然跑完即线程退出，避免 close() 反向被慢任务阻塞
        executor.shutdown(wait=False, cancel_futures=True)


def stream_strict_rag(
    retriever: BaseRetriever,
    query: str,
    model: Any | None = None,
    k: int = 4,
    use_hyde: bool = True,
    collection_name: str = "",
    vector_store: Any | None = None,
):
    """
    严格 RAG 流式查询（同步生成器）

    用于 Django 同步视图中的 SSE 流式输出。

    流程：
    1. HyDE 查询改写（可选）
    2. 先完成检索（同步）
    3. 将检索结果注入 prompt
    4. 流式生成回答

    Args:
        retriever: 向量检索器
        query: 用户查询
        model: LLM 模型
        k: 返回文档数量
        use_hyde: 是否启用 HyDE 查询改写，默认 True
        collection_name: 知识库 collection 名称，用于检索降级
        vector_store: 底层向量库实例（P0 Level 1 降级需要）

    Yields:
        事件字典，type 为 "chunk"（内容片段）、"sources"（来源信息）、"degradation"（降级提示）、"error"（错误）
    """
    logger.info(f"严格 RAG 同步流式查询: {query[:50]}...")

    try:
        # 0. 多路查询扩展（原 query + HyDE + MQ）：与检索合并进同一线程步骤，
        #    此处仅提示；LLM 等待由检索步骤的周期 heartbeat 覆盖
        if use_hyde:
            yield {"type": "heartbeat", "message": "正在扩展查询..."}

        # 1. 查询扩展 + 多路检索 + 融合/去冗/邻块扩展
        yield {"type": "heartbeat", "message": "正在检索文档..."}
        docs, degraded = yield from _blocking_step_with_heartbeat(
            _prepare_and_retrieve,
            (retriever, query, model, use_hyde, k, collection_name, vector_store),
            "正在检索文档...",
        )
        logger.info(f"检索到 {len(docs)} 个文档{' (降级模式)' if degraded else ''}")

        # 2. 无检索结果：受控空应答 + degradation 事件（Task 4/B3：不再全库
        #    重嵌入降级，不产生假正文；views 侧不写查询缓存）。
        #    B10：degradation 前置到正文 chunk 之前（检索完成即知状态）
        if not docs:
            yield {"type": "degradation", "message": NO_RESULT_NOTICE}
            yield {"type": "chunk", "content": NO_RESULT_ANSWER}
            logger.info("严格 RAG 同步流式查询完成（无检索结果）")
            return

        # B10：降级状态检索完成即知，degradation 事件前置到首个正文 chunk 之前
        # 发出（此前在全部正文 chunk 之后才发，用户看完低质正文才见到提示）
        if _is_degraded_result(degraded, docs):
            yield {"type": "degradation", "message": KEYWORD_DEGRADATION_NOTICE}

        # 3. 提取来源（与上下文编号同源）并构建 prompt（使用原始查询）
        sources = _extract_sources(docs)
        prompt_text = STRICT_RAG_QA_PROMPT.format(context=_format_docs(docs), question=query)

        # 4. 流式调用 LLM（_resolve_chat_model 已内置 fallback，model=None 时自动读 SystemConfig）
        yield {"type": "heartbeat", "message": "正在生成回答..."}
        llm = _resolve_chat_model(model, streaming=True)

        # 引用过滤：越界 [n] 在发出前剔除（可能跨 chunk 分片，见过滤器 docstring）
        citation_filter = _StreamingCitationFilter(source_count=len(sources))
        full_response = ""
        for chunk in llm.stream([HumanMessage(content=prompt_text)]):
            if hasattr(chunk, "content") and chunk.content:
                filtered = citation_filter.feed(chunk.content)
                if filtered:
                    full_response += filtered
                    yield {"type": "chunk", "content": filtered}
        tail = citation_filter.flush()
        if tail:
            full_response += tail
            yield {"type": "chunk", "content": tail}

        # 5. 发送来源信息
        if sources:
            yield {"type": "sources", "data": sources}

        logger.info(f"严格 RAG 同步流式查询完成, total_len={len(full_response)}")

    except Exception:
        logger.exception("严格 RAG 同步流式查询失败")
        yield {"type": "error", "message": "严格 RAG 查询失败，请稍后重试"}
