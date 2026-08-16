"""子代理注册表（深度研究子代理的单一来源）。

集中声明 ``web-researcher`` / ``doc-analyst`` / ``general-purpose`` 三类子代理：
- ``name`` / ``description`` / ``risk_ceiling`` / ``dedicated_tool_kind``
- ``system_prompt`` 懒加载（``research.prompts`` 为纯常量模块，无循环依赖）
- 专用工具解析（web-researcher→搜索工具；doc-analyst→检索工具；general-purpose→空）

调用方：``spawn_sub_agent`` 工具（agent_hub.subagent_tools.spawn）按 ``agent_name`` 查询注册表，
在主 agent 工具集基础上追加专用工具，并继承角色风险上限。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SubAgentSpec:
    """子代理注册表项。"""

    name: str
    description: str
    risk_ceiling: str  # RiskLevel.value（"controlled" / "high"）
    dedicated_tool_kind: str  # "web_search" | "retriever" | "none"


# 子代理注册表（单一来源）。risk_ceiling 与 deep_builder 历史语义一致：
# web-researcher / doc-analyst = controlled（研究型，禁止高危操作）；
# general-purpose = high（通用型，与主 agent 权限对等）。
SUBAGENT_SPECS: dict[str, SubAgentSpec] = {
    "web-researcher": SubAgentSpec(
        name="web-researcher",
        description="网络搜索和信息整理专家，负责从互联网搜索和整理研究信息",
        risk_ceiling="controlled",
        dedicated_tool_kind="web_search",
    ),
    "doc-analyst": SubAgentSpec(
        name="doc-analyst",
        description="文档分析和知识提取专家，负责在知识库中检索和分析文档",
        risk_ceiling="controlled",
        dedicated_tool_kind="retriever",
    ),
    "general-purpose": SubAgentSpec(
        name="general-purpose",
        description=(
            "General-purpose agent for researching complex questions, searching "
            "for files and content, and executing multi-step tasks. When you are "
            "searching for a keyword or file and are not confident that you will "
            "find the right match in the first few tries use this agent to perform "
            "the search for you. This agent has access to all tools as the main agent."
        ),
        risk_ceiling="high",
        dedicated_tool_kind="none",
    ),
}


def get_subagent_spec(name: str) -> SubAgentSpec | None:
    """按名称查询注册表项；未知名称返回 None（作为通用子代理处理）。"""
    return SUBAGENT_SPECS.get(name)


def resolve_system_prompt(name: str) -> str:
    """懒加载子代理 system_prompt。

    纯常量模块 ``research.prompts`` 仅含 prompt 字符串，无反向依赖；
    运行时导入避免 ai_engine → research 的模块加载期循环依赖。
    """
    if name == "general-purpose":
        return (
            "In order to complete the objective that the user asks of you, you "
            "have access to a set of tools that you can use to perform operations "
            "and find information. Use the tools available to you to complete the "
            "task. Do not make assumptions about the user's intent - if something "
            "is unclear, ask for clarification."
        )
    try:
        from Django_xm.apps.research.prompts import (
            DOC_ANALYST_SUBAGENT_PROMPT,
            WEB_RESEARCHER_SUBAGENT_PROMPT,
        )
    except Exception as e:
        logger.warning(f"加载子代理 system_prompt 失败: {name}, err={e}")
        return ""

    if name == "web-researcher":
        return WEB_RESEARCHER_SUBAGENT_PROMPT
    if name == "doc-analyst":
        return DOC_ANALYST_SUBAGENT_PROMPT
    return ""


def resolve_dedicated_tools(spec: SubAgentSpec, main_tools: list[BaseTool]) -> list[BaseTool]:
    """解析子代理专用工具（追加到主 agent 工具集，按 tool_name 去重）。

    Args:
        spec: 子代理注册表项。
        main_tools: 主 agent 工具集（用于识别 retriever_tool）。
    """
    if spec.dedicated_tool_kind == "web_search":
        try:
            from Django_xm.apps.tools.langchain.web_search import create_tavily_search_tool

            return [create_tavily_search_tool()]
        except (ValueError, ImportError) as e:
            logger.warning(f"web-researcher 搜索工具不可用（Tavily 未配置）: {e}")
            return []
    if spec.dedicated_tool_kind == "retriever":
        from Django_xm.apps.research.services._constants import RETRIEVER_TOOL_NAME_PREFIXES

        for t in main_tools:
            name = getattr(t, "name", "")
            if any(name.startswith(p) or name == p for p in RETRIEVER_TOOL_NAME_PREFIXES):
                return [t]
        return []
    return []
