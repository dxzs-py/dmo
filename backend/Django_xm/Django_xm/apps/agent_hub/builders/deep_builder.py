from __future__ import annotations

import logging
import os
import shutil
from typing import Any

# 官方 CompositeBackend：_SandboxGuardCompositeBackend 以其为基类（模块级导入，
# 类定义即求值；不能在函数内延迟导入）
from deepagents.backends import CompositeBackend
from langchain_core.tools import BaseTool, StructuredTool

from Django_xm.apps.agent_hub.builders._registry import register_builder
from Django_xm.apps.agent_hub.config import AgentType
from Django_xm.apps.agent_hub.exceptions import AgentCreationError
from Django_xm.apps.research.prompts import (
    DEEP_RESEARCH_SYSTEM_PROMPT,
    DOC_ANALYSIS_PROMPT_SUFFIX,
)
from Django_xm.apps.research.services._constants import (
    SANDBOX_ALLOWED_DIRS as _SANDBOX_ALLOWED_DIRS,
)

# OfficialDeepAgentAdapter 位于 research/services/adapter.py，作为 deep agent 的统一适配器
# （将 CompiledStateGraph 适配为 research/aresearch/astream_research_with_interrupts 接口）。
#
# 分层依赖说明：
# - agent_hub.builders.deep_builder → research.services.adapter（本导入）
# - research.services.adapter → agent_hub.builders.subagent_support（子 agent 官方支持中间件）
# 这是 agent_hub ↔ research 的循环依赖，与 spec 2.4 的 chat ↔ research 循环同性质，
# 后续应通过 cross_app 门面统一收敛。当前保持现状，不引入新的循环依赖。
#
# 设计决策：DeepAgentBuilder.build() 直接返回 OfficialDeepAgentAdapter（不返回 raw graph），
# 注入 original_tools / original_config / model，使韧性降级 _rebuild_with_degraded_tools
# 能获取完整构建参数重建 graph（根本性修复 af_16 残留：韧性降级失效）。
from Django_xm.apps.research.services.adapter import OfficialDeepAgentAdapter
from Django_xm.async_utils import run_async

logger = logging.getLogger(__name__)


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


