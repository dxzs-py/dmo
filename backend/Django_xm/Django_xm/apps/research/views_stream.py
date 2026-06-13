import logging
import time
import json

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import BaseRenderer

from Django_xm.common.sse_utils import sse_response, authenticate_sse_request, sse_error_response, sse_error_event
from Django_xm.common.permissions import IsAuthenticatedOrQueryParam

from .models import ResearchTask
from .services.task_manager import get_task_status

class SSERenderer(BaseRenderer):
    media_type = 'text/event-stream'
    format = 'txt'

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


logger = logging.getLogger(__name__)


def deep_research_stream(request, task_id):
    user = authenticate_sse_request(request)

    if not user:
        return sse_error_response('未登录或登录已过期', 401, code="40101")

    try:
        task = ResearchTask.objects.get(
            task_id=task_id,
            created_by=user,
            is_deleted=False,
        )
    except ResearchTask.DoesNotExist:
        return sse_error_response('研究任务不存在', 404, code="40401")

    logger.info(f"[API] SSE流式监听研究进度，task_id={task_id}, user_id={user.id}")

    def event_stream():
        last_status = None
        start_time = time.time()
        max_duration = 600

        # 订阅 Redis 审批频道
        approval_pubsub = None
        redis_client = None
        try:
            from django.core.cache import cache
            from Django_xm.apps.research.services.research_runner import REDIS_APPROVAL_PREFIX
            redis_client = cache.client.get_client()
            approval_channel = f"{REDIS_APPROVAL_PREFIX}{task_id}"
            approval_pubsub = redis_client.pubsub()
            approval_pubsub.subscribe(approval_channel)
            logger.info(f"[SSE] 已订阅审批频道: {approval_channel}")
        except Exception as e:
            logger.warning(f"[SSE] 订阅审批频道失败: {e}")

        # 读取 Redis List 中的历史审批数据（解决 Pub/Sub 即发即弃导致错过事件的问题）
        # 同时读取已处理审批的最终状态，确保迟连接的浏览器能看到完整审批状态
        if redis_client:
            try:
                approval_list_key = f"{REDIS_APPROVAL_PREFIX}pending:{task_id}"
                pending_approvals = redis_client.lrange(approval_list_key, 0, -1)
                pushed_ids = set()
                for item in pending_approvals:
                    try:
                        approval_data = json.loads(item)
                        interrupt_id = approval_data.get("interrupt_id", "")
                        if interrupt_id:
                            pushed_ids.add(interrupt_id)
                            # 检查是否已处理：如果有 processed key，用其完整数据覆盖（含最终状态）
                            processed_key = f"{REDIS_APPROVAL_PREFIX}processed:{task_id}:{interrupt_id}"
                            processed_raw = redis_client.get(processed_key)
                            if processed_raw:
                                try:
                                    processed_data = json.loads(processed_raw if isinstance(processed_raw, str) else processed_raw.decode('utf-8'))
                                    # 用已处理数据覆盖原始 pending 数据，保留最终状态
                                    approval_data = {**approval_data, **processed_data}
                                except (json.JSONDecodeError, UnicodeDecodeError):
                                    pass
                        yield f"data: {json.dumps({'type': 'approval_history', 'data': approval_data, 'task_id': task_id}, ensure_ascii=False)}\n\n"
                    except (json.JSONDecodeError, KeyError):
                        pass
                if pending_approvals:
                    approval_ids = []
                    for item in pending_approvals:
                        try:
                            approval_ids.append(json.loads(item).get("interrupt_id", "?"))
                        except Exception:
                            approval_ids.append("?")
                    logger.info(f"[SSE] 推送 {len(pending_approvals)} 个历史审批事件, task={task_id}, ids={approval_ids}")
                else:
                    logger.info(f"[SSE] 无历史审批事件, task={task_id}")
            except Exception as e:
                logger.warning(f"[SSE] 读取历史审批失败: {e}")

        try:
            yield f"data: {json.dumps({'type': 'connected', 'task_id': task_id}, ensure_ascii=False)}\n\n"

            while True:
                elapsed = time.time() - start_time
                if elapsed > max_duration:
                    yield f"data: {json.dumps({'type': 'timeout', 'message': '连接超时'}, ensure_ascii=False)}\n\n"
                    break

                status_data = get_task_status(task_id, user_id=user.id)
                if not status_data:
                    yield sse_error_event(code="40401", message='任务不存在或无权访问')
                    break
                current_status = status_data.get('status')

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

                    if current_status == 'completed':
                        event_data['final_report'] = status_data.get('final_report', '')

                    yield f"data: {json.dumps(event_data, ensure_ascii=False, default=str)}\n\n"

                    if current_status in ('completed', 'failed'):
                        break

                cached = get_task_status(task_id, user_id=user.id)
                if cached and cached.get('current_step'):
                    step = cached['current_step']
                    if step != current_status:
                        yield f"data: {json.dumps({'type': 'step_update', 'step': step, 'task_id': task_id}, ensure_ascii=False)}\n\n"

                # 分步 sleep，每 0.5 秒检查一次审批频道，减少审批事件推送延迟
                for _ in range(4):
                    if approval_pubsub:
                        try:
                            msg = approval_pubsub.get_message(timeout=0.1)
                            if msg and msg['type'] == 'message':
                                data = msg['data']
                                if isinstance(data, bytes):
                                    data = data.decode('utf-8')
                                approval_data = json.loads(data)
                                msg_type = approval_data.get('type', 'approval')
                                yield f"data: {json.dumps({'type': msg_type, 'data': approval_data, 'task_id': task_id}, ensure_ascii=False)}\n\n"
                                logger.info(f"[SSE] 推送实时审批事件: type={msg_type}, tool={approval_data.get('tool_name')}, id={approval_data.get('interrupt_id')}")
                        except Exception as e:
                            logger.warning(f"[SSE] 检查审批频道失败: {e}")
                    time.sleep(0.5)

            yield f"data: {json.dumps({'type': 'done', 'task_id': task_id}, ensure_ascii=False)}\n\n"

        except GeneratorExit:
            logger.info(f"[API] SSE连接关闭，task_id={task_id}")
        except Exception as e:
            logger.error(f"[API] SSE流式输出异常：{e}", exc_info=True)
            yield sse_error_event(code="50001", message=str(e))
        finally:
            if approval_pubsub:
                try:
                    approval_pubsub.unsubscribe()
                    approval_pubsub.close()
                except Exception:
                    pass

    response = sse_response(event_stream())
    return response


class DeepResearchStreamView(APIView):
    permission_classes = [IsAuthenticatedOrQueryParam]
    renderer_classes = [SSERenderer]

    def get(self, request, task_id):
        return deep_research_stream(request, task_id)
