"""
Research 服务层 - 提供深度研究的所有服务接口

包含：
- 统一入口（create_research_agent）- 优先使用官方 deepagents API，自动降级到自定义 StateGraph
- 官方深度研究智能体（OfficialDeepAgentAdapter）- 基于 deepagents 官方包 create_deep_agent
- 自定义深度研究智能体（DeepResearchAgent）- 基于 LangGraph StateGraph（降级方案）
- 安全深度研究智能体（SafeDeepResearchAgent）
- 子智能体（WebResearcher、DocAnalyst、ReportWriter）
- 任务管理器（TaskManager）
"""

from typing import Optional, Any, Sequence

from langchain_core.tools import BaseTool
from langchain.agents.middleware import AgentMiddleware

from Django_xm.apps.config_center.config import get_logger

from .official_deep_agent import (
    OfficialDeepAgentAdapter,
    create_official_deep_agent,
    create_official_deep_agent_with_interrupt,
)
from .deep_agent import (
    DeepResearchAgent,
    ResearchState,
    create_deep_research_agent,
)
from .safe_deep_agent import (
    SafeDeepResearchAgent,
)
from .subagents import (
    create_web_researcher,
    create_doc_analyst,
    create_report_writer,
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

logger = get_logger(__name__)


def create_research_agent(
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
    **kwargs,
):
    """
    统一深度研究智能体创建入口

    优先使用官方 deepagents.create_deep_agent API，
    当 deepagents 包不可用时自动降级到自定义 StateGraph 实现。

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
    if prefer_official:
        try:
            logger.info("使用官方 deepagents API 创建深度研究智能体")
            return create_official_deep_agent(
                thread_id=thread_id,
                enable_web_search=enable_web_search,
                enable_doc_analysis=enable_doc_analysis,
                retriever_tool=retriever_tool,
                middleware=middleware,
                enable_guardrails=enable_guardrails,
                guardrails_strict_mode=guardrails_strict_mode,
                checkpointer=checkpointer,
                store=store,
                user_id=user_id,
                session_id=session_id,
                **kwargs,
            )
        except ImportError:
            logger.warning(
                "deepagents 包不可用，降级到自定义 StateGraph 实现。"
                "安装命令: pip install deepagents"
            )

    logger.info("使用自定义 StateGraph 创建深度研究智能体")
    return create_deep_research_agent(
        thread_id=thread_id,
        enable_web_search=enable_web_search,
        enable_doc_analysis=enable_doc_analysis,
        retriever_tool=retriever_tool,
        user_id=user_id,
        session_id=session_id,
        middleware=middleware,
        enable_guardrails=enable_guardrails,
        guardrails_strict_mode=guardrails_strict_mode,
        checkpointer=checkpointer,
        **kwargs,
    )


__all__ = [
    "create_research_agent",
    "OfficialDeepAgentAdapter",
    "create_official_deep_agent",
    "create_official_deep_agent_with_interrupt",
    "DeepResearchAgent",
    "ResearchState",
    "create_deep_research_agent",
    "SafeDeepResearchAgent",
    "create_web_researcher",
    "create_doc_analyst",
    "create_report_writer",
    "get_subagent_info",
    "WEB_RESEARCHER_PROMPT",
    "DOC_ANALYST_PROMPT",
    "REPORT_WRITER_PROMPT",
    "TaskManager",
    "get_task_manager",
    "update_task_status",
]
