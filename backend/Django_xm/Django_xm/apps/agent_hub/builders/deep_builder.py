from __future__ import annotations

import logging
import os
import shutil
import sys
from collections.abc import Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import BaseTool, StructuredTool

from Django_xm.apps.agent_hub.builders._registry import register_builder
from Django_xm.apps.agent_hub.config import AgentType
from Django_xm.apps.research.prompts import (
    DEEP_RESEARCH_SYSTEM_PROMPT,
    DOC_ANALYSIS_PROMPT_SUFFIX,
    DOC_ANALYST_SUBAGENT_PROMPT,
    WEB_RESEARCHER_SUBAGENT_PROMPT,
)
from Django_xm.apps.research.services._constants import (
    RETRIEVER_TOOL_NAME_PREFIXES as _RETRIEVER_TOOL_NAME_PREFIXES,
)
from Django_xm.apps.research.services._constants import (
    SANDBOX_ALLOWED_DIRS as _SANDBOX_ALLOWED_DIRS,
)
from Django_xm.apps.research.services._constants import (
    SEARCH_TOOL_NAMES as _SEARCH_TOOL_NAMES,
)

# OfficialDeepAgentAdapter 位于 research/services/adapter.py，作为 deep agent 的统一适配器
# （将 CompiledStateGraph 适配为 research/aresearch/astream_research_with_interrupts 接口）。
#
# 分层依赖说明：
# - agent_hub.builders.deep_builder → research.services.adapter（本导入）
# - research.services.adapter → agent_hub.builders.subagent_patch（已存在）
# 这是 agent_hub ↔ research 的循环依赖，与 spec 2.4 的 chat ↔ research 循环同性质，
# 后续应通过 cross_app 门面统一收敛。当前保持现状，不引入新的循环依赖。
#
# 设计决策：DeepAgentBuilder.build() 直接返回 OfficialDeepAgentAdapter（不返回 raw graph），
# 注入 original_tools / original_config / model，使韧性降级 _rebuild_with_degraded_tools
# 能获取完整构建参数重建 graph（根本性修复 af_16 残留：韧性降级失效）。
from Django_xm.apps.research.services.adapter import OfficialDeepAgentAdapter
from Django_xm.async_utils import run_async
from Django_xm.common.risk_levels import RiskLevel

logger = logging.getLogger(__name__)


# 子 agent 角色风险上限（RiskLevel）。
# 设计参考 Claude Code：子 agent 拥有与主 agent 对等的工具权限，
# 但通过 risk_ceiling 约束角色边界，避免研究型子 agent 执行高危操作。
#
# 角色边界约束（非风险降级）：超过 risk_ceiling 的工具调用被拒绝，
# agent 收到 error ToolMessage 后可调整策略或委派给有权限的 agent。
#
# 映射由 subagent_patch.py 在 atask 中注入到子 agent configurable，
# ApprovalMiddleware._extract_subagent_context 读取后传给 policies.assess_risk。
_SUBAGENT_RISK_CEILINGS: dict[str, RiskLevel] = {}


def _register_subagent_risk_ceilings():
    """注册子 agent 角色风险上限。

    在模块加载时调用，将 RiskLevel 枚举值注册到 _SUBAGENT_RISK_CEILINGS。
    RiskLevel 已在模块顶层导入（common.risk_levels 仅依赖 enum，无循环依赖风险）。
    """
    _SUBAGENT_RISK_CEILINGS.clear()
    # web-researcher / doc-analyst：研究型子 agent，最高 CONTROLLED
    # （可执行只读 + 常规审批操作，但禁止 HIGH 级如沙箱外写文件）
    _SUBAGENT_RISK_CEILINGS["web-researcher"] = RiskLevel.CONTROLLED
    _SUBAGENT_RISK_CEILINGS["doc-analyst"] = RiskLevel.CONTROLLED
    # general-purpose：通用子 agent，与主 agent 一致不限制
    # （拥有完整工具权限，可执行 HIGH 级操作，充分发挥能力）
    _SUBAGENT_RISK_CEILINGS["general-purpose"] = RiskLevel.HIGH


