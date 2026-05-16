"""
深度研究 Celery 任务
将原本的 threading 后台任务迁移到 Celery 任务队列
"""
import logging
import time
from decimal import Decimal
from celery import shared_task
from celery.exceptions import Retry

from Django_xm.apps.ai_engine.services.token_counter import TokenUsageCallbackHandler
from Django_xm.apps.research.services import create_research_agent
from Django_xm.apps.research.services.task_manager import update_task_status
from Django_xm.apps.research.services.multi_kb_retriever import build_retriever_tool_for_research
from Django_xm.apps.ai_engine.services.cost_tracker import create_cost_tracker
from Django_xm.apps.ai_engine.services.usage_tracker import create_usage_tracker
from Django_xm.tasks.base import TrackedTask

logger = logging.getLogger(__name__)


def _sync_state_files_to_disk(thread_id: str, result: dict):
    try:
        from Django_xm.apps.core.services.file_manager import get_file_manager

        files = result.get('state_files') or result.get('files', {})
        if not files:
            logger.info(f"[Celery] 无状态文件需要同步: {thread_id}")
            return

        fm = get_file_manager()
        synced = 0
        for file_path, file_data in files.items():
            path = file_path.lstrip('/')
            content = file_data
            if isinstance(file_data, dict):
                content = file_data.get('content', '')
                if isinstance(content, list):
                    content = '\n'.join(content)
            if isinstance(content, str) and content:
                fm.write_file_content(thread_id, path, content, task_type='research')
                synced += 1
                logger.info(f"[Celery] 同步文件: {path} ({len(content)} chars)")

        logger.info(f"[Celery] 状态文件同步完成: {thread_id}, {synced}/{len(files)} 个文件")
    except Exception as e:
        logger.warning(f"[Celery] 同步状态文件到磁盘失败: {e}")


@shared_task(
    bind=True,
    name='research.run_research',
    max_retries=2,
    default_retry_delay=60,
    soft_time_limit=1800,
)
def run_research_task(self, thread_id: str, query: str,
                      enable_web_search: bool = True,
                      enable_doc_analysis: bool = False,
                      knowledge_base_ids: list = None,
                      user_id: int = None):
    tracker = TrackedTask(self)
    tracker.set_task_type('deep_research')
    if user_id:
        tracker.set_created_by(user_id)
    tracker.set_task_manager_id(thread_id, sync_fn=update_task_status)

    start_time = time.time()

    def _mark_failed(error_msg: str, exc: Exception = None):
        tracker.mark_failure(error_message=error_msg)
        if exc and self.request.retries < self.max_retries:
            raise self.retry(exc=exc)

    try:
        logger.info(f"[Celery] 深度研究任务开始：{thread_id}")
        tracker.mark_started()
        tracker.update_progress(10, '研究任务启动')

        retriever_tool = None
        if enable_doc_analysis and knowledge_base_ids and user_id:
            logger.info(f"[Celery] 构建多知识库检索工具: {knowledge_base_ids}")
            try:
                retriever_tool = build_retriever_tool_for_research(
                    knowledge_base_ids=knowledge_base_ids,
                    user_id=user_id,
                )
                if retriever_tool:
                    logger.info("[Celery] 知识库检索工具创建成功")
                else:
                    logger.warning("[Celery] 知识库检索工具创建失败，文档分析将无法检索")
            except Exception as e:
                logger.error(f"[Celery] 构建知识库检索工具异常: {e}")

        agent = create_research_agent(
            thread_id=thread_id,
            enable_web_search=enable_web_search,
            enable_doc_analysis=enable_doc_analysis,
            retriever_tool=retriever_tool,
            user_id=user_id,
        )
        tracker.update_progress(30, '智能体初始化完成')

        cost_tracker = create_cost_tracker()
        usage_tracker = create_usage_tracker()

        from langchain_core.globals import get_llm_cache, set_llm_cache
        saved_cache = get_llm_cache()
        set_llm_cache(None)

        try:
            with TokenUsageCallbackHandler() as cb:
                result = agent.research(query, callbacks=[cb])
        finally:
            set_llm_cache(saved_cache)

        usage_tracker.add_input_tokens(cb.prompt_tokens)
        usage_tracker.add_output_tokens(cb.completion_tokens)
        cost_tracker.update_from_metadata(
            {'usage_metadata': {
                'input_tokens': cb.prompt_tokens,
                'output_tokens': cb.completion_tokens,
            }},
            model=cb._current_model or None,
        )
        cost_tracker.finish_record()

        total_cost = cost_tracker.get_total_cost()
        total_tokens = usage_tracker.get_total_tokens()
        response_time = round(time.time() - start_time, 2)
        model_name = cb._current_model or usage_tracker.model_id

        tracker.update_progress(90, '研究执行完成')

        if not result.get('success', True):
            error_msg = result.get('error', '研究执行返回失败')
            logger.warning(f"[Celery] 研究逻辑失败：{thread_id}, {error_msg}")
            _mark_failed(error_msg)
            return {'status': 'error', 'thread_id': thread_id, 'error': error_msg}

        _sync_state_files_to_disk(thread_id, result)

        try:
            from Django_xm.apps.research.models import ResearchTask
            ResearchTask.objects.filter(task_id=thread_id).update(
                model=model_name,
                token_count=total_tokens,
                cost=Decimal(str(round(total_cost, 6))),
                response_time=response_time,
            )
        except Exception as e:
            logger.warning(f"[Celery] 更新研究任务成本失败: {e}")

        cost_tracker.log_summary()
        usage_tracker.log_summary()

        tracker.mark_success(result={
            'thread_id': thread_id,
            'final_report': result.get('final_report', ''),
        })
        logger.info(
            f"[Celery] 深度研究任务完成：{thread_id}, "
            f"模型: {model_name}, "
            f"成本: ${total_cost:.4f}, "
            f"Token: {total_tokens} (输入={cb.prompt_tokens}, 输出={cb.completion_tokens}), "
            f"调用: {cb.successful_requests}次"
        )
        return {'status': 'success', 'thread_id': thread_id}

    except Retry:
        raise
    except Exception as exc:
        logger.error(f"[Celery] 深度研究任务失败：{thread_id}, 错误：{exc}", exc_info=True)
        _mark_failed(str(exc), exc)
        return {'status': 'error', 'thread_id': thread_id, 'error': str(exc)}
