"""
学习工作流
基于 LangGraph 实现完整的学习流程，包含人机交互和条件分支
"""

import logging
import time
from collections import OrderedDict
from datetime import datetime
from decimal import Decimal
from typing import Literal, Dict, Any, Optional

from langchain_core.messages import HumanMessage, AIMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from Django_xm.apps.ai_engine.services.token_counter import TokenUsageCallbackHandler
from Django_xm.apps.ai_engine.services.checkpointer_factory import get_checkpointer

from Django_xm.apps.config_center.config import get_logger
from Django_xm.apps.ai_engine.services.cost_tracker import create_cost_tracker
from Django_xm.apps.ai_engine.services.usage_tracker import create_usage_tracker
from .state import StudyFlowState
from ..nodes import (
    planner_node,
    retrieval_node,
    quiz_generator_node,
    grading_node,
    feedback_node
)
from .persistence_service import get_persistence_service

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
            checkpointer = get_checkpointer()
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


_study_flow_cache: OrderedDict[str, tuple] = OrderedDict()
_STUDY_FLOW_CACHE_MAXSIZE = 64
_STUDY_FLOW_CACHE_TTL = 7200


def _is_cache_entry_valid(entry: tuple) -> bool:
    if len(entry) != 2:
        return False
    _, created_at = entry
    return (time.time() - created_at) < _STUDY_FLOW_CACHE_TTL


def _get_study_flow(thread_id: str) -> StudyFlow:
    if thread_id in _study_flow_cache:
        entry = _study_flow_cache[thread_id]
        if _is_cache_entry_valid(entry):
            _study_flow_cache.move_to_end(thread_id)
            return entry[0]
        else:
            _study_flow_cache.pop(thread_id, None)
            logger.debug(f"StudyFlow 缓存已过期，淘汰: {thread_id}")

    if len(_study_flow_cache) >= _STUDY_FLOW_CACHE_MAXSIZE:
        evicted_key, _ = _study_flow_cache.popitem(last=False)
        logger.debug(f"StudyFlow 缓存已满，LRU淘汰: {evicted_key}")
    study_flow = StudyFlow(thread_id=thread_id)
    _study_flow_cache[thread_id] = (study_flow, time.time())
    return study_flow


def _get_cached_study_flow(thread_id: str) -> StudyFlow:
    """
    获取 StudyFlow 实例，优先从进程内缓存，回退到 Redis 缓存重建

    Redis 缓存仅存储 thread_id -> 存在性标记和配置信息，
    StudyFlow 对象本身（含编译后的 LangGraph）在进程内缓存。

    进程内缓存带 TTL 过期机制，防止长时间运行导致内存泄漏。
    """
    if thread_id in _study_flow_cache:
        entry = _study_flow_cache[thread_id]
        if _is_cache_entry_valid(entry):
            _study_flow_cache.move_to_end(thread_id)
            return entry[0]
        else:
            _study_flow_cache.pop(thread_id, None)
            logger.debug(f"StudyFlow 缓存已过期，淘汰: {thread_id}")

    from django.core.cache import cache

    cache_key = f"study_flow:active:{thread_id}"
    cached_meta = cache.get(cache_key)

    if cached_meta is not None:
        logger.debug(f"从 Redis 恢复 StudyFlow 元数据: {thread_id}")

    study_flow = StudyFlow(thread_id=thread_id)

    if len(_study_flow_cache) >= _STUDY_FLOW_CACHE_MAXSIZE:
        evicted_key, _ = _study_flow_cache.popitem(last=False)
        logger.debug(f"StudyFlow 缓存已满，LRU淘汰: {evicted_key}")

    _study_flow_cache[thread_id] = (study_flow, time.time())

    cache.set(cache_key, {"thread_id": thread_id, "created": True}, timeout=_STUDY_FLOW_CACHE_TTL)

    return study_flow


def _invalidate_study_flow_cache(thread_id: str) -> None:
    """清除 StudyFlow 的进程内和 Redis 缓存"""
    _study_flow_cache.pop(thread_id, None)

    from django.core.cache import cache
    cache.delete(f"study_flow:active:{thread_id}")
    logger.debug(f"StudyFlow 缓存已清除: {thread_id}")


