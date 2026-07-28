"""
严格 RAG Chain 模块

标准 RAG 架构：检索 -> 上下文构建 -> 生成
与 Agent 模式的区别：
- Agent 模式：模型自主决定是否调用检索工具，可能绕过知识库直接回答
- Chain 模式：强制先检索，将检索结果注入 prompt 上下文，模型仅基于上下文生成

这是行业主流 RAG 架构，确保智能体严格依据知识库内容输出。
支持检索降级：向量检索失败时自动降级到 PostgreSQL 全文关键词检索。
"""

import asyncio
from typing import Any

from langchain_core.documents import Document
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.retrievers import BaseRetriever
from langchain_core.runnables import RunnablePassthrough

from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)


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

STRICT_RAG_SYSTEM_PROMPT = """你是一个严格基于知识库内容的问答助手。你必须且只能基于下方【检索到的参考资料】来回答用户的问题。

## 核心规则（必须严格遵守）

1. **仅使用参考资料**：你的回答必须且只能基于【检索到的参考资料】中的内容，不得使用你自身的知识储备。
2. **禁止编造**：如果参考资料中没有包含回答用户问题所需的信息，你必须明确告知用户"根据知识库中的资料，未找到与您问题相关的信息"，不得自行补充或推测。
3. **忠实引用**：回答时应忠实于参考资料的内容，不得歪曲、夸大或过度解读。
4. **标注来源**：在回答中应适当标注信息来源于哪个文档。
5. **综合归纳**：当多条参考资料涉及同一问题时，应综合归纳，提供完整准确的回答。

## 回答格式

- 如果参考资料充分：直接回答问题，在关键信息后标注来源文档名
- 如果参考资料部分相关：回答相关部分，并明确指出哪些方面知识库中未涵盖
- 如果参考资料完全不相关：回复"根据知识库中的资料，未找到与您问题相关的信息。知识库主要涵盖以下内容：[简要概括参考资料的主题]"

## 检索到的参考资料

{context}
"""

STRICT_RAG_QA_PROMPT = """基于以下参考资料回答用户问题。如果参考资料中没有相关信息，请明确说明。

参考资料：
{context}

用户问题：{question}

回答（仅基于上述参考资料，不得使用自身知识）："""


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
        result = (
            "【注意：向量检索服务暂时不可用，以下结果由关键词检索提供，相关性可能低于正常水平。】\n\n"
            + result
        )

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
            pass
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
            pass
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


def create_strict_rag_chain(
    retriever: BaseRetriever,
    model: Any | None = None,
    streaming: bool = False,
) -> dict[str, Any]:
    """
    创建严格 RAG Chain

    标准流程：
    1. 用户提问 -> 向量检索相关文档
    2. 将检索结果注入 prompt 上下文
    3. LLM 仅基于上下文生成回答

    Args:
        retriever: 向量检索器
        model: LLM 模型（字符串或 BaseChatModel 实例）
        streaming: 是否流式输出

    Returns:
        包含 chain 和 retriever 的字典
    """
    from Django_xm.apps.knowledge.config import get_model_string

    logger.info("创建严格 RAG Chain（检索-注入-生成模式）")

    if model is None:
        model = get_model_string()

    prompt = ChatPromptTemplate.from_template(STRICT_RAG_QA_PROMPT)

    def retrieve_and_format(query: str) -> dict[str, Any]:
        """检索并格式化文档"""
        docs = retriever.invoke(query)
        context = _format_docs(docs)
        return {
            "context": context,
            "question": query,
            "retrieved_docs": docs,
        }

    chain = (
        RunnablePassthrough.assign(
            context_and_docs=lambda x: retrieve_and_format(x["question"])
        )
        | {
            "answer": (
                lambda x: {
                    "context": x["context_and_docs"]["context"],
                    "question": x["context_and_docs"]["question"],
                }
            ) | prompt | (model if isinstance(model, BaseChatModel) else _get_chat_model(model))
              | StrOutputParser(),
            "retrieved_docs": (lambda x: x["context_and_docs"]["retrieved_docs"]),
        }
    )

    logger.info("严格 RAG Chain 创建成功")
    return {
        "chain": chain,
        "retriever": retriever,
        "model": model,
    }


