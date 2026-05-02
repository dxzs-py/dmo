"""
Celery 工作流异步任务
处理工作流执行、状态更新等异步操作
"""
import logging
from datetime import datetime
from celery import shared_task

from Django_xm.apps.learning.services.study_flow import _get_study_flow
from Django_xm.apps.learning.models import WorkflowSession, WorkflowSessionStatus
from Django_xm.apps.common.task_manager import (
    get_task_manager,
    TaskStatus,
    TaskType,
    update_task_status,
)

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    soft_time_limit=1200,
)
def execute_workflow_task(self, thread_id: str, user_question: str, user_id: int = None):
    """
    异步执行工作流任务

    Args:
        self: Celery task 实例
        thread_id: 线程 ID
        user_question: 用户问题
        user_id: 用户 ID（可选）
    """
    task_manager = get_task_manager()

    def _mark_failed(error_msg: str):
        update_task_status(thread_id, {
            'status': TaskStatus.FAILED.value,
            'current_step': 'failed',
            'end_time': datetime.now().isoformat(),
            'error': error_msg,
        })
        try:
            session = WorkflowSession.objects.filter(thread_id=thread_id).first()
            if session:
                session.status = WorkflowSessionStatus.FAILED
                session.error_message = error_msg
                session.save()
        except Exception:
            pass

    try:
        logger.info(f"[Celery Workflow] 工作流任务开始：{thread_id}")

        update_task_status(thread_id, {
            'status': TaskStatus.RUNNING.value,
            'current_step': 'planner',
            'start_time': datetime.now().isoformat(),
        })

        study_flow = _get_study_flow(thread_id)

        initial_state = {
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

        config = {"configurable": {"thread_id": thread_id}}

        for event in study_flow.graph.stream(initial_state, config, stream_mode="values"):
            current_step = event.get('current_step', 'unknown')
            update_task_status(thread_id, {
                'current_step': current_step,
                'progress': _calculate_progress(current_step),
            })

            if current_step == 'waiting_for_answers':
                logger.info(f"[Celery Workflow] 等待用户答案：{thread_id}")
                try:
                    session = WorkflowSession.objects.filter(thread_id=thread_id).first()
                    if session:
                        session.status = WorkflowSessionStatus.WAITING_FOR_ANSWERS
                        session.learning_plan = event.get('learning_plan')
                        session.quiz = event.get('quiz')
                        session.current_step = current_step
                        session.save()
                except Exception as e:
                    logger.warning(f"[Celery Workflow] 保存会话状态失败：{e}")
                break

        update_task_status(thread_id, {
            'status': TaskStatus.COMPLETED.value,
            'current_step': 'completed',
            'end_time': datetime.now().isoformat(),
            'result': event,
        })

        try:
            session = WorkflowSession.objects.filter(thread_id=thread_id).first()
            if session:
                session.status = WorkflowSessionStatus.COMPLETED
                session.current_step = 'completed'
                session.save()
        except Exception as e:
            logger.warning(f"[Celery Workflow] 保存最终会话状态失败：{e}")

        logger.info(f"[Celery Workflow] 工作流任务完成：{thread_id}")
        return {'status': 'completed', 'thread_id': thread_id}

    except Exception as exc:
        logger.error(f"[Celery Workflow] 工作流任务失败：{thread_id}, 错误：{exc}", exc_info=True)
        _mark_failed(str(exc))
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        return {'status': 'failed', 'thread_id': thread_id, 'error': str(exc)}


def _calculate_progress(step: str) -> int:
    """计算工作流进度百分比"""
    step_progress = {
        'start': 0,
        'planner': 20,
        'retrieval': 40,
        'quiz_generator': 60,
        'waiting_for_answers': 70,
        'grading': 80,
        'feedback': 90,
        'end': 100,
    }
    return step_progress.get(step, 50)
