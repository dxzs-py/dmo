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
"""

import asyncio
from typing import Any

from langchain_core.documents import Document
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.retrievers import BaseRetriever

from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)

# 空结果默认回答与两类降级提示（原三入口逐字重复的字符串收敛为单一权威源）
NO_RESULT_ANSWER = "根据知识库中的资料，未找到与您问题相关的信息。"
KB_DEGRADATION_NOTICE = "向量检索未命中，结果由知识库全文/降级检索提供"
KEYWORD_DEGRADATION_NOTICE = "向量检索服务暂时不可用，结果由关键词检索提供，相关性可能低于正常水平"

STRICT_RAG_QA_PROMPT = """基于以下参考资料回答用户问题。如果参考资料中没有相关信息，请明确说明。

参考资料：
{context}

用户问题：{question}

回答（仅基于上述参考资料，不得使用自身知识）："""


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


def _format_docs(docs: list[Document]) -> str:
    """将检索到的文档格式化为上下文字符串"""
    if not docs:
        return "（未检索到任何相关文档）"

    # 检测是否存在降级标记
    has_degraded = any(doc.metadata.get("degraded") for doc in docs)

    formatted_parts = []
    for i, doc in enumerate(docs, 1):
        source = doc.metadata.get("source", doc.metadata.get("filename", f"文档{i}"))
        content = doc.page_content.strip()
        degraded_tag = " [关键词检索-降级模式]" if doc.metadata.get("degraded") else ""
        formatted_parts.append(f"--- 参考资料 [{i}] 来源: {source}{degraded_tag} ---\n{content}\n")

    result = "\n".join(formatted_parts)

    if has_degraded:
        result = "【注意：向量检索服务暂时不可用，以下结果由关键词检索提供，相关性可能低于正常水平。】\n\n" + result

    return result


def _extract_sources(docs: list[Document]) -> list[dict[str, Any]]:
    """从检索文档中提取来源信息"""
    sources = []
    seen_sources = set()
    for doc in docs:
        source_name = doc.metadata.get("source", doc.metadata.get("filename", "未知来源"))
        if source_name not in seen_sources:
            seen_sources.add(source_name)
            source_info = {
                "content": doc.page_content[:200] + "..." if len(doc.page_content) > 200 else doc.page_content,
                "source": source_name,
                "metadata": doc.metadata,
            }
            if doc.metadata.get("degraded"):
                source_info["degraded"] = True
            sources.append(source_info)
    return sources


async def _hyde_rewrite_query(query: str, llm: BaseChatModel | None = None) -> str:
    """HyDE 查询改写，失败时回退到原始查询

    Args:
        query: 原始查询
        llm: 可选的 LLM 实例，不提供时优先使用辅助模型（轻量任务无需主模型）

    Returns:
        改写后的查询或原始查询
    """
    from Django_xm.apps.context_manager.services.retrieval_augmenter import RetrievalAugmenter

    if llm is None:
        try:
            from Django_xm.apps.ai_engine.services.llm_factory import get_helper_model

            llm = get_helper_model()
        except Exception:
            # 辅助模型获取失败时回退到 _resolve_chat_model
            logger.debug("获取辅助模型失败，回退到 _resolve_chat_model")
        if llm is None:
            try:
                llm = _resolve_chat_model(None, streaming=False)
            except Exception as e:
                logger.warning(f"HyDE: 获取 LLM 失败，使用原始查询 - {e}")
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
        llm: 可选的 LLM 实例，不提供时优先使用辅助模型（轻量任务无需主模型）

    Returns:
        改写后的查询或原始查询
    """
    from Django_xm.apps.context_manager.services.retrieval_augmenter import RetrievalAugmenter

    if llm is None:
        try:
            from Django_xm.apps.ai_engine.services.llm_factory import get_helper_model

            llm = get_helper_model()
        except Exception:
            # 辅助模型获取失败时回退到 _resolve_chat_model
            logger.debug("获取辅助模型失败（同步），回退到 _resolve_chat_model")
        if llm is None:
            try:
                llm = _resolve_chat_model(None, streaming=False)
            except Exception as e:
                logger.warning(f"HyDE: 获取 LLM 失败，使用原始查询 - {e}")
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
    retrieval_query: str,
    query: str,
    k: int,
    collection_name: str,
) -> tuple[list[Document], bool]:
    """初始检索（同步）：embedding 服务故障时降级 PG 全文关键词检索

    向量检索使用 HyDE 改写后的 retrieval_query；
    降级关键词检索使用原始 query（与原三入口内联实现一致）。

    Args:
        retriever: 向量检索器
        retrieval_query: HyDE 改写后的检索查询
        query: 原始用户查询（降级检索用）
        k: 返回文档数量
        collection_name: 知识库 collection 名称（降级指标记录用）

    Returns:
        (docs, degraded) 二元组，degraded 为 True 表示已走关键词降级

    Raises:
        Exception: 非 embedding 类异常原样上抛
    """
    from Django_xm.apps.knowledge.services.retrieval_service import (
        _is_embedding_error,
        _keyword_search_fallback,
        _record_degradation_metric,
    )

    try:
        from Django_xm.apps.knowledge.services.rag_retrieval import UnifiedRagPipeline

        pipeline = UnifiedRagPipeline(scenario="knowledge_base")
        docs = pipeline.retrieve_documents_from_retriever(
            retriever=retriever, query=retrieval_query, vector_store=None
        )
        return docs, False
    except Exception as e:
        if _is_embedding_error(e):
            logger.warning(f"向量检索失败，降级到全文关键词检索: {e}")
            if collection_name:
                _record_degradation_metric(collection_name, e)
            return _keyword_search_fallback(query, collection_name, k=k), True
        raise