class _SandboxGuardCompositeBackend(CompositeBackend):
    """继承官方 CompositeBackend，覆写写操作施加 /sandbox/ 写入守卫。

    设计说明（deepagents 0.7.5 升级后）：
    - 不再包装 CompositeBackend（旧 _PatchCompositeBackend）：deepagents 0.7.5 内部通过
      ``type(backend).delete is not BackendProtocol.delete`` 做类级能力检测，
      包装对象在类层面没有 delete 方法会抛 AttributeError。
    - 通过继承官方 CompositeBackend，write/edit/awrite/aedit 覆写守卫，
      delete/adelete 等其余方法沿用官方实现，能力检测正常。

    守卫规则：
    /sandbox/ 是 Agent 工具区（skills、MCP 工具、第三方依赖等），
    只允许预定义的工具子目录（_SANDBOX_ALLOWED_DIRS）写入，
    其他 /sandbox/ 路径一律拒绝，引导 Agent 将研究产出写入 /notes/、/plans/、/reports/。
    """

    def _check_sandbox_path(self, file_path: str) -> str | None:
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

    def write(self, file_path, content):
        err = self._check_sandbox_path(file_path)
        if err:
            from deepagents.backends.protocol import WriteResult

            return WriteResult(error=err, path=None)
        return super().write(file_path, content)

    async def awrite(self, file_path, content):
        err = self._check_sandbox_path(file_path)
        if err:
            from deepagents.backends.protocol import WriteResult

            return WriteResult(error=err, path=None)
        return await super().awrite(file_path, content)

    def edit(self, file_path, old_string, new_string, replace_all=False):
        err = self._check_sandbox_path(file_path)
        if err:
            from deepagents.backends.protocol import EditResult

            return EditResult(error=err, path=None, occurrences=None)
        return super().edit(file_path, old_string, new_string, replace_all=replace_all)

    async def aedit(self, file_path, old_string, new_string, replace_all=False):
        err = self._check_sandbox_path(file_path)
        if err:
            from deepagents.backends.protocol import EditResult

            return EditResult(error=err, path=None, occurrences=None)
        return await super().aedit(file_path, old_string, new_string, replace_all=replace_all)


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
            from deepagents.backends import FilesystemBackend

            fs_backend = FilesystemBackend(root_dir=work_dir or ".", virtual_mode=True)

            if sandbox_dir and os.path.isdir(sandbox_dir):
                sandbox_backend = FilesystemBackend(root_dir=sandbox_dir, virtual_mode=True)
                composite = _SandboxGuardCompositeBackend(
                    default=fs_backend,
                    routes={"/sandbox/": sandbox_backend},
                    artifacts_root="/sandbox/artifacts",
                )
                logger.info(f"CompositeBackend: default={work_dir}, /sandbox/={sandbox_dir}")
                return composite

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

            raise FrameworkNotAvailableError("deepagents 包不可用，深度研究功能无法启动") from None

        from Django_xm.apps.agent_hub.middleware import build_middleware
        from Django_xm.apps.agent_hub.model_resolver import resolve_model
        from Django_xm.apps.agent_hub.tool_resolver import resolve_tools

        model = resolve_model(config)
        tools = await resolve_tools(config)
        tools = [_ensure_sync_tool(t) for t in tools]

        # 过滤与 deepagents 内置工具冲突的自定义文件系统工具
        # 原因：deepagents 内置 write_file/read_file 使用 FilesystemBackend(root_dir=work_dir)，
        # 路径为 data/research/{thread_id}/，语义匹配深研任务目录。
        # 而 fs_* 工具（ResearchFileSystem）默认落 data/chat/{user_id}/{session_id}/
        # （按 用户·会话 分组，服务于代理模式），与深研任务目录语义不同。
        # 两套工具功能重复但路径语义不同，agent 可能调用错误的工具导致文件写入错误目录。
        # 解决方案：过滤 fs_* 工具，deepagents 内置工具已完全覆盖深研文件操作需求。
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

        # 显式注入 ApprovalMiddleware（核心安全组件，与 base_builder 共用
        # ensure_approval_middleware，不依赖 CapabilityRegistry 可选启用）
        from Django_xm.apps.agent_hub.builders.middleware_utils import ensure_approval_middleware

        ensure_approval_middleware(middleware_stack)

        # 子代理不再内联到 deepagents graph（废弃阻塞 task 工具），
        # 统一通过 spawn_sub_agent 工具经 SubAgentRuntime 派生（独立 thread + checkpoint）。
        # retriever_tool 保留在主 agent 工具集，供 spawn_sub_agent 派生 doc-analyst 时
        # 由注册表从主 agent 工具中识别。
        # 注入后 fail-fast 断言 spawn/wait 必在工具集内（spec D3），缺失即构建失败。
        tools = self._inject_and_assert_subagent_tools(tools)

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
            has_research_context = "先前研究" in system_prompt
            logger.info(
                f"DeepAgent 使用自定义 system_prompt "
                f"({len(system_prompt)} 字符, 含续研上下文: {has_research_context})"
            )

        # 当启用文档分析时，追加知识库检索引导到 system_prompt
        tool_config = config.tool_config or {}
        _enable_doc_analysis = tool_config.get("enable_doc_analysis") or tool_config.get("use_doc_analysis", False)
        has_doc_suffix = DOC_ANALYSIS_PROMPT_SUFFIX[:30] in system_prompt
        logger.info(
            f"DeepBuilder tool_config check: "
            f"enable_doc_analysis={_enable_doc_analysis}, has_suffix={has_doc_suffix}"
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

        # 禁用 deepagents 自动 general-purpose subagent（阻塞 task 工具），
        # 深度研究子代理统一走 SubAgentRuntime.spawn + spawn_sub_agent 工具。
        self._disable_general_purpose_subagent(model)

        # 创建 deep agent（deepagents 0.7.5 官方机制）。
        graph = create_deep_agent(**agent_kwargs)
        logger.info(
            f"DeepAgent 创建成功 "
            f"(tools={len(tools)}, middleware={len(middleware_stack)}, backend={backend_type})"
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

    @staticmethod
    def _inject_and_assert_subagent_tools(tools: list[BaseTool]) -> list[BaseTool]:
        """显式注入并断言研究主 agent 工具集包含子代理派生工具（fail-fast）。

        注入来源（唯一）：``agent_hub.subagent_tools``。CapabilityRegistry
        不注册 spawn/wait（``resolve_tools`` 不保证注入），因此研究主 agent 的
        ``spawn_sub_agent`` / ``wait_for_subagent`` 在此显式注入（按名称去重）。

        fail-fast 断言（spec fix-deep-research-subagent-activation D3）：
        注入后最终工具集必须同时包含两个子代理工具，缺失即抛
        :class:`AgentCreationError` 并携带完整工具集名单——静默缺失会导致主
        agent 无法派生子代理、研究链路退化为单 agent 直查，必须在构建期暴露。
        同时打印工具集完整名单，便于日志实证。

        Args:
            tools: ``resolve_tools`` 解析出的初始工具列表。

        Returns:
            注入子代理派生工具后的最终工具列表。

        Raises:
            AgentCreationError: 最终工具集缺失任一必需的子代理派生工具。
        """
        from Django_xm.apps.agent_hub.subagent_tools import spawn_sub_agent, wait_for_subagent

        required_names = ("spawn_sub_agent", "wait_for_subagent")
        existing_names = {getattr(t, "name", "") for t in tools}
        missing_tools = [
            t for t in (spawn_sub_agent, wait_for_subagent) if t.name not in existing_names
        ]
        tools = list(tools) + missing_tools

        tool_names = [getattr(t, "name", "") for t in tools]
        logger.info(f"研究主 agent 工具集 ({len(tool_names)} 个): {tool_names}")

        missing_names = [name for name in required_names if name not in set(tool_names)]
        if missing_names:
            raise AgentCreationError(
                f"深度研究主 agent 工具集缺失必需的子代理派生工具 {missing_names}，"
                f"研究链路强制子代理化（spec D3），构建失败；"
                f"当前工具集 ({len(tool_names)} 个): {tool_names}"
            )
        return tools

    @staticmethod
    def _disable_general_purpose_subagent(model: Any) -> None:
        """禁用 deepagents 自动 general-purpose subagent（阻塞 task 工具）。

        深度研究子代理统一走 SubAgentRuntime.spawn + spawn_sub_agent，
        deepagents 内置 task 工具（阻塞等待）不再暴露给深度研究主 agent。
        """
        try:
            from deepagents import GeneralPurposeSubagentProfile, HarnessProfile, register_harness_profile
            from deepagents._models import get_model_provider

            provider = get_model_provider(model)
            if not provider:
                logger.warning("无法解析深度研究模型 provider，跳过禁用 general-purpose subagent")
                return
            register_harness_profile(
                provider,
                HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)),
            )
            logger.info(f"已禁用 deepagents general-purpose subagent（task 工具）: provider={provider}")
        except Exception as e:
            logger.warning(f"禁用 deepagents general-purpose subagent 失败（非致命）: {e}")

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
            logger.exception("深度研究 prompt 导入失败，使用默认 prompt")
            return "You are a deep research assistant. Conduct thorough research on the given topic."
