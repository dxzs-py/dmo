"""
基础 Agent 模块
使用 LangChain v1.2.13 的 create_agent API 实现通用的智能体封装

改进：
1. 支持 middleware 参数传入 create_agent，使 GuardrailsMiddleware 生效
2. 支持 response_format 参数实现结构化输出
3. 支持 checkpointer/store 参数实现状态持久化和长期记忆
4. 集成 LangSmith 追踪配置
5. astream() 异步流式输出
6. ToolMessage 标准错误处理
7. build_middleware_stack 构建 Middleware 栈
"""

from typing import List, Optional, Dict, Any, Iterator, AsyncIterator, Union, Sequence

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_core.language_models.chat_models import BaseChatModel
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware

from Django_xm.apps.ai_engine.config import settings, get_logger
from Django_xm.apps.ai_engine.services.llm_factory import get_chat_model, get_model_string
from Django_xm.apps.ai_engine.prompts.system_prompts import get_system_prompt, get_prompt_with_tools, TOOL_USAGE_INSTRUCTIONS
from Django_xm.apps.ai_engine.guardrails import (
    create_guardrails_middleware,
    create_rate_limit_middleware,
    build_middleware_stack,
)
from Django_xm.apps.tools import BASIC_TOOLS
from Django_xm.apps.core.permissions import PermissionService

logger = get_logger(__name__)


def _configure_langsmith() -> None:
    if not settings.langsmith_tracing:
        return

    import os
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    if settings.langsmith_api_key:
        os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key)
    if settings.langsmith_project:
        os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
    if settings.langsmith_endpoint:
        os.environ.setdefault("LANGSMITH_ENDPOINT", settings.langsmith_endpoint)
    logger.info(f"LangSmith 追踪已启用, 项目: {settings.langsmith_project}")


_configure_langsmith()


