"""
RAG Agent 模块
基于 LangChain 1.0.3 实现 RAG Agent
"""

from typing import List, Optional, Dict, Any

from langchain.agents import create_agent
from langchain_core.retrievers import BaseRetriever

from Django_xm.apps.core.config import get_logger
from Django_xm.apps.core.models import get_model_string
from .retrievers import create_retriever_tool

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