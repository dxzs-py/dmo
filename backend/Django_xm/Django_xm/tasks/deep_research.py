"""
深度研究 Celery 任务
将原本的 threading 后台任务迁移到 Celery 任务队列
"""
import logging
from datetime import datetime
from celery import shared_task

from Django_xm.apps.research.services.deep_agent import create_deep_research_agent
from Django_xm.apps.research.services.task_manager import get_task_manager, update_task_status
from Django_xm.tasks.base import TrackedTask

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    soft_time_limit=1800,
)
def run_research_task(self, thread_id: str, query: str,
                      enable_web_search: bool = True,
                      enable_doc_analysis: bool = False):
    tracker = TrackedTask(self)
    tracker.set_task_type('deep_research')

    task_manager = get_task_manager()

    def _mark_failed(error_msg: str, exc: Exception = None):
        update_task_status(thread_id, {
            'status': 'failed',
            'current_step': 'failed',
            'end_time': datetime.now().isoformat(),
            'error': error_msg,
            'final_report': f"错误：{error_msg}",
        })
        tracker.mark_failure(error_message=error_msg)
        if exc and self.request.retries < self.max_retries:
            raise self.retry(exc=exc)

    try:
        logger.info(f"[Celery] 深度研究任务开始：{thread_id}")
        tracker.mark_started()

        update_task_status(thread_id, {
            'status': 'running',
            'current_step': 'researching',
            'start_time': datetime.now().isoformat(),
        })
        tracker.update_progress(10, '研究任务启动')

        agent = create_deep_research_agent(
            thread_id=thread_id,
            enable_web_search=enable_web_search,
            enable_doc_analysis=enable_doc_analysis
        )
        tracker.update_progress(30, '智能体初始化完成')

        result = agent.research(query)
        tracker.update_progress(90, '研究执行完成')

        if not result.get('success', True):
            error_msg = result.get('error', '研究执行返回失败')
            logger.warning(f"[Celery] 研究逻辑失败：{thread_id}, {error_msg}")
            _mark_failed(error_msg)
            return {'status': 'failed', 'thread_id': thread_id, 'error': error_msg}

        update_task_status(thread_id, {
            'status': 'completed',
            'current_step': 'completed',
            'end_time': datetime.now().isoformat(),
            'result': result,
            'final_report': result.get('final_report', ''),
        })

        tracker.mark_success(result={'thread_id': thread_id, 'status': 'completed'})
        logger.info(f"[Celery] 深度研究任务完成：{thread_id}")
        return {'status': 'completed', 'thread_id': thread_id}

    except Exception as exc:
        logger.error(f"[Celery] 深度研究任务失败：{thread_id}, 错误：{exc}", exc_info=True)
        _mark_failed(str(exc), exc)