class BaseAgent:
    """基础 Agent 类"""

    def __init__(
        self,
        model: Optional[Union[str, BaseChatModel]] = None,
        tools: Optional[Sequence[BaseTool]] = None,
        system_prompt: Optional[str] = None,
        prompt_mode: str = "default",
        middleware: Optional[Sequence[AgentMiddleware]] = None,
        enable_guardrails: Optional[bool] = None,
        guardrails_strict_mode: Optional[bool] = None,
        enable_pii: Optional[bool] = None,
        enable_human_in_loop: Optional[bool] = None,
        response_format: Optional[Any] = None,
        checkpointer: Optional[Any] = None,
        store: Optional[Any] = None,
        context_schema: Optional[Any] = None,
        cache: Optional[Any] = None,
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
            try:
                from Django_xm.apps.ai_engine.guardrails import create_permission_middleware
                perm_middleware = create_permission_middleware(
                    user_id=self.user_id,
                    session_id=self.session_id,
                )
                if middleware is None:
                    middleware = [perm_middleware]
                else:
                    middleware = list(middleware) + [perm_middleware]
                logger.info(f"PermissionMiddleware 已注入（动态权限过滤，user={self.user_id}）")
            except (ImportError, Exception) as e:
                logger.warning(f"PermissionMiddleware 不可用，回退到静态过滤: {e}")
                self.tools = PermissionService.wrap_tools_with_permission(
                    self.tools, user_id=self.user_id, session_id=self.session_id
                )
                logger.info(f"静态权限过滤后工具集 ({len(self.tools)} 个工具)")

        if self.tools:
            tool_names = [tool.name for tool in self.tools]
            logger.debug(f"工具列表: {', '.join(tool_names)}")

        if system_prompt is None:
            try:
                from Django_xm.apps.ai_engine.prompts.system_prompts import build_dynamic_prompt
                self.system_prompt = build_dynamic_prompt(
                    mode=prompt_mode,
                    user_id=self.user_id,
                    session_id=self.session_id,
                    include_document_context=bool(self.tools),
                    include_knowledge_graph=bool(self.tools),
                    query=None,
                    store=store,
                )
                if self.tools:
                    mcp_section = self._build_mcp_tools_section()
                    tool_instructions = TOOL_USAGE_INSTRUCTIONS.format(mcp_tools_section=mcp_section)
                    self.system_prompt += f"\n\n{tool_instructions}"
                logger.info(f"动态提示词已构建 (mode={prompt_mode}, user={self.user_id})")
            except Exception as e:
                logger.warning(f"动态提示词构建失败，回退到静态: {e}")
                if self.tools:
                    self.system_prompt = get_prompt_with_tools(mode=prompt_mode)
                else:
                    self.system_prompt = get_system_prompt(mode=prompt_mode)
        else:
            self.system_prompt = system_prompt

        self.debug = debug

        enable_guardrails = enable_guardrails if enable_guardrails is not None else settings.guardrails_enabled
        guardrails_strict_mode = guardrails_strict_mode if guardrails_strict_mode is not None else settings.guardrails_strict_mode
        enable_pii = enable_pii if enable_pii is not None else settings.guardrails_enable_pii
        enable_human_in_loop = enable_human_in_loop if enable_human_in_loop is not None else settings.guardrails_enable_human_in_loop

        middleware_list = list(middleware) if middleware else []

        if self._is_groq_model():
            try:
                from Django_xm.apps.ai_engine.guardrails.middleware import GroqToolCallCompatMiddleware
                middleware_list.append(GroqToolCallCompatMiddleware())
                logger.info("GroqToolCallCompatMiddleware 已注入（Groq 工具调用兼容）")
            except (ImportError, Exception) as e:
                logger.warning(f"GroqToolCallCompatMiddleware 不可用: {e}")

        if checkpointer is not None:
            try:
                from langchain.agents.middleware import SummarizationMiddleware
                summarization_model = self._resolve_summarization_model()
                summarization = SummarizationMiddleware(
                    model=summarization_model,
                    trigger=("tokens", getattr(settings, "summarization_trigger_tokens", 4000)),
                    keep=("messages", getattr(settings, "summarization_keep_messages", 20)),
                )
                middleware_list.append(summarization)
                logger.info("SummarizationMiddleware 已注入（Checkpointer 模式下自动启用）")
            except (ImportError, AttributeError) as e:
                logger.warning(f"SummarizationMiddleware 不可用，保留 SessionCompactor 降级: {e}")

        if enable_guardrails or enable_pii or enable_human_in_loop:
            stack = build_middleware_stack(
                enable_guardrails=enable_guardrails,
                enable_pii=enable_pii,
                enable_human_in_loop=enable_human_in_loop,
                guardrails_strict=guardrails_strict_mode,
                pii_reject=guardrails_strict_mode,
                enable_rate_limit=False,
                extra_middleware=middleware_list,
            )
            middleware_list = stack
            logger.info(
                f"构建 Middleware 栈: {len(middleware_list)} 个中间件 "
                f"(guardrails={enable_guardrails}, pii={enable_pii}, hitl={enable_human_in_loop})"
            )
        elif enable_guardrails:
            guardrails = create_guardrails_middleware(
                strict_mode=guardrails_strict_mode,
                validate_tool_calls=True,
                raise_on_error=guardrails_strict_mode,
            )
            middleware_list.append(guardrails)
            middleware_list.insert(0, create_rate_limit_middleware(
                max_model_calls=50,
                max_tool_calls=30,
            ))
            logger.info("GuardrailsMiddleware + RateLimitMiddleware 已启用")
        else:
            middleware_list.insert(0, create_rate_limit_middleware(
                max_model_calls=50,
                max_tool_calls=30,
            ))

        try:
            logger.info("创建 Agent（使用 LangChain create_agent API）...")

            agent_kwargs: Dict[str, Any] = {
                "model": self.model,
                "tools": self.tools if self.tools else None,
                "system_prompt": self.system_prompt,
                "debug": self.debug,
            }

            if middleware_list:
                agent_kwargs["middleware"] = middleware_list
                logger.info(f"传入 {len(middleware_list)} 个 Middleware")

            if response_format is not None:
                agent_kwargs["response_format"] = response_format
                logger.info("启用结构化输出")

            if checkpointer is not None:
                agent_kwargs["checkpointer"] = checkpointer

            if store is not None:
                agent_kwargs["store"] = store
                logger.info("使用传入的 Store")
            elif getattr(settings, "store_enabled", False):
                from Django_xm.apps.ai_engine.services.checkpointer_factory import get_store
                auto_store = get_store()
                if auto_store is not None:
                    agent_kwargs["store"] = auto_store
                    logger.info("自动注入 Store（长期记忆）")

            if context_schema is not None:
                agent_kwargs["context_schema"] = context_schema
                logger.info(f"使用 context_schema: {getattr(context_schema, '__name__', str(context_schema))}")

            if cache is not None:
                agent_kwargs["cache"] = cache
                logger.info("使用 Agent 级别缓存")
            elif getattr(settings, "agent_cache_enabled", False):
                try:
                    from langgraph.cache.memory import InMemoryCache
                    agent_kwargs["cache"] = InMemoryCache()
                    logger.info("自动注入 InMemoryCache（Agent 级别缓存）")
                except ImportError:
                    logger.warning("langgraph.cache.memory.InMemoryCache 不可用")

            agent_kwargs.update(kwargs)

            self.graph = create_agent(**agent_kwargs)
            logger.info("Agent 创建成功")
        except Exception as e:
            logger.error(f"Agent 创建失败: {e}")
            raise

    def _is_groq_model(self) -> bool:
        if isinstance(self.model, str):
            return "groq" in self.model.lower() or "llama" in self.model.lower()
        model_cls = type(self.model).__name__.lower()
        if "groq" in model_cls:
            return True
        model_name = getattr(self.model, "model_name", "") or getattr(self.model, "model", "") or ""
        return "llama" in str(model_name).lower()

    MCP_TOOL_NAMES = {
        "sequentialthinking", "resolve-library-id", "query-docs",
        "project_info", "system_status", "data_query",
    }

    MCP_TOOL_DESCRIPTIONS = {
        "sequentialthinking": "🧠 深度思考链工具 — 分步骤推理、拆解复杂问题、生成并验证假设（来源：sequential-thinking MCP Server）",
        "resolve-library-id": "🔖 解析库/包名称为 Context7 兼容的库 ID（来源：context7 MCP Server）",
        "query-docs": "📖 查询编程库/框架的最新文档和代码示例（来源：context7 MCP Server）",
        "project_info": "📋 查询当前项目信息，包括版本、技术栈、架构设计、模块划分等（来源：本地 MCP 工具）",
        "system_status": "⚙️ 检查系统运行状态，包括数据库连接、缓存服务、MCP 服务器连接等（来源：本地 MCP 工具）",
        "data_query": "🗄️ 查询项目数据，支持统计计数、列表查看和模型结构查看（来源：本地 MCP 工具）",
    }

    def _build_mcp_tools_section(self) -> str:
        if not self.tools:
            return "（当前未加载 MCP 工具）"

        mcp_tools = [t for t in self.tools if t.name in self.MCP_TOOL_NAMES]
        if not mcp_tools:
            return "（当前未加载 MCP 工具）"

        lines = []
        for tool in mcp_tools:
            desc = self.MCP_TOOL_DESCRIPTIONS.get(tool.name)
            if desc:
                lines.append(f"- {desc}")
            else:
                short_desc = (tool.description or "无描述")[:80]
                lines.append(f"- {tool.name}: {short_desc}")
        return "\n".join(lines)

    def _resolve_summarization_model(self) -> str:
        if isinstance(self.model, str):
            return self.model

        provider_id = getattr(self.model, "_provider_id", None)
        model_name = (
            getattr(self.model, "model_name", None)
            or getattr(self.model, "model", None)
        )

        if provider_id and model_name:
            return f"{provider_id}:{model_name}"

        if model_name:
            known_providers = {
                "ChatOpenAI": "openai",
                "ChatDeepSeek": "deepseek",
                "ChatAnthropic": "anthropic",
                "ChatOllama": "ollama",
                "ChatGroq": "groq",
                "ChatZhipuAI": "zhipu",
            }
            cls_name = type(self.model).__name__
            provider = known_providers.get(cls_name, "openai")
            return f"{provider}:{model_name}"

        return get_model_string()

    def _build_config(self, **kwargs) -> Dict[str, Any]:
        config: Dict[str, Any] = {"recursion_limit": kwargs.pop("recursion_limit", 50)}

        if settings.langsmith_tracing:
            config["run_name"] = kwargs.pop("run_name", "BaseAgent")
            tags = kwargs.pop("tags", [])
            if self.user_id:
                tags.append(f"user:{self.user_id}")
            if self.session_id:
                tags.append(f"session:{self.session_id}")
            if tags:
                config["tags"] = tags
            metadata = kwargs.pop("metadata", {})
            metadata["user_id"] = self.user_id
            metadata["session_id"] = self.session_id
            config["metadata"] = metadata

        config.update(kwargs)
        return config

    def _prepare_graph_input(
        self,
        input_text: str,
        chat_history: Optional[List[BaseMessage]] = None,
        config_override: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> tuple:
        messages = []
        if chat_history:
            messages.extend(chat_history)
        messages.append(HumanMessage(content=input_text))
        graph_input: dict[str, Any] = {"messages": messages}
        graph_input.update(kwargs)
        cfg = self._build_config()
        if config_override:
            cfg.update(config_override)
        return graph_input, cfg

    @staticmethod
    def _extract_ai_response(result: dict) -> str:
        output_messages = result.get("messages", [])
        for msg in reversed(output_messages):
            if isinstance(msg, AIMessage):
                return msg.content
        return ""

    def invoke(
        self,
        input_text: str,
        chat_history: Optional[List[BaseMessage]] = None,
        **kwargs: Any,
    ) -> str:
        logger.info(f"执行 Agent 调用: {input_text[:50]}...")

        try:
            graph_input, config = self._prepare_graph_input(input_text, chat_history, **kwargs)
            result = self.graph.invoke(graph_input, config=config)
            ai_response = self._extract_ai_response(result)

            logger.info(f"Agent 调用完成，输出长度: {len(ai_response)} 字符")
            return ai_response

        except Exception as e:
            error_msg = f"Agent 执行失败: {str(e)}"
            logger.error(error_msg)
            raise

    def stream(
        self,
        input_text: str,
        chat_history: Optional[List[BaseMessage]] = None,
        stream_mode: str = "messages",
        **kwargs: Any,
    ) -> Iterator[str]:
        logger.info(f"执行 Agent 流式调用: {input_text[:50]}...")

        try:
            graph_input, config = self._prepare_graph_input(input_text, chat_history, **kwargs)

            for chunk in self.graph.stream(graph_input, config=config, stream_mode=stream_mode):
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
            graph_input, final_config = self._prepare_graph_input(input_text, chat_history, config_override=config, **kwargs)
            result = await self.graph.ainvoke(graph_input, config=final_config)
            ai_response = self._extract_ai_response(result)
            return ai_response

        except Exception as e:
            error_msg = f"Agent 异步执行失败: {str(e)}"
            logger.error(error_msg)
            raise

    async def astream(
        self,
        input_text: str,
        chat_history: Optional[List[BaseMessage]] = None,
        stream_mode: str = "messages",
        config: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """
        异步流式输出

        使用 LangGraph 的 astream() 方法实现异步流式返回，
        适用于 SSE 等实时推送场景。
        """
        logger.info(f"执行 Agent 异步流式调用: {input_text[:50]}...")

        try:
            graph_input, final_config = self._prepare_graph_input(input_text, chat_history, config_override=config, **kwargs)

            async for chunk in self.graph.astream(
                graph_input, config=final_config, stream_mode=stream_mode
            ):
                if stream_mode == "messages":
                    if isinstance(chunk, tuple) and len(chunk) == 2:
                        message, metadata = chunk
                        if isinstance(message, AIMessage) and message.content:
                            yield message.content
                    elif isinstance(chunk, AIMessage) and chunk.content:
                        yield chunk.content

            logger.info("Agent 异步流式调用完成")

        except Exception as e:
            error_msg = f"Agent 异步流式执行失败: {str(e)}"
            logger.error(error_msg)
            yield f"\n\n抱歉，处理您的请求时出现错误: {str(e)}"

    async def astream_events(
        self,
        input_text: str,
        chat_history: Optional[List[BaseMessage]] = None,
        version: str = "v2",
        include_names: Optional[Sequence[str]] = None,
        include_types: Optional[Sequence[str]] = None,
        include_tags: Optional[Sequence[str]] = None,
        config: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        异步事件流（LangGraph astream_events）

        返回细粒度事件流，包含 on_chat_model_start/stream/end、
        on_tool_start/end 等事件，适用于需要完整可观测性的场景。

        参考: https://langchain-ai.github.io/langgraph/how-tos/streaming-events/

        Args:
            input_text: 用户输入
            chat_history: 聊天历史
            version: 事件协议版本，默认 "v2"
            include_names: 仅包含指定名称的事件
            include_types: 仅包含指定类型的事件
            include_tags: 仅包含指定标签的事件
            config: 运行时配置
            **kwargs: 额外图输入

        Yields:
            事件字典
        """
        logger.info(f"执行 Agent astream_events: {input_text[:50]}...")

        try:
            graph_input, final_config = self._prepare_graph_input(input_text, chat_history, config_override=config, **kwargs)

            stream_kwargs: Dict[str, Any] = {"version": version}
            if include_names:
                stream_kwargs["include_names"] = include_names
            if include_types:
                stream_kwargs["include_types"] = include_types
            if include_tags:
                stream_kwargs["include_tags"] = include_tags

            async for event in self.graph.astream_events(
                graph_input, config=final_config, **stream_kwargs
            ):
                yield event

            logger.info("Agent astream_events 完成")

        except Exception as e:
            logger.error(f"Agent astream_events 失败: {e}")
            raise


def create_base_agent(
    model: Optional[Union[str, BaseChatModel]] = None,
    tools: Optional[Sequence[BaseTool]] = None,
    prompt_mode: str = "default",
    middleware: Optional[Sequence[AgentMiddleware]] = None,
    enable_guardrails: Optional[bool] = None,
    guardrails_strict_mode: Optional[bool] = None,
    enable_pii: Optional[bool] = None,
    enable_human_in_loop: Optional[bool] = None,
    response_format: Optional[Any] = None,
    checkpointer: Optional[Any] = None,
    store: Optional[Any] = None,
    context_schema: Optional[Any] = None,
    cache: Optional[Any] = None,
    debug: bool = False,
    user_id: Optional[int] = None,
    session_id: Optional[str] = None,
    **kwargs: Any,
) -> BaseAgent:
    logger.info(f"创建 Base Agent (mode={prompt_mode}, debug={debug}, user_id={user_id}, guardrails={enable_guardrails})")

    return BaseAgent(
        model=model,
        tools=tools,
        prompt_mode=prompt_mode,
        middleware=middleware,
        enable_guardrails=enable_guardrails,
        guardrails_strict_mode=guardrails_strict_mode,
        enable_pii=enable_pii,
        enable_human_in_loop=enable_human_in_loop,
        response_format=response_format,
        checkpointer=checkpointer,
        store=store,
        context_schema=context_schema,
        cache=cache,
        debug=debug,
        user_id=user_id,
        session_id=session_id,
        **kwargs,
    )
