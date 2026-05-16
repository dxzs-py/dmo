"""
深度研究智能体模块
基于 LangGraph 实现深度研究功能

架构改进（v3）：
1. 条件边：运行时动态决定执行路径，替代构建时固定路径
2. 并行执行：web_research 和 doc_analysis 可并行运行
3. 错误恢复：独立的 error_handler 节点，支持重试和降级
4. 流式输出：支持 astream_events 逐节点流式返回
5. Middleware 传递：GuardrailsMiddleware 正确传入子智能体 create_agent
"""

from typing import Optional, Dict, Any, TypedDict, Annotated, List, Literal, Sequence
import json
from datetime import datetime

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langchain_core.tools import BaseTool
from langchain.agents.middleware import AgentMiddleware
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from Django_xm.apps.ai_engine.config import settings, get_logger
from Django_xm.apps.ai_engine.services.llm_factory import get_chat_model
from Django_xm.apps.ai_engine.services.checkpointer_factory import get_checkpointer
from Django_xm.apps.ai_engine.prompts.system_prompts import WRITER_GUIDELINES
from Django_xm.apps.tools.file.filesystem import ResearchFileSystem, get_filesystem
from Django_xm.apps.ai_engine.guardrails import OutputValidator
from Django_xm.apps.core.permissions import PermissionService
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
    retry_count: int
    max_retries: int


DEEP_RESEARCH_KEYWORDS = [
    "深度", "研究", "趋势", "对比", "分析", "报告", "总结",
    "未来", "影响", "市场", "发展", "机制", "原理",
    "architecture", "best practice", "最佳实践", "详解",
]


def should_use_deep_research(message: str) -> bool:
    if not message:
        return False
    if any(keyword in message for keyword in DEEP_RESEARCH_KEYWORDS):
        return True
    if len(message.strip()) >= 80:
        return True
    return False


def _route_after_planner(
    state: ResearchState,
) -> Literal["web_research", "doc_analysis", "report_writing", "error_handler"]:
    if state.get("error"):
        return "error_handler"

    plan = state.get("plan", {})
    key_questions = plan.get("key_questions", [])
    search_keywords = plan.get("search_keywords", [])
    needs_web = len(search_keywords) > 0 or len(key_questions) > 0

    if needs_web:
        return "web_research"
    elif state.get("doc_analysis_done") is False:
        return "doc_analysis"
    else:
        return "report_writing"


def _route_after_web_research(
    state: ResearchState,
) -> Literal["doc_analysis", "report_writing", "error_handler"]:
    if state.get("error"):
        return "error_handler"

    if not state.get("doc_analysis_done"):
        return "doc_analysis"
    return "report_writing"


def _route_after_doc_analysis(
    state: ResearchState,
) -> Literal["report_writing", "error_handler"]:
    if state.get("error"):
        return "error_handler"
    return "report_writing"


def _route_after_planner_parallel(
    state: ResearchState,
):
    """Fan-out routing: when both web_research and doc_analysis are enabled,
    send to both nodes in parallel."""
    if state.get("error"):
        return "error_handler"

    plan = state.get("plan", {})
    key_questions = plan.get("key_questions", [])
    search_keywords = plan.get("search_keywords", [])
    needs_research = len(search_keywords) > 0 or len(key_questions) > 0

    if needs_research:
        # Fan-out: run both web_research and doc_analysis in parallel
        return ["web_research", "doc_analysis"]
    elif not state.get("doc_analysis_done"):
        return "doc_analysis"
    else:
        return "report_writing"


def _route_after_parallel_branch(
    state: ResearchState,
) -> Literal["report_writing", "error_handler"]:
    """Fan-in routing: after a parallel branch completes, check for errors
    and route to report_writing (LangGraph will wait for all branches)."""
    if state.get("error"):
        return "error_handler"
    return "report_writing"


def _route_after_error(
    state: ResearchState,
) -> Literal["planner", "web_research", "doc_analysis", "report_writing", "__end__"]:
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 2)

    if retry_count >= max_retries:
        logger.warning(f"已达最大重试次数 ({max_retries})，终止研究")
        return END

    current_step = state.get("current_step", "planner")
    step_to_retry = {
        "planner": "planner",
        "web_research": "web_research",
        "doc_analysis": "doc_analysis",
        "report_writing": "report_writing",
    }
    return step_to_retry.get(current_step, "planner")