async def _aretrieve_with_fallback(
    retriever: BaseRetriever,
    retrieval_query: str,
    query: str,
    k: int,
    collection_name: str,
) -> tuple[list[Document], bool]:
    """初始检索（异步）：降级关键词检索经 asyncio.to_thread 执行避免阻塞事件循环

    初始 Pipeline 检索保持同步调用（与原实现一致）；
    向量检索使用 HyDE 改写后的 retrieval_query，降级检索使用原始 query。

    Args:
        retriever: 向量检索器
        retrieval_query: HyDE 改写后的检索查询
        query: 原始用户查询（降级检索用）
        k: 返回文档数量
        collection_name: 知识库 collection 名称（降级指标记录用）

    Returns:
        (docs, degraded) 二元组，degraded 为 True 表示已走关键词降级

    Raises:
        Exception: 非 embedding 类异常原样上抛
    """
    from Django_xm.apps.knowledge.services.retrieval_service import (
        _is_embedding_error,
        _keyword_search_fallback,
        _record_degradation_metric,
    )

    try:
        from Django_xm.apps.knowledge.services.rag_retrieval import UnifiedRagPipeline

        pipeline = UnifiedRagPipeline(scenario="knowledge_base")
        docs = pipeline.retrieve_documents_from_retriever(
            retriever=retriever, query=retrieval_query, vector_store=None
        )
        return docs, False
    except Exception as e:
        if _is_embedding_error(e):
            logger.warning(f"向量检索失败，降级到全文关键词检索: {e}")
            if collection_name:
                _record_degradation_metric(collection_name, e)
            docs = await asyncio.to_thread(_keyword_search_fallback, query, collection_name, k)
            return docs, True
        raise


def _handle_empty_result(query: str, collection_name: str) -> tuple[str, bool]:
    """无检索结果处理（同步）：加载知识库全量文档走统一降级管道

    Args:
        query: 原始用户查询
        collection_name: 知识库 collection 名称

    Returns:
        (answer, used_kb_pipeline)：used_kb_pipeline 为 True 表示答案来自
        知识库全量降级管道（调用方应将 degraded 置 True）
    """
    from Django_xm.apps.knowledge.services.rag_retrieval import UnifiedRagPipeline, load_kb_documents

    if collection_name:
        kb_docs, kb_tokens = load_kb_documents([collection_name])
    else:
        kb_docs, kb_tokens = [], 0
    if kb_docs:
        pipeline = UnifiedRagPipeline(scenario="knowledge_base")
        return pipeline.search(query, kb_docs, kb_tokens), True
    return NO_RESULT_ANSWER, False


