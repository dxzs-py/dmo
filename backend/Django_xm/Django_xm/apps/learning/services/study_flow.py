"""
学习工作流
基于 LangGraph 实现完整的学习流程，包含人机交互和条件分支
"""

import time
import uuid
from collections import OrderedDict
from typing import Any, Literal

from django.utils import timezone
from langgraph.graph import END, StateGraph

from Django_xm.apps.ai_engine.services.checkpointer_factory import get_checkpointer
from Django_xm.apps.ai_engine.services.token_counter import TokenUsageCallbackHandler
from Django_xm.apps.core.config import get_logger
from Django_xm.common.event_schema import EventSource, EventType
from Django_xm.common.realtime_events import publish_event_sync

from ..nodes import feedback_node, grading_node, planner_node, quiz_generator_node, retrieval_node
from .persistence_service import get_persistence_service
from .resilience import ainvoke_with_resilience, invoke_with_resilience, stream_with_resilience
from .state import StudyFlowState

logger = get_logger(__name__)
persistence_service = get_persistence_service()

# 允许提交答案的步骤（等待答题 / 执行中的中间态）
_ALLOWED_SUBMIT_STEPS = {
    "start",
    "planner",
    "retrieval",
    "quiz_generator",
    "waiting_for_answers",
    "grading",
    "feedback",
}


class WorkflowAlreadyFinishedError(Exception):
    """工作流已结束，无法提交答案。

    Attributes:
        current_phase: 触发时的当前阶段（用于 409 响应提示）
    """

    def __init__(self, message: str, current_phase: str = ""):
        super().__init__(message)
        self.current_phase = current_phase


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


def human_review_node(state: StudyFlowState) -> dict[str, Any]:
    logger.info("[Human Review Node] 等待用户提交答案...")
    return {"current_step": "waiting_for_answers", "updated_at": timezone.now().isoformat()}