class DeepResearchAgent:

    def __init__(
        self,
        thread_id: str,
        enable_web_search: bool = True,
        enable_doc_analysis: bool = False,
        retriever_tool: Optional[BaseTool] = None,
        middleware: Optional[Sequence[AgentMiddleware]] = None,
        enable_guardrails: bool = False,
        checkpointer: Optional[Any] = None,
        user_id: Optional[int] = None,
        session_id: Optional[str] = None,
        max_retries: int = 2,
        **kwargs,
    ):
        self.thread_id = thread_id
        self.enable_web_search = enable_web_search
        self.enable_doc_analysis = enable_doc_analysis
        self.retriever_tool = retriever_tool
        self.user_id = user_id
        self.session_id = session_id
        self.max_retries = max_retries
        self.middleware = list(middleware) if middleware else []
        self.enable_guardrails = enable_guardrails

        logger.info(f"初始化 DeepResearchAgent: {thread_id}")
        logger.info(f"  网络搜索: {enable_web_search}")
        logger.info(f"  文档分析: {enable_doc_analysis}")
        logger.info(f"  最大重试: {max_retries}")
        logger.info(f"  Guardrails: {enable_guardrails}")
        logger.info(f"  Middleware 数量: {len(self.middleware)}")

        self.filesystem = get_filesystem(thread_id)

        self._init_subagents(retriever_tool)

        self.graph = self._build_graph(checkpointer)
        logger.info("DeepResearchAgent 初始化完成")

    def _init_subagents(self, retriever_tool: Optional[BaseTool] = None) -> None:
        logger.info("初始化子智能体...")

        if self.enable_web_search:
            self.web_researcher = create_web_researcher(
                user_id=self.user_id, session_id=self.session_id,
                middleware=self.middleware if self.middleware else None,
                enable_guardrails=self.enable_guardrails,
            )
            logger.debug("   WebResearcher 已创建")
        else:
            self.web_researcher = None

        if self.enable_doc_analysis:
            self.doc_analyst = create_doc_analyst(
                retriever_tool=retriever_tool,
                user_id=self.user_id, session_id=self.session_id,
                middleware=self.middleware if self.middleware else None,
                enable_guardrails=self.enable_guardrails,
            )
            logger.debug("   DocAnalyst 已创建")
        else:
            self.doc_analyst = None

        self.report_writer = create_report_writer(
            user_id=self.user_id, session_id=self.session_id,
            middleware=self.middleware if self.middleware else None,
            enable_guardrails=self.enable_guardrails,
        )
        logger.debug("   ReportWriter 已创建")

    def _build_graph(self, checkpointer: Optional[Any] = None):
        logger.info("构建研究工作流（条件边 + 错误恢复）...")

        workflow = StateGraph(ResearchState)

        workflow.add_node("planner", self._planner_node)
        workflow.add_node("error_handler", self._error_handler_node)
        workflow.add_node("report_writing", self._report_writing_node)

        if self.enable_web_search:
            workflow.add_node("web_research", self._web_research_node)

        if self.enable_doc_analysis:
            workflow.add_node("doc_analysis", self._doc_analysis_node)

        workflow.set_entry_point("planner")

        if self.enable_web_search and self.enable_doc_analysis:
            # Fan-out: planner sends to both web_research and doc_analysis in parallel
            workflow.add_conditional_edges(
                "planner",
                _route_after_planner_parallel,
                {
                    "web_research": "web_research",
                    "doc_analysis": "doc_analysis",
                    "report_writing": "report_writing",
                    "error_handler": "error_handler",
                },
            )
            # Fan-in: both web_research and doc_analysis converge to report_writing
            workflow.add_conditional_edges(
                "web_research",
                _route_after_parallel_branch,
                {
                    "report_writing": "report_writing",
                    "error_handler": "error_handler",
                },
            )
            workflow.add_conditional_edges(
                "doc_analysis",
                _route_after_parallel_branch,
                {
                    "report_writing": "report_writing",
                    "error_handler": "error_handler",
                },
            )
        elif self.enable_web_search:
            workflow.add_conditional_edges(
                "planner",
                _route_after_planner,
                {
                    "web_research": "web_research",
                    "report_writing": "report_writing",
                    "error_handler": "error_handler",
                },
            )
            workflow.add_conditional_edges(
                "web_research",
                _route_after_doc_analysis,
                {
                    "report_writing": "report_writing",
                    "error_handler": "error_handler",
                },
            )
        elif self.enable_doc_analysis:
            workflow.add_conditional_edges(
                "planner",
                _route_after_planner,
                {
                    "doc_analysis": "doc_analysis",
                    "report_writing": "report_writing",
                    "error_handler": "error_handler",
                },
            )
            workflow.add_conditional_edges(
                "doc_analysis",
                _route_after_doc_analysis,
                {
                    "report_writing": "report_writing",
                    "error_handler": "error_handler",
                },
            )
        else:
            workflow.add_conditional_edges(
                "planner",
                _route_after_planner,
                {
                    "report_writing": "report_writing",
                    "error_handler": "error_handler",
                },
            )

        workflow.add_edge("report_writing", END)

        workflow.add_conditional_edges(
            "error_handler",
            _route_after_error,
            {
                "planner": "planner",
                "web_research": "web_research",
                "doc_analysis": "doc_analysis",
                "report_writing": "report_writing",
                END: END,
            },
        )

        if checkpointer is None:
            checkpointer = get_checkpointer()

        graph = workflow.compile(checkpointer=checkpointer)
        logger.info("工作流构建完成（条件边 + 错误恢复）")
        return graph

    def _error_handler_node(self, state: ResearchState) -> ResearchState:
        logger.warning(f"进入错误恢复节点，当前步骤: {state.get('current_step')}")

        retry_count = state.get("retry_count", 0) + 1
        error_msg = state.get("error", "未知错误")

        logger.info(f"错误处理: 第 {retry_count} 次重试，错误: {error_msg}")

        state["retry_count"] = retry_count
        state["error"] = None

        if retry_count >= state.get("max_retries", self.max_retries):
            logger.error(f"已达最大重试次数，生成降级报告")
            state["final_report"] = (
                f"# 研究报告（降级模式）\n\n"
                f"## 说明\n\n"
                f"研究过程中遇到错误，已达到最大重试次数。以下是部分结果：\n\n"
                f"- 研究问题: {state.get('query', '未知')}\n"
                f"- 最后错误: {error_msg}\n"
                f"- 重试次数: {retry_count}\n\n"
                f"建议稍后重试或联系管理员。\n"
            )
            state["report_done"] = True

        return state

    def _planner_node(self, state: ResearchState) -> ResearchState:
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
            state["current_step"] = "planner"
            state["plan"] = {"research_goal": query}

        return state

    def _web_research_node(self, state: ResearchState) -> ResearchState:
        logger.info("执行网络研究节点...")

        query = state["query"]
        thread_id = state["thread_id"]
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

