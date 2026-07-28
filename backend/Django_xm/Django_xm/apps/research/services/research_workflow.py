import json
import uuid
from typing import Literal, NotRequired, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Send

from Django_xm.apps.ai_engine.services.llm_factory import get_chat_model
from Django_xm.apps.ai_engine.workflows.state import WorkflowState
from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)

DECOMPOSE_PROMPT = """你是一个查询分解专家。请将以下复杂查询分解为多个独立的子查询，以便并行检索。

原始查询: {query}

请以 JSON 格式返回:
- "sub_queries": 子查询字符串列表（2-5个，每个聚焦一个方面）

只返回 JSON，不要其他文本。"""

SEARCH_PROMPT = """请针对以下子查询进行深入搜索和分析，提供详细、准确的信息。

子查询: {sub_query}

请提供:
1. 关键发现和事实
2. 相关数据和证据
3. 信息来源（如有）

回答应详尽且聚焦于子查询主题。"""

SYNTHESIZE_PROMPT = """你是一个研究综合专家。请将以下多个子查询的检索结果综合成一份连贯的研究报告。

原始查询: {query}
子查询及结果:
{results}

请生成一份结构化的研究报告，包含:
1. 摘要
2. 各方面发现
3. 综合分析
4. 结论

报告应全面、准确、逻辑清晰。"""


class SubQueryState(TypedDict):
    """子查询并行搜索的状态"""
    sub_query: str
    query: str
    error: NotRequired[str | None]


async def decompose(state: WorkflowState) -> dict:
    """查询分解节点：将复杂查询拆分为多个子查询"""
    query = state.get("query", "")
    logger.info(f"[ResearchWorkflow] decompose: query={query[:80]}")

    try:
        model = get_chat_model(temperature=0.3)
        prompt = DECOMPOSE_PROMPT.format(query=query)
        response = await model.ainvoke([HumanMessage(content=prompt)])

        text = response.content
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(text[start:end])
            else:
                data = {}

        sub_queries = data.get("sub_queries", [])
        if not sub_queries:
            sub_queries = [query]

        sub_queries = sub_queries[:5]

        logger.info(
            f"[ResearchWorkflow] decomposed into {len(sub_queries)} sub-queries"
        )
        return {"research_queries": sub_queries}
    except Exception as e:
        logger.error(f"[ResearchWorkflow] decompose error: {e}")
        return {"research_queries": [query], "error": str(e)}


def search_dispatcher(state: WorkflowState) -> list[Send]:
    """将子查询分发到并行搜索节点"""
    sub_queries = state.get("research_queries", [])
    query = state.get("query", "")

    sends = []
    for sub_query in sub_queries:
        sends.append(
            Send("search", {"sub_query": sub_query, "query": query})
        )

    logger.info(f"[ResearchWorkflow] dispatching {len(sends)} parallel searches")
    return sends


def _create_search_node(search_tool_node: ToolNode | None):
    """创建 search 节点，优先使用 ToolNode 调用搜索工具，无工具时回退到 LLM"""

    async def search_with_tools(state: SubQueryState) -> dict:
        """使用搜索工具执行子查询检索"""
        sub_query = state.get("sub_query", "")
        logger.info(f"[ResearchWorkflow] search (tools): sub_query={sub_query[:60]}")

        try:
            # 选择第一个可用的搜索工具
            tool_name = next(iter(search_tool_node.tools_by_name))
            ai_message = AIMessage(
                content="",
                tool_calls=[{
                    "name": tool_name,
                    "args": {"query": sub_query},
                    "id": f"search_{uuid.uuid4().hex[:8]}",
                    "type": "tool_call",
                }],
            )

            # ToolNode 自动处理工具调用和异常
            result = await search_tool_node.ainvoke({"messages": [ai_message]})

            # 从 ToolMessage 中提取结果
            tool_result = ""
            for msg in result.get("messages", []):
                if isinstance(msg, ToolMessage):
                    tool_result = msg.content
                    if msg.status == "error":
                        logger.warning(
                            f"[ResearchWorkflow] search tool error: {tool_result}"
                        )
                    break

            if not tool_result:
                tool_result = f"子查询 '{sub_query}' 未返回结果"

            return {"research_results": [tool_result]}
        except Exception as e:
            logger.error(f"[ResearchWorkflow] search error: {e}")
            return {
                "research_results": [f"子查询 '{sub_query}' 检索失败: {e}"],
                "search_errors": [str(e)],
                "error": str(e),
            }

    async def search_with_llm(state: SubQueryState) -> dict:
        """LLM 回退模式：无搜索工具时使用 LLM 生成内容"""
        sub_query = state.get("sub_query", "")
        logger.info(f"[ResearchWorkflow] search (LLM fallback): sub_query={sub_query[:60]}")

        try:
            model = get_chat_model()
            prompt = SEARCH_PROMPT.format(sub_query=sub_query)
            response = await model.ainvoke([HumanMessage(content=prompt)])
            return {"research_results": [response.content]}
        except Exception as e:
            logger.error(f"[ResearchWorkflow] search (LLM) error: {e}")
            return {
                "research_results": [f"子查询 '{sub_query}' 检索失败: {e}"],
                "search_errors": [str(e)],
                "error": str(e),
            }

    # 有搜索工具时使用 ToolNode，否则回退到 LLM
    if search_tool_node is not None and len(search_tool_node.tools_by_name) > 0:
        return search_with_tools
    return search_with_llm


