"""
SubAgents 子智能体模块

定义专门的子智能体，每个负责特定的研究任务：
1. WebResearcher: 网络搜索和信息整理
2. DocAnalyst: 文档分析和知识提取（支持网络搜索补充）
3. ReportWriter: 报告撰写和内容组织

改进：支持 middleware 传入 create_agent，使 Guardrails 在子智能体中生效
"""

from typing import Optional, List, Sequence

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import BaseTool
from langchain_core.language_models.chat_models import BaseChatModel

from Django_xm.apps.ai_engine.services.llm_factory import get_model_string
from Django_xm.apps.tools.web.search import create_tavily_search_tool
from Django_xm.apps.tools.file.filesystem import FILESYSTEM_TOOLS
from Django_xm.apps.config_center.config import get_logger
from Django_xm.apps.ai_engine.prompts.system_prompts import WRITER_GUIDELINES
from Django_xm.apps.ai_engine.guardrails import create_guardrails_middleware
from Django_xm.apps.core.permissions import PermissionService

logger = get_logger(__name__)


WEB_RESEARCHER_PROMPT = (
    "你是一个专业的网络研究员，负责从互联网搜索与整理信息。"
    "使用搜索工具查找并评估来源，提取关键数据，"
    "按来源类型自适配呈现（官方文档、论文、标准、新闻、博客），"
    "采用要点与段落混合的方式记录，使用内联引用并在结尾列出参考来源，"
    "将研究笔记保存到文件系统。"
)


DOC_ANALYST_PROMPT = (
    "你是一个专业的文档分析师，负责在知识库中检索并提炼信息。"
    "根据研究问题执行多次检索与评估，直接引用关键段落，"
    "整理为要点与段落混合的分析笔记，列出文档来源与位置，"
    "并保存到文件系统。\n\n"
    "## 结构化输出要求\n"
    "你的分析笔记必须包含以下结构：\n\n"
    "### 分析摘要\n"
    "简要概述从文档中发现的关键信息。\n\n"
    "### 关键发现\n"
    "每个发现按以下格式记录：\n"
    "- **发现内容**：具体发现描述\n"
    "  - 来源：文档名称/知识库名称\n"
    "  - 原文片段：直接引用原文关键段落（用 > 引用格式）\n"
    "  - 相关性：说明该发现与研究问题的关联\n\n"
    "### 文档来源列表\n"
    "列出所有引用的文档及其位置信息。\n\n"
    "### 知识缺口\n"
    "指出文档中缺失的信息，哪些方面需要网络搜索补充。\n\n"
    "## 网络搜索补充\n"
    "如果你同时拥有搜索工具，当知识库文档无法完全回答研究问题时，"
    "应主动使用搜索工具补充缺失信息，并在笔记中标注哪些内容来自网络搜索。"
)


REPORT_WRITER_PROMPT = (
    "你是一个专业的研究报告撰写者，负责整合研究材料并产出高质量报告。"
    "列出并阅读研究笔记与分析，识别关键发现与证据，"
    "根据主题与信息密度选择合适结构，提供真实示例或代码片段（技术主题），"
    "使用内联引用与参考列表，最终保存报告到文件系统。\n\n"
    "## 引用规范\n"
    "报告中的每个关键论点必须标注来源：\n"
    "- 知识库来源：[知识库:文档名] 或 [KB:doc_name]\n"
    "- 网络来源：[URL] 或 [Web:标题]\n"
    "在报告末尾列出完整的参考来源列表，分为「知识库来源」和「网络来源」两部分。"
)


def create_web_researcher(
    model: Optional[str] = None,
    tools: Optional[Sequence[BaseTool]] = None,
    middleware: Optional[Sequence[AgentMiddleware]] = None,
    enable_guardrails: bool = False,
    user_id: Optional[int] = None,
    session_id: Optional[str] = None,
    **kwargs,
):
    logger.info("🔍 创建 WebResearcher 子智能体")

    if model is None:
        model = get_model_string()

    if tools is None:
        agent_tools = []

        try:
            search_tool = create_tavily_search_tool()
            agent_tools.append(search_tool)
        except ValueError:
            logger.warning("⚠️ 无法创建搜索工具，Tavily API Key 未配置")

        agent_tools.extend(FILESYSTEM_TOOLS)
        tools = agent_tools

    if user_id:
        tools = PermissionService.wrap_tools_with_permission(
            tools, user_id=user_id, session_id=session_id
        )
        logger.info(f"WebResearcher 权限过滤后工具数: {len(tools)}")

    middleware_list = list(middleware) if middleware else []
    if enable_guardrails:
        middleware_list.append(create_guardrails_middleware(strict_mode=False, raise_on_error=False))

    agent_kwargs: dict = {
        "model": model,
        "tools": tools,
        "system_prompt": f"{WEB_RESEARCHER_PROMPT}\n\n{WRITER_GUIDELINES}",
    }
    if middleware_list:
        agent_kwargs["middleware"] = middleware_list
    agent_kwargs.update(kwargs)

    agent = create_agent(**agent_kwargs)

    logger.info("✅ WebResearcher 子智能体创建成功")
    return agent