def _get_chat_model(model_string: str):
    """根据模型字符串获取 ChatModel 实例"""
    return _resolve_chat_model(model_string, streaming=False)


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
    from Django_xm.apps.knowledge.services.retrieval_service import (
        _is_embedding_error,
        _keyword_search_fallback,
        _record_degradation_metric,
    )

    logger.info(f"严格 RAG 查询: {query[:50]}...")

    try:
        # 0. HyDE 查询改写
        retrieval_query = query
        if use_hyde:
            llm_for_hyde = None
            if model is not None and isinstance(model, BaseChatModel):
                llm_for_hyde = model
            retrieval_query = _hyde_rewrite_query_sync(query, llm=llm_for_hyde)
            if retrieval_query != query:
                logger.info(f"HyDE 改写: '{query[:50]}...' -> '{retrieval_query[:50]}...'")
            else:
                logger.info("HyDE 改写未生效，使用原始查询")

        # 1. 检索（使用改写后的查询），支持降级
        degraded = False
        try:
            docs = retriever.invoke(retrieval_query)
        except Exception as e:
            if _is_embedding_error(e):
                logger.warning(f"向量检索失败，降级到全文关键词检索: {e}")
                if collection_name:
                    _record_degradation_metric(collection_name, e)
                docs = _keyword_search_fallback(query, collection_name, k=k)
                degraded = True
            else:
                raise

        logger.info(f"检索到 {len(docs)} 个文档" + (" (降级模式)" if degraded else ""))

        # 2. 构建上下文
        context = _format_docs(docs)

        # 3. 构建 prompt（使用原始查询）
        prompt_text = STRICT_RAG_QA_PROMPT.format(context=context, question=query)

        # 4. 调用 LLM（_resolve_chat_model 已内置 fallback，model=None 时自动读 SystemConfig）
        llm = _resolve_chat_model(model, streaming=False)

        response = llm.invoke([HumanMessage(content=prompt_text)])
        answer = response.content if hasattr(response, 'content') else str(response)

        # 5. 提取来源
        sources = _extract_sources(docs)

        result = {
            "answer": answer,
            "sources": sources,
            "retrieved_docs": docs,
            "success": True,
        }

        # 6. 降级标记
        if degraded or any(doc.metadata.get("degraded") for doc in docs):
            result["degraded"] = True
            result["degradation_notice"] = "向量检索服务暂时不可用，结果由关键词检索提供，相关性可能低于正常水平"

        logger.info("严格 RAG 查询完成")
        return result

    except Exception as e:
        logger.error(f"严格 RAG 查询失败: {e}", exc_info=True)
        raise


