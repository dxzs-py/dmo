"""
安全学习工作流 - 集成 Guardrails 的学习工作流
"""

import logging
from typing import Literal

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from .state import StudyFlowState
from .nodes import (
    planner_node,
    retrieval_node,
    quiz_generator_node,
    grading_node,
    feedback_node
)
from .safe_nodes import (
    create_safe_node,
    create_human_review_node,
)
from Django_xm.apps.ai_engine.config import settings, get_logger

logger = get_logger(__name__)


def should_continue(state: StudyFlowState) -> Literal["retry", "end"]:
    should_retry = state.get("should_retry", False)
    retry_count = state.get("retry_count", 0)

    if state.get("validation_failed", False):
        logger.error("[Safe Flow] 检测到验证失败，终止流程")
        return "end"

    if should_retry and retry_count < 3:
        return "retry"
    else:
        return "end"


def create_safe_study_flow_graph(
    checkpointer_path: str = None,
    enable_human_review: bool = True,
    strict_mode: bool = False,
):
    logger.info("[Safe Study Flow] 开始创建安全学习工作流图")
    logger.info(f"   人工审核: {enable_human_review}")
    logger.info(f"   严格模式: {strict_mode}")

    workflow = StateGraph(StudyFlowState)

    safe_planner = create_safe_node(
        planner_node,
        validate_input=True,
        validate_output=True,
        input_field="question",
        output_field="plan",
        strict_mode=strict_mode,
    )

    safe_retrieval = create_safe_node(
        retrieval_node,
        validate_input=False,
        validate_output=True,
        output_field="retrieved_docs",
        require_sources=False,
        strict_mode=strict_mode,
    )

    safe_quiz_generator = create_safe_node(
        quiz_generator_node,
        validate_input=False,
        validate_output=True,
        output_field="quiz",
        strict_mode=strict_mode,
    )

    safe_grading = create_safe_node(
        grading_node,
        validate_input=True,
        validate_output=True,
        input_field="answers",
        output_field="score",
        strict_mode=strict_mode,
    )

    safe_feedback = create_safe_node(
        feedback_node,
        validate_input=False,
        validate_output=True,
        output_field="feedback",
        strict_mode=strict_mode,
    )

    logger.info("[Safe Study Flow] 添加安全节点...")

    workflow.add_node("planner", safe_planner)
    workflow.add_node("retrieval", safe_retrieval)
    workflow.add_node("quiz_generator", safe_quiz_generator)
    workflow.add_node("grading", safe_grading)
    workflow.add_node("feedback", safe_feedback)

    if enable_human_review:
        human_review = create_human_review_node(
            review_field="plan",
            approval_required=True,
        )
        workflow.add_node("human_review", human_review)
        logger.info("[Safe Study Flow] 已添加人工审核节点")

    logger.info("[Safe Study Flow] 定义工作流边...")

    workflow.set_entry_point("planner")

    if enable_human_review:
        workflow.add_edge("planner", "human_review")
        workflow.add_edge("human_review", "retrieval")
    else:
        workflow.add_edge("planner", "retrieval")

    workflow.add_edge("retrieval", "quiz_generator")
    workflow.add_edge("quiz_generator", "grading")
    workflow.add_edge("grading", "feedback")

    workflow.add_conditional_edges(
        "feedback",
        should_continue,
        {
            "retry": "quiz_generator",
            "end": END,
        }
    )

    logger.info("[Safe Study Flow] 编译工作流...")

    if checkpointer_path:
        from langgraph.checkpoint.sqlite import SqliteSaver
        checkpointer = SqliteSaver.from_conn_string(checkpointer_path)
        logger.info(f"[Safe Study Flow] 使用 SQLite 检查点: {checkpointer_path}")
    else:
        checkpointer = MemorySaver()
        logger.info("[Safe Study Flow] 使用内存检查点")

    compiled_graph = workflow.compile(checkpointer=checkpointer)

    logger.info("[Safe Study Flow] ✅ 安全学习工作流图创建完成")

    return compiled_graph


def create_default_safe_flow():
    import os

    checkpoint_dir = os.path.join(getattr(settings, 'DATA_DIR', '.'), "checkpoints", "safe_study_flow")
    os.makedirs(checkpoint_dir, exist_ok=True)
    checkpoint_path = os.path.join(checkpoint_dir, "safe_study_flow.db")

    return create_safe_study_flow_graph(
        checkpointer_path=checkpoint_path,
        enable_human_review=True,
        strict_mode=False,
    )


def run_safe_study_flow(
    question: str,
    thread_id: str = "default",
    enable_human_review: bool = True,
    strict_mode: bool = False,
):
    logger.info(f"[Safe Study Flow] 运行安全工作流: {question}")

    graph = create_safe_study_flow_graph(
        enable_human_review=enable_human_review,
        strict_mode=strict_mode,
    )

    config = {
        "configurable": {
            "thread_id": thread_id,
        }
    }

    initial_state = {
        "question": question,
        "messages": [],
        "plan": "",
        "retrieved_docs": [],
        "quiz": "",
        "answers": "",
        "score": 0,
        "feedback": "",
        "should_retry": False,
        "retry_count": 0,
        "validation_failed": False,
        "warnings": [],
    }

    try:
        result = graph.invoke(initial_state, config)
        logger.info("[Safe Study Flow] ✅ 工作流执行完成")
        return result
    except Exception as e:
        logger.error(f"[Safe Study Flow] ❌ 工作流执行失败: {e}")
        raise


async def stream_safe_study_flow(
    question: str,
    thread_id: str = "default",
    enable_human_review: bool = True,
    strict_mode: bool = False,
):
    logger.info(f"[Safe Study Flow] 流式运行安全工作流: {question}")

    graph = create_safe_study_flow_graph(
        enable_human_review=enable_human_review,
        strict_mode=strict_mode,
    )

    config = {
        "configurable": {
            "thread_id": thread_id,
        }
    }

    initial_state = {
        "question": question,
        "messages": [],
        "plan": "",
        "retrieved_docs": [],
        "quiz": "",
        "answers": "",
        "score": 0,
        "feedback": "",
        "should_retry": False,
        "retry_count": 0,
        "validation_failed": False,
        "warnings": [],
    }

    try:
        async for chunk in graph.astream(initial_state, config):
            yield chunk

        logger.info("[Safe Study Flow] ✅ 流式执行完成")
    except Exception as e:
        logger.error(f"[Safe Study Flow] ❌ 流式执行失败: {e}")
        raise