请使用网络搜索工具收集相关信息，并使用 write_research_file 保存研究笔记到 notes/web_research.md。

请确保：
1. 使用搜索工具查找相关信息
2. 评估信息的可信度和相关性
3. 提取关键信息和数据
4. 整理为要点与段落混合的研究笔记
5. 使用内联引用并在结尾列出参考来源

thread_id: {thread_id}
"""

        try:
            result = self.web_researcher.invoke({
                "messages": [HumanMessage(content=research_instruction)]
            })

            notes_saved = False
            try:
                self.filesystem.read_file("web_research.md", subdirectory="notes")
                notes_saved = True
                logger.info("网络研究笔记已保存到文件系统")
            except Exception:
                logger.debug("未在文件系统中找到笔记，尝试从 Agent 输出提取...")

            if not notes_saved:
                if isinstance(result, dict) and "messages" in result:
                    messages = result["messages"]

                    research_content = []
                    for msg in messages:
                        if isinstance(msg, AIMessage) and msg.content:
                            content = msg.content.strip()
                            if not content.startswith("找到") and not content.startswith("搜索"):
                                research_content.append(content)

                    if research_content:
                        combined_content = "\n\n".join(research_content)

                        if not combined_content.strip().startswith("#"):
                            combined_content = f"""# 研究笔记：{query}

## 研究内容

{combined_content}

---
*生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*
"""

                        try:
                            self.filesystem.write_file(
                                "web_research.md",
                                combined_content,
                                subdirectory="notes",
                                metadata={"source": "agent_output_extraction"}
                            )
                            logger.info("已从 Agent 输出提取并保存研究笔记")
                        except Exception as save_error:
                            logger.error(f"保存提取的笔记失败: {save_error}")

            logger.info("网络研究完成")

            state["web_research_done"] = True
            state["current_step"] = "web_research"
            state["messages"].append(AIMessage(content="网络研究已完成"))

        except Exception as e:
            logger.error(f"网络研究失败: {e}")
            state["error"] = str(e)
            state["current_step"] = "web_research"

        return state

    def _doc_analysis_node(self, state: ResearchState) -> ResearchState:
        logger.info("执行文档分析节点...")

        query = state["query"]
        thread_id = state["thread_id"]

        if self.doc_analyst is None:
            logger.warning("DocAnalyst 未启用，跳过文档分析")
            state["doc_analysis_done"] = True
            state["current_step"] = "doc_analysis"
            state["messages"].append(AIMessage(content="文档分析已跳过"))
            return state

        analysis_instruction = f"""请分析以下研究问题相关的文档：

