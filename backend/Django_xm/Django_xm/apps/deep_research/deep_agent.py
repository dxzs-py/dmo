"""
深度研究智能体模块
基于 LangChain 实现深度研究功能
"""

from typing import Optional, Dict, Any, TypedDict, Annotated, List
import json
from datetime import datetime

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langchain_core.tools import BaseTool
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver

from Django_xm.apps.core.config import settings, get_logger
from Django_xm.apps.core.models import get_chat_model
from Django_xm.apps.core.prompts import WRITER_GUIDELINES
from Django_xm.apps.core.tools.filesystem import ResearchFileSystem, get_filesystem
from .subagents import create_web_researcher, create_doc_analyst, create_report_writer

logger = get_logger(__name__)


class ResearchState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    query: str
    thread_id: str
    plan: Optional[Dict[str, Any]]
    web_research_done: bool
    doc_analysis_done: bool
    report_done: bool
    current_step: str
    error: Optional[str]
    final_report: Optional[str]


DEEP_RESEARCH_KEYWORDS = [
    "深度", "研究", "趋势", "对比", "分析", "报告", "总结",
    "未来", "影响", "市场", "发展", "机制", "原理",
    "architecture", "best practice", "最佳实践", "详解",
]


def should_use_deep_research(message: str) -> bool:
    """判断问题是否需要深度研究"""
    if not message:
        return False
    if any(keyword in message for keyword in DEEP_RESEARCH_KEYWORDS):
        return True
    if len(message.strip()) >= 80:
        return True
    return False


class DeepResearchAgent:
    """深度研究智能体"""

    def __init__(
        self,
        thread_id: str,
        enable_web_search: bool = True,
        enable_doc_analysis: bool = False,
        retriever_tool: Optional[BaseTool] = None,
        checkpointer: Optional[Any] = None,
        **kwargs,
    ):
        self.thread_id = thread_id
        self.enable_web_search = enable_web_search
        self.enable_doc_analysis = enable_doc_analysis
        self.retriever_tool = retriever_tool

        logger.info(f"初始化 DeepResearchAgent: {thread_id}")
        logger.info(f"  网络搜索: {enable_web_search}")
        logger.info(f"  文档分析: {enable_doc_analysis}")

        self.filesystem = get_filesystem(thread_id)

        self._init_subagents(retriever_tool)

        self.graph = self._build_graph(checkpointer)
        logger.info("DeepResearchAgent 初始化完成")

    def _init_subagents(self, retriever_tool: Optional[BaseTool] = None) -> None:
        """初始化子智能体"""
        logger.info("初始化子智能体...")

        if self.enable_web_search:
            self.web_researcher = create_web_researcher()
            logger.debug("   WebResearcher 已创建")
        else:
            self.web_researcher = None

        if self.enable_doc_analysis:
            self.doc_analyst = create_doc_analyst(retriever_tool=retriever_tool)
            logger.debug("   DocAnalyst 已创建")
        else:
            self.doc_analyst = None

        self.report_writer = create_report_writer()
        logger.debug("   ReportWriter 已创建")

    def _build_graph(self, checkpointer: Optional[Any] = None):
        """构建 LangGraph 工作流"""
        logger.info("构建研究工作流...")

        workflow = StateGraph(ResearchState)

        workflow.add_node("planner", self._planner_node)

        if self.enable_web_search:
            workflow.add_node("web_research", self._web_research_node)

        if self.enable_doc_analysis:
            workflow.add_node("doc_analysis", self._doc_analysis_node)

        workflow.add_node("report_writing", self._report_writing_node)

        workflow.set_entry_point("planner")

        if self.enable_web_search:
            workflow.add_edge("planner", "web_research")

            if self.enable_doc_analysis:
                workflow.add_edge("web_research", "doc_analysis")
                workflow.add_edge("doc_analysis", "report_writing")
            else:
                workflow.add_edge("web_research", "report_writing")
        else:
            if self.enable_doc_analysis:
                workflow.add_edge("planner", "doc_analysis")
                workflow.add_edge("doc_analysis", "report_writing")
            else:
                workflow.add_edge("planner", "report_writing")

        workflow.add_edge("report_writing", END)

        if checkpointer is None:
            checkpointer = MemorySaver()

        graph = workflow.compile(checkpointer=checkpointer)
        logger.info("工作流构建完成")
        return graph

    def _planner_node(self, state: ResearchState) -> ResearchState:
        """规划节点"""
        logger.info("执行规划节点...")

        query = state["query"]
        plan = state.get("plan", {})

        plan_prompt = f"""请为以下研究问题制定详细的研究计划：

研究问题：{query}

请输出 JSON 格式的研究计划：
{{
    "research_goal": "研究目标",
    "key_questions": ["问题1", "问题2", ...],
    "search_keywords": ["关键词1", "关键词2", ...],
    "expected_outcomes": ["预期成果1", "预期成果2", ...]
}}
"""

        try:
            model = get_chat_model()
            response = model.invoke([HumanMessage(content=plan_prompt)])

            plan_text = response.content

            import re
            json_match = re.search(r'\{.*\}', plan_text, re.DOTALL)
            if json_match:
                plan = json.loads(json_match.group())
            else:
                plan = {
                    "research_goal": query,
                    "key_questions": [query],
                    "search_keywords": query.split(),
                    "expected_outcomes": ["完整的研究报告"]
                }

            plan_content = f"""# 研究计划：{query}

## 研究目标
{plan.get('research_goal', query)}

## 关键问题
{chr(10).join([f"- {q}" for q in plan.get('key_questions', [query])])}

## 搜索关键词
{', '.join(plan.get('search_keywords', []))}

## 预期成果
{chr(10).join([f"- {o}" for o in plan.get('expected_outcomes', [])])}

---
生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

            self.filesystem.write_file(
                "research_plan.md",
                plan_content,
                subdir="plans"
            )

            logger.info("研究计划已生成")

            state["plan"] = plan
            state["current_step"] = "planner"
            state["messages"].append(AIMessage(content=f"研究计划已生成：{plan.get('research_goal')}"))

        except Exception as e:
            logger.error(f"生成计划失败: {e}")
            state["error"] = str(e)
            state["plan"] = {"research_goal": query}

        return state

    def _web_research_node(self, state: ResearchState) -> ResearchState:
        """网络研究节点"""
        logger.info("执行网络研究节点...")

        query = state["query"]
        plan = state.get("plan", {})

        if self.web_researcher is None:
            logger.warning("WebResearcher 未启用，跳过网络研究")
            state["web_research_done"] = True
            state["current_step"] = "web_research"
            state["messages"].append(AIMessage(content="网络研究已跳过"))
            return state

        research_instruction = f"""请研究以下问题：

