"""
官方 Deep Agent 模块
基于 deepagents 官方包的 create_deep_agent API 实现

核心原则：
1. 优先使用 create_deep_agent 内置的默认 Middleware 栈（Filesystem/SubAgent/Summarization/PatchToolCalls）
2. 仅通过 middleware 参数追加自定义 Middleware（如 Guardrails）
3. 通过 OfficialDeepAgentAdapter 适配层兼容现有 research/aresearch/astream_research 接口
4. 使用 create_summarization_middleware 工厂函数而非直接实例化

官方 create_deep_agent 默认内置 Middleware 栈：
  TodoListMiddleware → FilesystemMiddleware → SubAgentMiddleware →
  SummarizationMiddleware → PatchToolCallsMiddleware → [custom middleware] →
  AnthropicPromptCachingMiddleware → MemoryMiddleware

参考：
- https://reference.langchain.com/python/deepagents/graph/create_deep_agent
- https://reference.langchain.com/python/deepagents/middleware/summarization/create_summarization_middleware
"""

from typing import Optional, Dict, Any, List, Sequence, Type, AsyncIterator
import os

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langchain_core.tools import BaseTool
from langchain.agents.middleware import AgentMiddleware

from Django_xm.apps.config_center.config import get_logger
from Django_xm.apps.ai_engine.services.llm_factory import get_model_string, get_chat_model
from Django_xm.apps.ai_engine.services.checkpointer_factory import get_checkpointer, get_store
from Django_xm.apps.ai_engine.guardrails import create_guardrails_middleware, create_rate_limit_middleware
from Django_xm.apps.tools.web.search import create_tavily_search_tool

logger = get_logger(__name__)

DEEP_RESEARCH_SYSTEM_PROMPT = (
    "你是一个专业的深度研究智能体，负责执行复杂的多步骤研究任务。\n\n"
    "## 核心要求\n"
    "你必须使用工具来完成研究，不要直接回答问题。每次研究都必须：\n"
    "1. 使用 write_todos 制定研究计划\n"
    "2. 使用 task 工具调度子智能体执行搜索和分析\n"
    "3. 使用 write_file 保存研究笔记和最终报告\n\n"
    "## 工作流程\n"
    "1. 分析研究问题，使用 write_todos 创建待办事项\n"
    "2. 使用 task 工具调用子智能体执行网络搜索和文档分析\n"
    "3. 将搜索结果和分析笔记写入文件（notes/目录）\n"
    "4. 整合所有研究结果，撰写结构化研究报告\n"
    "5. 使用 write_file 将最终报告保存到 reports/目录\n\n"
    "## 报告要求\n"
    "- 标题和摘要\n"
    "- 分章节组织内容\n"
    "- 引用来源标注\n"
    "- 结论和建议\n"
    "- 参考文献列表\n\n"
    "重要：不要跳过工具使用步骤直接给出答案，必须通过多步骤研究过程完成任务。\n"
)

WEB_RESEARCHER_SUBAGENT_PROMPT = (
    "你是一个专业的网络研究员，负责从互联网搜索与整理信息。"
    "使用搜索工具查找并评估来源，提取关键数据，"
    "按来源类型自适配呈现，采用要点与段落混合的方式记录，"
    "使用内联引用并在结尾列出参考来源。"
)

DOC_ANALYST_SUBAGENT_PROMPT = (
    "你是一个专业的文档分析师，负责在知识库中检索并提炼信息。"
    "根据研究问题执行多次检索与评估，直接引用关键段落，"
    "整理为要点与段落混合的分析笔记，列出文档来源与位置。"
)