_register_subagent_risk_ceilings()


def _patch_filesystem_backend_windows():
    r"""Patch FilesystemBackend._resolve_path to handle Windows \\?\ prefix inconsistency.

    On Windows, Path.resolve() may add the extended-length path prefix (\\?\)
    inconsistently between init time and runtime, causing relative_to() to fail
    even when the path is logically within root_dir.
    """
    if sys.platform != "win32":
        return

    try:
        from deepagents.backends.filesystem import FilesystemBackend
    except ImportError:
        return

    if getattr(FilesystemBackend._resolve_path, "_win_patched", False):
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
    logger.debug("FilesystemBackend._resolve_path 已打 Windows 路径补丁")


# 模块加载时自动打补丁
_patch_filesystem_backend_windows()

# 应用 SubAgentMiddleware 补丁（幂等）：
# - Patch 1: _get_subagents 注入 checkpointer（修复子 agent 无 checkpointer 问题）
# - Patch 2: _build_task_tool atask 继承父 callbacks（修复子 agent 工具事件不转发问题）
#
# 补丁应用后，子 agent 拥有 checkpointer，interrupt() 不再被 Pregel 抑制，
# ApprovalMiddleware 可安全注入到子 agent（与 Claude Code Task 工具设计对齐）。
# 子 agent 工具调用事件通过 _on_tool_event 回调转发到父 SSE 流（adapter.py 注入）。
from Django_xm.apps.agent_hub.builders.subagent_patch import (
    patch_subagent_middleware as _patch_subagent_middleware,
)

_patch_subagent_middleware()


def _has_search_tools(extra_tools: list[BaseTool] | None = None) -> bool:
    if not extra_tools:
        return False
    return any(getattr(t, "name", "") in _SEARCH_TOOL_NAMES for t in extra_tools)


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


def _merge_tool_lists(*tool_lists) -> list[BaseTool]:
    """合并多个工具列表，按工具名去重（保留首次出现的版本）。

    用于子智能体工具合并：主 agent 工具 + 子智能体专用工具 + extra_tools，
    确保子智能体拥有与主 agent 相同的工具能力（参考 Claude Code Task 工具设计：
    子 agent 默认继承父 agent 的全部工具，并可追加专用工具）。
    """
    seen_names: set = set()
    merged: list[BaseTool] = []
    for tools in tool_lists:
        if not tools:
            continue
        for t in tools:
            name = getattr(t, "name", "") or ""
            if name and name in seen_names:
                continue
            seen_names.add(name)
            merged.append(t)
    return merged