def _create_retrieve_node(retrieval_tool_node: ToolNode):
    """创建 retrieve 节点，使用 ToolNode 调用知识库检索工具"""

    async def retrieve(state: WorkflowState) -> dict:
        """知识库检索：对每个子查询调用检索工具"""
        research_queries = state.get("research_queries", [])
        logger.info(f"[ResearchWorkflow] retrieve: {len(research_queries)} queries")

        all_results: list[str] = []
        tool_name = next(iter(retrieval_tool_node.tools_by_name))

        for query in research_queries:
            try:
                ai_message = AIMessage(
                    content="",
                    tool_calls=[{
                        "name": tool_name,
                        "args": {"query": query},
                        "id": f"retrieve_{uuid.uuid4().hex[:8]}",
                        "type": "tool_call",
                    }],
                )

                result = await retrieval_tool_node.ainvoke({"messages": [ai_message]})

                for msg in result.get("messages", []):
                    if isinstance(msg, ToolMessage):
                        all_results.append(f"[知识库] {query}:\n{msg.content}")
                        break
            except Exception as e:
                logger.warning(f"[ResearchWorkflow] retrieve error for '{query}': {e}")
                all_results.append(f"[知识库] {query}: 检索失败 - {e}")

        return {"research_results": all_results}

    return retrieve


async def error_handler(state: WorkflowState) -> dict:
    """错误处理节点：捕获搜索失败、超时等异常，生成友好错误响应"""
    error = state.get("error", "")
    search_errors = state.get("search_errors", [])

    if search_errors:
        error_detail = "; ".join(search_errors)
        logger.error(f"[ResearchWorkflow] error_handler: search_errors={error_detail}")
    else:
        logger.error(f"[ResearchWorkflow] error_handler: {error}")

    return {
        "final_response": f"研究过程中出现错误: {error or '; '.join(search_errors) or '未知错误'}",
        "error": None,  # 清除错误，避免循环
    }


def _make_route_after_search(has_retrieval: bool):
    """创建搜索后条件路由函数

    Args:
        has_retrieval: 是否启用了知识库检索节点
    """
    def route_after_search(
        state: WorkflowState,
    ) -> Literal["error_handler", "retrieve", "synthesize"]:
        search_errors = state.get("search_errors", [])
        error = state.get("error")

        # 搜索失败时路由到错误处理
        if search_errors or error:
            return "error_handler"

        # 有检索工具时先检索知识库
        if has_retrieval:
            return "retrieve"

        return "synthesize"

    return route_after_search


async def synthesize(state: WorkflowState) -> dict:
    """综合节点：将多个子查询检索结果综合成研究报告"""
    query = state.get("query", "")
    research_queries = state.get("research_queries", [])
    research_results = state.get("research_results", [])

    logger.info(
        f"[ResearchWorkflow] synthesize: {len(research_results)} results"
    )

    results_text = ""
    for i, (q, r) in enumerate(
        zip(research_queries, research_results), 1
    ):
        results_text += f"\n### 子查询 {i}: {q}\n{r}\n"

    if not results_text:
        results_text = "无检索结果"

    try:
        model = get_chat_model()
        prompt = SYNTHESIZE_PROMPT.format(query=query, results=results_text)
        response = await model.ainvoke([HumanMessage(content=prompt)])

        logger.info(
            f"[ResearchWorkflow] synthesize: {len(response.content)} chars"
        )
        return {
            "messages": [response],
            "final_response": response.content,
        }
    except Exception as e:
        logger.error(f"[ResearchWorkflow] synthesize error: {e}")
        return {"error": str(e), "final_response": f"综合研究时出错: {e}"}


async def respond(state: WorkflowState) -> dict:
    """响应节点：输出最终研究结果"""
    final_response = state.get("final_response") or ""
    error = state.get("error")

    if error and not final_response:
        final_response = f"研究过程中出现错误: {error}"

    if not final_response:
        messages = state.get("messages", [])
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and msg.content:
                final_response = msg.content
                break

    logger.info(f"[ResearchWorkflow] respond: {len(final_response)} chars")
    return {"final_response": final_response}


