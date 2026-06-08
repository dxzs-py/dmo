"""
Research 服务层 - 提供深度研究的所有服务接口

包含：
- 统一入口（create_research_agent）- 通过 agent_hub 创建智能体，自动降级
- 官方深度研究智能体（OfficialDeepAgentAdapter）- 基于 deepagents 官方包 create_deep_agent
- 自定义深度研究智能体（DeepResearchAgent）- 基于 LangGraph StateGraph（降级方案）
- 安全深度研究智能体（SafeDeepResearchAgent）
- 子智能体（WebResearcher、DocAnalyst、ReportWriter）
- 任务管理器（TaskManager）
"""

import os
from typing import Optional, Any, Sequence

from langchain_core.tools import BaseTool
from langchain.agents.middleware import AgentMiddleware

from Django_xm.apps.core.config import get_logger
from Django_xm.apps.agent_hub import create as agent_hub_create, AgentType, AgentConfig

from .official_deep_agent import (
    OfficialDeepAgentAdapter,
)
from .deep_agent import (
    DeepResearchAgent,
    ResearchState,
)
from .safe_deep_agent import (
    SafeDeepResearchAgent,
)
from .subagents import (
    get_subagent_info,
    WEB_RESEARCHER_PROMPT,
    DOC_ANALYST_PROMPT,
    REPORT_WRITER_PROMPT,
)
from .task_manager import (
    TaskManager,
    get_task_manager,
    update_task_status,
)
from .research_workflow import (
    build_research_workflow,
    compile_research_workflow,
    create_research_with_checkpointer,
    decompose,
    synthesize,
    search_dispatcher,
    error_handler,
)

logger = get_logger(__name__)


