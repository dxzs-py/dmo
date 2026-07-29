import json
import logging
import time

from rest_framework.renderers import BaseRenderer
from rest_framework.views import APIView

from Django_xm.common.permissions import IsAuthenticatedOrQueryParam
from Django_xm.common.sse_utils import authenticate_sse_request, sse_error_event, sse_error_response, sse_response

from .models import ResearchTask
from .services.task_manager import get_task_status


class SSERenderer(BaseRenderer):
    media_type = "text/event-stream"
    format = "txt"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


logger = logging.getLogger(__name__)


def _load_approval_history_from_db(task_id: str) -> list[dict]:
    """从 Approval DB 读取历史审批数据（Path D：DB 为唯一真相源）。

    替代原 Redis List 读取逻辑：
    - 旧：REDIS_APPROVAL_PREFIX:pending:{task_id} + processed key
    - 新：Approval.objects.filter(source=DEEP_RESEARCH, source_id=task_id)

    实时审批事件通过 WebSocket 推送，SSE 仅负责初始 history 加载。

    Returns:
        审批数据 dict 列表（与原 Redis 格式兼容）
    """
    try:
        from Django_xm.apps.approvals.models import Approval

        approvals = Approval.objects.filter(
            source=Approval.SOURCE_DEEP_RESEARCH,
            source_id=task_id,
        ).order_by("created_at")

        result = []
        for approval in approvals:
            extra = approval.extra if isinstance(approval.extra, dict) else {}
            approval_data = {
                "interrupt_id": approval.interrupt_id,
                "tool_name": approval.tool_name or "",
                "title": approval.title or "",
                "description": approval.description or "",
                "operation": approval.operation or "",
                "danger_level": approval.danger_level or "medium",
                "action": approval.action or "confirm",
                "parameters": approval.parameters or {},
                "state": approval.state,
                "tool_call_id": extra.get("tool_call_id", ""),
                "graph_interrupt_id": extra.get("graph_interrupt_id", ""),
                "langgraph_resume_id": extra.get("langgraph_resume_id", ""),
                "risk_level": extra.get("risk_level", ""),
                "created_at": approval.created_at.isoformat() if approval.created_at else None,
                "resolved_at": approval.resolved_at.isoformat() if approval.resolved_at else None,
            }
            if approval.user_input:
                approval_data["user_input"] = approval.user_input
            result.append(approval_data)

        return result
    except Exception as e:
        logger.warning(f"[SSE] 从 DB 读取审批历史失败: {e}")
        return []


def deep_research_stream(request, task_id):
    user = authenticate_sse_request(request)

    if not user:
        return sse_error_response("未登录或登录已过期", 401, code="40101")

    try:
        ResearchTask.objects.get(
            task_id=task_id,
            created_by=user,
            is_deleted=False,
        )
    except ResearchTask.DoesNotExist:
        return sse_error_response("研究任务不存在", 404, code="40401")

    logger.info(f"[API] SSE流式监听研究进度，task_id={task_id}, user_id={user.id}")

    def event_stream():
        last_status = None
        start_time = time.time()
        max_duration = 600

        # Path D：从 DB 读取历史审批数据（替代原 Redis List）
        # 实时 approval 事件统一通过 WebSocket 推送（与 tool 事件一致），
        # SSE 仅负责初始 approval_history 加载。
        approval_history = _load_approval_history_from_db(task_id)
        if approval_history:
            for approval_data in approval_history:
                yield f"data: {json.dumps({'type': 'approval_history', 'data': approval_data, 'task_id': task_id}, ensure_ascii=False, default=str)}\n\n"
            logger.info(
                f"[SSE] 推送 {len(approval_history)} 个历史审批事件, task={task_id}, "
                f"ids={[a.get('interrupt_id', '?') for a in approval_history]}"
            )
        else:
            logger.info(f"[SSE] 无历史审批事件, task={task_id}")

        try:
            yield f"data: {json.dumps({'type': 'connected', 'task_id': task_id}, ensure_ascii=False)}\n\n"

            while True:
                elapsed = time.time() - start_time
                if elapsed > max_duration:
                    yield f"data: {json.dumps({'type': 'timeout', 'message': '连接超时'}, ensure_ascii=False)}\n\n"
                    break

                status_data = get_task_status(task_id, user_id=user.id)
                if not status_data:
                    yield sse_error_event(code="40401", message="任务不存在或无权访问")
                    break
                current_status = status_data.get("status")

                if current_status != last_status:
                    last_status = current_status

                    step_messages = {
                        "pending": "研究任务已创建，等待执行...",
                        "running": "正在执行深度研究...",
                        "completed": "研究已完成！",
                        "failed": "研究执行失败",
                    }

                    event_data = {
                        "type": "status_change",
                        "status": current_status,
                        "message": step_messages.get(current_status, f"状态: {current_status}"),
                        "task_id": task_id,
                    }

                    if current_status == "completed":
                        event_data["final_report"] = status_data.get("final_report", "")

                    yield f"data: {json.dumps(event_data, ensure_ascii=False, default=str)}\n\n"

                    if current_status in ("completed", "failed"):
                        break

                cached = get_task_status(task_id, user_id=user.id)
                if cached and cached.get("current_step"):
                    step = cached["current_step"]
                    if step != current_status:
                        yield f"data: {json.dumps({'type': 'step_update', 'step': step, 'task_id': task_id}, ensure_ascii=False)}\n\n"

                # 分步 sleep 控制 while 循环频率（每 0.5s 让出一次，总 2s 间隔）
                # 实时 approval 事件统一通过 WebSocket 推送（与 tool 事件一致）
                for _ in range(4):
                    time.sleep(0.5)

            yield f"data: {json.dumps({'type': 'done', 'task_id': task_id}, ensure_ascii=False)}\n\n"

        except GeneratorExit:
            logger.info(f"[API] SSE连接关闭，task_id={task_id}")
        except Exception as e:
            logger.exception("[API] SSE流式输出异常：")
            yield sse_error_event(code="50001", message=str(e))

    response = sse_response(event_stream())
    return response


class DeepResearchStreamView(APIView):
    permission_classes = [IsAuthenticatedOrQueryParam]
    renderer_classes = [SSERenderer]

    def get(self, request, task_id):
        return deep_research_stream(request, task_id)