def create_doc_analyst(
    model: Optional[str] = None,
    tools: Optional[Sequence[BaseTool]] = None,
    retriever_tool: Optional[BaseTool] = None,
    enable_web_supplement: bool = True,
    middleware: Optional[Sequence[AgentMiddleware]] = None,
    enable_guardrails: bool = False,
    user_id: Optional[int] = None,
    session_id: Optional[str] = None,
    **kwargs,
):
    logger.info("📚 创建 DocAnalyst 子智能体")

    if model is None:
        model = get_model_string()

    if tools is None:
        agent_tools = []

        if retriever_tool:
            agent_tools.append(retriever_tool)
            logger.debug("   添加 RAG 检索工具")
        else:
            logger.warning("⚠️ 未提供 retriever_tool，DocAnalyst 将无法检索文档")

        if enable_web_supplement:
            try:
                search_tool = create_tavily_search_tool()
                agent_tools.append(search_tool)
                logger.debug("   添加网络搜索工具（用于补充文档分析）")
            except ValueError:
                logger.warning("⚠️ 无法创建搜索工具，DocAnalyst 将无法进行网络补充搜索")

        agent_tools.extend(FILESYSTEM_TOOLS)
        logger.debug(f"   添加文件系统工具: {len(FILESYSTEM_TOOLS)} 个")
        tools = agent_tools

    if user_id:
        tools = PermissionService.wrap_tools_with_permission(
            tools, user_id=user_id, session_id=session_id
        )
        logger.info(f"DocAnalyst 权限过滤后工具数: {len(tools)}")

    middleware_list = list(middleware) if middleware else []
    if enable_guardrails:
        middleware_list.append(create_guardrails_middleware(strict_mode=False, raise_on_error=False))

    agent_kwargs: dict = {
        "model": model,
        "tools": tools,
        "system_prompt": f"{DOC_ANALYST_PROMPT}\n\n{WRITER_GUIDELINES}",
    }
    if middleware_list:
        agent_kwargs["middleware"] = middleware_list
    agent_kwargs.update(kwargs)

    agent = create_agent(**agent_kwargs)

    logger.info("✅ DocAnalyst 子智能体创建成功")
    return agent


def create_report_writer(
    model: Optional[str] = None,
    tools: Optional[Sequence[BaseTool]] = None,
    middleware: Optional[Sequence[AgentMiddleware]] = None,
    enable_guardrails: bool = False,
    user_id: Optional[int] = None,
    session_id: Optional[str] = None,
    **kwargs,
):
    logger.info("✍️ 创建 ReportWriter 子智能体")

    if model is None:
        model = get_model_string()

    if tools is None:
        tools = FILESYSTEM_TOOLS
        logger.debug(f"   添加文件系统工具: {len(FILESYSTEM_TOOLS)} 个")

    if user_id:
        tools = PermissionService.wrap_tools_with_permission(
            tools, user_id=user_id, session_id=session_id
        )
        logger.info(f"ReportWriter 权限过滤后工具数: {len(tools)}")

    middleware_list = list(middleware) if middleware else []
    if enable_guardrails:
        middleware_list.append(create_guardrails_middleware(strict_mode=False, raise_on_error=False))

    agent_kwargs: dict = {
        "model": model,
        "tools": tools,
        "system_prompt": f"{REPORT_WRITER_PROMPT}\n\n{WRITER_GUIDELINES}",
    }
    if middleware_list:
        agent_kwargs["middleware"] = middleware_list
    agent_kwargs.update(kwargs)

    agent = create_agent(**agent_kwargs)

    logger.info("✅ ReportWriter 子智能体创建成功")
    return agent


def get_subagent_info() -> dict:
    return {
        "web_researcher": {
            "name": "WebResearcher",
            "description": "网络搜索和信息整理专家",
            "capabilities": [
                "网络搜索",
                "信息筛选",
                "来源评估",
                "笔记整理",
            ],
            "tools": ["tavily_search", "write_research_file", "read_research_file"],
        },
        "doc_analyst": {
            "name": "DocAnalyst",
            "description": "文档分析和知识提取专家（支持网络搜索补充）",
            "capabilities": [
                "文档检索",
                "内容分析",
                "信息提炼",
                "关联识别",
                "网络搜索补充",
                "结构化来源输出",
            ],
            "tools": ["knowledge_base", "tavily_search", "write_research_file", "read_research_file"],
        },
        "report_writer": {
            "name": "ReportWriter",
            "description": "研究报告撰写专家",
            "capabilities": [
                "内容组织",
                "报告撰写",
                "引用管理",
                "质量把控",
            ],
            "tools": ["write_research_file", "read_research_file", "list_research_files"],
        },
    }
