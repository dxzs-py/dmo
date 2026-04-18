"""
学习工作流
基于 LangGraph 实现完整的学习流程，包含人机交互和条件分支
"""

import logging
from datetime import datetime
from typing import Literal, Dict, Any, Optional

from langchain_core.messages import HumanMessage, AIMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver

from Django_xm.apps.core.config import get_logger
from .state import StudyFlowState
from .nodes import (
    planner_node,
    retrieval_node,
    quiz_generator_node,
    grading_node,
    feedback_node
)
from .persistence import get_persistence_service

logger = get_logger(__name__)
persistence_service = get_persistence_service()


def should_continue(state: StudyFlowState) -> Literal["retry", "end"]:
    should_retry = state.get("should_retry", False)
    retry_count = state.get("retry_count", 0)

    logger.info(f"[Conditional Edge] should_retry={should_retry}, retry_count={retry_count}")

    if should_retry and retry_count < 3:
        logger.info("[Conditional Edge] 决定重新出题")
        return "retry"
    else:
        logger.info("[Conditional Edge] 决定结束流程")
        return "end"


def human_review_node(state: StudyFlowState) -> Dict[str, Any]:
    logger.info("[Human Review Node] 等待用户提交答案...")
    return {
        "current_step": "waiting_for_answers",
        "updated_at": datetime.now().isoformat()
    }


class StudyFlow:
    def __init__(
        self,
        thread_id: str = None,
        checkpointer: Any = None,
        **kwargs,
    ):
        self.thread_id = thread_id
        logger.info(f"初始化学习工作流: thread_id={thread_id}")

        if checkpointer is None:
            checkpointer = MemorySaver()
        self.checkpointer = checkpointer

        self.graph = self._build_graph()
        logger.info("学习工作流初始化完成")

    def _build_graph(self):
        workflow = StateGraph(StudyFlowState)

        workflow.add_node("planner", planner_node)
        workflow.add_node("retrieval", retrieval_node)
        workflow.add_node("quiz_generator", quiz_generator_node)
        workflow.add_node("human_review", human_review_node)
        workflow.add_node("grading", grading_node)
        workflow.add_node("feedback", feedback_node)

        workflow.set_entry_point("planner")
        workflow.add_edge("planner", "retrieval")
        workflow.add_edge("retrieval", "quiz_generator")
        workflow.add_edge("quiz_generator", "human_review")
        workflow.add_edge("human_review", "grading")
        workflow.add_edge("grading", "feedback")

        workflow.add_conditional_edges(
            "feedback",
            should_continue,
            {
                "retry": "quiz_generator",
                "end": END
            }
        )

        return workflow.compile(checkpointer=self.checkpointer, interrupt_before=["human_review"])

    def invoke(self, inputs: Dict[str, Any], config: Dict[str, Any] = None):
        if self.thread_id:
            if config is None:
                config = {}
            config["configurable"] = {"thread_id": self.thread_id}

        return self.graph.invoke(inputs, config=config)

    async def ainvoke(self, inputs: Dict[str, Any], config: Dict[str, Any] = None):
        if self.thread_id:
            if config is None:
                config = {}
            config["configurable"] = {"thread_id": self.thread_id}

        return await self.graph.ainvoke(inputs, config=config)

    def stream(self, inputs: Dict[str, Any], config: Dict[str, Any] = None):
        if self.thread_id:
            if config is None:
                config = {}
            config["configurable"] = {"thread_id": self.thread_id}

        return self.graph.stream(inputs, config=config)

    def get_state(self, thread_id: str = None):
        tid = thread_id or self.thread_id
        if not tid:
            raise ValueError("thread_id is required")
        return self.graph.get_state(config={"configurable": {"thread_id": tid}})

    def update_state(self, thread_id: str, new_state: Dict[str, Any]):
        tid = thread_id or self.thread_id
        if not tid:
            raise ValueError("thread_id is required")
        self.graph.update_state(
            config={"configurable": {"thread_id": tid}},
            values=new_state
        )


def create_study_flow(thread_id: str = None, checkpointer: Any = None) -> StudyFlow:
    return StudyFlow(thread_id=thread_id, checkpointer=checkpointer)


_study_flow_cache: Dict[str, StudyFlow] = {}


def _get_study_flow(thread_id: str) -> StudyFlow:
    if thread_id not in _study_flow_cache:
        _study_flow_cache[thread_id] = StudyFlow(thread_id=thread_id)
    return _study_flow_cache[thread_id]