研究问题：{query}

研究计划：
- 目标：{plan.get('research_goal', query)}
- 关键问题：{', '.join(plan.get('key_questions', []))}
- 搜索关键词：{', '.join(plan.get('search_keywords', []))}

请使用网络搜索工具收集相关信息，并保存研究笔记。
"""

        try:
            result = self.web_researcher.invoke({
                "messages": [HumanMessage(content=research_instruction)]
            })

            logger.info("网络研究完成")

            state["web_research_done"] = True
            state["current_step"] = "web_research"
            state["messages"].append(AIMessage(content="网络研究已完成"))

        except Exception as e:
            logger.error(f"网络研究失败: {e}")
            state["error"] = str(e)

        return state

    def _doc_analysis_node(self, state: ResearchState) -> ResearchState:
        """文档分析节点"""
        logger.info("执行文档分析节点...")

        query = state["query"]

        if self.doc_analyst is None:
            logger.warning("DocAnalyst 未启用，跳过文档分析")
            state["doc_analysis_done"] = True
            state["current_step"] = "doc_analysis"
            state["messages"].append(AIMessage(content="文档分析已跳过"))
            return state

        analysis_instruction = f"""请分析以下研究问题相关的文档：

研究问题：{query}

请使用检索工具在知识库中查找相关文档，并保存分析笔记。
"""

        try:
            result = self.doc_analyst.invoke({
                "messages": [HumanMessage(content=analysis_instruction)]
            })

            logger.info("文档分析完成")

            state["doc_analysis_done"] = True
            state["current_step"] = "doc_analysis"
            state["messages"].append(AIMessage(content="文档分析已完成"))

        except Exception as e:
            logger.error(f"文档分析失败: {e}")
            state["error"] = str(e)

        return state

    def _report_writing_node(self, state: ResearchState) -> ResearchState:
        """报告撰写节点"""
        logger.info("执行报告撰写节点...")

        query = state["query"]
        plan = state.get("plan", {})

        writing_instruction = f"""请根据研究计划撰写最终研究报告：

研究问题：{query}

研究计划：
- 目标：{plan.get('research_goal', query)}
- 关键问题：{', '.join(plan.get('key_questions', []))}
- 预期成果：{', '.join(plan.get('expected_outcomes', []))}

写作指南：
{WRITER_GUIDELINES}

请撰写一份完整、有深度、有见解的研究报告，并保存到文件系统。
"""

        try:
            result = self.report_writer.invoke({
                "messages": [HumanMessage(content=writing_instruction)]
            })

            if isinstance(result, dict) and "messages" in result:
                messages = result["messages"]
                for msg in reversed(messages):
                    if isinstance(msg, AIMessage) and msg.content and len(msg.content) > 100:
                        final_report = msg.content
                        break
                else:
                    final_report = messages[-1].content if messages else ""
            else:
                final_report = str(result)

            logger.info("报告撰写完成")

            state["report_done"] = True
            state["current_step"] = "report_writing"
            state["final_report"] = final_report
            state["messages"].append(AIMessage(content="研究报告已完成"))

        except Exception as e:
            logger.error(f"撰写报告失败: {e}")
            state["error"] = str(e)
            state["final_report"] = f"报告生成过程中遇到错误: {str(e)}"

        return state

    def research(self, query: str, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """执行深度研究"""
        logger.info(f"开始深度研究: {query[:50]}...")

        if config is None:
            config = {}

        if "configurable" not in config:
            config["configurable"] = {}

        config["configurable"]["thread_id"] = self.thread_id

        initial_state: ResearchState = {
            "messages": [HumanMessage(content=query)],
            "query": query,
            "thread_id": self.thread_id,
            "plan": None,
            "web_research_done": False,
            "doc_analysis_done": False,
            "report_done": False,
            "current_step": "start",
            "error": None,
            "final_report": None,
        }

        try:
            result = self.graph.invoke(initial_state, config)
            logger.info("深度研究完成")

            return {
                "success": True,
                "query": query,
                "final_report": result.get("final_report", ""),
                "plan": result.get("plan"),
                "current_step": result.get("current_step"),
                "error": result.get("error"),
            }

        except Exception as e:
            logger.error(f"深度研究失败: {e}")
            return {
                "success": False,
                "query": query,
                "final_report": None,
                "error": str(e),
            }


def create_deep_research_agent(
    thread_id: str,
    enable_web_search: bool = True,
    enable_doc_analysis: bool = False,
    **kwargs,
) -> DeepResearchAgent:
    """创建深度研究智能体的便捷函数"""
    return DeepResearchAgent(
        thread_id=thread_id,
        enable_web_search=enable_web_search,
        enable_doc_analysis=enable_doc_analysis,
        **kwargs,
    )