研究问题：{query}

请使用检索工具在知识库中查找相关文档，并使用 write_research_file 保存分析笔记到 notes/doc_analysis.md。

请确保：
1. 检索多个相关文档
2. 直接引用原文
3. 记录文档来源
4. 提炼核心观点

thread_id: {thread_id}
"""

        try:
            result = self.doc_analyst.invoke({
                "messages": [HumanMessage(content=analysis_instruction)]
            })

            notes_saved = False
            try:
                self.filesystem.read_file("doc_analysis.md", subdirectory="notes")
                notes_saved = True
                logger.info("文档分析笔记已保存到文件系统")
            except Exception:
                logger.debug("未在文件系统中找到文档分析笔记...")

            if not notes_saved:
                if isinstance(result, dict) and "messages" in result:
                    messages = result["messages"]
                    analysis_content = []
                    for msg in messages:
                        if isinstance(msg, AIMessage) and msg.content:
                            content = msg.content.strip()
                            if content and len(content) > 50:
                                analysis_content.append(content)

                    if analysis_content:
                        combined = "\n\n".join(analysis_content)
                        try:
                            self.filesystem.write_file(
                                "doc_analysis.md",
                                combined,
                                subdirectory="notes",
                                metadata={"source": "agent_output_extraction"}
                            )
                            logger.info("已从 Agent 输出提取并保存文档分析笔记")
                        except Exception as save_error:
                            logger.error(f"保存文档分析笔记失败: {save_error}")

            logger.info("文档分析完成")

            state["doc_analysis_done"] = True
            state["current_step"] = "doc_analysis"
            state["messages"].append(AIMessage(content="文档分析已完成"))

        except Exception as e:
            logger.error(f"文档分析失败: {e}")
            state["error"] = str(e)
            state["current_step"] = "doc_analysis"

        return state

    def _report_writing_node(self, state: ResearchState) -> ResearchState:
        logger.info("执行报告撰写节点...")

        query = state["query"]
        thread_id = state["thread_id"]
        plan = state.get("plan", {})

        writing_instruction = f"""请根据研究计划撰写最终研究报告：

研究问题：{query}

研究计划：
- 目标：{plan.get('research_goal', query)}
- 关键问题：{', '.join(plan.get('key_questions', []))}
- 搜索关键词：{', '.join(plan.get('search_keywords', []))}
- 预期成果：{', '.join(plan.get('expected_outcomes', []))}

写作指南：
{WRITER_GUIDELINES}

请撰写一份完整、有深度、有见解的研究报告，并使用 write_research_file 保存到 reports/final_report.md。

请确保报告：
1. 有清晰的章节结构
2. 包含真实示例或代码片段（技术主题）
3. 保留内联引用并在结尾列出参考来源
4. 提供可操作的建议或结论

