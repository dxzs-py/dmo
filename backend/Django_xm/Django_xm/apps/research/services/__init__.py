"""
Research 服务层 - 提供深度研究的所有服务接口

包含：
- 深度研究智能体（DeepResearchAgent）
- 安全深度研究智能体（SafeDeepResearchAgent）
- 子智能体（WebResearcher、DocAnalyst、ReportWriter）
- 任务管理器（TaskManager）
"""

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

__all__ = [
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
