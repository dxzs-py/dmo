"""
RAG Agent 模块
基于 LangChain v1.2.13 实现 RAG Agent
"""

from typing import List, Optional, Dict, Any

from langchain.agents import create_agent
from langchain_core.retrievers import BaseRetriever

from Django_xm.apps.ai_engine.config import get_logger
from Django_xm.apps.ai_engine.services.llm_factory import get_model_string
from Django_xm.apps.core.permissions import PermissionService
from .retrieval_service import create_retriever_tool

logger = get_logger(__name__)

DEFAULT_RAG_SYSTEM_PROMPT = """你是一个智能问答助手，专门回答基于知识库的问题。

你的任务：
1. 使用 knowledge_base 工具搜索相关信息
2. 基于检索到的文档内容回答用户问题
3. 如果文档中没有相关信息，诚实地告诉用户
4. 在回答中引用来源文档

回答要求：
- 准确：严格基于文档内容，不要编造信息
- 完整：尽可能提供详细的回答
- 引用：在回答末尾列出参考的文档来源
"""


def create_rag_agent(
    retriever: BaseRetriever,
    model: Optional[str] = None,
    system_prompt: Optional[str] = None,
    tool_name: str = "knowledge_base",
    tool_description: Optional[str] = None,
    streaming: bool = True,
    user_id: Optional[int] = None,
    session_id: Optional[str] = None,
    **kwargs,
):
    """创建 RAG Agent"""
    logger.info("创建 RAG Agent")

    if model is None:
        model = get_model_string()

    if system_prompt is None:
        system_prompt = DEFAULT_RAG_SYSTEM_PROMPT

    if tool_description is None:
        tool_description = (
            "搜索知识库中的相关信息。"
            "当需要回答关于文档内容的问题时使用此工具。"
            "输入应该是一个搜索查询。"
        )

    retriever_tool = create_retriever_tool(
        retriever=retriever,
        name=tool_name,
        description=tool_description,
    )

    tools = [retriever_tool]

    if user_id:
        tools = PermissionService.wrap_tools_with_permission(
            tools, user_id=user_id, session_id=session_id
        )
        logger.info(f"RAG Agent 权限过滤后工具数: {len(tools)}")

    logger.debug("创建 Agent...")

    agent = create_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        **kwargs,
    )

    logger.info(f"RAG Agent 创建成功，模型: {model}")
    return agent


def query_rag_agent(agent, query: str, return_sources: bool = True) -> Dict[str, Any]:
    """查询 RAG Agent 的便捷函数"""
    logger.info(f"查询 RAG Agent: {query[:50]}...")

    try:
        result = agent.invoke({"messages": [{"role": "user", "content": query}]})

        if isinstance(result, dict) and "messages" in result:
            messages = result["messages"]
            if messages:
                answer = messages[-1].content if hasattr(messages[-1], 'content') else str(messages[-1])
            else:
                answer = str(result)
        else:
            answer = str(result)

        formatted = {"answer": answer}
        logger.info("查询完成")
        return formatted

    except Exception as e:
        logger.error(f"查询失败: {e}")
        raise


async def aquery_rag_agent(agent, query: str, return_sources: bool = True) -> Dict[str, Any]:
    """异步查询 RAG Agent"""
    logger.info(f"异步查询 RAG Agent: {query[:50]}...")

    try:
        result = await agent.ainvoke({"messages": [{"role": "user", "content": query}]})

        if isinstance(result, dict) and "messages" in result:
            messages = result["messages"]
            if messages:
                answer = messages[-1].content if hasattr(messages[-1], 'content') else str(messages[-1])
            else:
                answer = str(result)
        else:
            answer = str(result)

        formatted = {"answer": answer}
        logger.info("异步查询完成")
        return formatted

    except Exception as e:
        logger.error(f"异步查询失败: {e}")
        raise


def format_rag_response(
    output: str,
    intermediate_steps: Optional[List] = None,
) -> Dict[str, Any]:
    """格式化 RAG 响应，提取来源文档"""
    response = {
        "answer": output,
        "sources": [],
        "retrieved_documents": [],
    }

    if not intermediate_steps:
        return response

    for step in intermediate_steps:
        if len(step) >= 2:
            action, observation = step[0], step[1]

            if hasattr(action, "tool") and "knowledge" in action.tool.lower():
                if isinstance(observation, list):
                    for doc in observation:
                        response["retrieved_documents"].append(doc)

                        if hasattr(doc, "metadata") and doc.metadata:
                            source = doc.metadata.get("source") or doc.metadata.get("filename")
                            if source and source not in response["sources"]:
                                response["sources"].append(source)

    return response


def create_conversational_rag_agent(
    retriever: BaseRetriever,
    model: Optional[str] = None,
    system_prompt: Optional[str] = None,
    user_id: Optional[int] = None,
    session_id: Optional[str] = None,
    **kwargs,
):
    """创建支持对话历史的 RAG Agent"""
    if system_prompt is None:
        system_prompt = """你是一个智能问答助手，专门回答基于知识库的问题。

你的任务：
1. 理解用户的问题，考虑对话历史的上下文
2. 使用 knowledge_base 工具搜索相关信息
3. 基于检索到的文档内容和对话历史回答问题
4. 保持对话的连贯性和上下文感知

回答要求：
- 上下文感知：理解用户问题与之前对话的关系
- 准确：严格基于文档内容
- 自然：像人类一样进行对话
- 引用：适当引用来源文档
"""

    logger.info("💬 创建对话式 RAG Agent")

    return create_rag_agent(
        retriever=retriever,
        model=model,
        system_prompt=system_prompt,
        user_id=user_id,
        session_id=session_id,
        **kwargs,
    )