thread_id: {thread_id}
"""

        try:
            result = self.report_writer.invoke({
                "messages": [HumanMessage(content=writing_instruction)]
            })

            final_report = None

            try:
                final_report = self.filesystem.read_file(
                    "final_report.md",
                    subdirectory="reports"
                )
                logger.info("从文件系统读取最终报告")
            except Exception as fs_error:
                logger.debug(f"无法从文件系统读取报告: {fs_error}")

            if not final_report:
                logger.info("从 Agent 输出中提取报告内容...")

                if isinstance(result, dict) and "messages" in result:
                    messages = result["messages"]

                    ai_contents = []
                    for msg in messages:
                        if isinstance(msg, AIMessage) and msg.content:
                            content = msg.content.strip()
                            if content and not content.startswith("找到") and not content.startswith("文件已保存"):
                                ai_contents.append(content)

                    for content in sorted(ai_contents, key=len, reverse=True):
                        is_report = (
                            len(content) > 200 and
                            (content.startswith("#") or
                             "##" in content or
                             "执行摘要" in content or
                             "研究背景" in content or
                             "主要发现" in content)
                        )

                        if is_report:
                            final_report = content
                            logger.info(f"从 Agent 输出中提取到报告（长度: {len(content)} 字符）")
                            break

            if not final_report:
                logger.info("Agent 未直接生成报告，使用研究材料生成综合报告")

                research_materials = []

                try:
                    plan_content = self.filesystem.read_file(
                        "research_plan.md",
                        subdirectory="plans"
                    )
                    research_materials.append(("研究计划", plan_content))
                    logger.debug("读取研究计划")
                except Exception:
                    logger.debug("未找到研究计划")

                try:
                    web_notes = self.filesystem.read_file(
                        "web_research.md",
                        subdirectory="notes"
                    )
                    research_materials.append(("网络研究笔记", web_notes))
                    logger.debug("读取网络研究笔记")
                except Exception:
                    logger.debug("未找到网络研究笔记")

                try:
                    doc_notes = self.filesystem.read_file(
                        "doc_analysis.md",
                        subdirectory="notes"
                    )
                    research_materials.append(("文档分析报告", doc_notes))
                    logger.debug("读取文档分析报告")
                except Exception:
                    logger.debug("未找到文档分析报告")

                if research_materials:
                    logger.info(f"找到 {len(research_materials)} 个研究材料，生成综合报告")

                    materials_section = ""
                    for title, content in research_materials:
                        materials_section += f"\n### {title}\n\n{content}\n\n"

                    final_report = f"""# {query}

{materials_section}

结论与建议：基于上述材料给出清晰结论与可执行建议，并在文中保留关键证据的内联引用。

参考来源：请在文末列出来源。

---
*报告生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*
*研究任务ID：{thread_id}*
"""
                else:
                    logger.warning("未找到任何研究材料")
                    final_report = f"""# {query}

## 执行摘要

本研究针对"{query}"进行了调研。

## 说明

研究过程已完成，但未能找到保存的研究材料。这可能是由于：
1. 研究任务刚刚启动，材料尚未生成
2. 文件保存过程中出现问题
3. 研究工具未能正确调用

建议：
- 查看日志文件了解详细情况
- 重新运行研究任务
- 检查 API 配置和网络连接
"""

            validator = OutputValidator()
            validation_result = validator.validate(final_report)

            if not validation_result.is_valid:
                logger.warning(f"报告验证失败: {validation_result.errors}")

                revision_prompt = f"""请修订以下研究报告，解决以下问题：
{', '.join(validation_result.errors)}

原报告：
{final_report}