def _build_subagents(
    enable_web_search: bool = True,
    enable_doc_analysis: bool = False,
    retriever_tool: BaseTool | None = None,
    extra_tools: list[BaseTool] | None = None,
    subagent_middleware: Sequence[AgentMiddleware] | None = None,
    main_tools: list[BaseTool] | None = None,
) -> list[Any]:
    """构建 SubAgent 列表。

    子智能体工具权限设计（参考 Claude Code）：
    - 子智能体继承主 agent 的完整工具集（main_tools），确保能力对等
    - 在主 agent 工具基础上追加子智能体专用工具（search_tool / retriever_tool）
    - extra_tools 也合并到子智能体工具集中
    - 工具按 name 去重，避免重复注册

    deepagents 原生支持 SubAgent 省略 tools 时自动继承主 agent tools
    （graph.py: `spec.get("tools") if "tools" in spec else tools`），
    但此处需追加专用工具，因此显式传入合并后的完整工具列表。

    Args:
        enable_web_search: 是否启用网络搜索子智能体
        enable_doc_analysis: 是否启用文档分析子智能体
        retriever_tool: RAG 检索工具（仅 doc-analyst 专用）
        extra_tools: 额外工具列表（MCP/自定义工具，合并到所有子智能体）
        subagent_middleware: 子智能体额外中间件
        main_tools: 主 agent 的完整工具列表，子智能体将继承这些工具

    Returns:
        SubAgent 列表
    """
    from deepagents import SubAgent

    subagents: list[Any] = []
    # deepagents 内部 spec.get("middleware", []) 在 middleware=None 时返回 None，
    # 导致 extend 失败，因此必须确保 middleware 不为 None。
    # SubAgent.middleware 字段类型为 list[AgentMiddleware]，需将 Sequence 转为 list。
    safe_middleware: list[AgentMiddleware] = list(subagent_middleware or [])

    need_web_researcher = enable_web_search or _has_search_tools(extra_tools)

    if need_web_researcher:
        try:
            from Django_xm.apps.tools.langchain.web_search import create_tavily_search_tool

            search_tool = create_tavily_search_tool()
            # 子智能体继承主 agent 全部工具 + 专用搜索工具 + extra_tools
            web_tools = _merge_tool_lists(main_tools, [search_tool], extra_tools)
            web_subagent = SubAgent(
                name="web-researcher",
                description="网络搜索和信息整理专家，负责从互联网搜索和整理研究信息",
                system_prompt=WEB_RESEARCHER_SUBAGENT_PROMPT,
                tools=web_tools,
                middleware=safe_middleware,
            )
            subagents.append(web_subagent)
            logger.debug(f"添加 WebResearcher 子智能体 (tools={len(web_tools)}, 含主 agent 工具继承)")
        except ValueError:
            logger.warning("Tavily API Key 未配置，web-researcher 子智能体将使用继承工具")
            # 无 search_tool 时：继承主 agent 工具 + extra_tools
            web_fallback_tools = _merge_tool_lists(main_tools, extra_tools)
            web_subagent = SubAgent(
                name="web-researcher",
                description="网络搜索和信息整理专家",
                system_prompt=WEB_RESEARCHER_SUBAGENT_PROMPT,
                # SubAgent.tools 字段为 NotRequired[Sequence[...]]，不接受 None。
                # _merge_tool_lists 已合并 main_tools，空列表与 None 行为等价（key 存在即不继承）。
                tools=web_fallback_tools,
                middleware=safe_middleware,
            )
            subagents.append(web_subagent)

    if enable_doc_analysis:
        # doc-analyst 专用 retriever_tool + 继承主 agent 工具 + extra_tools
        doc_specialized = [retriever_tool] if retriever_tool else []
        doc_tools = _merge_tool_lists(main_tools, doc_specialized, extra_tools)
        doc_subagent = SubAgent(
            name="doc-analyst",
            description="文档分析和知识提取专家，负责在知识库中检索和分析文档",
            system_prompt=DOC_ANALYST_SUBAGENT_PROMPT,
            # _merge_tool_lists 已合并 main_tools + retriever_tool + extra_tools
            tools=doc_tools,
            middleware=safe_middleware,
        )
        subagents.append(doc_subagent)
        logger.debug(f"添加 DocAnalyst 子智能体 (tools={len(doc_tools)}, 含主 agent 工具继承)")

    return subagents


class _PatchCompositeBackend:
    def __init__(self, composite):
        self._composite = composite

    def __getattr__(self, name):
        return getattr(self._composite, name)

    @staticmethod
    def _check_sandbox_path(file_path: str) -> str | None:
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
            object.__setattr__(res, "path", file_path)
        return res

    async def awrite(self, file_path, content):
        err = self._check_sandbox_path(file_path)
        if err:
            from deepagents.backends.protocol import WriteResult

            return WriteResult(error=err, path=None)
        backend, key = self._resolve(file_path)
        res = await backend.awrite(key, content)
        if res.path is not None:
            object.__setattr__(res, "path", file_path)
        return res

    def edit(self, file_path, old_string, new_string, replace_all=False):
        err = self._check_sandbox_path(file_path)
        if err:
            from deepagents.backends.protocol import EditResult

            return EditResult(error=err, path=None, occurrences=None)
        backend, key = self._resolve(file_path)
        res = backend.edit(key, old_string, new_string, replace_all=replace_all)
        if res.path is not None:
            object.__setattr__(res, "path", file_path)
        return res

    async def aedit(self, file_path, old_string, new_string, replace_all=False):
        err = self._check_sandbox_path(file_path)
        if err:
            from deepagents.backends.protocol import EditResult

            return EditResult(error=err, path=None, occurrences=None)
        backend, key = self._resolve(file_path)
        res = await backend.aedit(key, old_string, new_string, replace_all=replace_all)
        if res.path is not None:
            object.__setattr__(res, "path", file_path)
        return res


