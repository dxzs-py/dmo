"""
基础工作流模块

提供通用的 LangGraph StateGraph 构建工具。
注意：路由分发逻辑已迁移至 ChatService._dispatch_by_mode，
本模块构建基础对话流程（preprocess -> retrieve -> generate -> postprocess -> respond），
retrieve 节点在提供检索器时执行 RAG 检索，否则直接透传。
generate 节点失败时路由至 error_handler，生成友好错误响应。

Plan-Execute 和 Research 工作流由各自模块独立编译：
- plan_execute.py -> compile_plan_execute_workflow()
- research/services/research_workflow.py -> compile_research_workflow()
"""

from typing import Literal

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.retrievers import BaseRetriever
from langgraph.graph import END, START, StateGraph

from Django_xm.apps.ai_engine.prompts.system_prompts import get_system_prompt
from Django_xm.apps.ai_engine.services.llm_factory import get_chat_model
from Django_xm.apps.core.config import get_logger

from .state import WorkflowState

logger = get_logger(__name__)

# RAG 上下文注入模板
_RAG_CONTEXT_TEMPLATE = """【检索到的参考资料】
{context}

请基于以上参考资料回答用户的问题。如果参考资料中没有相关信息，请明确说明。"""


def _format_docs_to_context(docs: list[Document]) -> str:
    """将检索文档格式化为上下文字符串"""
    if not docs:
        return "（未检索到相关文档）"
    parts = []
    for i, doc in enumerate(docs, 1):
        source = doc.metadata.get("source", doc.metadata.get("filename", f"文档{i}"))
        content = doc.page_content.strip()
        parts.append(f"--- 参考资料 [{i}] 来源: {source} ---\n{content}\n")
    return "\n".join(parts)


async def preprocess(state: WorkflowState) -> dict:
    messages = list(state.get("messages", []))
    query = state.get("query", "")

    if messages and isinstance(messages[-1], HumanMessage):
        pass
    else:
        messages.append(HumanMessage(content=query))

    mode = state.get("mode", "default")
    system_prompt = get_system_prompt(mode=mode)
    if system_prompt and not any(isinstance(m, SystemMessage) for m in messages):
        messages.insert(0, SystemMessage(content=system_prompt))

    logger.info(f"[BaseWorkflow] preprocess: {len(messages)} messages")
    return {"messages": messages}


async def retrieve(state: WorkflowState, retriever: BaseRetriever | None = None) -> dict:
    """RAG 检索节点

    当提供了 retriever 时，执行向量检索并将结果写入 tool_results；
    否则跳过检索，直接透传到 generate 节点。
    """
    if retriever is None:
        logger.info("[BaseWorkflow] retrieve: 无检索器，跳过检索")
        return {"tool_results": []}

    query = state.get("query", "")
    # 从消息列表中提取用户查询（优先使用 query 字段）
    if not query:
        messages = state.get("messages", [])
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage) and msg.content:
                query = msg.content
                break

    if not query:
        logger.warning("[BaseWorkflow] retrieve: 无有效查询，跳过检索")
        return {"tool_results": []}

    try:
        docs: list[Document] = await retriever.ainvoke(query)
        logger.info(f"[BaseWorkflow] retrieve: 检索到 {len(docs)} 个文档")

        # 格式化为上下文字符串
        context = _format_docs_to_context(docs)

        # 将检索结果写入 tool_results，供 generate 节点注入上下文
        tool_results = [{"source": "rag_retrieve", "context": context, "doc_count": len(docs)}]
        # 同时提取来源信息供 postprocess 使用
        for doc in docs:
            source_name = doc.metadata.get("source", doc.metadata.get("filename", "未知来源"))
            tool_results.append({"source": source_name, "content": doc.page_content[:200]})

        return {"tool_results": tool_results}
    except Exception:
        logger.exception("[BaseWorkflow] retrieve: 检索失败 -")
        return {"tool_results": []}


async def generate(state: WorkflowState) -> dict:
    messages = list(state.get("messages", []))
    tool_results = state.get("tool_results", [])

    # 检查是否有 RAG 检索结果，注入上下文到消息列表
    rag_context = None
    for result in tool_results:
        if isinstance(result, dict) and result.get("source") == "rag_retrieve" and result.get("context"):
            rag_context = result["context"]
            break

    if rag_context:
        # 将检索结果作为系统消息注入到消息列表末尾（用户消息之前）
        context_message = SystemMessage(content=_RAG_CONTEXT_TEMPLATE.format(context=rag_context))
        # 找到最后一条 HumanMessage 的位置，在其前插入上下文
        insert_idx = len(messages)
        for i in range(len(messages) - 1, -1, -1):
            if isinstance(messages[i], HumanMessage):
                insert_idx = i
                break
        messages.insert(insert_idx, context_message)
        logger.info("[BaseWorkflow] generate: 已注入 RAG 检索上下文")

    try:
        model = get_chat_model()
        response = await model.ainvoke(messages)
        return {
            "messages": [response],
            "final_response": response.content,
        }
    except Exception as e:
        logger.exception("[BaseWorkflow] generate error")
        return {"error": str(e)}