请确保修订后的报告：
1. 包含实际示例或代码片段
2. 保留原有的研究和引用内容
3. 提供可操作的建议
"""
                try:
                    model = get_chat_model()
                    revised = model.invoke([HumanMessage(content=revision_prompt)])
                    revised_text = revised.content or final_report

                    revised_validation = validator.validate(revised_text)
                    if revised_validation.is_valid:
                        final_report = revised_text
                        logger.info("报告修订成功并通过验证")
                    else:
                        logger.warning(f"修订后仍验证失败: {revised_validation.errors}")
                        final_report = revised_text
                except Exception as revision_error:
                    logger.error(f"修订报告失败: {revision_error}")

            try:
                self.filesystem.write_file(
                    "final_report.md",
                    final_report,
                    subdirectory="reports",
                    metadata={
                        "source": "deep_research_agent",
                        "query": query,
                        "validated": validation_result.is_valid,
                        "generated_at": datetime.now().isoformat()
                    }
                )
                logger.info("最终报告已保存到文件系统")
            except Exception as save_error:
                logger.warning(f"保存报告失败: {save_error}")

            logger.info("报告撰写完成")

            state["report_done"] = True
            state["current_step"] = "report_writing"
            state["final_report"] = final_report
            state["messages"].append(AIMessage(content="研究报告已完成"))

        except Exception as e:
            logger.error(f"撰写报告失败: {e}")
            state["error"] = str(e)
            state["current_step"] = "report_writing"
            state["final_report"] = f"报告生成过程中遇到错误: {str(e)}"

        return state

    def research(self, query: str, config: Optional[Dict[str, Any]] = None, callbacks: Optional[list] = None) -> Dict[str, Any]:
        logger.info(f"开始深度研究: {query[:50]}...")

        if config is None:
            config = {}

        if "configurable" not in config:
            config["configurable"] = {}

        config["configurable"]["thread_id"] = self.thread_id
        if callbacks:
            config["callbacks"] = callbacks

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
            "retry_count": 0,
            "max_retries": self.max_retries,
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

    async def aresearch(
        self,
        query: str,
        config: Optional[Dict[str, Any]] = None,
        callbacks: Optional[list] = None,
    ) -> Dict[str, Any]:
        logger.info(f"异步开始深度研究: {query[:50]}...")

        if config is None:
            config = {}

        if "configurable" not in config:
            config["configurable"] = {}

        config["configurable"]["thread_id"] = self.thread_id
        if callbacks:
            config["callbacks"] = callbacks

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
            "retry_count": 0,
            "max_retries": self.max_retries,
        }

        try:
            result = await self.graph.ainvoke(initial_state, config)
            logger.info("异步深度研究完成")

            return {
                "success": True,
                "query": query,
                "final_report": result.get("final_report", ""),
                "plan": result.get("plan"),
                "current_step": result.get("current_step"),
                "error": result.get("error"),
            }

        except Exception as e:
            logger.error(f"异步深度研究失败: {e}")
            return {
                "success": False,
                "query": query,
                "final_report": None,
                "error": str(e),
            }

    async def astream_research(
        self,
        query: str,
        config: Optional[Dict[str, Any]] = None,
    ):
        """
        流式执行深度研究，逐节点返回事件

        使用 astream_events 获取每个节点的执行事件，
        前端可据此实时展示研究进度。

        Yields:
            dict: 每个节点的事件数据，包含 event_type, node_name, data
        """
        logger.info(f"流式开始深度研究: {query[:50]}...")

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
            "retry_count": 0,
            "max_retries": self.max_retries,
        }

        try:
            async for event in self.graph.astream_events(
                initial_state,
                config,
                version="v2",
            ):
                kind = event.get("event", "")
                name = event.get("name", "")
                data = event.get("data", {})

                if kind == "on_chain_start" and name == "LangGraph":
                    yield {
                        "event_type": "research_start",
                        "node_name": "root",
                        "data": {"query": query},
                    }
                elif kind == "on_chain_start" and name in (
                    "planner", "web_research", "doc_analysis",
                    "report_writing", "error_handler",
                ):
                    yield {
                        "event_type": "node_start",
                        "node_name": name,
                        "data": {},
                    }
                elif kind == "on_chain_end" and name in (
                    "planner", "web_research", "doc_analysis",
                    "report_writing", "error_handler",
                ):
                    output = data.get("output", {})
                    yield {
                        "event_type": "node_end",
                        "node_name": name,
                        "data": {
                            "current_step": output.get("current_step", name),
                            "error": output.get("error"),
                            "final_report": output.get("final_report"),
                        },
                    }
                elif kind == "on_chain_end" and name == "LangGraph":
                    output = data.get("output", {})
                    yield {
                        "event_type": "research_end",
                        "node_name": "root",
                        "data": {
                            "final_report": output.get("final_report", ""),
                            "success": output.get("report_done", False),
                        },
                    }
                elif kind == "on_chain_error":
                    yield {
                        "event_type": "error",
                        "node_name": name,
                        "data": {"error": str(data.get("error", "未知错误"))},
                    }

        except Exception as e:
            logger.error(f"流式深度研究失败: {e}")
            yield {
                "event_type": "error",
                "node_name": "root",
                "data": {"error": str(e)},
            }

    def get_status(self) -> Dict[str, Any]:
        files = self.filesystem.list_files()

        return {
            "thread_id": self.thread_id,
            "filesystem_files": files,
            "web_search_enabled": self.enable_web_search,
            "doc_analysis_enabled": self.enable_doc_analysis,
            "max_retries": self.max_retries,
        }


def create_deep_research_agent(
    thread_id: str,
    enable_web_search: bool = True,
    enable_doc_analysis: bool = False,
    retriever_tool: Optional[BaseTool] = None,
    user_id: Optional[int] = None,
    session_id: Optional[str] = None,
    max_retries: int = 2,
    middleware: Optional[Sequence[AgentMiddleware]] = None,
    enable_guardrails: bool = False,
    guardrails_strict_mode: bool = False,
    checkpointer: Optional[Any] = None,
    **kwargs,
) -> DeepResearchAgent:
    return DeepResearchAgent(
        thread_id=thread_id,
        enable_web_search=enable_web_search,
        enable_doc_analysis=enable_doc_analysis,
        retriever_tool=retriever_tool,
        user_id=user_id,
        session_id=session_id,
        max_retries=max_retries,
        middleware=middleware,
        enable_guardrails=enable_guardrails,
        guardrails_strict_mode=guardrails_strict_mode,
        checkpointer=checkpointer,
        **kwargs,
    )