def _get_backend(
    backend_type: str = "state",
    work_dir: str | None = None,
    sandbox_dir: str | None = None,
) -> Any:
    try:
        if backend_type == "state":
            from deepagents.backends import StateBackend

            return StateBackend()
        elif backend_type == "filesystem":
            from deepagents.backends import CompositeBackend, FilesystemBackend

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

            # LocalShellBackend 签名：root_dir, virtual_mode, timeout, max_output_bytes, env, inherit_env
            # 使用 root_dir 指定工作目录（原 workdir 参数不存在，是历史误用）
            return LocalShellBackend(root_dir=work_dir or ".")
        else:
            logger.warning(f"未知的 backend 类型: {backend_type}，使用 StateBackend")
            from deepagents.backends import StateBackend

            return StateBackend()
    except ImportError as e:
        logger.warning(f"Backend 导入失败: {e}，将不使用 backend")
        return None


def _get_retriever_tool(retriever: Any) -> BaseTool | None:
    if retriever is None:
        return None
    if isinstance(retriever, BaseTool):
        return retriever
    try:
        # langchain.tools.retriever 模块不存在；create_retriever_tool 实际位于 langchain_core.tools.retriever
        from langchain_core.tools.retriever import create_retriever_tool

        return create_retriever_tool(
            retriever=retriever,
            name="knowledge_retrieve",
            description="在知识库中检索相关文档信息",
        )
    except Exception:
        logger.warning("无法将 retriever 转换为工具")
        return None


