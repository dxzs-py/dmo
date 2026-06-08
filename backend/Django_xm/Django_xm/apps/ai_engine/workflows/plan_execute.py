import json
from typing import Literal

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

from Django_xm.apps.ai_engine.config import get_logger
from Django_xm.apps.ai_engine.prompts.plan_execute_prompts import PLAN_PROMPT, REFLECT_PROMPT, RESPOND_PROMPT
from Django_xm.apps.ai_engine.services.llm_factory import get_chat_model
from .state import WorkflowState, PlanModel, PlanStep

logger = get_logger(__name__)


def _parse_json_response(text: str) -> dict:
    """解析 LLM 返回的 JSON 文本（用于 reflect 节点的评估结果解析）"""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
    return {}


async def plan(state: WorkflowState) -> dict:
    """规划节点：使用 with_structured_output 生成结构化 PlanModel"""
    query = state.get("query", "")
    logger.info(f"[PlanExecute] plan: query={query[:80]}")

    try:
        model = get_chat_model(temperature=0.3)
        structured_model = model.with_structured_output(PlanModel)
        prompt = PLAN_PROMPT.format(query=query)
        plan_result: PlanModel = await structured_model.ainvoke([HumanMessage(content=prompt)])

        if not plan_result.steps:
            plan_result = PlanModel(
                steps=[PlanStep(description=f"回答: {query}", tool="none")],
                total_steps=1,
            )

        logger.info(f"[PlanExecute] plan generated: {plan_result.total_steps} steps")
        return {
            "plan": plan_result,
            "current_step": 0,
        }
    except Exception as e:
        logger.error(f"[PlanExecute] plan error: {e}")
        return {"error": str(e), "plan": None}


def _create_execute_node(tool_node: ToolNode):
    """创建使用 ToolNode 的 execute 节点，利用其原生工具调用和错误处理机制"""

    async def execute(state: WorkflowState) -> dict:
        plan_data: PlanModel | None = state.get("plan")
        current_step: int = state.get("current_step", 0)

        if not plan_data:
            return {"error": "无执行计划"}

        steps = plan_data.steps
        if current_step >= len(steps):
            return {"current_step": current_step}

        step: PlanStep = steps[current_step]
        step_description = step.description
        tool_name = step.tool

        logger.info(
            f"[PlanExecute] execute step {current_step + 1}/{len(steps)}: "
            f"{step_description[:60]}, tool={tool_name}"
        )

        # 使用 ToolNode 执行工具调用
        if tool_name and tool_name != "none":
            # 构造 AIMessage 包含 tool_calls，供 ToolNode 处理
            ai_message = AIMessage(
                content="",
                tool_calls=[{
                    "name": tool_name,
                    "args": step.args or {},
                    "id": f"plan_step_{current_step}",
                    "type": "tool_call",
                }],
            )

            # ToolNode 自动处理工具调用和异常，将错误作为 ToolMessage 返回
            result = await tool_node.ainvoke({"messages": [ai_message]})

            # 从 ToolMessage 中提取结果
            tool_result = ""
            for msg in result.get("messages", []):
                if isinstance(msg, ToolMessage):
                    tool_result = msg.content
                    if msg.status == "error":
                        logger.warning(
                            f"[PlanExecute] tool '{tool_name}' returned error: {tool_result}"
                        )
                    else:
                        logger.info(f"[PlanExecute] tool '{tool_name}' executed successfully")
                    break

            tool_results = list(state.get("tool_results", []))
            tool_results.append({
                "step": current_step + 1,
                "description": step_description,
                "result": str(tool_result),
                "tool": tool_name,
            })

            return {
                "current_step": current_step + 1,
                "tool_results": tool_results,
            }

        # 无工具调用，直接使用步骤描述作为结果
        tool_results = list(state.get("tool_results", []))
        tool_results.append({
            "step": current_step + 1,
            "description": step_description,
            "result": step_description,
            "tool": "none",
        })

        return {
            "current_step": current_step + 1,
            "tool_results": tool_results,
        }

    return execute


async def reflect(state: WorkflowState) -> dict:
    """反思节点：评估执行结果，支持修正计划"""
    plan_data: PlanModel | None = state.get("plan")
    current_step: int = state.get("current_step", 0)
    tool_results = state.get("tool_results", [])
    query = state.get("query", "")
    error = state.get("error")

    if error:
        logger.info("[PlanExecute] reflect: error detected -> error")
        return {"error": error}

    if not plan_data:
        return {}

    steps = plan_data.steps
    total_steps = len(steps)

    if current_step >= total_steps:
        logger.info("[PlanExecute] reflect: all steps completed -> complete")
        return {}

    step = steps[current_step - 1] if current_step > 0 else PlanStep(description="", tool="none")
    last_result = tool_results[-1] if tool_results else {}

    try:
        model = get_chat_model(temperature=0.2)
        plan_str = plan_data.model_dump_json(indent=2)
        prompt = REFLECT_PROMPT.format(
            query=query,
            plan=plan_str,
            current_step=current_step,
            total_steps=total_steps,
            step_description=step.description,
            result=last_result.get("result", ""),
        )
        response = await model.ainvoke([HumanMessage(content=prompt)])
        assessment = _parse_json_response(response.content)

        status = assessment.get("status", "continue")
        logger.info(f"[PlanExecute] reflect: status={status}")

        if status == "revise" and assessment.get("revised_plan"):
            revised = assessment["revised_plan"]
            if isinstance(revised, dict):
                try:
                    revised_plan = PlanModel.model_validate(revised)
                    return {"plan": revised_plan}
                except Exception as e:
                    logger.warning(f"[PlanExecute] 修正计划验证失败: {e}")

        return {}
    except Exception as e:
        logger.warning(f"[PlanExecute] reflect error, defaulting to continue: {e}")
        return {}


