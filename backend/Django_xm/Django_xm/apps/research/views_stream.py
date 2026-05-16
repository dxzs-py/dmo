import logging
import time
import json

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from Django_xm.apps.common.sse_utils import sse_response, authenticate_sse_request, sse_error_response
from Django_xm.apps.core.permissions import IsAuthenticatedOrQueryParam

from .models import ResearchTask
from .services.task_manager import get_task_status

logger = logging.getLogger(__name__)


def deep_research_stream(request, task_id):
    user = authenticate_sse_request(request)

    if not user:
        return sse_error_response('未登录或登录已过期', 401)

    try:
        task = ResearchTask.objects.get(
            task_id=task_id,
            created_by=user,
            is_deleted=False,
        )
    except ResearchTask.DoesNotExist:
        return sse_error_response('研究任务不存在', 404)

    logger.info(f"[API] SSE流式监听研究进度，task_id={task_id}, user_id={user.id}")

    def event_stream():
        last_status = None
        start_time = time.time()
        max_duration = 600

        try:
            yield f"data: {json.dumps({'type': 'connected', 'task_id': task_id}, ensure_ascii=False)}\n\n"

            while True:
                elapsed = time.time() - start_time
                if elapsed > max_duration:
                    yield f"data: {json.dumps({'type': 'timeout', 'message': '连接超时'}, ensure_ascii=False)}\n\n"
                    break

                try:
                    current_task = ResearchTask.objects.get(task_id=task_id, created_by=user)
                    current_status = current_task.status
                except ResearchTask.DoesNotExist:
                    yield f"data: {json.dumps({'type': 'error', 'message': '任务不存在或无权访问'}, ensure_ascii=False)}\n\n"
                    break

                if current_status != last_status:
                    last_status = current_status

                    step_messages = {
                        'pending': '研究任务已创建，等待执行...',
                        'running': '正在执行深度研究...',
                        'completed': '研究已完成！',
                        'failed': '研究执行失败',
                    }

                    event_data = {
                        'type': 'status_change',
                        'status': current_status,
                        'message': step_messages.get(current_status, f'状态: {current_status}'),
                        'task_id': task_id,
                    }

                    if current_status == 'completed' and current_task.final_report:
                        event_data['final_report'] = current_task.final_report

                    yield f"data: {json.dumps(event_data, ensure_ascii=False, default=str)}\n\n"

                    if current_status in ('completed', 'failed'):
                        break

                cached = get_task_status(task_id, user_id=user.id)
                if cached and cached.get('current_step'):
                    step = cached['current_step']
                    if step != current_status:
                        yield f"data: {json.dumps({'type': 'step_update', 'step': step, 'task_id': task_id}, ensure_ascii=False)}\n\n"

                time.sleep(2)

            yield f"data: {json.dumps({'type': 'done', 'task_id': task_id}, ensure_ascii=False)}\n\n"

        except GeneratorExit:
            logger.info(f"[API] SSE连接关闭，task_id={task_id}")
        except Exception as e:
            logger.error(f"[API] SSE流式输出异常：{e}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"

    response = sse_response(event_stream())
    return response


class DeepResearchStreamView(APIView):
    permission_classes = [IsAuthenticatedOrQueryParam]

    def get(self, request, task_id):
        return deep_research_stream(request, task_id)