@register_builder(AgentType.DEEP_RESEARCH)
class DeepAgentBuilder:
    async def build(self, config) -> Any:
        from Django_xm.apps.agent_hub.builders._common import build_with_timeout

        return await build_with_timeout(
            self._build_internal,
            config,
            "DeepAgentBuilder.build",
        )

    async def _build_internal(self, config) -> Any:
        try:
            from deepagents import create_deep_agent
        except ImportError:
            from Django_xm.apps.agent_hub.exceptions import FrameworkNotAvailableError

            raise FrameworkNotAvailableError("deepagents 包不可用，请降级到 DEEP_RESEARCH_CUSTOM") from None

        from Django_xm.apps.agent_hub.middleware import build_middleware
        from Django_xm.apps.agent_hub.model_resolver import resolve_model
        from Django_xm.apps.agent_hub.tool_resolver import resolve_tools

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
            "fs_write_file",
            "fs_read_file",
            "fs_list_files",
            "fs_search_files",
        }
        original_count = len(tools)
        tools = [t for t in tools if getattr(t, "name", "") not in _CONFLICT_TOOL_NAMES]
        filtered_count = original_count - len(tools)
        if filtered_count > 0:
            logger.info(
                f"已过滤 {filtered_count} 个冗余文件系统工具 "
                f"(fs_write_file/fs_read_file 等)，deepagents 内置工具已覆盖文件操作"
            )

        middleware_stack = build_middleware(config)

        # 显式注入 ApprovalMiddleware（核心安全组件，不依赖 CapabilityRegistry 可选启用）
        # 设计原因：
        # 1. CapabilityRegistry.build_middleware_for_agent 不会为 deep_research 注入 ApprovalMiddleware
        #    （GuardrailsCapability.is_compatible 只接受 "base" 类型）
        # 2. 审批机制是核心安全能力，应对所有有工具的 agent 强制启用，与 chat 模块保持统一
        # 3. 统一三模块（chat / deep_research / learning）的审批入口，确保实时同步行为一致
        from Django_xm.apps.agent_hub.approval.middleware import ApprovalMiddleware

        approval_middleware_instance: ApprovalMiddleware | None = None
        for m in middleware_stack:
            if isinstance(m, ApprovalMiddleware):
                approval_middleware_instance = m
                break
        if approval_middleware_instance is None:
            approval_middleware_instance = ApprovalMiddleware()
            middleware_stack.append(approval_middleware_instance)
            logger.info("已显式注入 ApprovalMiddleware 到 deep_research 中间件栈")
        else:
            logger.info("deep_research 中间件栈已包含 ApprovalMiddleware（来自 CapabilityRegistry）")

        subagents, retriever_tool_name = self._resolve_subagents(
            config,
            tools,
            approval_middleware=approval_middleware_instance,
        )

        # 从主 agent 工具列表中移除 retriever_tool，避免主 agent 直接调用
        # retriever_tool 应由 doc-analyst 子智能体使用，主 agent 通过 task 工具委派
        if retriever_tool_name:
            tools = [t for t in tools if getattr(t, "name", "") != retriever_tool_name]
            logger.info(
                f"已从主 agent 工具列表中移除 retriever_tool: {retriever_tool_name}，由 doc-analyst 子智能体使用"
            )

        backend_type = getattr(config, "backend_type", "filesystem") or "filesystem"
        work_dir = getattr(config, "work_dir", None)

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
            logger.info(
                f"DeepAgent 使用自定义 system_prompt ({len(system_prompt)} 字符, 含续研上下文: {'先前研究' in system_prompt})"
            )

        # 当启用文档分析时，追加知识库检索引导到 system_prompt
        tool_config = config.tool_config or {}
        _enable_doc_analysis = tool_config.get("enable_doc_analysis") or tool_config.get("use_doc_analysis", False)
        logger.info(
            f"DeepBuilder tool_config check: enable_doc_analysis={_enable_doc_analysis}, has_suffix={DOC_ANALYSIS_PROMPT_SUFFIX[:30] in system_prompt}"
        )
        if _enable_doc_analysis and DOC_ANALYSIS_PROMPT_SUFFIX not in system_prompt:
            system_prompt += DOC_ANALYSIS_PROMPT_SUFFIX
            logger.info(f"DeepAgent system_prompt 已追加知识库文档分析引导 ({len(system_prompt)} 字符)")

        agent_kwargs: dict[str, Any] = {
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

        interrupt_on = getattr(config, "interrupt_on", None)
        if interrupt_on:
            agent_kwargs["interrupt_on"] = interrupt_on

        # 关键修复（缺陷 1）：在 create_deep_agent 调用前设置 checkpointer contextvar
        # 原因：SubAgentMiddleware.__init__ 在 create_deep_agent 内部执行，
        # __init__ 调用 _get_subagents() 创建子 agent graph（含 ApprovalMiddleware）。
        # patched _get_subagents 从 contextvar 读取 checkpointer 注入到子 agent，
        # 使子 agent 的 interrupt() 不被 Pregel 抑制（is_nested=False）。
        # 若此处不设置，contextvar 为 None，走 fallback 分支，
        # 子 agent 无 checkpointer → ApprovalMiddleware 的 interrupt 完全失效。
        # adapter.astream_research_with_interrupts 中仍保留 set_current_checkpointer 调用，
        # 用于韧性降级重建路径（_rebuild_with_degraded_tools 在执行期重建 graph）。
        from Django_xm.apps.agent_hub.builders.subagent_patch import (
            patch_subagent_middleware,
            reset_current_checkpointer,
            set_current_checkpointer,
        )

        # 确保补丁已应用（幂等，重复调用无副作用）
        patch_subagent_middleware()
        _ck_token = set_current_checkpointer(getattr(config, "checkpointer", None))
        try:
            graph = create_deep_agent(**agent_kwargs)
        finally:
            reset_current_checkpointer(_ck_token)
        logger.info(
            f"DeepAgent 创建成功 "
            f"(tools={len(tools)}, middleware={len(middleware_stack)}, "
            f"subagents={len(subagents)}, backend={backend_type})"
        )
        # 返回 OfficialDeepAgentAdapter（不返回 raw graph），注入韧性降级所需的完整构建元数据：
        # - original_tools / original_config：_rebuild_with_degraded_tools 用降级工具集重建 graph
        # - model：_fallback_direct_answer 用原 model 直接 LLM 回答
        # - thread_id / work_dir：研究任务标识与文件系统根目录
        # - chat_session_id=None：由 adapter 内部从 ResearchTask.session_id 反查
        #   （chat 模块触发的深度研究需要跨模块同步事件到 session 频道）
        #
        # 根本性修复 af_16 残留：原实现返回 raw graph，经 AgentFactory.create 包装为
        # AgentWrapper（仅含 graph + work_dir），original_tools / original_config / model
        # 全部丢失，导致 _rebuild_with_degraded_tools 因 original_config=None 返回 None，
        # 韧性降级完全失效。
        adapter = OfficialDeepAgentAdapter(
            graph=graph,
            thread_id=config.session_id or "",
            work_dir=work_dir,
            model=model,
            original_tools=tools,
            original_config=agent_kwargs,
            chat_session_id=None,
        )
        return adapter

    def _resolve_subagents(
        self,
        config,
        tools: list[BaseTool],
        approval_middleware: AgentMiddleware | None = None,
    ) -> tuple:
        """返回 (subagents, retriever_tool_name) 元组

        Args:
            config: AgentConfig
            tools: 主 agent 工具列表
            approval_middleware: 主 agent 的 ApprovalMiddleware 实例，
                将注入到子 agent middleware 栈（subagent_patch 已修复 checkpointer 问题，
                interrupt 可正常工作，与 Claude Code Task 工具设计对齐）
        """
        if config.subagents:
            logger.debug(f"使用预配置的 {len(config.subagents)} 个 SubAgent")
            return config.subagents, None

        tool_config = config.tool_config or {}
        enable_web_search = tool_config.get("enable_web_search") or tool_config.get("use_web_search", False)
        enable_doc_analysis = tool_config.get("enable_doc_analysis") or tool_config.get("use_doc_analysis", False)

        # 构建子 agent middleware 栈（含 ApprovalMiddleware），即使无 web_search/doc_analysis
        # 也需要为 general-purpose 子 agent 注入审批机制（见下方 general-purpose 添加逻辑）
        subagent_middleware_list: list[AgentMiddleware] = []
        if config.middleware:
            for m in config.middleware:
                subagent_middleware_list.append(m)
        if approval_middleware is not None:
            already_has = any(
                m is approval_middleware or isinstance(m, type(approval_middleware)) for m in subagent_middleware_list
            )
            if not already_has:
                subagent_middleware_list.append(approval_middleware)
                logger.info(
                    "已注入主 agent 的 ApprovalMiddleware 到子 agent middleware 栈"
                    "（subagent_patch 已启用，interrupt 可正常工作）"
                )

        if not enable_web_search and not enable_doc_analysis:
            # 即使不启用 web_search/doc_analysis，仍需添加 general-purpose 子 agent
            # 避免 create_deep_agent 自动添加无 ApprovalMiddleware 的版本（缺陷 2 修复）
            gp_subagent = self._build_general_purpose_subagent(
                tools=tools,
                subagent_middleware=subagent_middleware_list,
            )
            return [gp_subagent], None

        # 优先从 config.retriever 获取 retriever_tool
        retriever_tool = _get_retriever_tool(config.retriever)

        # 如果 config.retriever 为空，从 tools 列表中识别已有的 retriever_tool
        # （deep_research.py 将 retriever_tool 放入了 config.tools 而非 config.retriever）
        retriever_tool_name = None
        if retriever_tool is None and tools:
            for t in tools:
                name = getattr(t, "name", "")
                if any(name.startswith(prefix) or name == prefix for prefix in _RETRIEVER_TOOL_NAME_PREFIXES):
                    retriever_tool = t
                    retriever_tool_name = name
                    logger.info(f"从 tools 列表中识别到 retriever_tool: {name}")
                    break
        elif retriever_tool is not None:
            retriever_tool_name = getattr(retriever_tool, "name", None)

        extra_tools = [
            t for t in tools if isinstance(t, BaseTool) and getattr(t, "name", "") in _SEARCH_TOOL_NAMES
        ] or None

        # ============================================================
        # 子 agent 审批机制设计说明（subagent_patch 已启用）
        # ============================================================
        # subagent_patch.py 已在模块加载时应用（见文件顶部 _patch_subagent_middleware），
        # 修复了原 deepagents SubAgentMiddleware 的两个根本性缺陷：
        #
        # 1. Patch 1（_get_subagents 注入 checkpointer）：
        #    原实现子 agent 无 checkpointer，interrupt() 要求 checkpointer 才能暂停，
        #    导致 GraphInterrupt 被子 agent 的 Pregel 吞掉（is_nested=False 时抑制）。
        #    补丁从 contextvar 获取父 graph 的 checkpointer 并注入到 create_agent。
        #    缺陷 1 修复：DeepAgentBuilder._build_internal 在调用 create_deep_agent 前
        #    设置 contextvar，确保构建期间子 agent 能获取 checkpointer。
        #
        # 2. Patch 2（_build_task_tool atask 继承 callbacks）：
        #    原 atask 只继承 configurable，父 graph 看不到子 agent 内部工具调用。
        #    补丁让 atask 继承父 callbacks + 通过 _on_tool_event 回调转发工具事件。
        #
        # 补丁应用后的设计决策（与 Claude Code Task 工具设计对齐）：
        # - 子 agent 继承主 agent 的完整工具集（_merge_tool_lists），确保工具能力对等
        # - 子 agent 的 ApprovalMiddleware 与主 agent 共享同一实例，
        #   interrupt 通过 task 工具冒泡到父 graph，由父 graph 的 interrupt 循环处理
        # - 子 agent 工具调用事件通过 _on_tool_event 回调转发到父 SSE 流
        #   （adapter.py 在 config["configurable"] 中注入回调）
        # - 审批拒绝/超时后子 agent 收到 error ToolMessage 反馈，调整策略继续
        # - 缺陷 2 修复：general-purpose 子 agent 在此显式添加（覆盖 create_deep_agent
        #   自动添加的无 ApprovalMiddleware 版本），确保所有子 agent 审批入口统一
        # ============================================================

        subagents = _build_subagents(
            enable_web_search=enable_web_search,
            enable_doc_analysis=enable_doc_analysis,
            retriever_tool=retriever_tool,
            extra_tools=extra_tools,
            main_tools=tools,
            subagent_middleware=subagent_middleware_list if subagent_middleware_list else None,
        )

        # 无条件追加 general-purpose 子 agent（覆盖 create_deep_agent 的自动添加）
        # 详见 _build_general_purpose_subagent 的设计说明。
        gp_subagent = self._build_general_purpose_subagent(
            tools=tools,
            subagent_middleware=subagent_middleware_list,
            extra_tools=extra_tools,
        )
        subagents.append(gp_subagent)

        return subagents, retriever_tool_name

    def _build_general_purpose_subagent(
        self,
        tools: list[BaseTool],
        subagent_middleware: Sequence[AgentMiddleware] | None = None,
        extra_tools: list[BaseTool] | None = None,
    ) -> Any:
        """构建 general-purpose 子 agent（覆盖 create_deep_agent 的自动添加）

        缺陷 2 根因：create_deep_agent 自动添加的 general-purpose 子 agent 使用其
        内部硬编码的 middleware 栈（TodoListMiddleware + FilesystemMiddleware +
        SummarizationMiddleware + PatchToolCallsMiddleware + AnthropicPromptCachingMiddleware），
        不包含用户通过 agent_kwargs["middleware"] 传入的 middleware_stack
        （含 ApprovalMiddleware）。后果：general-purpose 子 agent 继承主 agent
        的全部工具（含 write_file/edit_file/execute 等危险工具），但不经过审批机制，
        违反安全约束。

        修复：在此显式构建含 ApprovalMiddleware 的 general-purpose SubAgent。
        create_deep_agent 检测到已存在 name="general-purpose" 的 spec 后，
        跳过自动添加（graph.py: `not any(spec["name"] == GENERAL_PURPOSE_SUBAGENT["name"]
        for spec in inline_subagents)`）。

        工具集与主 agent 完全一致（tools + extra_tools），保持 Claude Code 设计原则：
        子 agent 拥有与主 agent 对等的工具权限。
        """
        from deepagents import SubAgent

        # SubAgent.middleware 字段类型为 list[AgentMiddleware]，需将 Sequence 转为 list
        safe_middleware: list[AgentMiddleware] = list(subagent_middleware or [])
        gp_tools = _merge_tool_lists(tools, extra_tools)
        gp_subagent = SubAgent(
            name="general-purpose",
            description=(
                "General-purpose agent for researching complex questions, searching "
                "for files and content, and executing multi-step tasks. When you are "
                "searching for a keyword or file and are not confident that you will "
                "find the right match in the first few tries use this agent to perform "
                "the search for you. This agent has access to all tools as the main agent."
            ),
            system_prompt=(
                "In order to complete the objective that the user asks of you, you "
                "have access to a set of tools that you can use to perform operations "
                "and find information. Use the tools available to you to complete the "
                "task. Do not make assumptions about the user's intent - if something "
                "is unclear, ask for clarification."
            ),
            # _merge_tool_lists 已合并 main_tools + ApprovalMiddleware 工具
            tools=gp_tools,
            middleware=safe_middleware,
        )
        logger.debug(
            f"添加 GeneralPurpose 子智能体 (tools={len(gp_tools) if gp_tools else 0}, "
            f"含主 agent 工具继承 + ApprovalMiddleware 注入)"
        )
        return gp_subagent

    def _resolve_backend(self, config, backend_type: str, work_dir: str | None, sandbox_dir: str | None = None) -> Any:
        if config.backend is not None:
            return config.backend

        return _get_backend(backend_type=backend_type, work_dir=work_dir, sandbox_dir=sandbox_dir)

    def _ensure_work_dir(self, backend_type: str, work_dir: str | None, config) -> tuple:
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
                "..",
                "..",
                "..",
                "..",
                "data",
            )
        )
        session_id = getattr(config, "session_id", None)
        if not session_id:
            raise ValueError("深度研究任务缺少 session_id，无法创建工作目录")
        work_dir = os.path.join(data_dir, "research", session_id)
        os.makedirs(work_dir, exist_ok=True)
        sandbox_dir = os.path.join(work_dir, "sandbox")
        os.makedirs(sandbox_dir, exist_ok=True)
        logger.info(f"自动创建工作目录: {work_dir}, sandbox: {sandbox_dir}")
        return (work_dir, sandbox_dir)

    def _resolve_skills(self, config, backend_type: str, work_dir: str | None) -> list[str] | None:
        skills = config.skills
        if not skills or backend_type != "filesystem" or not work_dir:
            return skills

        skills_work_dir = os.path.join(work_dir, "sandbox", "skills")
        os.makedirs(skills_work_dir, exist_ok=True)
        sandbox_skills: list[str] = []
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
            # prompt 归属 research 模块（单一来源），从 research.prompts 导入
            from Django_xm.apps.research.prompts import get_deep_research_prompt

            return get_deep_research_prompt()
        except Exception:
            return "You are a deep research assistant. Conduct thorough research on the given topic."
