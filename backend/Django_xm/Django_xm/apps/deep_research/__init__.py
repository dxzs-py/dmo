"""
deep_research模块

提供深度研究功能：
- 深度研究智能体
- 研究工作流
- 子智能体（WebResearcher, DocAnalyst, ReportWriter）
- 安全深度研究智能体（集成Guardrails）
"""

from .deep_agent import DeepResearchAgent, create_deep_research_agent, should_use_deep_research, ResearchState
from .safe_deep_agent import SafeDeepResearchAgent
from .subagents import (
    create_web_researcher,
    create_doc_analyst,
    create_report_writer,
    get_all_subagents,
)

__all__ = [
    "DeepResearchAgent",
    "create_deep_research_agent",
    "should_use_deep_research",
    "ResearchState",
    "SafeDeepResearchAgent",
    "create_web_researcher",
    "create_doc_analyst",
    "create_report_writer",
    "get_all_subagents",
]