def start_study_flow(
    user_question: str, 
    thread_id: str, 
    user_id: Optional[int] = None
) -> dict:
    logger.info(f"[Study Flow] 启动新的学习工作流，thread_id={thread_id}")

    study_flow = _get_cached_study_flow(thread_id)

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

    cb = TokenUsageCallbackHandler()

    config = {
        "configurable": {
            "thread_id": thread_id
        },
        "callbacks": [cb],
    }

    logger.info("[Study Flow] 开始执行工作流...")
    start_time = time.time()
    result = study_flow.invoke(initial_state, config)

    logger.info(f"[Study Flow] 工作流暂停在: {result.get('current_step')}")

    total_cost = cb.total_cost
    total_tokens = cb.prompt_tokens + cb.completion_tokens
    response_time = round(time.time() - start_time, 2)

    _update_workflow_session_cost(thread_id, total_cost, total_tokens, response_time)

    persistence_service.save_workflow_state(thread_id, result, user_id)

    return result


def submit_answers(
    thread_id: str, 
    user_answers: dict,
    user_id: Optional[int] = None
) -> dict:
    logger.info(f"[Study Flow] 提交答案，thread_id={thread_id}")

    study_flow = _get_cached_study_flow(thread_id)

    config = {
        "configurable": {
            "thread_id": thread_id
        }
    }

    current_state = study_flow.get_state(thread_id)
    if not current_state or not current_state.values or not current_state.values.get('current_step'):
        logger.info(f"[Study Flow] 内存中无状态，从持久化恢复，thread_id={thread_id}")
        saved_state = persistence_service.load_workflow_state(thread_id)
        if saved_state:
            study_flow.graph.update_state(
                config={"configurable": {"thread_id": thread_id}},
                values=saved_state
            )
            current_state = study_flow.get_state(thread_id)
        else:
            raise ValueError(f"工作流状态不存在: {thread_id}")

    logger.info(f"[Study Flow] 当前状态: {current_state.values.get('current_step')}")
    
    study_flow.update_state(
        thread_id,
        {
            "user_answers": user_answers,
            "updated_at": datetime.now().isoformat()
        }
    )

    logger.info("[Study Flow] 继续执行工作流...")
    start_time = time.time()

    cb = TokenUsageCallbackHandler()
    invoke_config = {
        "configurable": {
            "thread_id": thread_id
        },
        "callbacks": [cb],
    }

    study_flow.invoke(None, config=invoke_config)

    result = get_workflow_state(thread_id)

    logger.info(f"[Study Flow] 工作流执行完成，最终状态: {result.get('current_step') if result else 'unknown'}")

    if result:
        total_cost = cb.total_cost
        total_tokens = cb.prompt_tokens + cb.completion_tokens
        response_time = round(time.time() - start_time, 2)
        _update_workflow_session_cost(thread_id, total_cost, total_tokens, response_time, is_incremental=True)

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

    study_flow = _get_cached_study_flow(thread_id)

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

    study_flow = _get_cached_study_flow(thread_id)

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
    if thread_id:
        return _get_cached_study_flow(thread_id)
    return StudyFlow()


def _update_workflow_session_cost(
    thread_id: str,
    total_cost: float,
    total_tokens: int,
    response_time: float,
    is_incremental: bool = False,
):
    try:
        from ..models import WorkflowSession
        session = WorkflowSession.objects.filter(thread_id=thread_id, is_deleted=False).first()
        if not session:
            logger.warning(f"[Study Flow] 未找到工作流会话: {thread_id}")
            return

        from Django_xm.apps.ai_engine.config import settings as ai_settings
        model_name = getattr(ai_settings, 'openai_model', 'gpt-4o')

        if is_incremental:
            session.token_count = (session.token_count or 0) + total_tokens
            session.cost = (session.cost or 0) + Decimal(str(round(total_cost, 6)))
            session.response_time = (session.response_time or 0) + response_time
        else:
            session.model = model_name
            session.token_count = total_tokens
            session.cost = Decimal(str(round(total_cost, 6)))
            session.response_time = response_time

        session.save(update_fields=['model', 'token_count', 'cost', 'response_time'])
        logger.info(f"[Study Flow] 更新工作流成本: thread_id={thread_id}, cost=${total_cost:.4f}, tokens={total_tokens}")
    except Exception as e:
        logger.warning(f"[Study Flow] 更新工作流会话成本失败: {e}")