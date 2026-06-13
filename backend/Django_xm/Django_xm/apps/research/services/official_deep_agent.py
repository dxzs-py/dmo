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
import asyncio
import os
import warnings

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langchain_core.tools import BaseTool, StructuredTool
from langchain.agents.middleware import AgentMiddleware

from Django_xm.apps.core.config import get_logger
from Django_xm.async_utils import run_async
from Django_xm.apps.ai_engine.services.llm_factory import get_model_string, get_chat_model
from Django_xm.apps.ai_engine.services.checkpointer_factory import get_checkpointer, get_store
from Django_xm.apps.ai_engine.guardrails import create_guardrails_middleware, create_rate_limit_middleware
from Django_xm.apps.tools.langchain.web_search import create_tavily_search_tool
from Django_xm.apps.ai_engine.capabilities import registry as capability_registry

logger = get_logger(__name__)

DEEP_RESEARCH_SYSTEM_PROMPT = (
    "你是一个专业的深度研究智能体，负责执行复杂的多步骤研究任务。\n\n"
    "## 核心要求\n"
    "你必须使用工具来完成研究，不要直接回答问题。每次研究都必须：\n"
    "1. 使用 write_todos 制定研究计划\n"
    "2. 使用 task 工具调度子智能体执行搜索和分析\n"
    "3. 使用 write_file 保存研究笔记和最终报告\n\n"
    "## 文件目录规范（必须严格遵守）\n"
    "所有文件必须写入以下规范目录，禁止写入根目录或其他位置：\n"
    "- 研究计划必须写入 /plans/ 目录（如 /plans/research_plan.md）\n"
    "- 研究笔记必须写入 /notes/ 目录（如 /notes/web_research.md、/notes/doc_analysis.md）\n"
    "- 研究报告必须写入 /reports/ 目录（如 /reports/final_report.md）\n\n"
    "## 沙箱目录说明\n"
    "/sandbox/ 目录是 Agent 工具区，用于存放和运行工具资源。\n"
    "允许写入的 sandbox 子目录：\n"
    "- /sandbox/skills/ — Skill 工具脚本\n"
    "- /sandbox/mcp/ — MCP 工具资源\n"
    "- /sandbox/deps/ — 第三方依赖\n"
    "- /sandbox/tmp/ — Agent 临时文件\n"
    "研究产出（笔记、计划、报告）必须写入 /notes/、/plans/、/reports/，不得写入 /sandbox/ 根目录或其他未列出的子目录。\n\n"
    "## 工作流程\n"
    "1. 分析研究问题，使用 write_todos 创建待办事项\n"
    "2. 使用 task 工具调用子智能体执行网络搜索和文档分析\n"
    "3. 将搜索结果和分析笔记写入 /notes/ 目录\n"
    "4. 将研究计划写入 /plans/ 目录\n"
    "5. 整合所有研究结果，撰写结构化研究报告\n"
    "6. 使用 write_file 将最终报告保存到 /reports/ 目录\n\n"
    "## 报告要求\n"
    "- 标题和摘要\n"
    "- 分章节组织内容\n"
    "- 引用来源标注\n"
    "- 结论和建议\n"
    "- 参考文献列表\n\n"
    "重要：不要跳过工具使用步骤直接给出答案，必须通过多步骤研究过程完成任务。\n"
    "重要：所有研究产出必须写入规范目录（/plans/、/notes/、/reports/），不得写入根目录或 /sandbox/ 的非工具子目录。\n"
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


_SEARCH_TOOL_NAMES = {
    'web_search', 'tavily_search', 'duckduckgo_search',
    'bing_search', 'google_search', 'serpapi_search',
    'searx_search', 'brave_search',
}


def _has_search_tools(extra_tools: Optional[List[BaseTool]] = None) -> bool:
    if not extra_tools:
        return False
    return any(
        getattr(t, 'name', '') in _SEARCH_TOOL_NAMES
        for t in extra_tools
    )


def _ensure_sync_tool(tool: BaseTool) -> BaseTool:
    """确保工具支持同步调用

    langchain-mcp-adapters 生成的 MCP 工具只设置了 coroutine（异步），
    未设置 func（同步），导致同步 graph.invoke() 调用 tool._run() 时
    抛出 "StructuredTool does not support sync invocation"。
    对缺少 func 的 StructuredTool，通过 run_async() 桥接到异步实现。
    """
    if not isinstance(tool, StructuredTool):
        return tool
    if tool.func is not None:
        return tool
    if tool.coroutine is None:
        return tool

    original_coroutine = tool.coroutine

    def _sync_run(**kwargs):
        return run_async(original_coroutine(**kwargs))

    return StructuredTool(
        name=tool.name,
        description=tool.description or "",
        args_schema=tool.args_schema,
        func=_sync_run,
        coroutine=original_coroutine,
    )


def _build_subagents(
    enable_web_search: bool = True,
    enable_doc_analysis: bool = False,
    retriever_tool: Optional[BaseTool] = None,
    extra_tools: Optional[List[BaseTool]] = None,
    subagent_middleware: Optional[Sequence[AgentMiddleware]] = None,
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
        extra_tools: 额外工具列表（MCP/自定义工具）

    Returns:
        SubAgent 列表
    """
    from deepagents import SubAgent

    subagents: List[Any] = []
    # deepagents 内部 spec.get("middleware", []) 在 middleware=None 时返回 None，
    # 导致 extend 失败，因此必须确保 middleware 不为 None
    safe_middleware: Sequence[AgentMiddleware] = subagent_middleware or []

    need_web_researcher = enable_web_search or _has_search_tools(extra_tools)

    if need_web_researcher:
        try:
            search_tool = create_tavily_search_tool()
            web_tools: List[BaseTool] = [search_tool]
            if extra_tools:
                web_tools.extend(extra_tools)
            web_subagent = SubAgent(
                name="web-researcher",
                description="网络搜索和信息整理专家，负责从互联网搜索和整理研究信息",
                system_prompt=WEB_RESEARCHER_SUBAGENT_PROMPT,
                tools=web_tools,
                middleware=safe_middleware,
            )
            subagents.append(web_subagent)
            logger.debug("添加 WebResearcher 子智能体")
        except ValueError:
            logger.warning("Tavily API Key 未配置，web-researcher 子智能体将使用默认工具")
            web_fallback_tools = list(extra_tools) if extra_tools else None
            web_subagent = SubAgent(
                name="web-researcher",
                description="网络搜索和信息整理专家",
                system_prompt=WEB_RESEARCHER_SUBAGENT_PROMPT,
                tools=web_fallback_tools,
                middleware=safe_middleware,
            )
            subagents.append(web_subagent)

    if enable_doc_analysis:
        doc_tools: List[BaseTool] = [retriever_tool] if retriever_tool else []
        if extra_tools:
            doc_tools.extend(extra_tools)
        doc_subagent = SubAgent(
            name="doc-analyst",
            description="文档分析和知识提取专家，负责在知识库中检索和分析文档",
            system_prompt=DOC_ANALYST_SUBAGENT_PROMPT,
            tools=doc_tools if doc_tools else None,
            middleware=safe_middleware,
        )
        subagents.append(doc_subagent)
        logger.debug("添加 DocAnalyst 子智能体")

    return subagents


def _build_extra_middleware(
    extra_middleware: Optional[Sequence[AgentMiddleware]] = None,
    enable_guardrails: bool = False,
    guardrails_strict_mode: bool = False,
    enable_rate_limit: bool = True,
    thread_id: str = None,
) -> List[AgentMiddleware]:
    middleware_list: List[AgentMiddleware] = []

    if enable_rate_limit:
        rate_limit = create_rate_limit_middleware(task_id=thread_id)
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


async def _fallback_load_tools(enable_web_search=False, retriever_tool=None, extra_tools=None):
    from Django_xm.apps.tools import get_tools_for_request_async
    tools = await get_tools_for_request_async(
        use_tools=True,
        use_web_search=enable_web_search,
        tool_tier="extended",
    )
    if retriever_tool:
        existing_names = {t.name for t in tools}
        if retriever_tool.name not in existing_names:
            tools.append(retriever_tool)
    if extra_tools:
        existing_names = {t.name for t in tools}
        for t in extra_tools:
            if t.name not in existing_names:
                tools.append(t)
    return tools


_SANDBOX_ALLOWED_DIRS = (
    "/sandbox/skills/",
    "/sandbox/mcp/",
    "/sandbox/deps/",
    "/sandbox/tmp/",
)


class _PatchCompositeBackend:
    """修复 CompositeBackend 的 files_update 弃用警告 + sandbox 写入守卫

    1. deepagents 0.5.x 的 CompositeBackend.write/edit 使用 dataclasses.replace()
       重建 WriteResult/EditResult，触发 files_update 参数的弃用警告。
       绕过方式：直接修改 path 属性。

    2. /sandbox/ 是 Agent 工具区（skills、MCP 工具、第三方依赖等），
       只允许预定义的工具子目录（_SANDBOX_ALLOWED_DIRS）写入，
       其他 /sandbox/ 路径一律拒绝，引导 Agent 将研究产出写入 /notes/、/plans/、/reports/。
    """

    def __init__(self, composite):
        self._composite = composite

    def __getattr__(self, name):
        return getattr(self._composite, name)

    @staticmethod
    def _check_sandbox_path(file_path: str) -> Optional[str]:
        norm = file_path.replace("\\", "/")
        if not norm.startswith("/sandbox/"):
            return None
        for allowed in _SANDBOX_ALLOWED_DIRS:
            if norm.startswith(allowed):
                return None
        allowed_list = ", ".join(_SANDBOX_ALLOWED_DIRS)
        return (
            f"Error: Path {norm} is not allowed in /sandbox/. "
            f"/sandbox/ is for tool execution only. "
            f"Research output must be written to /notes/, /plans/, or /reports/. "
            f"Allowed sandbox paths: {allowed_list}"
        )

    def _resolve(self, file_path: str):
        backend, key = self._composite._get_backend_and_key(file_path)
        return backend, key

    def write(self, file_path, content):
        err = self._check_sandbox_path(file_path)
        if err:
            from deepagents.backends.protocol import WriteResult
            return WriteResult(error=err, path=None, files_update=None)
        backend, key = self._resolve(file_path)
        res = backend.write(key, content)
        if res.path is not None:
            object.__setattr__(res, 'path', file_path)
        return res

    async def awrite(self, file_path, content):
        err = self._check_sandbox_path(file_path)
        if err:
            from deepagents.backends.protocol import WriteResult
            return WriteResult(error=err, path=None, files_update=None)
        backend, key = self._resolve(file_path)
        res = await backend.awrite(key, content)
        if res.path is not None:
            object.__setattr__(res, 'path', file_path)
        return res

    def edit(self, file_path, old_string, new_string, replace_all=False):
        err = self._check_sandbox_path(file_path)
        if err:
            from deepagents.backends.protocol import EditResult
            return EditResult(error=err, path=None, files_update=None, occurrences=None)
        backend, key = self._resolve(file_path)
        res = backend.edit(key, old_string, new_string, replace_all=replace_all)
        if res.path is not None:
            object.__setattr__(res, 'path', file_path)
        return res

    async def aedit(self, file_path, old_string, new_string, replace_all=False):
        err = self._check_sandbox_path(file_path)
        if err:
            from deepagents.backends.protocol import EditResult
            return EditResult(error=err, path=None, files_update=None, occurrences=None)
        backend, key = self._resolve(file_path)
        res = await backend.aedit(key, old_string, new_string, replace_all=replace_all)
        if res.path is not None:
            object.__setattr__(res, 'path', file_path)
        return res


def _get_backend(
    backend_type: str = "state",
    work_dir: Optional[str] = None,
    sandbox_dir: Optional[str] = None,
) -> Any:
    try:
        if backend_type == "state":
            from deepagents.backends import StateBackend
            return StateBackend()
        elif backend_type == "filesystem":
            from deepagents.backends import FilesystemBackend, CompositeBackend

            fs_backend = FilesystemBackend(root_dir=work_dir or ".", virtual_mode=True)

            if sandbox_dir and os.path.isdir(sandbox_dir):
                sandbox_backend = FilesystemBackend(root_dir=sandbox_dir, virtual_mode=True)
                composite = CompositeBackend(
                    default=fs_backend,
                    routes={"/sandbox/": sandbox_backend},
                )
                patched = _PatchCompositeBackend(composite)
                logger.info(f"CompositeBackend: default={work_dir}, /sandbox/={sandbox_dir}")
                return patched

            return fs_backend
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

    def __init__(self, graph, thread_id: str, work_dir: Optional[str] = None, model=None, **kwargs):
        self.graph = graph
        self.thread_id = thread_id
        self.work_dir = work_dir
        self.model = model
        self._kwargs = kwargs

    def research(self, query: str, config: Optional[Dict[str, Any]] = None, callbacks: Optional[list] = None) -> Dict[str, Any]:
        logger.info(f"[OfficialDeepAgent] 同步研究: {query[:50]}...")

        if config is None:
            config = {}
        if "configurable" not in config:
            config["configurable"] = {}
        config["configurable"]["thread_id"] = self.thread_id
        config.setdefault("recursion_limit", 1000)
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

            # 当 AI 回复过短时（通常是确认语），从磁盘文件提取完整报告
            if len(final_report.strip()) < 200 and disk_files:
                for report_key in ("reports/final_report.md", "final_report.md"):
                    if report_key in disk_files:
                        report_content = disk_files[report_key].get("content", "")
                        if len(report_content.strip()) > len(final_report.strip()):
                            final_report = report_content
                            logger.info(f"[OfficialDeepAgent] 使用磁盘报告文件替代短回复 ({len(final_report)} 字符)")
                        break

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
        config.setdefault("recursion_limit", 1000)
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

            # 当 AI 回复过短时（通常是确认语），从磁盘文件提取完整报告
            if len(final_report.strip()) < 200 and disk_files:
                for report_key in ("reports/final_report.md", "final_report.md"):
                    if report_key in disk_files:
                        report_content = disk_files[report_key].get("content", "")
                        if len(report_content.strip()) > len(final_report.strip()):
                            final_report = report_content
                            logger.info(f"[OfficialDeepAgent] 使用磁盘报告文件替代短回复 ({len(final_report)} 字符)")
                        break

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

    async def astream_research_with_interrupts(
        self,
        query: str,
        config: Optional[Dict[str, Any]] = None,
        callbacks: Optional[list] = None,
        on_interrupt=None,
    ) -> Dict[str, Any]:
        """流式研究 + interrupt 审批机制

        使用 graph.astream() + stream_mode=["messages", "updates"] 执行，
        捕获 __interrupt__ 事件，通过 on_interrupt 回调通知外部，
        等待回调返回后使用 Command(resume=...) 恢复 agent 继续执行。

        集成韧性模块：重试（指数退避）、降级（减少工具）、回退（无工具直接 LLM 回答）。

        Args:
            query: 研究查询
            config: LangGraph 运行配置
            callbacks: LangChain 回调列表
            on_interrupt: 审批中断回调，签名 on_interrupt(interrupt_data: dict) -> resume_value
                          interrupt_data 包含 tool_name, interrupt_id, title, description 等
                          返回值作为 Command(resume=...) 的 resume_value（True/False/用户输入）

        Returns:
            与 research() 方法相同格式的结果字典
        """
        from langgraph.types import Command, Interrupt
        from Django_xm.apps.tools.base import is_approval_interrupt
        from Django_xm.utils.agent_resilience import (
            classify_and_decide, ErrorAction, DegradationLevel,
            get_resilience_config, calculate_backoff,
            ExecutionTimeoutManager,
        )

        logger.info(f"[OfficialDeepAgent] 流式研究(interrupt): {query[:50]}...")

        if config is None:
            config = {}
        if "configurable" not in config:
            config["configurable"] = {}
        config["configurable"]["thread_id"] = self.thread_id
        config.setdefault("recursion_limit", 1000)
        if callbacks:
            config["callbacks"] = callbacks

        resilience_config = get_resilience_config()
        current_degradation = DegradationLevel.FULL
        timeout_mgr = ExecutionTimeoutManager(
            soft_timeout=resilience_config.soft_timeout or 900,
            hard_timeout=resilience_config.hard_timeout or 1800,
        )

        try:
            graph_input = {"messages": [HumanMessage(content=query)]}
            accumulated_result = None

            while True:
                all_resume_values = {}  # 收集实时回调的审批结果
                retry_count = 0

                while retry_count <= resilience_config.max_retries:
                    try:
                        async for chunk in self.graph.astream(
                            graph_input,
                            config=config,
                            stream_mode=["messages", "updates"],
                        ):
                            # 检查 hard timeout
                            if timeout_mgr.hard_timeout and timeout_mgr.elapsed >= timeout_mgr.hard_timeout:
                                logger.warning(
                                    f"[Resilience] 深度研究执行超时 (hard): {timeout_mgr.elapsed:.1f}s"
                                )
                                return await self._fallback_direct_answer(query, config)

                            # 检查 soft timeout（仅警告一次）
                            if timeout_mgr.check_soft_timeout():
                                logger.warning(
                                    f"[Resilience] 深度研究执行超时 (soft): {timeout_mgr.elapsed:.1f}s"
                                )

                            # 多 stream mode 下 chunk 是 (mode_name, data) 元组
                            if isinstance(chunk, tuple) and len(chunk) == 2:
                                mode_name, mode_data = chunk
                            else:
                                mode_name, mode_data = "messages", chunk

                            # 处理 updates stream mode（包含 interrupt 事件）
                            if mode_name == "updates":
                                if isinstance(mode_data, dict) and "__interrupt__" in mode_data:
                                    interrupts = mode_data["__interrupt__"]
                                    if interrupts:
                                        for intr in interrupts:
                                            if isinstance(intr, Interrupt):
                                                interrupt_value = intr.value
                                                interrupt_id = intr.id
                                            elif isinstance(intr, dict):
                                                interrupt_value = intr.get("value", intr)
                                                interrupt_id = intr.get("id", "")
                                            else:
                                                interrupt_value = intr
                                                interrupt_id = ""

                                            if is_approval_interrupt(interrupt_value):
                                                tool_name = interrupt_value.get("tool_name", "unknown")
                                                logger.info(
                                                    f"[OfficialDeepAgent] 审批中断: tool={tool_name}, "
                                                    f"danger={interrupt_value.get('danger_level', 'medium')}"
                                                )
                                                interrupt_data = {
                                                    "tool_name": tool_name,
                                                    "interrupt_id": interrupt_id,
                                                    "title": interrupt_value.get("title", "确认操作"),
                                                    "description": interrupt_value.get("description", ""),
                                                    "action": interrupt_value.get("action", "confirm"),
                                                    "danger_level": interrupt_value.get("danger_level", "medium"),
                                                    "operation": interrupt_value.get("operation", ""),
                                                    "state": "pending",
                                                }
                                                if interrupt_value.get("extra"):
                                                    interrupt_data["extra"] = interrupt_value["extra"]
                                                if interrupt_value.get("input_placeholder"):
                                                    interrupt_data["input_placeholder"] = interrupt_value["input_placeholder"]
                                                # 实时回调：立即发布审批请求，不等流结束
                                                if on_interrupt is not None:
                                                    logger.info(
                                                        f"[OfficialDeepAgent] 实时通知审批回调: tool={tool_name}"
                                                    )
                                                    single_resume = on_interrupt([interrupt_data])
                                                    all_resume_values.update(single_resume)
                                                else:
                                                    all_resume_values[interrupt_id] = False
                                continue

                            # messages 模式：收集最终结果
                            # astream 在 messages 模式下产出 (message, metadata) 元组
                            # 我们不需要逐步处理，只需等待流结束

                        # 流正常结束，退出 retry 循环
                        break

                    except Exception as e:
                        action, classified = classify_and_decide(e, retry_count, resilience_config.max_retries)

                        if action == ErrorAction.RETRY:
                            retry_count += 1
                            backoff = calculate_backoff(retry_count, resilience_config)
                            logger.warning(
                                f"[Resilience] 深度研究重试 {retry_count}/{resilience_config.max_retries}, "
                                f"退避 {backoff:.1f}s: {classified.error_code}"
                            )
                            await asyncio.sleep(backoff)
                            continue  # 重试

                        elif action == ErrorAction.DEGRADE:
                            if current_degradation == DegradationLevel.FULL:
                                current_degradation = DegradationLevel.REDUCED_TOOLS
                                logger.warning("[Resilience] 深度研究降级: FULL → REDUCED_TOOLS")
                                # 工具降级需要重建 graph，此处先重试当前 graph
                                retry_count += 1
                                backoff = calculate_backoff(retry_count, resilience_config)
                                await asyncio.sleep(backoff)
                                continue
                            elif current_degradation == DegradationLevel.REDUCED_TOOLS:
                                current_degradation = DegradationLevel.NO_TOOLS
                                logger.warning("[Resilience] 深度研究降级: REDUCED_TOOLS → NO_TOOLS，回退到直接回答")
                                return await self._fallback_direct_answer(query, config)
                            else:
                                return await self._fallback_direct_answer(query, config)

                        elif action == ErrorAction.FALLBACK:
                            return await self._fallback_direct_answer(query, config)

                        else:  # FAIL
                            error_msg = str(e) or repr(e) or type(e).__name__
                            logger.error(
                                f"[Resilience] 深度研究不可恢复错误: {classified.error_code}: {error_msg}",
                                exc_info=True,
                            )
                            return {
                                "success": False,
                                "query": query,
                                "final_report": None,
                                "error": classified.user_message,
                                "error_code": classified.error_code,
                            }

                # 流结束后检查是否有实时回调收集的审批结果
                if all_resume_values:
                    # 使用 Command(resume=...) 一次性恢复所有 interrupt
                    graph_input = Command(resume=all_resume_values)
                    logger.info(
                        f"[OfficialDeepAgent] 恢复 agent: {len(all_resume_values)} 个 interrupt, "
                        f"resume_dict={all_resume_values}"
                    )
                    # 重置 all_resume_values，继续循环
                    all_resume_values = {}
                    # 继续循环，重新流式执行
                    continue
                else:
                    # 无中断，流已完成，提取最终结果
                    break

            # 从 checkpointer 获取最终状态
            final_state = await self.graph.aget_state(config)
            if final_state and hasattr(final_state, 'values') and final_state.values:
                accumulated_result = final_state.values
            else:
                accumulated_result = {}

            final_report = _extract_ai_response(accumulated_result)
            files_info = self._extract_files_info(accumulated_result)
            disk_files = self._scan_disk_files()

            # 当 AI 回复过短时，从磁盘文件提取完整报告
            if len(final_report.strip()) < 200 and disk_files:
                for report_key in ("reports/final_report.md", "final_report.md"):
                    if report_key in disk_files:
                        report_content = disk_files[report_key].get("content", "")
                        if len(report_content.strip()) > len(final_report.strip()):
                            final_report = report_content
                            logger.info(f"[OfficialDeepAgent] 使用磁盘报告文件替代短回复 ({len(final_report)} 字符)")
                        break

            result = {
                "success": True,
                "query": query,
                "final_report": final_report,
                "plan": None,
                "current_step": "completed",
                "error": None,
                "files": files_info if files_info else disk_files,
                "state_files": disk_files,
            }
            if current_degradation != DegradationLevel.FULL:
                result["degraded"] = True
                result["degradation_level"] = current_degradation.value
            return result

        except Exception as e:
            error_msg = str(e) or repr(e) or type(e).__name__
            logger.error(f"[OfficialDeepAgent] 流式研究(interrupt)失败: {error_msg}", exc_info=True)
            return {
                "success": False,
                "query": query,
                "final_report": None,
                "error": error_msg,
            }

    async def _fallback_direct_answer(self, query: str, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """深度研究最终回退：无工具直接 LLM 回答"""
        logger.warning("[Resilience] 深度研究回退到无工具直接回答")
        try:
            messages = [HumanMessage(content=query)]
            if hasattr(self, 'model') and self.model:
                # model 可能是 ChatModel 实例或 model string
                if hasattr(self.model, 'ainvoke'):
                    response = await self.model.ainvoke(messages)
                    content = response.content if hasattr(response, 'content') else str(response)
                else:
                    # model 是字符串，需要创建 ChatModel 实例
                    from Django_xm.apps.ai_engine.services.llm_factory import get_chat_model
                    chat_model = get_chat_model()
                    response = await chat_model.ainvoke(messages)
                    content = response.content if hasattr(response, 'content') else str(response)
            else:
                from Django_xm.apps.ai_engine.services.llm_factory import get_chat_model
                chat_model = get_chat_model()
                response = await chat_model.ainvoke(messages)
                content = response.content if hasattr(response, 'content') else str(response)

            return {
                "success": True,
                "query": query,
                "final_report": content,
                "plan": None,
                "current_step": "completed",
                "error": None,
                "files": [],
                "state_files": {},
                "degraded": True,
                "degradation_level": "no_tools",
            }
        except Exception as e:
            logger.error(f"[Resilience] 深度研究回退回答也失败: {e}")
            return {
                "success": False,
                "query": query,
                "final_report": None,
                "error": f"深度研究执行失败: {str(e)}",
                "error_code": "FALLBACK_FAILED",
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
        config.setdefault("recursion_limit", 1000)

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
        for subdir in ("notes", "plans", "reports"):
            sub_path = os.path.join(self.work_dir, subdir)
            if not os.path.isdir(sub_path):
                continue
            for root, dirs, fnames in os.walk(sub_path):
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
    extra_tools: Optional[List[BaseTool]] = None,
    selected_skill_names: Optional[List[str]] = None,
    research_context: str = "",
    capabilities: Optional[Sequence[str]] = None,
    tool_config: Optional[Dict[str, Any]] = None,
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
        extra_tools: 额外工具列表（MCP 工具、用户自定义 LangChain 工具等）

    Returns:
        OfficialDeepAgentAdapter 实例
    """
    from deepagents import create_deep_agent

    warnings.warn("create_official_deep_agent 已废弃，请使用 Django_xm.apps.agent_hub.create()", DeprecationWarning, stacklevel=2)
    logger.info(f"创建官方 Deep Agent: thread_id={thread_id}")

    _provider_id = kwargs.pop('provider_id', None)
    _model_name = kwargs.pop('model_name', None)
    _temperature = kwargs.pop('temperature', None)
    _max_tokens = kwargs.pop('max_tokens', None)
    _special_params = kwargs.pop('special_params', None)

    # 使用统一模型解析（带自动 fallback）
    from Django_xm.apps.agent_hub.config import AgentConfig
    from Django_xm.apps.agent_hub.model_resolver import resolve_model

    model_config = AgentConfig(
        provider_id=_provider_id,
        model_name=_model_name,
        temperature=_temperature,
        max_tokens=_max_tokens,
        special_params=_special_params,
    )
    model = resolve_model(model_config)
    model_string = _model_name if _provider_id and _model_name else (str(model) if isinstance(model, str) else get_model_string())

    if checkpointer is None:
        checkpointer = get_checkpointer()

    if store is None:
        auto_store = get_store()
        if auto_store is not None:
            store = auto_store
            logger.info("自动注入 Store（长期记忆）")

    effective_capabilities = list(capabilities or capability_registry.get_default_capabilities("deep_research"))

    if effective_capabilities:
        extra_middleware = list(capability_registry.build_middleware_for_agent(
            "deep_research", effective_capabilities,
            model=model_string,
            model_name=model_string,
            user_id=str(user_id) if user_id else None,
            store=store,
            task_id=thread_id,
            thread_id=thread_id,
        ))
        if enable_guardrails:
            guardrails = create_guardrails_middleware(
                strict_mode=guardrails_strict_mode,
                validate_tool_calls=True,
                raise_on_error=guardrails_strict_mode,
            )
            extra_middleware.append(guardrails)
            logger.info("GuardrailsMiddleware 已启用")
        if middleware:
            extra_middleware.extend(middleware)
            logger.debug(f"追加 {len(middleware)} 个自定义 Middleware")
    else:
        extra_middleware = _build_extra_middleware(
            extra_middleware=middleware,
            enable_guardrails=enable_guardrails,
            guardrails_strict_mode=guardrails_strict_mode,
            thread_id=thread_id,
        )

    _selected_mcp_servers = kwargs.pop('selected_mcp_servers', None)
    _selected_tools = kwargs.pop('selected_tools', None)

    effective_tool_config = tool_config or {
        "use_tools": True,
        "use_web_search": enable_web_search,
        "use_mcp": bool(extra_tools),
        "selected_tools": _selected_tools or [],
        "selected_mcp_servers": _selected_mcp_servers or [],
        "user_id": user_id,
    }

    if "tool_injection" in effective_capabilities:
        try:
            tools = run_async(capability_registry.build_tools_for_agent_async(
                "deep_research", effective_capabilities, tool_config=effective_tool_config,
            ))
        except Exception as e:
            logger.warning(f"CapabilityRegistry 工具加载失败，回退到手动加载: {e}")
            tools = run_async(_fallback_load_tools(
                enable_web_search=enable_web_search,
                retriever_tool=retriever_tool,
                extra_tools=extra_tools,
            ))
    else:
        tools = run_async(_fallback_load_tools(
            enable_web_search=enable_web_search,
            retriever_tool=retriever_tool,
            extra_tools=extra_tools,
        ))

    tools = [_ensure_sync_tool(t) for t in tools]

    subagent_middleware_list: Optional[Sequence[AgentMiddleware]] = None
    if "context_management" in effective_capabilities:
        try:
            from Django_xm.apps.context_manager.middleware import ContextManagerMiddleware
            lightweight_ctx = ContextManagerMiddleware(
                model_name=model_string,
                trigger_tokens=60000,
                strategy="hybrid",
                user_id=str(user_id) if user_id else None,
                store=store,
                thread_id=thread_id,
            )
            subagent_middleware_list = [lightweight_ctx]
            logger.info("为子智能体注入轻量级 ContextManagerMiddleware (trigger_tokens=60000)")
        except Exception as e:
            logger.warning(f"子智能体上下文管理 Middleware 创建失败: {e}")

    has_search = _has_search_tools(extra_tools)
    if has_search and not enable_web_search:
        logger.info("extra_tools 中包含搜索工具，自动启用 web-researcher 子智能体")

    subagents = _build_subagents(
        enable_web_search=enable_web_search,
        enable_doc_analysis=enable_doc_analysis,
        retriever_tool=retriever_tool,
        extra_tools=extra_tools,
        subagent_middleware=subagent_middleware_list,
    )

    backend = _get_backend(backend_type=backend_type, work_dir=work_dir)

    if backend_type == "filesystem" and work_dir is None:
        from django.conf import settings as django_settings
        data_dir = str(getattr(django_settings, "DATA_DIR", None) or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            "data"
        ))
        work_dir = os.path.join(data_dir, "research", thread_id)
        os.makedirs(work_dir, exist_ok=True)
        sandbox_dir = os.path.join(work_dir, "sandbox")
        os.makedirs(sandbox_dir, exist_ok=True)
        backend = _get_backend(backend_type="filesystem", work_dir=work_dir, sandbox_dir=sandbox_dir)
        logger.info(f"CompositeBackend 工作目录: {work_dir}, sandbox: {sandbox_dir}")

    system_prompt = DEEP_RESEARCH_SYSTEM_PROMPT
    if research_context:
        system_prompt = f"{DEEP_RESEARCH_SYSTEM_PROMPT}\n\n---\n\n{research_context}"

    agent_kwargs: Dict[str, Any] = {
        "model": model,
        "tools": tools if tools else None,
        "system_prompt": system_prompt,
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

    # Skills 集成：按用户选择加载 Skill 目录
    if skills is None and selected_skill_names:
        try:
            from Django_xm.apps.tools.skills.adapter import SkillAdapter
            adapter = SkillAdapter(user_id=user_id)
            skill_dirs = adapter.to_deep_agent_skills(selected_skill_names=selected_skill_names)
            if skill_dirs:
                skills = skill_dirs
                logger.info(f"按选择加载 {len(skill_dirs)} 个 Skill: {selected_skill_names}")
        except Exception as e:
            logger.warning(f"加载 Skill 目录失败: {e}")

    if skills and backend_type == "filesystem" and work_dir:
        skills_work_dir = os.path.join(work_dir, "sandbox", "skills")
        os.makedirs(skills_work_dir, exist_ok=True)
        sandbox_skills = []
        for skill_dir in skills:
            if not os.path.isdir(skill_dir):
                continue
            skill_name = os.path.basename(skill_dir)
            dest = os.path.join(skills_work_dir, skill_name)
            if not os.path.exists(dest):
                try:
                    os.symlink(skill_dir, dest)
                    logger.debug(f"符号链接 Skill 到沙箱: {skill_dir} -> {dest}")
                except OSError:
                    import shutil
                    shutil.copytree(skill_dir, dest)
                    logger.debug(f"复制 Skill 目录到沙箱: {skill_dir} -> {dest}")
            sandbox_skills.append(dest)
        if sandbox_skills:
            skills = sandbox_skills
            logger.info(f"Skill 已链接到沙箱: {skills_work_dir}")

    if skills:
        agent_kwargs["skills"] = skills

    if context_schema is not None:
        agent_kwargs["context_schema"] = context_schema
        logger.info(f"使用 context_schema: {context_schema.__name__}")

    if cache is not None:
        agent_kwargs["cache"] = cache
        logger.info("使用 Agent 级别缓存")

    agent_kwargs.update(kwargs)

    # 过滤掉 create_deep_agent 不支持的参数
    _unsupported = {'enable_deep_thinking'}
    for key in _unsupported:
        agent_kwargs.pop(key, None)

    graph = create_deep_agent(**agent_kwargs)

    logger.info(
        f"官方 Deep Agent 创建成功 "
        f"(tools={len(tools)}, extra_middleware={len(extra_middleware)}, subagents={len(subagents)}, "
        f"backend={backend_type}, store={'yes' if store else 'no'}, "
        f"extra_tools={len(extra_tools) if extra_tools else 0}, "
        f"capabilities={effective_capabilities})"
    )

    return OfficialDeepAgentAdapter(graph=graph, thread_id=thread_id, work_dir=work_dir, model=model)


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