def _should_continue(state: WorkflowState) -> Literal["execute", "respond", "error"]:
    """条件路由：根据状态决定下一步"""
    error = state.get("error")
    if error:
        return "error"

    plan_data: PlanModel | None = state.get("plan")
    current_step: int = state.get("current_step", 0)

    if not plan_data:
        return "error"

    total_steps = len(plan_data.steps)

    if current_step >= total_steps:
        return "respond"

    return "execute"


async def respond(state: WorkflowState) -> dict:
    """响应节点：生成最终回答"""
    query = state.get("query", "")
    plan_data: PlanModel | None = state.get("plan")
    tool_results = state.get("tool_results", [])

    try:
        model = get_chat_model()
        plan_str = plan_data.model_dump_json(indent=2) if plan_data else ""
        prompt = RESPOND_PROMPT.format(
            query=query,
            plan=plan_str,
            results=json.dumps(tool_results, ensure_ascii=False, indent=2),
        )
        response = await model.ainvoke([HumanMessage(content=prompt)])

        logger.info(f"[PlanExecute] respond: {len(response.content)} chars")
        return {
            "messages": [response],
            "final_response": response.content,
        }
    except Exception as e:
        logger.error(f"[PlanExecute] respond error: {e}")
        return {"error": str(e), "final_response": f"生成最终回答时出错: {e}"}


async def error_handler(state: WorkflowState) -> dict:
    """错误处理节点"""
    error = state.get("error", "未知错误")
    logger.error(f"[PlanExecute] error_handler: {error}")
    return {
        "final_response": f"执行过程中出现错误: {error}",
        "error": None,
    }


def build_plan_execute_workflow(tools: list[BaseTool] | None = None) -> StateGraph:
    """构建 Plan-Execute 工作流图

    Args:
        tools: 可用工具列表，将传入 ToolNode 进行原生工具调用
    """
    # 创建 ToolNode 实例，利用其原生工具调用和错误处理机制
    tool_node = ToolNode(tools or [])
    execute_node = _create_execute_node(tool_node)

    graph = StateGraph(WorkflowState)

    graph.add_node("plan", plan)
    graph.add_node("execute", execute_node)
    graph.add_node("reflect", reflect)
    graph.add_node("respond", respond)
    graph.add_node("error", error_handler)

    graph.set_entry_point("plan")

    graph.add_edge("plan", "execute")
    graph.add_edge("execute", "reflect")

    graph.add_conditional_edges(
        "reflect",
        _should_continue,
        {
            "execute": "execute",
            "respond": "respond",
            "error": "error",
        },
    )

    graph.add_edge("respond", END)
    graph.add_edge("error", END)

    return graph


def compile_plan_execute_workflow(
    tools: list[BaseTool] | None = None,
    checkpointer=None,
    interrupt_before: list[str] | None = None,
    interrupt_after: list[str] | None = None,
):
    """编译 Plan-Execute 工作流

    Args:
        tools: 可用工具列表，将传入 ToolNode 进行原生工具调用
        checkpointer: LangGraph checkpointer 实例（用于持久化工作流状态）
        interrupt_before: 在指定节点之前中断（如 ["execute"] 用于人工审批）
        interrupt_after: 在指定节点之后中断
    """
    graph = build_plan_execute_workflow(tools)

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
        "interrupt_before": interrupt_before,
        "interrupt_after": interrupt_after,
    }

    return compiled


def create_plan_execute_with_checkpointer(
    tools: list[BaseTool] | None = None,
    thread_id: str | None = None,
    use_postgres: bool = True,
    interrupt_before: list[str] | None = None,
    interrupt_after: list[str] | None = None,
):
    """创建带 checkpointer 的 Plan-Execute 工作流

    Args:
        tools: 可用工具列表，将传入 ToolNode 进行原生工具调用
        thread_id: 会话线程 ID（用于 checkpointer 隔离）
        use_postgres: 是否使用 PostgreSQL checkpointer
        interrupt_before: 在指定节点之前中断
        interrupt_after: 在指定节点之后中断
    """
    from Django_xm.apps.ai_engine.services.checkpointer_factory import get_checkpointer

    backend = "postgres" if use_postgres else "sqlite"
    checkpointer = get_checkpointer(backend=backend)
    return compile_plan_execute_workflow(
        tools=tools,
        checkpointer=checkpointer,
        interrupt_before=interrupt_before,
        interrupt_after=interrupt_after,
    )