def build_research_workflow(
    search_tools: list[BaseTool] | None = None,
    retrieval_tools: list[BaseTool] | None = None,
) -> StateGraph:
    """构建 Research 工作流图

    Args:
        search_tools: 搜索工具列表（Tavily/DuckDuckGo），传入 ToolNode 执行
        retrieval_tools: 知识库检索工具列表，传入 ToolNode 执行
    """
    # 创建搜索 ToolNode
    search_tool_node = ToolNode(search_tools) if search_tools else None
    search_node = _create_search_node(search_tool_node)

    has_retrieval = bool(retrieval_tools)

    graph = StateGraph(WorkflowState)

    graph.add_node("decompose", decompose)
    graph.add_node("search", search_node)
    graph.add_node("synthesize", synthesize)
    graph.add_node("respond", respond)
    graph.add_node("error_handler", error_handler)

    # 仅在提供检索工具时添加 retrieve 节点
    if has_retrieval:
        retrieval_tool_node = ToolNode(retrieval_tools)
        retrieve_node = _create_retrieve_node(retrieval_tool_node)
        graph.add_node("retrieve", retrieve_node)

    graph.set_entry_point("decompose")

    # decompose → search_dispatcher → search（并行）
    graph.add_conditional_edges("decompose", search_dispatcher, ["search"])

    # search → 条件路由：error_handler / retrieve / synthesize
    route_after_search = _make_route_after_search(has_retrieval)

    if has_retrieval:
        graph.add_conditional_edges("search", route_after_search, {
            "error_handler": "error_handler",
            "retrieve": "retrieve",
            "synthesize": "synthesize",
        })
        graph.add_edge("retrieve", "synthesize")
    else:
        graph.add_conditional_edges("search", route_after_search, {
            "error_handler": "error_handler",
            "synthesize": "synthesize",
        })

    graph.add_edge("synthesize", "respond")
    graph.add_edge("error_handler", "respond")
    graph.add_edge("respond", END)

    return graph


def compile_research_workflow(
    search_tools: list[BaseTool] | None = None,
    retrieval_tools: list[BaseTool] | None = None,
    checkpointer=None,
    interrupt_before: list[str] | None = None,
    interrupt_after: list[str] | None = None,
):
    """编译 Research 工作流

    Args:
        search_tools: 搜索工具列表（Tavily/DuckDuckGo）
        retrieval_tools: 知识库检索工具列表
        checkpointer: LangGraph checkpointer 实例（用于持久化工作流状态）
        interrupt_before: 在指定节点之前中断
        interrupt_after: 在指定节点之后中断
    """
    graph = build_research_workflow(
        search_tools=search_tools,
        retrieval_tools=retrieval_tools,
    )

    compile_kwargs = {}
    if checkpointer is not None:
        compile_kwargs["checkpointer"] = checkpointer
    if interrupt_before:
        compile_kwargs["interrupt_before"] = interrupt_before
    if interrupt_after:
        compile_kwargs["interrupt_after"] = interrupt_after

    compiled = graph.compile(**compile_kwargs)

    # 附加配置信息
    compiled._workflow_config = {
        "has_checkpointer": checkpointer is not None,
        "checkpointer_type": type(checkpointer).__name__ if checkpointer else None,
        "has_search_tools": bool(search_tools),
        "has_retrieval_tools": bool(retrieval_tools),
        "interrupt_before": interrupt_before,
        "interrupt_after": interrupt_after,
    }

    return compiled


def create_research_with_checkpointer(
    search_tools: list[BaseTool] | None = None,
    retrieval_tools: list[BaseTool] | None = None,
    thread_id: str | None = None,
    use_postgres: bool = True,
    interrupt_before: list[str] | None = None,
    interrupt_after: list[str] | None = None,
):
    """创建带 checkpointer 的 Research 工作流

    Args:
        search_tools: 搜索工具列表
        retrieval_tools: 知识库检索工具列表
        thread_id: 会话线程 ID（用于 checkpointer 隔离）
        use_postgres: 是否使用 PostgreSQL checkpointer
        interrupt_before: 在指定节点之前中断
        interrupt_after: 在指定节点之后中断
    """
    from Django_xm.apps.ai_engine.services.checkpointer_factory import get_checkpointer

    backend = "postgres" if use_postgres else "sqlite"
    checkpointer = get_checkpointer(backend=backend)
    return compile_research_workflow(
        search_tools=search_tools,
        retrieval_tools=retrieval_tools,
        checkpointer=checkpointer,
        interrupt_before=interrupt_before,
        interrupt_after=interrupt_after,
    )
