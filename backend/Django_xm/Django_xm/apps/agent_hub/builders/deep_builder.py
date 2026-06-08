from __future__ import annotations

import logging
import os
import shutil
import sys
from typing import Any, Dict, List, Optional, Sequence

from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import BaseTool, StructuredTool

from Django_xm.async_utils import run_async

logger = logging.getLogger(__name__)

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
    "- /sandbox/artifacts/ — 工具结果缓存（系统自动管理）\n"
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

DOC_ANALYSIS_PROMPT_SUFFIX = (
    "\n\n## 知识库文档分析\n"
    "本次研究已启用文档分析功能，关联了知识库。\n"
    "你必须使用 task 工具委派 doc-analyst 子智能体在知识库中检索相关文档，"
    "获取与研究主题相关的已有文档内容作为研究素材。\n"
    "工作流程：\n"
    "1. 在研究计划中安排文档分析步骤\n"
    "2. 使用 task 工具调用 doc-analyst 子智能体，让它检索知识库文档\n"
    "3. 将 doc-analyst 返回的分析结果整理写入 /notes/doc_analysis.md\n"
    "4. 结合网络搜索结果和文档分析结果撰写最终报告\n"
    "重要：不要跳过知识库检索步骤，文档分析是研究的重要组成部分。"
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

_RETRIEVER_TOOL_NAME_PREFIXES = ('knowledge_base_', 'knowledge_bases', 'knowledge_retrieve')

_SANDBOX_ALLOWED_DIRS = (
    "/sandbox/skills/",
    "/sandbox/mcp/",
    "/sandbox/deps/",
    "/sandbox/tmp/",
    "/sandbox/artifacts/",
    "/sandbox/inherited/",  # 续研时继承的父任务文件，供 agent 读取参考
)


def _patch_filesystem_backend_windows():
    """Patch FilesystemBackend._resolve_path to handle Windows \\?\ prefix inconsistency.

    On Windows, Path.resolve() may add the extended-length path prefix (\\\\?\\)
    inconsistently between init time and runtime, causing relative_to() to fail
    even when the path is logically within root_dir.
    """
    if sys.platform != 'win32':
        return

    try:
        from deepagents.backends.filesystem import FilesystemBackend
    except ImportError:
        return

    if getattr(FilesystemBackend._resolve_path, '_win_patched', False):
        return

    _original_resolve_path = FilesystemBackend._resolve_path

    def _patched_resolve_path(self, key: str):
        if not self.virtual_mode:
            return _original_resolve_path(self, key)

        from pathlib import Path as _Path

        vpath = key if key.startswith("/") else "/" + key
        if ".." in vpath or vpath.startswith("~"):
            raise ValueError("Path traversal not allowed")

        full = (self.cwd / vpath.lstrip("/")).resolve()

        # Normalize \\?\ prefix for consistent comparison
        full_str = str(full)
        cwd_str = str(self.cwd)
        if full_str.startswith("\\\\?\\") != cwd_str.startswith("\\\\?\\"):
            if full_str.startswith("\\\\?\\"):
                full_str = full_str[4:]
            else:
                cwd_str = cwd_str[4:]

        try:
            _Path(full_str).relative_to(_Path(cwd_str))
        except ValueError:
            msg = f"Path:{full} outside root directory: {self.cwd}"
            raise ValueError(msg) from None

        from deepagents.backends.filesystem import _raise_if_symlink_loop
        _raise_if_symlink_loop(full)
        return full

    _patched_resolve_path._win_patched = True
    FilesystemBackend._resolve_path = _patched_resolve_path
    logger.debug("FilesystemBackend._resolve_path 已打 Windows \\?\ 路径补丁")


# 模块加载时自动打补丁
_patch_filesystem_backend_windows()


def _has_search_tools(extra_tools: Optional[List[BaseTool]] = None) -> bool:
    if not extra_tools:
        return False
    return any(
        getattr(t, 'name', '') in _SEARCH_TOOL_NAMES
        for t in extra_tools
    )


def _ensure_sync_tool(tool: BaseTool) -> BaseTool:
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
    from deepagents import SubAgent

    subagents: List[Any] = []
    # deepagents 内部 spec.get("middleware", []) 在 middleware=None 时返回 None，
    # 导致 extend 失败，因此必须确保 middleware 不为 None
    safe_middleware: Sequence[AgentMiddleware] = subagent_middleware or []

    need_web_researcher = enable_web_search or _has_search_tools(extra_tools)

    if need_web_researcher:
        try:
            from Django_xm.apps.tools.langchain.web_search import create_tavily_search_tool
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


class _PatchCompositeBackend:
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
            return WriteResult(error=err, path=None)
        backend, key = self._resolve(file_path)
        res = backend.write(key, content)
        if res.path is not None:
            object.__setattr__(res, 'path', file_path)
        return res

    async def awrite(self, file_path, content):
        err = self._check_sandbox_path(file_path)
        if err:
            from deepagents.backends.protocol import WriteResult
            return WriteResult(error=err, path=None)
        backend, key = self._resolve(file_path)
        res = await backend.awrite(key, content)
        if res.path is not None:
            object.__setattr__(res, 'path', file_path)
        return res

    def edit(self, file_path, old_string, new_string, replace_all=False):
        err = self._check_sandbox_path(file_path)
        if err:
            from deepagents.backends.protocol import EditResult
            return EditResult(error=err, path=None, occurrences=None)
        backend, key = self._resolve(file_path)
        res = backend.edit(key, old_string, new_string, replace_all=replace_all)
        if res.path is not None:
            object.__setattr__(res, 'path', file_path)
        return res

    async def aedit(self, file_path, old_string, new_string, replace_all=False):
        err = self._check_sandbox_path(file_path)
        if err:
            from deepagents.backends.protocol import EditResult
            return EditResult(error=err, path=None, occurrences=None)
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
                    artifacts_root="/sandbox/artifacts",
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


def _get_retriever_tool(retriever: Any) -> Optional[BaseTool]:
    if retriever is None:
        return None
    if isinstance(retriever, BaseTool):
        return retriever
    try:
        from langchain.tools.retriever import create_retriever_tool
        return create_retriever_tool(
            retriever=retriever,
            name="knowledge_retrieve",
            description="在知识库中检索相关文档信息",
        )
    except Exception:
        logger.warning("无法将 retriever 转换为工具")
        return None


class DeepAgentBuilder:

    async def build(self, config) -> Any:
        try:
            from deepagents import create_deep_agent
        except ImportError:
            from Django_xm.apps.agent_hub.exceptions import FrameworkNotAvailableError
            raise FrameworkNotAvailableError("deepagents 包不可用，请降级到 DEEP_RESEARCH_CUSTOM")

        from Django_xm.apps.agent_hub.model_resolver import resolve_model
        from Django_xm.apps.agent_hub.tool_resolver import resolve_tools
        from Django_xm.apps.agent_hub.middleware import build_middleware

        model = resolve_model(config)
        tools = await resolve_tools(config)
        tools = [_ensure_sync_tool(t) for t in tools]

        # 过滤与 deepagents 内置工具冲突的自定义文件系统工具
        # 原因：deepagents 内置 write_file/read_file 使用 FilesystemBackend(root_dir=work_dir)，
        # 路径为 data/research/{thread_id}/，完全正确。
        # 而 fs_write_file 等使用 ResearchFileSystem(base_path=TOOLS_LANGCHAIN_DIR/research)，
        # thread_id 默认为 "default"，路径为 data/tools/langchain/research/default/，完全错误。
        # 两套工具功能重复但路径不同，agent 可能调用错误的工具导致文件写入错误目录。
        # 解决方案：过滤 fs_* 工具，deepagents 内置工具已完全覆盖文件操作需求。
        _CONFLICT_TOOL_NAMES = {
            "fs_write_file", "fs_read_file", "fs_list_files", "fs_search_files",
        }
        original_count = len(tools)
        tools = [t for t in tools if getattr(t, 'name', '') not in _CONFLICT_TOOL_NAMES]
        filtered_count = original_count - len(tools)
        if filtered_count > 0:
            logger.info(
                f"已过滤 {filtered_count} 个冗余文件系统工具 "
                f"(fs_write_file/fs_read_file 等)，deepagents 内置工具已覆盖文件操作"
            )

        middleware_stack = build_middleware(config)

        subagents, retriever_tool_name = self._resolve_subagents(config, tools)

        # 从主 agent 工具列表中移除 retriever_tool，避免主 agent 直接调用
        # retriever_tool 应由 doc-analyst 子智能体使用，主 agent 通过 task 工具委派
        if retriever_tool_name:
            tools = [t for t in tools if getattr(t, 'name', '') != retriever_tool_name]
            logger.info(f"已从主 agent 工具列表中移除 retriever_tool: {retriever_tool_name}，由 doc-analyst 子智能体使用")

        backend_type = getattr(config, 'backend_type', 'filesystem') or 'filesystem'
        work_dir = getattr(config, 'work_dir', None)

        # 先确保 work_dir 存在，再创建 backend（backend 依赖 work_dir 作为 root_dir）
        work_dir, sandbox_dir = self._ensure_work_dir(backend_type, work_dir, config)
        config.work_dir = work_dir

        backend = self._resolve_backend(config, backend_type, work_dir, sandbox_dir)

        skills = self._resolve_skills(config, backend_type, work_dir)

        system_prompt = config.system_prompt
        if system_prompt is None:
            system_prompt = self._build_system_prompt(config)
            logger.info(f"DeepAgent 使用默认 system_prompt ({len(system_prompt)} 字符)")
        else:
            logger.info(f"DeepAgent 使用自定义 system_prompt ({len(system_prompt)} 字符, 含续研上下文: {'先前研究' in system_prompt})")

        # 当启用文档分析时，追加知识库检索引导到 system_prompt
        tool_config = config.tool_config or {}
        _enable_doc_analysis = tool_config.get("enable_doc_analysis") or tool_config.get("use_doc_analysis", False)
        logger.info(f"DeepBuilder tool_config check: enable_doc_analysis={_enable_doc_analysis}, has_suffix={DOC_ANALYSIS_PROMPT_SUFFIX[:30] in system_prompt}")
        if _enable_doc_analysis and DOC_ANALYSIS_PROMPT_SUFFIX not in system_prompt:
            system_prompt += DOC_ANALYSIS_PROMPT_SUFFIX
            logger.info(f"DeepAgent system_prompt 已追加知识库文档分析引导 ({len(system_prompt)} 字符)")

        agent_kwargs: Dict[str, Any] = {
            "model": model,
            "tools": tools,
            "system_prompt": system_prompt,
        }
        if middleware_stack:
            agent_kwargs["middleware"] = middleware_stack
        if subagents:
            agent_kwargs["subagents"] = subagents
        if skills:
            agent_kwargs["skills"] = skills
        if config.memory:
            agent_kwargs["memory"] = config.memory
        if config.permissions:
            agent_kwargs["permissions"] = config.permissions
        if backend is not None:
            agent_kwargs["backend"] = backend

        from Django_xm.apps.agent_hub.builders._common import _build_common_agent_kwargs
        _build_common_agent_kwargs(config, agent_kwargs)

        interrupt_on = getattr(config, 'interrupt_on', None)
        if interrupt_on:
            agent_kwargs["interrupt_on"] = interrupt_on

        graph = create_deep_agent(**agent_kwargs)
        logger.info(
            f"DeepAgent 创建成功 "
            f"(tools={len(tools)}, middleware={len(middleware_stack)}, "
            f"subagents={len(subagents)}, backend={backend_type})"
        )
        return graph

    def _resolve_subagents(self, config, tools: List[BaseTool]) -> tuple:
        """返回 (subagents, retriever_tool_name) 元组"""
        if config.subagents:
            logger.debug(f"使用预配置的 {len(config.subagents)} 个 SubAgent")
            return config.subagents, None

        tool_config = config.tool_config or {}
        enable_web_search = tool_config.get("enable_web_search") or tool_config.get("use_web_search", False)
        enable_doc_analysis = tool_config.get("enable_doc_analysis") or tool_config.get("use_doc_analysis", False)

        if not enable_web_search and not enable_doc_analysis:
            return [], None

        # 优先从 config.retriever 获取 retriever_tool
        retriever_tool = _get_retriever_tool(config.retriever)

        # 如果 config.retriever 为空，从 tools 列表中识别已有的 retriever_tool
        # （deep_research.py 将 retriever_tool 放入了 config.tools 而非 config.retriever）
        retriever_tool_name = None
        if retriever_tool is None and tools:
            for t in tools:
                name = getattr(t, 'name', '')
                if any(name.startswith(prefix) or name == prefix for prefix in _RETRIEVER_TOOL_NAME_PREFIXES):
                    retriever_tool = t
                    retriever_tool_name = name
                    logger.info(f"从 tools 列表中识别到 retriever_tool: {name}")
                    break
        elif retriever_tool is not None:
            retriever_tool_name = getattr(retriever_tool, 'name', None)

        extra_tools = [
            t for t in tools
            if isinstance(t, BaseTool) and getattr(t, 'name', '') in _SEARCH_TOOL_NAMES
        ] or None

        subagents = _build_subagents(
            enable_web_search=enable_web_search,
            enable_doc_analysis=enable_doc_analysis,
            retriever_tool=retriever_tool,
            extra_tools=extra_tools,
        )

        return subagents, retriever_tool_name

    def _resolve_backend(self, config, backend_type: str, work_dir: Optional[str], sandbox_dir: Optional[str] = None) -> Any:
        if config.backend is not None:
            return config.backend

        return _get_backend(backend_type=backend_type, work_dir=work_dir, sandbox_dir=sandbox_dir)

    def _ensure_work_dir(
        self, backend_type: str, work_dir: Optional[str], config
    ) -> tuple:
        """返回 (work_dir, sandbox_dir) 元组"""
        if backend_type != "filesystem":
            return (work_dir, None)
        if work_dir is not None:
            sandbox_dir = os.path.join(work_dir, "sandbox")
            return (work_dir, sandbox_dir)

        from django.conf import settings as django_settings
        data_dir = str(
            getattr(django_settings, "DATA_DIR", None)
            or os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..", "..", "..", "..", "data",
            )
        )
        session_id = getattr(config, 'session_id', 'default') or 'default'
        work_dir = os.path.join(data_dir, "research", session_id)
        os.makedirs(work_dir, exist_ok=True)
        sandbox_dir = os.path.join(work_dir, "sandbox")
        os.makedirs(sandbox_dir, exist_ok=True)
        logger.info(f"自动创建工作目录: {work_dir}, sandbox: {sandbox_dir}")
        return (work_dir, sandbox_dir)

    def _resolve_skills(
        self, config, backend_type: str, work_dir: Optional[str]
    ) -> Optional[List[str]]:
        skills = config.skills
        if not skills or backend_type != "filesystem" or not work_dir:
            return skills

        skills_work_dir = os.path.join(work_dir, "sandbox", "skills")
        os.makedirs(skills_work_dir, exist_ok=True)
        sandbox_skills: List[str] = []
        for skill_dir in skills:
            if not os.path.isdir(skill_dir):
                sandbox_skills.append(skill_dir)
                continue
            skill_name = os.path.basename(skill_dir)
            dest = os.path.join(skills_work_dir, skill_name)
            if not os.path.exists(dest):
                try:
                    os.symlink(skill_dir, dest)
                except OSError:
                    shutil.copytree(skill_dir, dest)
            sandbox_skills.append(dest)
        logger.info(f"Skill 已链接到沙箱: {skills_work_dir}")
        return sandbox_skills

    def _build_system_prompt(self, config) -> str:
        from Django_xm.apps.agent_hub.config import AgentType
        if config.agent_type == AgentType.DEEP_RESEARCH:
            return DEEP_RESEARCH_SYSTEM_PROMPT
        try:
            from Django_xm.apps.ai_engine.prompts.system_prompts import get_deep_research_prompt
            return get_deep_research_prompt()
        except Exception:
            return "You are a deep research assistant. Conduct thorough research on the given topic."