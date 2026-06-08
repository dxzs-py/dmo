"""
深度研究公共执行服务

提取 Celery 任务和聊天路径的公共逻辑：
- 智能体执行（LLM Cache 控制 + Token 追踪）
- 结果处理（文件同步 + Token 更新 + 状态更新 + Redis 发布）
"""
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from Django_xm.apps.ai_engine.services.token_counter import TokenUsageCallbackHandler

logger = logging.getLogger(__name__)

REDIS_CHANNEL_PREFIX = "research:result:"


@dataclass
class ResearchResult:
    success: bool
    final_report: str = ""
    files: Optional[Dict[str, Any]] = None
    state_files: Optional[Dict[str, Any]] = None
    usage_data: Optional[Dict[str, int]] = None
    model_name: str = ""
    error_message: str = ""
    raw_result: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "final_report": self.final_report,
            "files": self.files,
            "state_files": self.state_files,
            "usage_data": self.usage_data,
            "model_name": self.model_name,
            "error_message": self.error_message,
        }


def load_research_context(task_id: str, max_content_length: int = 12000) -> str:
    """加载指定研究任务的上下文（final_report + 关键文件内容）

    续研时注入到 system_prompt，提供先前研究的结构化摘要。
    """
    from Django_xm.apps.research.models import ResearchTask
    from Django_xm.apps.core.services.file_manager import get_file_manager

    try:
        task = ResearchTask.objects.filter(task_id=task_id, is_deleted=False).first()
        if not task:
            return ""

        parts = []

        if task.final_report and task.final_report.strip():
            report = task.final_report.strip()
            if len(report) > max_content_length:
                report = report[:max_content_length] + "\n...(报告过长已截断)"
            parts.append(f"### 研究报告\n{report}")

        try:
            file_manager = get_file_manager()
            files = file_manager.list_task_files(task_id, 'research')
            md_files = [f for f in files if f.path.suffix in ('.md', '.txt')]
            for f in md_files[:8]:
                relative_path = str(f.path.relative_to(f.base_dir))
                content = file_manager.read_file_content(task_id, relative_path, 'research')
                if content and content.strip():
                    truncated = content.strip()
                    if len(truncated) > 3000:
                        truncated = truncated[:3000] + "\n...(内容过长已截断)"
                    parts.append(f"### {relative_path}\n{truncated}")
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"加载研究文件失败: {e}")

        return "\n\n".join(parts)

    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"加载研究上下文失败: {e}")
        return ""


def execute_research(
    agent,
    query: str,
    disable_llm_cache: bool = True,
) -> ResearchResult:
    """
    公共研究执行逻辑

    Args:
        agent: 已创建的研究智能体
        query: 研究查询
        disable_llm_cache: 是否禁用 LLM Cache（避免缓存干扰研究结果）

    Returns:
        ResearchResult 标准化结果
    """
    saved_cache = None
    if disable_llm_cache:
        from langchain_core.globals import get_llm_cache, set_llm_cache
        saved_cache = get_llm_cache()
        set_llm_cache(None)

    try:
        with TokenUsageCallbackHandler() as cb:
            result = agent.research(query, callbacks=[cb])

        usage_data = {
            'prompt_tokens': cb.prompt_tokens,
            'completion_tokens': cb.completion_tokens,
            'successful_requests': cb.successful_requests,
        }
        model_name = getattr(cb, '_current_model', '') or ''

        success = result.get('success', True)
        final_report = result.get('final_report', '')
        error_message = result.get('error', '') if not success else ''

        return ResearchResult(
            success=success,
            final_report=final_report,
            files=result.get('files'),
            state_files=result.get('state_files'),
            usage_data=usage_data,
            model_name=model_name,
            error_message=error_message,
            raw_result=result,
        )
    finally:
        if disable_llm_cache and saved_cache is not None:
            from langchain_core.globals import set_llm_cache
            set_llm_cache(saved_cache)


def _normalize_file_path(path: str) -> str:
    path = path.lstrip('/')
    if path.startswith(('notes/', 'reports/', 'plans/')):
        return path
    name = path.split('/')[-1]
    name_lower = name.lower()
    if 'report' in name_lower:
        return f'reports/{name}'
    if 'plan' in name_lower:
        return f'plans/{name}'
    if name.endswith(('.md', '.txt')):
        return f'notes/{name}'
    return path


def _sync_state_files_to_disk(thread_id: str, result: ResearchResult):
    try:
        from Django_xm.apps.core.services.file_manager import get_file_manager

        files = result.state_files or result.files or {}
        if not files:
            logger.info(f"无状态文件需要同步: {thread_id}")
            return

        fm = get_file_manager()
        synced = 0
        for file_path, file_data in files.items():
            path = _normalize_file_path(file_path)
            content = file_data
            if isinstance(file_data, dict):
                content = file_data.get('content', '')
                if isinstance(content, list):
                    content = '\n'.join(content)
            if isinstance(content, str) and content:
                fm.write_file_content(thread_id, path, content, task_type='research')
                synced += 1

        logger.info(f"状态文件同步完成: {thread_id}, {synced}/{len(files)} 个文件")
    except Exception as e:
        logger.warning(f"同步状态文件到磁盘失败: {e}")


def _publish_result_to_redis(thread_id: str, result: ResearchResult, response_time: float):
    try:
        from django.core.cache import cache
        redis_client = cache.client.get_client()
        channel = f"{REDIS_CHANNEL_PREFIX}{thread_id}"
        payload = json.dumps({
            "thread_id": thread_id,
            "response_time": response_time,
            **result.to_dict(),
        }, ensure_ascii=False)
        redis_client.publish(channel, payload)
        logger.info(f"研究结果已发布到 Redis: {channel}")
    except Exception as e:
        logger.warning(f"发布研究结果到 Redis 失败: {e}")


def finalize_research(
    thread_id: str,
    result: ResearchResult,
    response_time: float,
    sync_files: bool = True,
    publish_to_redis: bool = False,
) -> None:
    """
    公共结果处理逻辑

    Args:
        thread_id: 研究任务 ID
        result: ResearchResult 执行结果
        response_time: 响应时间（秒）
        sync_files: 是否同步文件到磁盘
        publish_to_redis: 是否将结果发布到 Redis（供聊天路径订阅）
    """
    if sync_files:
        _sync_state_files_to_disk(thread_id, result)

    try:
        from Django_xm.apps.research.services.cross_app import update_research_task_model_and_tokens
        total_tokens = 0
        token_detail = None
        if result.usage_data:
            total_tokens = result.usage_data.get('prompt_tokens', 0) + result.usage_data.get('completion_tokens', 0)

        update_research_task_model_and_tokens(
            task_id=thread_id,
            model_name=result.model_name,
            token_count=total_tokens,
            token_detail=token_detail,
            response_time=response_time,
        )
    except Exception as e:
        logger.warning(f"更新研究任务 Token 数据失败: {e}")

    if result.usage_data:
        logger.info(
            f"研究任务 Token 统计: {thread_id}, "
            f"模型: {result.model_name}, "
            f"Token: {result.usage_data.get('prompt_tokens', 0) + result.usage_data.get('completion_tokens', 0)} "
            f"(输入={result.usage_data.get('prompt_tokens', 0)}, "
            f"输出={result.usage_data.get('completion_tokens', 0)}), "
            f"调用: {result.usage_data.get('successful_requests', 0)}次"
        )

    if publish_to_redis:
        _publish_result_to_redis(thread_id, result, response_time)
