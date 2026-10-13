"""子代理注册表（深度研究子代理的单一来源）。

集中声明 ``web-researcher`` / ``doc-analyst`` / ``general-purpose`` 三类子代理：
- ``name`` / ``description`` / ``risk_ceiling`` / ``dedicated_tool_kind``
- ``system_prompt``：角色提示词随注册表同域定义（spec 任务 4.3 环 3 自
  research.prompts 上移——角色 prompt 是子代理机制的组成部分，与 SUBAGENT_SPECS
  同源维护；resolve_system_prompt 保留 prompt_provider 覆盖参数以兼容依赖倒置先例）
- 专用工具解析（web-researcher→搜索工具；doc-analyst→检索工具；general-purpose→空）

依赖方向（ARCH-03 forbidden 契约）：ai_engine 禁止 import research/knowledge/learning，
角色提示词为注册表内置数据（不再经 agent_hub.spawn 从 research.prompts 注入），
本模块不感知 research 任何模块。

调用方：``spawn_sub_agent`` 工具（agent_hub.subagent_tools.spawn）按 ``agent_name`` 查询注册表，
在主 agent 工具集基础上追加专用工具，并继承角色风险上限。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
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
    # 角色提示词（与注册表同域单源；空串表示由 task 作为任务输入）
    system_prompt: str = ""


# 子代理注册表（单一来源）。risk_ceiling 与 deep_builder 历史语义一致：
# web-researcher / doc-analyst = controlled（研究型，禁止高危操作）；
# general-purpose = high（通用型，与主 agent 权限对等）。
SUBAGENT_SPECS: dict[str, SubAgentSpec] = {
    "web-researcher": SubAgentSpec(
        name="web-researcher",
        description="网络搜索和信息整理专家，负责从互联网搜索和整理研究信息",
        risk_ceiling="controlled",
        dedicated_tool_kind="web_search",
        system_prompt=(
            "你是一个专业的网络研究员（子代理，执行者角色），负责从互联网搜索与整理信息。"
            "你是执行者，直接调用搜索工具完成任务：查找并评估来源，提取关键数据，"
            "按来源类型自适配呈现，采用要点与段落混合的方式记录，"
            "使用内联引用并在结尾列出参考来源。"
        ),
    ),
    "doc-analyst": SubAgentSpec(
        name="doc-analyst",
        description="文档分析和知识提取专家，负责在知识库中检索和分析文档",
        risk_ceiling="controlled",
        dedicated_tool_kind="retriever",
        system_prompt=(
            "你是一个专业的文档分析师（子代理，执行者角色），负责在知识库中检索并提炼信息。"
            "你是执行者，直接调用知识库检索工具完成任务：根据研究问题执行多次检索与评估，"
            "直接引用关键段落，整理为要点与段落混合的分析笔记，列出文档来源与位置。"
        ),
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


def resolve_system_prompt(
    name: str,
    prompt_provider: Callable[[str], str] | None = None,
) -> str:
    """解析子代理 system_prompt（注册表内置单源 + 可选 provider 覆盖）。

    解析优先级：
    1. general-purpose：内置通用提示词（不依赖外部数据）。
    2. prompt_provider 覆盖（依赖倒置先例保留，如调用方需运行期定制角色词）。
    3. 注册表 spec.system_prompt（角色词与 SUBAGENT_SPECS 同域单源；
       spec 任务 4.3 环 3：不再经 agent_hub.spawn 从 research.prompts 注入）。

    Args:
        name: 子代理名称。
        prompt_provider: 可选的角色提示词提供器，入参为 agent_name，
            返回提示词（可为空串）。ai_engine 不得直接 import research
            （ARCH-03 forbidden 契约）。

    未命中注册表且无 provider 覆盖时返回空串（由 task 作为任务输入）。
    """
    if name == "general-purpose":
        return (
            "In order to complete the objective that the user asks of you, you "
            "have access to a set of tools that you can use to perform operations "
            "and find information. Use the tools available to you to complete the "
            "task. Do not make assumptions about the user's intent - if something "
            "is unclear, ask for clarification."
        )
    if prompt_provider is not None:
        try:
            return prompt_provider(name) or ""
        except Exception as e:
            logger.warning(f"加载子代理 system_prompt 失败: {name}, err={e}")
            return ""
    spec = SUBAGENT_SPECS.get(name)
    if spec is not None and spec.system_prompt:
        return spec.system_prompt
    logger.warning(f"子代理 system_prompt 未注入 prompt_provider 且注册表无内置词，返回空串: {name}")
    return ""


def resolve_dedicated_tools(
    spec: SubAgentSpec,
    main_tools: list[BaseTool],
    retriever_prefixes: Sequence[str] | None = None,
) -> list[BaseTool]:
    """解析子代理专用工具（追加到主 agent 工具集，按 tool_name 去重）。

    Args:
        spec: 子代理注册表项。
        main_tools: 主 agent 工具集（用于识别 retriever_tool）。
        retriever_prefixes: 检索类工具名前缀，由调用方注入（ARCH-03 依赖倒置，
            如 agent_hub 侧传入 ``research.services._constants.RETRIEVER_TOOL_NAME_PREFIXES``）。
            未注入时 retriever 类专用工具返回空列表。
    """
    if spec.dedicated_tool_kind == "web_search":
        try:
            from Django_xm.apps.tools.langchain.web_search import create_tavily_search_tool

            return [create_tavily_search_tool()]
        except (ValueError, ImportError) as e:
            logger.warning(f"web-researcher 搜索工具不可用（Tavily 未配置）: {e}")
            return []
    if spec.dedicated_tool_kind == "retriever":
        if retriever_prefixes is None:
            logger.warning("doc-analyst 检索工具前缀未注入 retriever_prefixes，返回空列表")
            return []
        for t in main_tools:
            name = getattr(t, "name", "")
            if any(name.startswith(p) or name == p for p in retriever_prefixes):
                return [t]
        return []
    return []