def _build_subagents(
    enable_web_search: bool = True,
    enable_doc_analysis: bool = False,
    retriever_tool: Optional[BaseTool] = None,
) -> List[Any]:
    """
    构建 SubAgent 列表

    官方 create_deep_agent 通过 subagents 参数声明子智能体，
    主 Agent 通过 task() 工具自动调度。

    如果没有名为 "general-purpose" 的子智能体，
    官方会自动添加一个默认的通用子智能体。

    Args:
        enable_web_search: 是否启用网络搜索子智能体
        enable_doc_analysis: 是否启用文档分析子智能体
        retriever_tool: RAG 检索工具

    Returns:
        SubAgent 列表
    """
    from deepagents import SubAgent

    subagents: List[Any] = []

    if enable_web_search:
        try:
            search_tool = create_tavily_search_tool()
            web_subagent = SubAgent(
                name="web-researcher",
                description="网络搜索和信息整理专家，负责从互联网搜索和整理研究信息",
                system_prompt=WEB_RESEARCHER_SUBAGENT_PROMPT,
                tools=[search_tool],
            )
            subagents.append(web_subagent)
            logger.debug("添加 WebResearcher 子智能体")
        except ValueError:
            logger.warning("Tavily API Key 未配置，web-researcher 子智能体将使用默认工具")

            web_subagent = SubAgent(
                name="web-researcher",
                description="网络搜索和信息整理专家",
                system_prompt=WEB_RESEARCHER_SUBAGENT_PROMPT,
            )
            subagents.append(web_subagent)

    if enable_doc_analysis:
        doc_tools = [retriever_tool] if retriever_tool else []
        doc_subagent = SubAgent(
            name="doc-analyst",
            description="文档分析和知识提取专家，负责在知识库中检索和分析文档",
            system_prompt=DOC_ANALYST_SUBAGENT_PROMPT,
            tools=doc_tools if doc_tools else None,
        )
        subagents.append(doc_subagent)
        logger.debug("添加 DocAnalyst 子智能体")

    return subagents


def _build_extra_middleware(
    extra_middleware: Optional[Sequence[AgentMiddleware]] = None,
    enable_guardrails: bool = False,
    guardrails_strict_mode: bool = False,
    enable_rate_limit: bool = True,
) -> List[AgentMiddleware]:
    middleware_list: List[AgentMiddleware] = []

    if enable_rate_limit:
        rate_limit = create_rate_limit_middleware(
            max_model_calls=30,
            max_tool_calls=20,
        )
        middleware_list.append(rate_limit)
        logger.info("RateLimitMiddleware 已启用")

    if enable_guardrails:
        guardrails = create_guardrails_middleware(
            strict_mode=guardrails_strict_mode,
            validate_tool_calls=True,
            raise_on_error=guardrails_strict_mode,
        )
        middleware_list.append(guardrails)
        logger.info("GuardrailsMiddleware 已启用")

    if extra_middleware:
        middleware_list.extend(extra_middleware)
        logger.debug(f"追加 {len(extra_middleware)} 个自定义 Middleware")

    return middleware_list


def _get_backend(
    backend_type: str = "state",
    work_dir: Optional[str] = None,
) -> Any:
    try:
        if backend_type == "state":
            from deepagents.backends import StateBackend
            return StateBackend()
        elif backend_type == "filesystem":
            from deepagents.backends import FilesystemBackend
            return FilesystemBackend(root_dir=work_dir or ".", virtual_mode=True)
        elif backend_type == "local_shell":
            from deepagents.backends import LocalShellBackend
            return LocalShellBackend(workdir=work_dir or ".")
        else:
            logger.warning(f"未知的 backend 类型: {backend_type}，使用 StateBackend")
            from deepagents.backends import StateBackend
            return StateBackend()
    except ImportError as e:
        logger.warning(f"Backend 导入失败: {e}，将不使用 backend")
        return None


def _extract_ai_response(result: Dict[str, Any]) -> str:
    """从 create_deep_agent 的 invoke 结果中提取最终 AI 回复"""
    messages = result.get("messages", [])
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            content = msg.content
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(item.get("text", ""))
                    elif isinstance(item, str):
                        parts.append(item)
                return "\n".join(parts)
            return content
    return ""