async def _ahandle_empty_result(query: str, collection_name: str) -> tuple[str, bool]:
    """无检索结果处理（异步）：IO 经 asyncio.to_thread 执行避免阻塞事件循环

    Args:
        query: 原始用户查询
        collection_name: 知识库 collection 名称

    Returns:
        (answer, used_kb_pipeline)：used_kb_pipeline 为 True 表示答案来自
        知识库全量降级管道（调用方应将 degraded 置 True）
    """
    from Django_xm.apps.knowledge.services.rag_retrieval import UnifiedRagPipeline, load_kb_documents

    if collection_name:
        kb_docs, kb_tokens = await asyncio.to_thread(load_kb_documents, [collection_name])
    else:
        kb_docs, kb_tokens = [], 0
    if kb_docs:
        pipeline = UnifiedRagPipeline(scenario="knowledge_base")
        answer = await asyncio.to_thread(pipeline.search, query, kb_docs, kb_tokens)
        return answer, True
    return NO_RESULT_ANSWER, False


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

    Returns:
        包含 answer、sources、retrieved_docs 的字典
    """
    logger.info(f"严格 RAG 查询: {query[:50]}...")

    try:
        # 0. HyDE 查询改写
        retrieval_query = _maybe_rewrite_query(query, model, use_hyde)

        # 1. 检索（使用改写后的查询），通过 Pipeline 执行初始检索
        docs, degraded = _retrieve_with_fallback(retriever, retrieval_query, query, k, collection_name)
        logger.info(f"检索到 {len(docs)} 个文档{' (降级模式)' if degraded else ''}")

        # 2. 无检索结果时，加载知识库全量文档走统一降级管道
        if not docs:
            answer, used_kb = _handle_empty_result(query, collection_name)
            degraded = degraded or used_kb
            result: dict[str, Any] = {"answer": answer, "sources": [], "retrieved_docs": [], "success": True}
            if degraded:
                result["degraded"] = True
                result["degradation_notice"] = KB_DEGRADATION_NOTICE
            logger.info("严格 RAG 查询完成（无检索结果，走知识库降级）")
            return result

        # 3. 构建上下文与 prompt（使用原始查询）
        prompt_text = STRICT_RAG_QA_PROMPT.format(context=_format_docs(docs), question=query)

        # 4. 调用 LLM（_resolve_chat_model 已内置 fallback，model=None 时自动读 SystemConfig）
        llm = _resolve_chat_model(model, streaming=False)
        response = llm.invoke([HumanMessage(content=prompt_text)])
        answer = response.content if hasattr(response, "content") else str(response)

        # 5. 提取来源
        sources = _extract_sources(docs)

        result = {
            "answer": answer,
            "sources": sources,
            "retrieved_docs": docs,
            "success": True,
        }

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

    Yields:
        事件字典，type 为 "chunk"（内容片段）、"sources"（来源信息）、"degradation"（降级提示）、"error"（错误）
    """
    logger.info(f"严格 RAG 流式查询: {query[:50]}...")

    try:
        # 0. HyDE 查询改写
        if use_hyde:
            yield {"type": "heartbeat", "message": "正在改写查询..."}
        retrieval_query = await _amaybe_rewrite_query(query, model, use_hyde)

        # 1. 检索（非流式，必须先完成，使用改写后的查询），通过 Pipeline 执行初始检索
        yield {"type": "heartbeat", "message": "正在检索文档..."}
        docs, degraded = await _aretrieve_with_fallback(retriever, retrieval_query, query, k, collection_name)
        logger.info(f"检索到 {len(docs)} 个文档{' (降级模式)' if degraded else ''}")

        # 2. 无检索结果时，加载知识库全量文档走统一降级管道
        if not docs:
            degraded_text, used_kb = await _ahandle_empty_result(query, collection_name)
            degraded = degraded or used_kb
            yield {"type": "chunk", "content": degraded_text}
            if degraded:
                yield {"type": "degradation", "message": KB_DEGRADATION_NOTICE}
            logger.info("严格 RAG 流式查询完成（无检索结果，走知识库降级）")
            return

        # 3. 构建上下文与 prompt（使用原始查询）
        prompt_text = STRICT_RAG_QA_PROMPT.format(context=_format_docs(docs), question=query)

        # 4. 流式调用 LLM（_resolve_chat_model 已内置 fallback，model=None 时自动读 SystemConfig）
        yield {"type": "heartbeat", "message": "正在生成回答..."}
        llm = _resolve_chat_model(model, streaming=True)

        full_response = ""
        async for chunk in llm.astream([HumanMessage(content=prompt_text)]):
            if hasattr(chunk, "content") and chunk.content:
                full_response += chunk.content
                yield {"type": "chunk", "content": chunk.content}

        # 5. 发送降级提示
        if _is_degraded_result(degraded, docs):
            yield {"type": "degradation", "message": KEYWORD_DEGRADATION_NOTICE}

        # 6. 发送来源信息
        sources = _extract_sources(docs)
        if sources:
            yield {"type": "sources", "data": sources}

        logger.info(f"严格 RAG 流式查询完成, total_len={len(full_response)}")

    except Exception:
        logger.exception("严格 RAG 流式查询失败")
        yield {"type": "error", "message": "严格 RAG 查询失败，请稍后重试"}


