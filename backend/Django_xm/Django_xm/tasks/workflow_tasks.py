"""
工作流执行 Celery 任务
处理学习工作流的异步执行
"""
import logging
from datetime import datetime
from celery import shared_task
from celery.exceptions import Retry

from Django_xm.apps.learning.services.study_flow import _get_study_flow
from Django_xm.apps.learning.models import WorkflowSession, WorkflowSessionStatus
from Django_xm.tasks.base import TrackedTask

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name='workflow.execute',
    max_retries=2,
    default_retry_delay=30,
    soft_time_limit=1200,
)
def execute_workflow_task(self, thread_id: str, user_question: str, user_id: int = None):
    tracker = TrackedTask(self)
    tracker.set_task_type('workflow')
    if user_id:
        tracker.set_created_by(user_id)
    tracker.set_task_manager_id(thread_id)

    try:
        logger.info(f"[Celery Workflow] 开始执行工作流：{thread_id}")
        tracker.mark_started()

        session = WorkflowSession.objects.filter(session_id=thread_id).first()
        if not session:
            tracker.mark_failure(error_message='工作流会话不存在')
            return {'status': 'error', 'error': '工作流会话不存在'}

        session.status = WorkflowSessionStatus.RUNNING
        session.save(update_fields=['status', 'updated_at'])
        tracker.update_progress(20, '工作流开始执行')

        study_flow = _get_study_flow(thread_id)
        if not study_flow:
            session.status = WorkflowSessionStatus.FAILED
            session.error_message = '无法获取工作流配置'
            session.save(update_fields=['status', 'error_message', 'updated_at'])
            tracker.mark_failure(error_message='无法获取工作流配置')
            return {'status': 'error', 'error': '无法获取工作流配置'}

        tracker.update_progress(40, '工作流配置加载完成')

        result = study_flow.invoke({
            'user_question': user_question,
            'thread_id': thread_id,
        })

        tracker.update_progress(80, '工作流执行完成')

        if result and not isinstance(result, dict):
            result = {'output': str(result)}

        session.status = WorkflowSessionStatus.COMPLETED
        session.completed_at = datetime.now()
        session.save(update_fields=['status', 'completed_at', 'updated_at'])

        tracker.mark_success(result=result)
        logger.info(f"[Celery Workflow] 工作流执行完成：{thread_id}")
        return {'status': 'success', 'thread_id': thread_id, 'result': result}

    except Retry:
        raise
    except Exception as exc:
        logger.error(f"[Celery Workflow] 工作流执行失败：{thread_id}, 错误：{exc}", exc_info=True)
        try:
            session = WorkflowSession.objects.filter(session_id=thread_id).first()
            if session:
                session.status = WorkflowSessionStatus.FAILED
                session.error_message = str(exc)[:500]
                session.save(update_fields=['status', 'error_message', 'updated_at'])
        except Exception:
            pass

        try:
            tracker.mark_failure(error_message=str(exc))
        except Exception:
            pass

        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        return {'status': 'error', 'thread_id': thread_id, 'error': str(exc)}