class OfficialDeepAgentAdapter:
    """
    官方 Deep Agent 适配器

    将 create_deep_agent 返回的 CompiledStateGraph 适配为
    与 DeepResearchAgent 兼容的接口（research/aresearch/astream_research），
    使上层调用方无需关心底层实现差异。
    """

    def __init__(self, graph, thread_id: str, work_dir: Optional[str] = None, **kwargs):
        self.graph = graph
        self.thread_id = thread_id
        self.work_dir = work_dir
        self._kwargs = kwargs

    def research(self, query: str, config: Optional[Dict[str, Any]] = None, callbacks: Optional[list] = None) -> Dict[str, Any]:
        logger.info(f"[OfficialDeepAgent] 同步研究: {query[:50]}...")

        if config is None:
            config = {}
        if "configurable" not in config:
            config["configurable"] = {}
        config["configurable"]["thread_id"] = self.thread_id
        config.setdefault("recursion_limit", 50)
        if callbacks:
            config["callbacks"] = callbacks

        try:
            result = self.graph.invoke(
                {"messages": [HumanMessage(content=query)]},
                config=config,
            )

            final_report = _extract_ai_response(result)
            files_info = self._extract_files_info(result)
            disk_files = self._scan_disk_files()

            return {
                "success": True,
                "query": query,
                "final_report": final_report,
                "plan": None,
                "current_step": "completed",
                "error": None,
                "files": files_info if files_info else disk_files,
                "state_files": disk_files,
            }

        except Exception as e:
            logger.error(f"[OfficialDeepAgent] 同步研究失败: {e}")
            return {
                "success": False,
                "query": query,
                "final_report": None,
                "error": str(e),
            }

    async def aresearch(self, query: str, config: Optional[Dict[str, Any]] = None, callbacks: Optional[list] = None) -> Dict[str, Any]:
        logger.info(f"[OfficialDeepAgent] 异步研究: {query[:50]}...")

        if config is None:
            config = {}
        if "configurable" not in config:
            config["configurable"] = {}
        config["configurable"]["thread_id"] = self.thread_id
        config.setdefault("recursion_limit", 50)
        if callbacks:
            config["callbacks"] = callbacks

        try:
            result = await self.graph.ainvoke(
                {"messages": [HumanMessage(content=query)]},
                config=config,
            )

            final_report = _extract_ai_response(result)
            files_info = self._extract_files_info(result)
            disk_files = self._scan_disk_files()

            return {
                "success": True,
                "query": query,
                "final_report": final_report,
                "plan": None,
                "current_step": "completed",
                "error": None,
                "files": files_info if files_info else disk_files,
                "state_files": disk_files,
            }

        except Exception as e:
            logger.error(f"[OfficialDeepAgent] 异步研究失败: {e}")
            return {
                "success": False,
                "query": query,
                "final_report": None,
                "error": str(e),
            }

    async def astream_research(
        self,
        query: str,
        config: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        logger.info(f"[OfficialDeepAgent] 流式研究: {query[:50]}...")

        if config is None:
            config = {}
        if "configurable" not in config:
            config["configurable"] = {}
        config["configurable"]["thread_id"] = self.thread_id
        config.setdefault("recursion_limit", 30)

        try:
            yield {
                "event_type": "research_start",
                "node_name": "root",
                "data": {"query": query},
            }

            async for event in self.graph.astream_events(
                {"messages": [HumanMessage(content=query)]},
                config=config,
                version="v2",
            ):
                kind = event.get("event", "")
                name = event.get("name", "")
                data = event.get("data", {})

                if kind == "on_chat_model_stream":
                    chunk = data.get("chunk")
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        yield {
                            "event_type": "token_stream",
                            "node_name": name,
                            "data": {"content": chunk.content},
                        }
                elif kind == "on_tool_start":
                    yield {
                        "event_type": "tool_start",
                        "node_name": name,
                        "data": {"input": str(data.get("input", ""))[:200]},
                    }
                elif kind == "on_tool_end":
                    yield {
                        "event_type": "tool_end",
                        "node_name": name,
                        "data": {"output": str(data.get("output", ""))[:200]},
                    }
                elif kind == "on_chain_end" and name == "LangGraph":
                    output = data.get("output", {})
                    final_report = _extract_ai_response(output)
                    yield {
                        "event_type": "research_end",
                        "node_name": "root",
                        "data": {
                            "final_report": final_report,
                            "success": True,
                        },
                    }
                elif kind == "on_chain_error":
                    yield {
                        "event_type": "error",
                        "node_name": name,
                        "data": {"error": str(data.get("error", "未知错误"))},
                    }

        except Exception as e:
            logger.error(f"[OfficialDeepAgent] 流式研究失败: {e}")
            yield {
                "event_type": "error",
                "node_name": "root",
                "data": {"error": str(e)},
            }

    def get_status(self) -> Dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "engine": "deepagents_official",
        }

    def _get_files_from_state(self, config: Dict[str, Any]) -> Dict[str, Any]:
        try:
            state = self.graph.get_state(config)
            if state and hasattr(state, 'values') and state.values:
                files = state.values.get('files', {})
                if files:
                    logger.info(f"[OfficialDeepAgent] 从 state 获取到 {len(files)} 个文件")
                    return files
            logger.info("[OfficialDeepAgent] state 中无 files 数据")
        except Exception as e:
            logger.warning(f"[OfficialDeepAgent] 从 state 获取文件失败: {e}")
        return {}

    def _scan_disk_files(self) -> Dict[str, Any]:
        if not self.work_dir or not os.path.isdir(self.work_dir):
            return {}
        files = {}
        for root, dirs, fnames in os.walk(self.work_dir):
            for fname in fnames:
                full_path = os.path.join(root, fname)
                rel_path = os.path.relpath(full_path, self.work_dir).replace("\\", "/")
                try:
                    with open(full_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    files[rel_path] = {
                        "content": content,
                        "encoding": "utf-8",
                    }
                except (UnicodeDecodeError, OSError):
                    pass
        if files:
            logger.info(f"[OfficialDeepAgent] 从磁盘扫描到 {len(files)} 个文件: {list(files.keys())}")
        return files

    @staticmethod
    def _extract_files_info(result: Dict[str, Any]) -> List[Dict[str, Any]]:
        files = []
        messages = result.get("messages", [])
        for msg in messages:
            if hasattr(msg, "tool_calls"):
                for tc in msg.tool_calls:
                    if tc.get("name") == "write_file":
                        args = tc.get("args", {})
                        path = args.get("path", "")
                        if path:
                            files.append({
                                "path": path,
                                "name": os.path.basename(path),
                                "type": "file",
                                "size": len(args.get("content", "")),
                            })
            if hasattr(msg, "name") and msg.name == "write_file":
                pass
        return files


def create_official_deep_agent(
    thread_id: str,
    enable_web_search: bool = True,
    enable_doc_analysis: bool = False,
    retriever_tool: Optional[BaseTool] = None,
    middleware: Optional[Sequence[AgentMiddleware]] = None,
    enable_guardrails: bool = False,
    guardrails_strict_mode: bool = False,
    checkpointer: Optional[Any] = None,
    store: Optional[Any] = None,
    user_id: Optional[int] = None,
    session_id: Optional[str] = None,
    response_format: Optional[Any] = None,
    memory: Optional[List[str]] = None,
    skills: Optional[List[str]] = None,
    context_schema: Optional[Type] = None,
    backend_type: str = "filesystem",
    work_dir: Optional[str] = None,
    cache: Optional[Any] = None,
    **kwargs,
) -> OfficialDeepAgentAdapter:
    """
    使用官方 deepagents.create_deep_agent 创建深度研究智能体

    核心设计：
    - 使用 create_deep_agent 内置的默认 Middleware 栈（不再手动构建）
    - 仅通过 middleware 参数追加自定义 Middleware
    - 返回 OfficialDeepAgentAdapter 适配器，兼容现有接口

    官方 create_deep_agent 完整参数：
    - model: 模型标识符（支持 "provider:model" 格式）
    - tools: 工具列表
    - system_prompt: 系统提示
    - middleware: 追加到默认栈之后的自定义 Middleware
    - subagents: 子智能体声明
    - skills: 技能文件路径
    - memory: 记忆文件路径
    - permissions: 文件系统权限
    - backend: 后端协议
    - interrupt_on: 人机交互中断
    - response_format: 结构化输出
    - context_schema: 运行时上下文
    - checkpointer: 状态持久化
    - store: 长期记忆存储
    - debug: 调试模式
    - name: 智能体名称
    - cache: Agent 级别缓存

    Args:
        thread_id: 线程 ID
        enable_web_search: 是否启用网络搜索
        enable_doc_analysis: 是否启用文档分析
        retriever_tool: RAG 检索工具
        middleware: 额外中间件（追加到官方默认栈之后）
        enable_guardrails: 是否启用 Guardrails
        guardrails_strict_mode: Guardrails 严格模式
        checkpointer: 状态持久化
        store: 长期记忆存储
        user_id: 用户 ID
        session_id: 会话 ID
        response_format: 结构化输出格式
        memory: 记忆文件路径列表
        skills: 技能文件路径列表
        context_schema: 运行时上下文 Schema
        backend_type: 后端类型 ("state"/"filesystem"/"local_shell")
        work_dir: 工作目录
        cache: Agent 级别缓存实例

    Returns:
        OfficialDeepAgentAdapter 实例
    """
    from deepagents import create_deep_agent

    logger.info(f"创建官方 Deep Agent: thread_id={thread_id}")

    from langchain.chat_models import init_chat_model
    from Django_xm.apps.ai_engine.config import settings as ai_settings

    model = init_chat_model(
        get_model_string(),
        use_responses_api=False,
        temperature=0.7,
        api_key=ai_settings.openai_api_key,
        base_url=ai_settings.openai_api_base,
    )

    tools: List[BaseTool] = []
    if enable_web_search:
        try:
            search_tool = create_tavily_search_tool()
            tools.append(search_tool)
            logger.debug("添加 Tavily 搜索工具到主 Agent")
        except ValueError:
            logger.warning("Tavily API Key 未配置，跳过主 Agent 搜索工具")

    if retriever_tool:
        tools.append(retriever_tool)
        logger.debug("添加 RAG 检索工具到主 Agent")

    subagents = _build_subagents(
        enable_web_search=enable_web_search,
        enable_doc_analysis=enable_doc_analysis,
        retriever_tool=retriever_tool,
    )

    extra_middleware = _build_extra_middleware(
        extra_middleware=middleware,
        enable_guardrails=enable_guardrails,
        guardrails_strict_mode=guardrails_strict_mode,
    )

    if checkpointer is None:
        checkpointer = get_checkpointer()

    if store is None:
        auto_store = get_store()
        if auto_store is not None:
            store = auto_store
            logger.info("自动注入 Store（长期记忆）")

    backend = _get_backend(backend_type=backend_type, work_dir=work_dir)

    if backend_type == "filesystem" and work_dir is None:
        from django.conf import settings as django_settings
        data_dir = str(getattr(django_settings, "DATA_DIR", None) or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            "data"
        ))
        work_dir = os.path.join(data_dir, "research", thread_id)
        os.makedirs(work_dir, exist_ok=True)
        backend = _get_backend(backend_type="filesystem", work_dir=work_dir)
        logger.info(f"FilesystemBackend 工作目录: {work_dir}")

    agent_kwargs: Dict[str, Any] = {
        "model": model,
        "tools": tools if tools else None,
        "system_prompt": DEEP_RESEARCH_SYSTEM_PROMPT,
        "middleware": extra_middleware if extra_middleware else (),
        "subagents": subagents if subagents else None,
        "checkpointer": checkpointer,
        "debug": False,
    }

    if store is not None:
        agent_kwargs["store"] = store

    if backend is not None:
        agent_kwargs["backend"] = backend
        logger.info(f"使用 Backend: {backend_type}")

    if response_format is not None:
        agent_kwargs["response_format"] = response_format

    if memory:
        agent_kwargs["memory"] = memory

    if skills:
        agent_kwargs["skills"] = skills

    if context_schema is not None:
        agent_kwargs["context_schema"] = context_schema
        logger.info(f"使用 context_schema: {context_schema.__name__}")

    if cache is not None:
        agent_kwargs["cache"] = cache
        logger.info("使用 Agent 级别缓存")

    agent_kwargs.update(kwargs)

    graph = create_deep_agent(**agent_kwargs)

    logger.info(
        f"官方 Deep Agent 创建成功 "
        f"(extra_middleware={len(extra_middleware)}, subagents={len(subagents)}, "
        f"backend={backend_type}, store={'yes' if store else 'no'})"
    )

    return OfficialDeepAgentAdapter(graph=graph, thread_id=thread_id, work_dir=work_dir)


def create_official_deep_agent_with_interrupt(
    thread_id: str,
    interrupt_tools: Optional[Dict[str, bool]] = None,
    **kwargs,
) -> OfficialDeepAgentAdapter:
    """
    创建支持人机交互中断的官方 Deep Agent

    interrupt_on 参数指定哪些工具调用需要人工审批，
    需要配合 checkpointer 使用（中断后可恢复）。

    Args:
        thread_id: 线程 ID
        interrupt_tools: 需要中断的工具映射，如 {"write_file": True, "execute": True}
        **kwargs: 传递给 create_official_deep_agent 的其他参数

    Returns:
        OfficialDeepAgentAdapter 实例
    """
    if interrupt_tools is None:
        interrupt_tools = {"write_file": True}

    return create_official_deep_agent(
        thread_id=thread_id,
        interrupt_on=interrupt_tools,
        **kwargs,
    )