def start_study_flow(
    user_question: str, 
    thread_id: str, 
    user_id: Optional[int] = None
) -> dict:
    """
    启动新的学习工作流

    Args:
        user_question: 用户的学习问题
        thread_id: 线程 ID（用于标识会话）
        user_id: 用户ID（可选，用于持久化）

    Returns:
        工作流执行结果
    """
    logger.info(f"[Study Flow] 启动新的学习工作流，thread_id={thread_id}")

    study_flow = _get_study_flow(thread_id)

    initial_state: StudyFlowState = {
        "messages": [],
        "user_question": user_question,
        "learning_plan": None,
        "retrieved_docs": None,
        "quiz": None,
        "user_answers": None,
        "score": None,
        "score_details": None,
        "feedback": None,
        "retry_count": 0,
        "should_retry": False,
        "current_step": "start",
        "thread_id": thread_id,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "error": None,
        "error_node": None
    }

    config = {
        "configurable": {
            "thread_id": thread_id
        }
    }

    logger.info("[Study Flow] 开始执行工作流...")
    result = study_flow.invoke(initial_state, config)

    logger.info(f"[Study Flow] 工作流暂停在: {result.get('current_step')}")

    persistence_service.save_workflow_state(thread_id, result, user_id)

    return result


def submit_answers(
    thread_id: str, 
    user_answers: dict,
    user_id: Optional[int] = None
) -> dict:
    """
    提交用户答案，继续执行工作流

    Args:
        thread_id: 线程 ID
        user_answers: 用户答案字典 {"q1": "A", "q2": "xxx", ...}
        user_id: 用户ID（可选，用于持久化）

    Returns:
        工作流执行结果
    """
    logger.info(f"[Study Flow] 提交答案，thread_id={thread_id}")

    study_flow = _get_study_flow(thread_id)

    config = {
        "configurable": {
            "thread_id": thread_id
        }
    }

    current_state = study_flow.get_state(thread_id)
    logger.info(f"[Study Flow] 当前状态: {current_state.values.get('current_step')}")

    study_flow.update_state(
        thread_id,
        {
            "user_answers": user_answers,
            "updated_at": datetime.now().isoformat()
        }
    )

    logger.info("[Study Flow] 继续执行工作流...")
    study_flow.invoke(None, config=config)

    result = get_workflow_state(thread_id)

    logger.info(f"[Study Flow] 工作流执行完成，最终状态: {result.get('current_step') if result else 'unknown'}")

    if result:
        persistence_service.save_workflow_state(thread_id, result, user_id)

    return result


def get_workflow_state(thread_id: str) -> dict:
    """
    获取工作流的当前状态

    Args:
        thread_id: 线程 ID

    Returns:
        当前状态字典
    """
    logger.info(f"[Study Flow] 获取工作流状态，thread_id={thread_id}")

    study_flow = _get_study_flow(thread_id)

    config = {
        "configurable": {
            "thread_id": thread_id
        }
    }

    try:
        state = study_flow.get_state(thread_id)
        if state and state.values:
            return state.values
    except Exception as e:
        logger.warning(f"[Study Flow] 从内存获取状态失败: {e}")

    logger.info(f"[Study Flow] 尝试从持久化服务恢复状态，thread_id={thread_id}")
    saved_state = persistence_service.load_workflow_state(thread_id)
    if saved_state:
        study_flow = _get_study_flow(thread_id)
        study_flow.graph.update_state(
            config={"configurable": {"thread_id": thread_id}},
            values=saved_state
        )
        return saved_state

    return None


def get_workflow_history(thread_id: str) -> list:
    """
    获取工作流的执行历史

    Args:
        thread_id: 线程 ID

    Returns:
        历史状态列表
    """
    logger.info(f"[Study Flow] 获取工作流历史，thread_id={thread_id}")

    study_flow = _get_study_flow(thread_id)

    config = {
        "configurable": {
            "thread_id": thread_id
        }
    }

    history = []
    try:
        for state in study_flow.graph.get_state_history(config):
            history.append({
                "step": state.metadata.get("step") if hasattr(state, "metadata") else None,
                "values": state.values if hasattr(state, "values") else state,
            })
    except Exception as e:
        logger.warning(f"[Study Flow] 获取历史失败: {e}")

    return history


def get_study_flow_app(thread_id: str = None) -> StudyFlow:
    """
    获取工作流实例（兼容性别名）

    Args:
        thread_id: 线程 ID

    Returns:
        StudyFlow 实例
    """
    if thread_id:
        return _get_study_flow(thread_id)
    return StudyFlow()