class StudyFlow:
    def __init__(
        self,
        thread_id: str | None = None,
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

        workflow.add_conditional_edges("feedback", should_continue, {"retry": "quiz_generator", "end": END})

        return workflow.compile(checkpointer=self.checkpointer, interrupt_before=["human_review"])

    def invoke(self, inputs: dict[str, Any], config: dict[str, Any] | None = None):
        if self.thread_id:
            if config is None:
                config = {}
            config["configurable"] = {"thread_id": self.thread_id}

        return invoke_with_resilience(self.graph, inputs, config)

    async def ainvoke(self, inputs: dict[str, Any], config: dict[str, Any] | None = None):
        if self.thread_id:
            if config is None:
                config = {}
            config["configurable"] = {"thread_id": self.thread_id}

        return await ainvoke_with_resilience(self.graph, inputs, config)

    def stream(self, inputs: dict[str, Any], config: dict[str, Any] | None = None, stream_mode: str = "values"):
        if self.thread_id:
            if config is None:
                config = {}
            config["configurable"] = {"thread_id": self.thread_id}

        return stream_with_resilience(self.graph, inputs, config, stream_mode=stream_mode)

    def get_state(self, thread_id: str | None = None):
        tid = thread_id or self.thread_id
        if not tid:
            raise ValueError("thread_id is required")
        return self.graph.get_state(config={"configurable": {"thread_id": tid}})

    def update_state(self, thread_id: str, new_state: dict[str, Any]):
        tid = thread_id or self.thread_id
        if not tid:
            raise ValueError("thread_id is required")
        self.graph.update_state(config={"configurable": {"thread_id": tid}}, values=new_state)


def create_study_flow(thread_id: str | None = None, checkpointer: Any = None) -> StudyFlow:
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


def _safe_publish(
    event_type: EventType,
    payload: dict,
    task_id: str | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
) -> None:
    """安全发布事件，吞掉异常以避免影响主流程。"""
    try:
        publish_event_sync(
            event_type,
            payload,
            task_id=task_id,
            session_id=session_id,
            user_id=user_id,
        )
    except Exception as e:
        logger.warning(f"[Study Flow] publish_event_sync 失败: {event_type}, {e}")


def _ensure_session_created(
    thread_id: str,
    user_question: str,
    user_id: int | None,
    knowledge_base_ids: list | None,
    provider_id: str | None,
    model_name: str | None,
    temperature: float | None,
    max_tokens: int | None,
    special_params: dict | None,
    enable_deep_thinking: bool = False,
    use_web_search: bool = False,
    learning_plan: dict | None = None,
    retry_count: int = 0,
    root_thread_id: str | None = None,
) -> None:
    """预创建 WorkflowSession（幂等），供节点持久化题目等依赖 session 的流程使用。

    必须早于图执行：quiz_generator_node._persist_questions 依赖该记录定位 session，
    否则题目无法落库（练习历史/修改答案随之失效）。restart_quiz 同样调用此函数。
    """
    from ..models import WorkflowSession

    WorkflowSession.objects.update_or_create(
        thread_id=thread_id,
        defaults={
            "user_question": user_question,
            "learning_plan": learning_plan,
            "retry_count": retry_count,
            "current_step": "start",
            "created_by_id": user_id,
            "root_thread_id": root_thread_id,
            "knowledge_base_ids": list(knowledge_base_ids) if knowledge_base_ids else [],
            "provider_id": provider_id,
            "model_name": model_name,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "special_params": dict(special_params) if special_params else {},
            "enable_deep_thinking": enable_deep_thinking,
            "use_web_search": use_web_search,
        },
    )
    logger.info(f"[Study Flow] 预创建工作流会话: thread_id={thread_id}, retry_count={retry_count}")


def start_study_flow(
    user_question: str,
    thread_id: str,
    user_id: int | None = None,
    knowledge_base_ids: list | None = None,
    provider_id: str | None = None,
    model_name: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    special_params: dict | None = None,
    enable_deep_thinking: bool = False,
    use_web_search: bool = False,
) -> dict:
    logger.info(f"[Study Flow] 启动新的学习工作流，thread_id={thread_id}")

    study_flow = _get_cached_study_flow(thread_id)

    initial_state: StudyFlowState = {
        "messages": [],
        "user_question": user_question,
        "user_id": user_id,
        "knowledge_base_ids": list(knowledge_base_ids) if knowledge_base_ids else [],
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
        "provider_id": provider_id,
        "model_name": model_name,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "special_params": dict(special_params) if special_params else {},
        "enable_deep_thinking": enable_deep_thinking,
        "use_web_search": use_web_search,
        "created_at": timezone.now().isoformat(),
        "updated_at": timezone.now().isoformat(),
        "error": None,
        "error_node": None,
    }

    # 预创建 WorkflowSession（幂等，保留已存在 session 的配置字段）：
    # 必须早于图执行——quiz_generator_node._persist_questions 依赖该记录定位 session，
    # 否则题目无法落库，练习历史/修改答案随之失效（与 restart_quiz 的预创建保持一致）。
    _ensure_session_created(
        thread_id=thread_id,
        user_question=user_question,
        user_id=user_id,
        knowledge_base_ids=initial_state["knowledge_base_ids"],
        provider_id=provider_id,
        model_name=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        special_params=initial_state["special_params"],
        enable_deep_thinking=enable_deep_thinking,
        use_web_search=use_web_search,
    )

    cb = TokenUsageCallbackHandler()

    config = {
        "configurable": {"thread_id": thread_id},
        "callbacks": [cb],
    }

    # 工作流开始：发布 planner 步骤事件到 task 频道
    _safe_publish(
        EventType.WORKFLOW_STEP,
        {
            "source": EventSource.LEARNING.value,
            "source_id": thread_id,
            "step": "planner",
            "message": "正在生成学习计划...",
            "thread_id": thread_id,
        },
        task_id=thread_id,
        user_id=str(user_id) if user_id is not None else None,
    )

    logger.info("[Study Flow] 开始执行工作流...")
    start_time = time.time()
    try:
        result = study_flow.invoke(dict(initial_state), config)
    except Exception as e:
        # 工作流失败：发布失败事件
        _safe_publish(
            EventType.WORKFLOW_FAILED,
            {
                "source": EventSource.LEARNING.value,
                "source_id": thread_id,
                "step": "planner",
                "error": str(e),
                "thread_id": thread_id,
            },
            task_id=thread_id,
            user_id=str(user_id) if user_id is not None else None,
        )
        raise

    current_step = result.get("current_step")
    logger.info(f"[Study Flow] 工作流暂停在: {current_step}")

    # 工作流暂停/完成事件
    if current_step == "waiting_for_answers":
        _safe_publish(
            EventType.WORKFLOW_STATE_UPDATE,
            {
                "source": EventSource.LEARNING.value,
                "source_id": thread_id,
                "step": current_step,
                "state": "waiting_for_answers",
                "message": "等待用户提交答案",
                "thread_id": thread_id,
            },
            task_id=thread_id,
            user_id=str(user_id) if user_id is not None else None,
        )
    else:
        _safe_publish(
            EventType.WORKFLOW_COMPLETED,
            {
                "source": EventSource.LEARNING.value,
                "source_id": thread_id,
                "step": current_step,
                "thread_id": thread_id,
            },
            task_id=thread_id,
            user_id=str(user_id) if user_id is not None else None,
        )

    total_tokens = cb.prompt_tokens + cb.completion_tokens
    response_time = round(time.time() - start_time, 2)

    # 先保存工作流状态（创建 WorkflowSession），再更新 token 统计
    persistence_service.save_workflow_state(thread_id, result, user_id)
    _update_workflow_session_tokens(thread_id, total_tokens, response_time)

    return result


def submit_answers(thread_id: str, user_answers: dict, user_id: int | None = None) -> dict:
    logger.info(f"[Study Flow] 提交答案，thread_id={thread_id}")

    study_flow = _get_cached_study_flow(thread_id)

    current_state = study_flow.get_state(thread_id)
    if not current_state or not current_state.values or not current_state.values.get("current_step"):
        logger.info(f"[Study Flow] 内存中无状态，从持久化恢复，thread_id={thread_id}")
        saved_state = persistence_service.load_workflow_state(thread_id, user_id=user_id)
        if saved_state:
            study_flow.graph.update_state(config={"configurable": {"thread_id": thread_id}}, values=saved_state)
            current_state = study_flow.get_state(thread_id)
        else:
            raise ValueError(f"工作流状态不存在: {thread_id}")

    logger.info(f"[Study Flow] 当前状态: {current_state.values.get('current_step')}")

    # 提交守卫：仅允许在答题/执行中间态提交，工作流已结束（feedback_completed/end/错误态）返回 409
    current_step = current_state.values.get("current_step", "")
    if current_step not in _ALLOWED_SUBMIT_STEPS:
        raise WorkflowAlreadyFinishedError(
            f"工作流已结束，无法提交答案。当前步骤: {current_step}",
            current_phase=current_step,
        )

    study_flow.update_state(thread_id, {"user_answers": user_answers, "updated_at": timezone.now().isoformat()})

    # 答案已提交，继续执行 grading → feedback 步骤
    _safe_publish(
        EventType.WORKFLOW_STEP,
        {
            "source": EventSource.LEARNING.value,
            "source_id": thread_id,
            "step": "grading",
            "message": "正在评分...",
            "thread_id": thread_id,
        },
        task_id=thread_id,
        user_id=str(user_id) if user_id is not None else None,
    )

    logger.info("[Study Flow] 继续执行工作流...")
    start_time = time.time()

    cb = TokenUsageCallbackHandler()
    invoke_config = {
        "configurable": {"thread_id": thread_id},
        "callbacks": [cb],
    }

    try:
        study_flow.invoke(None, config=invoke_config)  # type: ignore[arg-type]  # LangGraph accepts None for checkpoint resumption
    except Exception as e:
        # 工作流失败：发布失败事件
        _safe_publish(
            EventType.WORKFLOW_FAILED,
            {
                "source": EventSource.LEARNING.value,
                "source_id": thread_id,
                "step": "grading",
                "error": str(e),
                "thread_id": thread_id,
            },
            task_id=thread_id,
            user_id=str(user_id) if user_id is not None else None,
        )
        raise

    result = get_workflow_state(thread_id)

    final_step = result.get("current_step") if result else "unknown"
    logger.info(f"[Study Flow] 工作流执行完成，最终状态: {final_step}")

    # 工作流完成事件
    _safe_publish(
        EventType.WORKFLOW_COMPLETED,
        {
            "source": EventSource.LEARNING.value,
            "source_id": thread_id,
            "step": final_step,
            "thread_id": thread_id,
        },
        task_id=thread_id,
        user_id=str(user_id) if user_id is not None else None,
    )

    if result:
        total_tokens = cb.prompt_tokens + cb.completion_tokens
        response_time = round(time.time() - start_time, 2)
        # 先保存工作流状态，再更新 token 统计
        persistence_service.save_workflow_state(thread_id, result, user_id)
        _update_workflow_session_tokens(thread_id, total_tokens, response_time, is_incremental=True)

        # 评分完成后创建 WorkflowAttempt 记录 + 锁定题目（失败不影响主流程）
        _persist_attempt_and_lock_questions(thread_id, result)

    return result


def _persist_attempt_and_lock_questions(thread_id: str, result: dict) -> None:
    """评分完成后创建/更新 WorkflowAttempt 记录，并锁定当前轮次题目（scored -> locked）。"""
    try:
        from ..models import WorkflowAttempt, WorkflowQuestion, WorkflowQuestionStatus, WorkflowSession

        session = WorkflowSession.objects.filter(thread_id=thread_id, is_deleted=False).first()
        if not session:
            logger.warning(f"[Study Flow] 未找到工作流会话，跳过轮次落库: {thread_id}")
            return

        attempt_index = result.get("retry_count", 0) or 0
        score = result.get("score")
        feedback = result.get("feedback")

        # 创建或更新 WorkflowAttempt 记录（同轮次幂等）
        WorkflowAttempt.objects.update_or_create(
            session=session,
            attempt_index=attempt_index,
            defaults={
                "thread_id": thread_id,
                "total_score": score,
                "feedback": feedback,
            },
        )
        logger.info(f"[Study Flow] 已创建练习轮次记录: attempt_index={attempt_index}, score={score}")

        # 锁定当前轮次的题目（scored -> locked）
        locked = WorkflowQuestion.objects.filter(
            session=session,
            attempt_index=attempt_index,
            status=WorkflowQuestionStatus.SCORED,
        ).update(status=WorkflowQuestionStatus.LOCKED)
        if locked:
            logger.info(f"[Study Flow] 已锁定 {attempt_index} 轮次的 {locked} 道题目")
    except Exception:
        logger.exception("[Study Flow] 创建 WorkflowAttempt 失败")


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
        study_flow.graph.update_state(config={"configurable": {"thread_id": thread_id}}, values=saved_state)
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

    config = {"configurable": {"thread_id": thread_id}}

    history = []
    try:
        for state in study_flow.graph.get_state_history(config):
            history.append(
                {
                    "step": state.metadata.get("step") if hasattr(state, "metadata") else None,
                    "values": state.values if hasattr(state, "values") else state,
                }
            )
    except Exception as e:
        logger.warning(f"[Study Flow] 获取历史失败: {e}")

    return history


def get_study_flow_app(thread_id: str | None = None) -> StudyFlow:
    if thread_id:
        return _get_cached_study_flow(thread_id)
    return StudyFlow()


def _update_workflow_session_tokens(
    thread_id: str,
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

        from Django_xm.apps.ai_engine.services.llm_factory import get_model_string

        # get_model_string() 内部已优先读 SystemConfig.default_chat_model，
        # 写入 WorkflowSession.model 与实际 LLM 调用模型保持一致
        model_name = get_model_string()

        if is_incremental:
            session.token_count = (session.token_count or 0) + total_tokens
            session.response_time = (session.response_time or 0) + response_time
        else:
            session.model = model_name
            session.token_count = total_tokens
            session.response_time = response_time

        session.save(update_fields=["model", "token_count", "response_time"])
        logger.info(f"[Study Flow] 更新工作流统计: thread_id={thread_id}, tokens={total_tokens}")
    except Exception as e:
        logger.warning(f"[Study Flow] 更新工作流会话 Token 失败: {e}")


def restart_quiz(thread_id: str, user_id: int | None = None) -> dict:
    """继续练习：创建新 thread_id，复用学习计划与运行时配置，生成新一轮题目

    流程：
    1. 从旧 session 读取 learning_plan、user_question、retry_count 与运行时配置
    2. 生成新 thread_id，预创建新 WorkflowSession（供 quiz_generator_node 持久化题目）
    3. 调用 study_flow.invoke() 从 START 正常执行：planner 检测到 learning_plan 跳过
    4. quiz_generator 生成新题目并持久化为新 attempt_index 的 WorkflowQuestion

    Args:
        thread_id: 旧工作流线程 ID
        user_id: 用户 ID（可选）

    Returns:
        包含 new_thread_id 的工作流状态字典

    Raises:
        ValueError: 旧 session 不存在或缺少 learning_plan 时
    """
    logger.info(f"[Study Flow] 继续练习，旧 thread_id={thread_id}")

    from ..models import WorkflowSession

    # 1. 获取旧 session
    old_session = WorkflowSession.objects.filter(thread_id=thread_id, is_deleted=False).first()
    if not old_session:
        raise ValueError(f"工作流会话不存在: {thread_id}")

    if not old_session.learning_plan:
        raise ValueError(f"工作流学习计划不存在，无法继续练习: {thread_id}")

    # 2. 生成新 thread_id
    new_thread_id = f"study_{uuid.uuid4().hex[:12]}"
    new_retry_count = (old_session.retry_count or 0) + 1

    logger.info(f"[Study Flow] 创建新 thread_id={new_thread_id}, retry_count={new_retry_count}")

    # 3. 预创建新 WorkflowSession（供 quiz_generator_node 持久化题目）
    # 继承老 session 的用户运行时配置（知识库/模型/深度思考/网络查询），
    # 保证「继续练习」使用与原练习一致的运行时环境
    new_root_thread_id = old_session.root_thread_id or old_session.thread_id
    _ensure_session_created(
        thread_id=new_thread_id,
        user_question=old_session.user_question,
        user_id=user_id,
        knowledge_base_ids=old_session.knowledge_base_ids or [],
        provider_id=old_session.provider_id,
        model_name=old_session.model_name,
        temperature=old_session.temperature,
        max_tokens=old_session.max_tokens,
        special_params=old_session.special_params or {},
        enable_deep_thinking=old_session.enable_deep_thinking,
        use_web_search=old_session.use_web_search,
        learning_plan=old_session.learning_plan,
        retry_count=new_retry_count,
        root_thread_id=new_root_thread_id,
    )

    # 4. 创建新 StudyFlow 实例
    study_flow = _get_study_flow(new_thread_id)

    # 5. 构建初始状态（复用旧 session 的 learning_plan 与运行时配置）
    initial_state: StudyFlowState = {
        "messages": [],
        "user_question": old_session.user_question,
        "learning_plan": old_session.learning_plan,
        "retrieved_docs": None,
        "quiz": None,
        "user_answers": None,
        "score": None,
        "score_details": None,
        "feedback": None,
        "retry_count": new_retry_count,
        "should_retry": False,
        "current_step": "start",
        "thread_id": new_thread_id,
        "root_thread_id": new_root_thread_id,
        "knowledge_base_ids": old_session.knowledge_base_ids or [],
        "provider_id": old_session.provider_id,
        "model_name": old_session.model_name,
        "temperature": old_session.temperature,
        "max_tokens": old_session.max_tokens,
        "special_params": old_session.special_params or {},
        "enable_deep_thinking": old_session.enable_deep_thinking,
        "use_web_search": old_session.use_web_search,
        "created_at": timezone.now().isoformat(),
        "updated_at": timezone.now().isoformat(),
        "error": None,
        "error_node": None,
        "user_id": old_session.created_by_id,
    }

    # 6. 执行工作流（planner 会跳过，因为 learning_plan 已存在）
    config = {
        "configurable": {"thread_id": new_thread_id},
        "callbacks": [TokenUsageCallbackHandler()],
    }

    logger.info("[Study Flow] 开始执行继续练习工作流...")
    result = study_flow.invoke(initial_state, config)

    logger.info(f"[Study Flow] 继续练习完成，新 thread_id={new_thread_id}, 当前步骤: {result.get('current_step')}")

    # 7. 持久化新 session（更新预创建的 session）
    persistence_service.save_workflow_state(new_thread_id, result, user_id)

    # 8. 更新旧 session 的 retry_count（用于追踪总练习轮次）
    old_session.retry_count = new_retry_count
    old_session.save(update_fields=["retry_count"])

    # 9. 返回结果（包含新 thread_id）
    result["new_thread_id"] = new_thread_id
    result["retry_count"] = new_retry_count

    return result
