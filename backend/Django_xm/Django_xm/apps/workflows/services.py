"""
工作流服务层
封装工作流相关的业务逻辑
"""
import uuid
from datetime import datetime
from typing import Dict, Any, Optional

from .study_flow import (
    _get_study_flow,
    submit_answers,
    get_workflow_state,
    get_workflow_history,
    _study_flow_cache
)
from .state import StudyFlowState
from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)


class WorkflowService:
    """工作流服务类"""

    @staticmethod
    def start_workflow(user_question: str, thread_id: Optional[str] = None) -> Dict[str, Any]:
        """
        启动新的学习工作流

        Args:
            user_question: 用户的学习问题
            thread_id: 可选的线程ID

        Returns:
            工作流执行结果
        """
        thread_id = thread_id or f"study_{uuid.uuid4().hex[:12]}"

        logger.info(f"[Service] 启动工作流，thread_id={thread_id}")

        study_flow = _get_study_flow(thread_id=thread_id)

        now = datetime.now().isoformat()
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
            "created_at": now,
            "updated_at": now,
            "error": None,
            "error_node": None
        }
        config = {"configurable": {"thread_id": thread_id}}
        study_flow.invoke(dict(initial_state), config=config)
        
        result = get_workflow_state(thread_id)

        logger.info(f"[Service] 工作流启动成功，thread_id={thread_id}")

        return {
            "thread_id": thread_id,
            "user_question": user_question,
            "status": result.get("current_step", "waiting_for_answers"),
            "current_step": result.get("current_step", "waiting_for_answers"),
            "learning_plan": result.get("learning_plan"),
            "retrieved_docs": result.get("retrieved_docs"),
            "quiz": result.get("quiz"),
            "user_answers": result.get("user_answers"),
            "score": result.get("score"),
            "score_details": result.get("score_details"),
            "feedback": result.get("feedback"),
            "should_retry": result.get("should_retry", False),
            "retry_count": result.get("retry_count", 0),
            "created_at": result.get("created_at", ""),
            "updated_at": result.get("updated_at", ""),
            "message": "学习计划和练习题已生成"
        }

    @staticmethod
    def submit_user_answers(thread_id: str, answers: Dict[str, str]) -> Dict[str, Any]:
        """
        提交用户答案，继续执行工作流

        Args:
            thread_id: 线程ID
            answers: 用户答案字典

        Returns:
            工作流执行结果
        """
        logger.info(f"[Service] 提交答案，thread_id={thread_id}")
        result = submit_answers(thread_id, answers)

        should_retry = result.get("should_retry", False)
        if should_retry:
            status = "retry"
            message = "得分未达标，已重新生成练习题，请继续答题。"
        else:
            score = result.get("score", 0)
            if score >= 60:
                status = "completed"
                message = "恭喜通过测验！"
            else:
                status = "failed"
                message = "已达到最大重试次数，建议复习后再来挑战。"

        return {
            "thread_id": thread_id,
            "user_question": result.get("user_question"),
            "status": status,
            "current_step": result.get("current_step"),
            "learning_plan": result.get("learning_plan"),
            "quiz": result.get("quiz"),
            "user_answers": result.get("user_answers"),
            "score": result.get("score"),
            "score_details": result.get("score_details"),
            "feedback": result.get("feedback"),
            "should_retry": should_retry,
            "retry_count": result.get("retry_count", 0),
            "created_at": result.get("created_at", ""),
            "updated_at": result.get("updated_at", ""),
            "message": message
        }

    @staticmethod
    def get_workflow_status(thread_id: str) -> Optional[Dict[str, Any]]:
        """
        获取工作流的当前状态

        Args:
            thread_id: 线程ID

        Returns:
            工作流状态字典，不存在则返回None
        """
        logger.info(f"[Service] 查询工作流状态，thread_id={thread_id}")
        return get_workflow_state(thread_id)

    @staticmethod
    def get_workflow_history(thread_id: str) -> list:
        """
        获取工作流的执行历史

        Args:
            thread_id: 线程ID

        Returns:
            历史状态列表
        """
        logger.info(f"[Service] 查询工作流历史，thread_id={thread_id}")
        return get_workflow_history(thread_id)

    @staticmethod
    def delete_workflow(thread_id: str) -> Dict[str, Any]:
        """
        删除工作流

        Args:
            thread_id: 线程ID

        Returns:
            删除结果
        """
        logger.info(f"[Service] 删除工作流，thread_id={thread_id}")

        if thread_id in _study_flow_cache:
            del _study_flow_cache[thread_id]

        return {
            "thread_id": thread_id,
            "status": "deleted",
            "message": "工作流已删除（注意：检查点数据可能仍然存在）"
        }