async def create_research_agent(
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
    prefer_official: bool = True,
    research_context: str = "",
    **kwargs,
):
    """
    统一深度研究智能体创建入口

    通过 agent_hub 创建智能体，优先使用 DEEP_RESEARCH（官方 deepagents），
    当 deepagents 包不可用时自动降级到 DEEP_RESEARCH_CUSTOM（自定义 StateGraph）。

    两种实现均返回兼容接口（research/aresearch/astream_research）：
    - 官方：OfficialDeepAgentAdapter（内部封装 CompiledStateGraph）
    - 降级：DeepResearchAgent（自定义 StateGraph）

    Args:
        thread_id: 线程 ID
        enable_web_search: 是否启用网络搜索
        enable_doc_analysis: 是否启用文档分析
        retriever_tool: RAG 检索工具
        middleware: 额外中间件（官方模式下追加到默认栈之后）
        enable_guardrails: 是否启用 Guardrails
        guardrails_strict_mode: Guardrails 严格模式
        checkpointer: 状态持久化
        store: 长期记忆存储
        user_id: 用户 ID
        session_id: 会话 ID
        prefer_official: 是否优先使用官方 API（默认 True）
        **kwargs: 传递给底层实现的额外参数

    Returns:
        OfficialDeepAgentAdapter（官方）或 DeepResearchAgent（降级）
    """
    agent_type = AgentType.DEEP_RESEARCH if prefer_official else AgentType.DEEP_RESEARCH_CUSTOM

    extra_tools = kwargs.pop('extra_tools', None)
    selected_skill_names = kwargs.pop('selected_skill_names', None)
    provider_id = kwargs.pop('provider_id', None)
    model_name = kwargs.pop('model_name', None)
    temperature = kwargs.pop('temperature', None)
    max_tokens = kwargs.pop('max_tokens', None)
    special_params = kwargs.pop('special_params', None)
    capabilities = kwargs.pop('capabilities', None)
    tool_config_kwarg = kwargs.pop('tool_config', None)
    response_format = kwargs.pop('response_format', None)
    memory = kwargs.pop('memory', None)
    skills = kwargs.pop('skills', None)
    context_schema = kwargs.pop('context_schema', None)
    backend_type = kwargs.pop('backend_type', 'filesystem')
    work_dir = kwargs.pop('work_dir', None)
    cache = kwargs.pop('cache', None)

    tools = []
    if retriever_tool:
        tools.append(retriever_tool)
    if extra_tools:
        tools.extend(extra_tools)

    tool_config = tool_config_kwarg or {
        "use_tools": True,
        "use_web_search": enable_web_search,
        "use_doc_analysis": enable_doc_analysis,
        "user_id": user_id,
    }

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

    system_prompt = None
    if research_context:
        try:
            from Django_xm.apps.ai_engine.prompts.system_prompts import get_deep_research_prompt
            base_prompt = get_deep_research_prompt()
        except Exception:
            base_prompt = None
        if base_prompt:
            system_prompt = f"{base_prompt}\n\n---\n\n{research_context}"

    if checkpointer is None:
        try:
            from Django_xm.apps.ai_engine.services.checkpointer_factory import get_checkpointer
            checkpointer = get_checkpointer()
        except Exception:
            pass

    if store is None:
        try:
            from Django_xm.apps.ai_engine.services.checkpointer_factory import get_store
            auto_store = get_store()
            if auto_store is not None:
                store = auto_store
                logger.info("自动注入 Store（长期记忆）")
        except Exception:
            pass

    subagents = kwargs.pop('subagents', None)
    backend = kwargs.pop('backend', None)
    final_work_dir = None

    if agent_type == AgentType.DEEP_RESEARCH and subagents is None:
        try:
            from .official_deep_agent import _build_subagents
            subagent_middleware = None
            if capabilities and "context_management" in capabilities:
                try:
                    from Django_xm.apps.context_manager.middleware import ContextManagerMiddleware
                    from Django_xm.apps.ai_engine.services.llm_factory import get_model_string
                    model_string = model_name if provider_id and model_name else get_model_string()
                    lightweight_ctx = ContextManagerMiddleware(
                        model_name=model_string,
                        trigger_tokens=60000,
                        strategy="hybrid",
                        user_id=str(user_id) if user_id else None,
                        store=store,
                        thread_id=thread_id,
                    )
                    subagent_middleware = [lightweight_ctx]
                except Exception as e:
                    logger.warning(f"子智能体上下文管理 Middleware 创建失败: {e}")

            subagents = _build_subagents(
                enable_web_search=enable_web_search,
                enable_doc_analysis=enable_doc_analysis,
                retriever_tool=retriever_tool,
                extra_tools=extra_tools,
                subagent_middleware=subagent_middleware,
            )
        except Exception as e:
            logger.warning(f"构建子智能体失败: {e}")

    if agent_type == AgentType.DEEP_RESEARCH and backend is None:
        try:
            from .official_deep_agent import _get_backend

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
            else:
                backend = _get_backend(backend_type=backend_type, work_dir=work_dir)

            if skills and backend_type == "filesystem" and work_dir:
                sandbox_dir = os.path.join(work_dir, "sandbox")
                skills_work_dir = os.path.join(sandbox_dir, "skills")
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
                        except OSError:
                            import shutil
                            shutil.copytree(skill_dir, dest)
                    sandbox_skills.append(dest)
                if sandbox_skills:
                    skills = sandbox_skills

            final_work_dir = work_dir
        except Exception as e:
            logger.warning(f"构建 Backend 失败: {e}")

    config = AgentConfig(
        agent_type=agent_type,
        provider_id=provider_id,
        model_name=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        special_params=special_params,
        tools=tools if tools else None,
        tool_config=tool_config,
        system_prompt=system_prompt,
        middleware=list(middleware) if middleware else None,
        capabilities=capabilities,
        checkpointer=checkpointer,
        store=store,
        context_schema=context_schema,
        response_format=response_format,
        cache=cache,
        debug=False,
        user_id=user_id,
        session_id=thread_id,
        subagents=subagents,
        skills=skills,
        memory=memory,
        backend=backend,
        name=session_id,
        enable_guardrails=enable_guardrails,
        guardrails_strict_mode=guardrails_strict_mode,
    )

    agent = await agent_hub_create(config)

    from Django_xm.apps.agent_hub.factory import AgentWrapper
    if isinstance(agent, AgentWrapper):
        return OfficialDeepAgentAdapter(graph=agent.graph, thread_id=thread_id, work_dir=final_work_dir)
    return agent


__all__ = [
    "create_research_agent",
    "OfficialDeepAgentAdapter",
    "DeepResearchAgent",
    "ResearchState",
    "SafeDeepResearchAgent",
    "get_subagent_info",
    "WEB_RESEARCHER_PROMPT",
    "DOC_ANALYST_PROMPT",
    "REPORT_WRITER_PROMPT",
    "TaskManager",
    "get_task_manager",
    "update_task_status",
    "build_research_workflow",
    "compile_research_workflow",
    "create_research_with_checkpointer",
    "decompose",
    "synthesize",
    "search_dispatcher",
    "error_handler",
]
