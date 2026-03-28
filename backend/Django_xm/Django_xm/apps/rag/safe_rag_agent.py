"""
安全 RAG Agent - 集成 Guardrails 和结构化输出

这是增强版的 RAG Agent，具有以下特性：
1. 输入安全检查（防止 prompt injection、敏感信息等）
2. 输出安全检查和结构化输出
3. 强制引用来源
4. 自动格式化为 Pydantic 模型
"""

from typing import Optional, Dict, Any, List
from langchain.agents import create_agent
from langchain_core.retrievers import BaseRetriever
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate

from Django_xm.apps.core.config import settings, get_logger
from Django_xm.apps.core.models import get_model_string
from Django_xm.apps.core.guardrails import (
    InputValidator,
    OutputValidator,
    RAGResponse,
    ContentFilter,
)
from Django_xm.apps.rag.retrievers import create_retriever_tool

logger = get_logger(__name__)


SAFE_RAG_SYSTEM_PROMPT = """你是一个智能问答助手，专门回答基于知识库的问题。

你的任务：
1. 使用 knowledge_base 工具搜索相关信息
2. 基于检索到的文档内容回答用户问题
3. **必须**引用来源文档
4. 如果文档中没有相关信息，诚实地告诉用户

回答要求：
- 准确：严格基于文档内容，不要编造信息
- 完整：尽可能提供详细的回答
- 清晰：使用简洁明了的语言
- **必须引用**：必须在回答中列出所有参考的文档来源

安全要求：
- 不要泄露敏感信息
- 不要生成不安全或不当内容
- 不要执行可能有害的操作
"""


def create_safe_rag_agent(
    retriever: BaseRetriever,
    model: Optional[str] = None,
    enable_input_validation: bool = True,
    enable_output_validation: bool = True,
    strict_mode: bool = False,
    **kwargs,
):
    logger.info("🛡️ 创建安全 RAG Agent（带 Guardrails）")

    content_filter = ContentFilter(
        enable_pii_detection=True,
        enable_content_safety=True,
        enable_injection_detection=True,
        mask_pii=True,
    )

    input_validator = InputValidator(
        content_filter=content_filter,
        strict_mode=strict_mode,
    ) if enable_input_validation else None

    output_validator = OutputValidator(
        content_filter=content_filter,
        require_sources=True,
        strict_mode=strict_mode,
    ) if enable_output_validation else None

    if model is None:
        model = get_model_string()

    retriever_tool = create_retriever_tool(
        retriever=retriever,
        name="knowledge_base",
        description="搜索知识库中的相关信息。当需要回答关于文档内容的问题时使用此工具。",
    )

    agent = create_agent(
        model=model,
        tools=[retriever_tool],
        system_prompt=SAFE_RAG_SYSTEM_PROMPT,
        **kwargs,
    )

    safe_agent = SafeRAGAgent(
        agent=agent,
        retriever=retriever,
        input_validator=input_validator,
        output_validator=output_validator,
    )

    logger.info(f"✅ 安全 RAG Agent 创建成功")
    logger.info(f"   模型: {model}")
    logger.info(f"   输入验证: {enable_input_validation}")
    logger.info(f"   输出验证: {enable_output_validation}")
    logger.info(f"   严格模式: {strict_mode}")

    return safe_agent