async def aquery_strict_rag(
    retriever: BaseRetriever,
    query: str,
    model: Any | None = None,
    k: int = 4,
    use_hyde: bool = True,
    collection_name: str = "",
) -> dict[str, Any]:
    """
    严格 RAG 查询（异步）

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
    from Django_xm.apps.knowledge.services.retrieval_service import (
        _is_embedding_error,
        _keyword_search_fallback,
        _record_degradation_metric,
    )

    logger.info(f"异步严格 RAG 查询: {query[:50]}...")

    try:
        # 0. HyDE 查询改写
        retrieval_query = query
        if use_hyde:
            llm_for_hyde = None
            if model is not None and isinstance(model, BaseChatModel):
                llm_for_hyde = model
            retrieval_query = await _hyde_rewrite_query(query, llm=llm_for_hyde)
            if retrieval_query != query:
                logger.info(f"HyDE 改写: '{query[:50]}...' -> '{retrieval_query[:50]}...'")
            else:
                logger.info("HyDE 改写未生效，使用原始查询")

        # 1. 检索（使用改写后的查询），支持降级
        degraded = False
        try:
            docs = await retriever.ainvoke(retrieval_query)
        except Exception as e:
            if _is_embedding_error(e):
                logger.warning(f"向量检索失败，降级到全文关键词检索: {e}")
                if collection_name:
                    _record_degradation_metric(collection_name, e)
                docs = await asyncio.to_thread(_keyword_search_fallback, query, collection_name, k)
                degraded = True
            else:
                raise

        logger.info(f"检索到 {len(docs)} 个文档" + (" (降级模式)" if degraded else ""))

        # 2. 构建上下文
        context = _format_docs(docs)

        # 3. 构建 prompt（使用原始查询）
        prompt_text = STRICT_RAG_QA_PROMPT.format(context=context, question=query)

        # 4. 调用 LLM（_resolve_chat_model 已内置 fallback，model=None 时自动读 SystemConfig）
        llm = _resolve_chat_model(model, streaming=True)

        response = await llm.ainvoke([HumanMessage(content=prompt_text)])
        answer = response.content if hasattr(response, 'content') else str(response)

        # 5. 提取来源
        sources = _extract_sources(docs)

        result = {
            "answer": answer,
            "sources": sources,
            "retrieved_docs": docs,
            "success": True,
        }

        # 6. 降级标记
        if degraded or any(doc.metadata.get("degraded") for doc in docs):
            result["degraded"] = True
            result["degradation_notice"] = "向量检索服务暂时不可用，结果由关键词检索提供，相关性可能低于正常水平"

        logger.info("异步严格 RAG 查询完成")
        return result

    except Exception as e:
        logger.error(f"异步严格 RAG 查询失败: {e}", exc_info=True)
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
    from Django_xm.apps.knowledge.services.retrieval_service import (
        _is_embedding_error,
        _keyword_search_fallback,
        _record_degradation_metric,
    )

    logger.info(f"严格 RAG 流式查询: {query[:50]}...")

    try:
        # 0. HyDE 查询改写
        retrieval_query = query
        if use_hyde:
            yield {"type": "heartbeat", "message": "正在改写查询..."}
            llm_for_hyde = None
            if model is not None and isinstance(model, BaseChatModel):
                llm_for_hyde = model
            retrieval_query = await _hyde_rewrite_query(query, llm=llm_for_hyde)
            if retrieval_query != query:
                logger.info(f"HyDE 改写: '{query[:50]}...' -> '{retrieval_query[:50]}...'")
            else:
                logger.info("HyDE 改写未生效，使用原始查询")

        # 1. 检索（非流式，必须先完成，使用改写后的查询），支持降级
        yield {"type": "heartbeat", "message": "正在检索文档..."}
        degraded = False
        try:
            docs = await retriever.ainvoke(retrieval_query)
        except Exception as e:
            if _is_embedding_error(e):
                logger.warning(f"向量检索失败，降级到全文关键词检索: {e}")
                if collection_name:
                    _record_degradation_metric(collection_name, e)
                docs = await asyncio.to_thread(_keyword_search_fallback, query, collection_name, k)
                degraded = True
            else:
                raise

        logger.info(f"检索到 {len(docs)} 个文档" + (" (降级模式)" if degraded else ""))

        # 2. 构建上下文
        context = _format_docs(docs)

        # 3. 构建 prompt（使用原始查询）
        prompt_text = STRICT_RAG_QA_PROMPT.format(context=context, question=query)

        # 4. 流式调用 LLM（_resolve_chat_model 已内置 fallback，model=None 时自动读 SystemConfig）
        yield {"type": "heartbeat", "message": "正在生成回答..."}
        llm = _resolve_chat_model(model, streaming=True)

        full_response = ""
        async for chunk in llm.astream([HumanMessage(content=prompt_text)]):
            if hasattr(chunk, 'content') and chunk.content:
                full_response += chunk.content
                yield {"type": "chunk", "content": chunk.content}

        # 5. 发送降级提示
        if degraded or any(doc.metadata.get("degraded") for doc in docs):
            yield {
                "type": "degradation",
                "message": "向量检索服务暂时不可用，结果由关键词检索提供，相关性可能低于正常水平",
            }

        # 6. 发送来源信息
        sources = _extract_sources(docs)
        if sources:
            yield {"type": "sources", "data": sources}

        logger.info(f"严格 RAG 流式查询完成, total_len={len(full_response)}")

    except Exception as e:
        logger.error(f"严格 RAG 流式查询失败: {e}", exc_info=True)
        yield {"type": "error", "message": str(e)}


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
    from Django_xm.apps.knowledge.services.retrieval_service import (
        _is_embedding_error,
        _keyword_search_fallback,
        _record_degradation_metric,
    )

    logger.info(f"严格 RAG 同步流式查询: {query[:50]}...")

    try:
        # 0. HyDE 查询改写
        retrieval_query = query
        if use_hyde:
            yield {"type": "heartbeat", "message": "正在改写查询..."}
            llm_for_hyde = None
            if model is not None and isinstance(model, BaseChatModel):
                llm_for_hyde = model
            retrieval_query = _hyde_rewrite_query_sync(query, llm=llm_for_hyde)
            if retrieval_query != query:
                logger.info(f"HyDE 改写: '{query[:50]}...' -> '{retrieval_query[:50]}...'")
            else:
                logger.info("HyDE 改写未生效，使用原始查询")

        # 1. 检索（同步，使用改写后的查询），支持降级
        yield {"type": "heartbeat", "message": "正在检索文档..."}
        degraded = False
        try:
            docs = retriever.invoke(retrieval_query)
        except Exception as e:
            if _is_embedding_error(e):
                logger.warning(f"向量检索失败，降级到全文关键词检索: {e}")
                if collection_name:
                    _record_degradation_metric(collection_name, e)
                docs = _keyword_search_fallback(query, collection_name, k=k)
                degraded = True
            else:
                raise

        logger.info(f"检索到 {len(docs)} 个文档" + (" (降级模式)" if degraded else ""))

        # 2. 构建上下文
        context = _format_docs(docs)

        # 3. 构建 prompt（使用原始查询）
        prompt_text = STRICT_RAG_QA_PROMPT.format(context=context, question=query)

        # 4. 流式调用 LLM（_resolve_chat_model 已内置 fallback，model=None 时自动读 SystemConfig）
        yield {"type": "heartbeat", "message": "正在生成回答..."}
        llm = _resolve_chat_model(model, streaming=True)

        full_response = ""
        for chunk in llm.stream([HumanMessage(content=prompt_text)]):
            if hasattr(chunk, 'content') and chunk.content:
                full_response += chunk.content
                yield {"type": "chunk", "content": chunk.content}

        # 5. 发送降级提示
        if degraded or any(doc.metadata.get("degraded") for doc in docs):
            yield {
                "type": "degradation",
                "message": "向量检索服务暂时不可用，结果由关键词检索提供，相关性可能低于正常水平",
            }

        # 6. 发送来源信息
        sources = _extract_sources(docs)
        if sources:
            yield {"type": "sources", "data": sources}

        logger.info(f"严格 RAG 同步流式查询完成, total_len={len(full_response)}")

    except Exception as e:
        logger.error(f"严格 RAG 同步流式查询失败: {e}", exc_info=True)
        yield {"type": "error", "message": str(e)}