async def postprocess(state: WorkflowState) -> dict:
    final_response = state.get("final_response")
    tool_results = state.get("tool_results", [])

    sources = []
    for result in tool_results:
        if isinstance(result, dict) and result.get("source") and result.get("source") != "rag_retrieve":
            sources.append(result["source"])

    if sources and final_response:
        sources_text = "\n".join(f"- {s}" for s in sources)
        final_response = f"{final_response}\n\n参考来源:\n{sources_text}"

    logger.info("[BaseWorkflow] postprocess: done")
    return {"final_response": final_response}


async def respond(state: WorkflowState) -> dict:
    final_response = state.get("final_response") or ""
    error = state.get("error")

    if error:
        final_response = f"处理过程中出现错误: {error}"

    if not final_response:
        messages = state.get("messages", [])
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and msg.content:
                final_response = msg.content if isinstance(msg.content, str) else str(msg.content)
                break

    logger.info(f"[BaseWorkflow] respond: {len(final_response)} chars")
    return {"final_response": final_response}


async def error_handler(state: WorkflowState) -> dict:
    """错误处理节点：捕获 generate 节点异常，生成友好错误响应"""
    error = state.get("error", "未知错误")
    logger.error(f"[BaseWorkflow] error_handler: {error}")
    return {
        "error": None,
        "final_response": f"处理过程中出现错误: {error}",
    }


def _route_after_generate(state: WorkflowState) -> Literal["postprocess", "error_handler"]:
    """条件路由：generate 成功走 postprocess，失败走 error_handler"""
    if state.get("error"):
        return "error_handler"
    return "postprocess"


def build_base_workflow(retriever: BaseRetriever | None = None) -> StateGraph:
    """构建基础对话工作流

    流程：
    - START -> preprocess -> retrieve -> generate
    - generate 成功 -> postprocess -> respond -> END
    - generate 失败 -> error_handler -> respond -> END

    retrieve 节点在提供了检索器时执行 RAG 检索，否则直接透传。

    Args:
        retriever: 可选的向量检索器，提供时启用 RAG 检索

    Returns:
        StateGraph 实例
    """
    graph = StateGraph(WorkflowState)

    # 使用闭包将 retriever 绑定到 retrieve 节点
    async def _retrieve_node(state: WorkflowState) -> dict:
        return await retrieve(state, retriever=retriever)

    graph.add_node("preprocess", preprocess)
    graph.add_node("retrieve", _retrieve_node)
    graph.add_node("generate", generate)
    graph.add_node("postprocess", postprocess)
    graph.add_node("error_handler", error_handler)
    graph.add_node("respond", respond)

    # 基础流程：START -> preprocess -> retrieve -> generate
    graph.add_edge(START, "preprocess")
    graph.add_edge("preprocess", "retrieve")
    graph.add_edge("retrieve", "generate")

    # generate 后条件路由：成功 -> postprocess，失败 -> error_handler
    graph.add_conditional_edges(
        "generate",
        _route_after_generate,
        {
            "postprocess": "postprocess",
            "error_handler": "error_handler",
        },
    )

    graph.add_edge("postprocess", "respond")
    graph.add_edge("error_handler", "respond")
    graph.add_edge("respond", END)

    return graph


def compile_base_workflow(
    retriever: BaseRetriever | None = None,
    checkpointer=None,
):
    """编译基础对话工作流

    Args:
        retriever: 可选的向量检索器，提供时启用 RAG 检索
        checkpointer: LangGraph checkpointer 实例（用于持久化工作流状态）

    Returns:
        编译后的 CompiledGraph
    """
    graph = build_base_workflow(retriever=retriever)

    compile_kwargs = {}
    if checkpointer is not None:
        compile_kwargs["checkpointer"] = checkpointer

    compiled = graph.compile(**compile_kwargs)

    # 附加配置信息
    compiled._workflow_config = {
        "has_checkpointer": checkpointer is not None,
        "checkpointer_type": type(checkpointer).__name__ if checkpointer else None,
    }

    return compiled