def stream_strict_rag(
    retriever: BaseRetriever,
    query: str,
    model: Any | None = None,
    k: int = 4,
    use_hyde: bool = True,
    collection_name: str = "",
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

    Yields:
        事件字典，type 为 "chunk"（内容片段）、"sources"（来源信息）、"degradation"（降级提示）、"error"（错误）
    """
    logger.info(f"严格 RAG 同步流式查询: {query[:50]}...")

    try:
        # 0. HyDE 查询改写
        if use_hyde:
            yield {"type": "heartbeat", "message": "正在改写查询..."}
        retrieval_query = _maybe_rewrite_query(query, model, use_hyde)

        # 1. 检索（同步，使用改写后的查询），通过 Pipeline 执行初始检索
        yield {"type": "heartbeat", "message": "正在检索文档..."}
        docs, degraded = _retrieve_with_fallback(retriever, retrieval_query, query, k, collection_name)
        logger.info(f"检索到 {len(docs)} 个文档{' (降级模式)' if degraded else ''}")

        # 2. 无检索结果时，加载知识库全量文档走统一降级管道
        if not docs:
            degraded_text, used_kb = _handle_empty_result(query, collection_name)
            degraded = degraded or used_kb
            yield {"type": "chunk", "content": degraded_text}
            if degraded:
                yield {"type": "degradation", "message": KB_DEGRADATION_NOTICE}
            logger.info("严格 RAG 同步流式查询完成（无检索结果，走知识库降级）")
            return

        # 3. 构建上下文与 prompt（使用原始查询）
        prompt_text = STRICT_RAG_QA_PROMPT.format(context=_format_docs(docs), question=query)

        # 4. 流式调用 LLM（_resolve_chat_model 已内置 fallback，model=None 时自动读 SystemConfig）
        yield {"type": "heartbeat", "message": "正在生成回答..."}
        llm = _resolve_chat_model(model, streaming=True)

        full_response = ""
        for chunk in llm.stream([HumanMessage(content=prompt_text)]):
            if hasattr(chunk, "content") and chunk.content:
                full_response += chunk.content
                yield {"type": "chunk", "content": chunk.content}

        # 5. 发送降级提示
        if _is_degraded_result(degraded, docs):
            yield {"type": "degradation", "message": KEYWORD_DEGRADATION_NOTICE}

        # 6. 发送来源信息
        sources = _extract_sources(docs)
        if sources:
            yield {"type": "sources", "data": sources}

        logger.info(f"严格 RAG 同步流式查询完成, total_len={len(full_response)}")

    except Exception:
        logger.exception("严格 RAG 同步流式查询失败")
        yield {"type": "error", "message": "严格 RAG 查询失败，请稍后重试"}