class SafeRAGAgent:
    def __init__(
        self,
        agent,
        retriever: BaseRetriever,
        input_validator: Optional[InputValidator] = None,
        output_validator: Optional[OutputValidator] = None,
    ):
        self.agent = agent
        self.retriever = retriever
        self.input_validator = input_validator
        self.output_validator = output_validator

    def query(
        self,
        query: str,
        return_structured: bool = True,
    ):
        logger.info(f"🔍 安全查询: {query[:50]}...")

        if self.input_validator:
            validation_result = self.input_validator.validate(query)

            if not validation_result.is_valid:
                error_msg = "输入验证失败:\n" + "\n".join(
                    f"- {err}" for err in validation_result.errors
                )
                logger.error(f"❌ {error_msg}")
                raise ValueError(error_msg)

            filtered_query = validation_result.filtered_input

            if validation_result.warnings:
                logger.warning(f"⚠️ 输入警告: {validation_result.warnings}")
        else:
            filtered_query = query

        try:
            result = self.agent.invoke({
                "messages": [{"role": "user", "content": filtered_query}]
            })

            if isinstance(result, dict) and "messages" in result:
                messages = result["messages"]
                if messages:
                    answer = messages[-1].content if hasattr(messages[-1], 'content') else str(messages[-1])
                else:
                    answer = str(result)
            else:
                answer = str(result)

        except Exception as e:
            logger.error(f"❌ Agent 执行失败: {e}")
            raise

        sources = self._extract_sources(result)

        if self.output_validator:
            validation_result = self.output_validator.validate(
                answer,
                sources=sources,
            )

            if not validation_result.is_valid:
                error_msg = "输出验证失败:\n" + "\n".join(
                    f"- {err}" for err in validation_result.errors
                )
                logger.error(f"❌ {error_msg}")
                raise ValueError(error_msg)

            filtered_answer = validation_result.filtered_output

            if validation_result.warnings:
                logger.warning(f"⚠️ 输出警告: {validation_result.warnings}")
        else:
            filtered_answer = answer

        logger.info("✅ 安全查询完成")

        if return_structured:
            return RAGResponse(
                answer=filtered_answer,
                sources=sources,
                confidence=None,
                metadata={
                    "original_query": query,
                    "filtered_query": filtered_query,
                }
            )
        else:
            return {
                "answer": filtered_answer,
                "sources": sources,
            }

    async def aquery(
        self,
        query: str,
        return_structured: bool = True,
    ):
        logger.info(f"🔍 异步安全查询: {query[:50]}...")

        if self.input_validator:
            validation_result = self.input_validator.validate(query)

            if not validation_result.is_valid:
                error_msg = "输入验证失败:\n" + "\n".join(
                    f"- {err}" for err in validation_result.errors
                )
                logger.error(f"❌ {error_msg}")
                raise ValueError(error_msg)

            filtered_query = validation_result.filtered_input

            if validation_result.warnings:
                logger.warning(f"⚠️ 输入警告: {validation_result.warnings}")
        else:
            filtered_query = query

        try:
            result = await self.agent.ainvoke({
                "messages": [{"role": "user", "content": filtered_query}]
            })

            if isinstance(result, dict) and "messages" in result:
                messages = result["messages"]
                if messages:
                    answer = messages[-1].content if hasattr(messages[-1], 'content') else str(messages[-1])
                else:
                    answer = str(result)
            else:
                answer = str(result)

        except Exception as e:
            logger.error(f"❌ Agent 执行失败: {e}")
            raise

        sources = self._extract_sources(result)

        if self.output_validator:
            validation_result = self.output_validator.validate(
                answer,
                sources=sources,
            )

            if not validation_result.is_valid:
                error_msg = "输出验证失败:\n" + "\n".join(
                    f"- {err}" for err in validation_result.errors
                )
                logger.error(f"❌ {error_msg}")
                raise ValueError(error_msg)

            filtered_answer = validation_result.filtered_output

            if validation_result.warnings:
                logger.warning(f"⚠️ 输出警告: {validation_result.warnings}")
        else:
            filtered_answer = answer

        logger.info("✅ 异步安全查询完成")

        if return_structured:
            return RAGResponse(
                answer=filtered_answer,
                sources=sources,
                confidence=None,
                metadata={
                    "original_query": query,
                    "filtered_query": filtered_query,
                }
            )
        else:
            return {
                "answer": filtered_answer,
                "sources": sources,
            }

    def stream(self, query: str):
        logger.info(f"🔍 流式安全查询: {query[:50]}...")

        if self.input_validator:
            validation_result = self.input_validator.validate(query)

            if not validation_result.is_valid:
                error_msg = "输入验证失败:\n" + "\n".join(
                    f"- {err}" for err in validation_result.errors
                )
                logger.error(f"❌ {error_msg}")
                raise ValueError(error_msg)

            filtered_query = validation_result.filtered_input
        else:
            filtered_query = query

        try:
            for chunk in self.agent.stream({
                "messages": [{"role": "user", "content": filtered_query}]
            }):
                yield chunk
        except Exception as e:
            logger.error(f"❌ 流式查询失败: {e}")
            raise

    def _extract_sources(self, result: Any) -> List[str]:
        sources = []

        if isinstance(result, dict):
            if "intermediate_steps" in result:
                for step in result["intermediate_steps"]:
                    if len(step) >= 2:
                        action, observation = step[0], step[1]

                        if hasattr(action, "tool") and "knowledge" in action.tool.lower():
                            if isinstance(observation, list):
                                for doc in observation:
                                    if hasattr(doc, "metadata") and doc.metadata:
                                        source = doc.metadata.get("source") or doc.metadata.get("filename")
                                        if source and source not in sources:
                                            sources.append(source)

            if "sources" in result:
                sources.extend(result["sources"])

        if not sources:
            sources = ["知识库"]

        return sources

    def invoke(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(input_data, dict) and "messages" in input_data:
            messages = input_data["messages"]
            if messages:
                last_msg = messages[-1]
                query = last_msg.get("content") if isinstance(last_msg, dict) else str(last_msg)
                result = self.query(query, return_structured=False)
                return result

        return self.agent.invoke(input_data)

    async def ainvoke(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(input_data, dict) and "messages" in input_data:
            messages = input_data["messages"]
            if messages:
                last_msg = messages[-1]
                query = last_msg.get("content") if isinstance(last_msg, dict) else str(last_msg)
                result = await self.aquery(query, return_structured=False)
                return result

        return await self.agent.ainvoke(input_data)