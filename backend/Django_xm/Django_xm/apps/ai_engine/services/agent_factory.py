"""
基础 Agent 模块
使用 LangChain 1.0.3 的 create_agent API 实现通用的智能体封装
"""

from typing import List, Optional, Dict, Any, Iterator, AsyncIterator, Union, Sequence

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langchain_core.tools import BaseTool
from langchain_core.language_models.chat_models import BaseChatModel
from langchain.agents import create_agent

from Django_xm.apps.ai_engine.config import settings, get_logger
from Django_xm.apps.ai_engine.services.llm_factory import get_chat_model, get_model_string
from Django_xm.apps.ai_engine.prompts.system_prompts import get_system_prompt, get_prompt_with_tools
from Django_xm.apps.tools import BASIC_TOOLS
from Django_xm.apps.core.permissions import PermissionService

logger = get_logger(__name__)


class BaseAgent:
    """基础 Agent 类"""

    def __init__(
        self,
        model: Optional[Union[str, BaseChatModel]] = None,
        tools: Optional[Sequence[BaseTool]] = None,
        system_prompt: Optional[str] = None,
        prompt_mode: str = "default",
        debug: bool = False,
        user_id: Optional[int] = None,
        session_id: Optional[str] = None,
        **kwargs: Any,
    ):
        if model is None:
            self.model = get_model_string()
            logger.info(f"使用默认模型: {self.model}")
        elif isinstance(model, str):
            self.model = model
            logger.info(f"使用模型标识符: {model}")
        else:
            self.model = model
            logger.info(f"使用自定义模型实例: {model.__class__.__name__}")

        if tools is None:
            self.tools = BASIC_TOOLS
            logger.info(f"使用基础工具集 ({len(self.tools)} 个工具)")
        else:
            self.tools = list(tools) if tools else []
            logger.info(f"使用自定义工具集 ({len(self.tools)} 个工具)")

        self.user_id = user_id
        self.session_id = session_id

        if self.user_id and self.tools:
            self.tools = PermissionService.wrap_tools_with_permission(
                self.tools, user_id=self.user_id, session_id=self.session_id
            )
            logger.info(f"权限过滤后工具集 ({len(self.tools)} 个工具)")

        if self.tools:
            tool_names = [tool.name for tool in self.tools]
            logger.debug(f"工具列表: {', '.join(tool_names)}")

        if system_prompt is None:
            if self.tools:
                self.system_prompt = get_prompt_with_tools(mode=prompt_mode)
            else:
                self.system_prompt = get_system_prompt(mode=prompt_mode)
        else:
            self.system_prompt = system_prompt

        self.debug = debug

        try:
            logger.info("创建 Agent（使用 LangChain create_agent API）...")
            self.graph = create_agent(
                model=self.model,
                tools=self.tools if self.tools else None,
                system_prompt=self.system_prompt,
                debug=self.debug,
                **kwargs,
            )
            logger.info("Agent 创建成功")
        except Exception as e:
            logger.error(f"Agent 创建失败: {e}")
            raise

    def invoke(
        self,
        input_text: str,
        chat_history: Optional[List[BaseMessage]] = None,
        **kwargs: Any,
    ) -> str:
        logger.info(f"执行 Agent 调用: {input_text[:50]}...")

        try:
            messages = []
            if chat_history:
                messages.extend(chat_history)
            messages.append(HumanMessage(content=input_text))

            graph_input = {"messages": messages}
            graph_input.update(kwargs)

            result = self.graph.invoke(graph_input)

            output_messages = result.get("messages", [])
            ai_response = ""
            for msg in reversed(output_messages):
                if isinstance(msg, AIMessage):
                    ai_response = msg.content
                    break

            logger.info(f"Agent 调用完成，输出长度: {len(ai_response)} 字符")
            return ai_response

        except Exception as e:
            error_msg = f"Agent 执行失败: {str(e)}"
            logger.error(error_msg)
            return f"抱歉，处理您的请求时出现错误: {str(e)}"

    def stream(
        self,
        input_text: str,
        chat_history: Optional[List[BaseMessage]] = None,
        stream_mode: str = "messages",
        **kwargs: Any,
    ) -> Iterator[str]:
        logger.info(f"执行 Agent 流式调用: {input_text[:50]}...")

        try:
            messages = []
            if chat_history:
                messages.extend(chat_history)
            messages.append(HumanMessage(content=input_text))

            graph_input = {"messages": messages}
            graph_input.update(kwargs)

            for chunk in self.graph.stream(graph_input, stream_mode=stream_mode):
                if stream_mode == "messages":
                    if isinstance(chunk, tuple) and len(chunk) == 2:
                        message, metadata = chunk
                        if isinstance(message, AIMessage) and message.content:
                            yield message.content
                    elif isinstance(chunk, AIMessage) and chunk.content:
                        yield chunk.content

            logger.info("Agent 流式调用完成")

        except Exception as e:
            error_msg = f"Agent 流式执行失败: {str(e)}"
            logger.error(error_msg)
            yield f"\n\n抱歉，处理您的请求时出现错误: {str(e)}"

    async def ainvoke(
        self,
        input_text: str,
        chat_history: Optional[List[BaseMessage]] = None,
        config: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> str:
        logger.info(f"执行 Agent 异步调用: {input_text[:50]}...")

        try:
            messages = []
            if chat_history:
                messages.extend(chat_history)
            messages.append(HumanMessage(content=input_text))

            graph_input = {"messages": messages}
            graph_input.update(kwargs)

            result = await self.graph.ainvoke(graph_input, config=config)

            output_messages = result.get("messages", [])
            ai_response = ""
            for msg in reversed(output_messages):
                if isinstance(msg, AIMessage):
                    ai_response = msg.content
                    break

            return ai_response

        except Exception as e:
            error_msg = f"Agent 异步执行失败: {str(e)}"
            logger.error(error_msg)
            return f"抱歉，处理您的请求时出现错误: {str(e)}"


def create_base_agent(
    model: Optional[Union[str, BaseChatModel]] = None,
    tools: Optional[Sequence[BaseTool]] = None,
    prompt_mode: str = "default",
    debug: bool = False,
    user_id: Optional[int] = None,
    session_id: Optional[str] = None,
    **kwargs: Any,
) -> BaseAgent:
    """创建基础 Agent 的便捷工厂函数"""
    logger.info(f"创建 Base Agent (mode={prompt_mode}, debug={debug}, user_id={user_id})")

    return BaseAgent(
        model=model,
        tools=tools,
        prompt_mode=prompt_mode,
        debug=debug,
        user_id=user_id,
        session_id=session_id,
        **kwargs,
    )