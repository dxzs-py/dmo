"""学习工作流 SSE 流式服务（从 views.py 抽离，P1 #7 拆分）。

职责：
    承载 learning 模块 SSE 生成器与流式辅助函数，使 ``views.py`` 仅保留
    类视图（CRUD + 启动/提交），符合"视图层薄、服务层厚"的分层架构。

模块内容：
    1. ``safe_publish_workflow_event`` — SSE 流中同步推送 WS 事件的共享工具
    2. ``workflow_stream`` — 函数视图：流式获取工作流执行进度（SSE）

设计说明：
    - 原位于 ``views.py``（L43-82 共享工具 + L700-912 函数视图），与视图层职责不同，
      故抽出独立服务模块。
    - 原模块内私有 ``_WorkflowJSONEncoder`` 与 ``common.json_utils.WorkflowJSONEncoder``
      实现完全一致，属于冗余死代码，本次拆分统一复用 ``json_utils`` 版本，不再保留本地副本。
    - ``views.py`` 通过 facade re-export 保持 ``urls.py`` 中
      ``views.workflow_stream`` 引用兼容；新代码应直接从本模块导入。

架构说明（工具调用 / 审批 / 实时同步）：
    Learning 模块是**预定义学习评测工作流**（StateGraph：planner → retrieval →
    quiz_generator → grading → feedback），不使用 agent 自主决策调用工具，
    因此不需要工具选择、工具调用生命周期事件、审批中断、实时同步等能力。
    知识库选择是用户启动前配置的参数（knowledge_base_ids），非运行时工具选择。
    对应前端代码无 ToolCallCard 渲染、无工具调用 UI。
"""

import json

from django.http import StreamingHttpResponse

from Django_xm.apps.core.config import get_logger
from Django_xm.common.event_schema import EventSource, EventType
from Django_xm.common.json_utils import WorkflowJSONEncoder
from Django_xm.common.realtime_events import publish_event
from Django_xm.common.sse_utils import sse_error_event, sse_response

from ..models import WorkflowSession
from .resilience import astream_with_resilience
from .study_flow import _get_study_flow, get_workflow_state

logger = get_logger(__name__)


async def safe_publish_workflow_event(
    event_type: EventType,
    thread_id: str,
    data: dict,
    user_id=None,
) -> None:
    """安全发布工作流事件到 task 频道，吞掉异常以避免影响 SSE 主流程。

    用于在 SSE 流式输出时同步推送 WebSocket 事件，使其他浏览器也能收到进度。
    使用异步 ``publish_event`` 避免在 ASGI 事件循环中阻塞（原 ``publish_event_sync``
    会在事件循环中执行同步 Redis 调用，导致其他请求被阻塞）。
    """
    try:
        payload = {
            "source": EventSource.LEARNING.value,
            "source_id": thread_id,
            "thread_id": thread_id,
            **data,
        }
        await publish_event(
            event_type,
            payload,
            task_id=thread_id,
            user_id=str(user_id) if user_id is not None else None,
        )
    except Exception as e:
        logger.warning(f"[API] publish_event 失败: {event_type}, {e}")


def workflow_stream(request, thread_id):
    """流式获取工作流执行进度（Server-Sent Events）。

    使用 LangGraph 的 stream 方法获取真实的事件流。
    支持 Authorization header 和查询参数 token 认证。

    SSE 事件类型统一为 ``workflow_*`` 格式，与 ``EventType`` 枚举对齐：
        - ``workflow_step``: 工作流步骤更新（含 step / message）
        - ``workflow_state_update``: 状态更新（含完整数据，如 learning_plan / quiz 等）
        - ``workflow_completed``: 工作流完成
        - ``workflow_failed``: 工作流失败
        - ``error``: 旧版兼容错误事件
    """
    from Django_xm.common.sse_utils import authenticate_sse_request, sse_error_response

    user = authenticate_sse_request(request)

    if not user:
        return sse_error_response("未登录或登录已过期", 401, code="40101")

    try:
        session = WorkflowSession.objects.filter(thread_id=thread_id, created_by=user, is_deleted=False).first()

        if not session:
            state = get_workflow_state(thread_id)
            if not state:
                return sse_error_response("工作流不存在或无权访问", 404, code="40401")

            try:
                from .persistence_service import get_persistence_service

                persistence_service = get_persistence_service()
                persistence_service.save_workflow_state(thread_id=thread_id, state=state, user_id=user.id)
                logger.info(f"[API] 从checkpointer恢复工作流会话: {thread_id}")
            except Exception as persist_err:
                logger.warning(f"[API] 恢复工作流会话失败: {persist_err}")

        logger.info(f"[API] 流式获取工作流，thread_id={thread_id}, user_id={user.id}")

        async def event_stream():
            """生成 SSE 事件流（统一 workflow_* 事件格式，与 EventType 枚举对齐）"""
            import asyncio

            try:
                state = await asyncio.to_thread(get_workflow_state, thread_id)
                if not state:
                    yield sse_error_event(code="40401", message="工作流不存在")
                    return

                current_step = state.get("current_step", "unknown")
                start_evt = {
                    "type": "workflow_step",
                    "data": {"step": "start", "message": "工作流启动中...", "state": current_step},
                }
                yield f"data: {json.dumps(start_evt, ensure_ascii=False)}\n\n"
                await safe_publish_workflow_event(
                    EventType.WORKFLOW_STEP,
                    thread_id,
                    {"step": "start", "message": "工作流启动中...", "state": current_step},
                    user_id=user.id,
                )

                if current_step == "waiting_for_answers":
                    waiting_state_evt = {
                        "type": "workflow_state_update",
                        "data": {
                            "step": current_step,
                            "state": "waiting_for_answers",
                            "learning_plan": state.get("learning_plan"),
                            "quiz": state.get("quiz"),
                            "current_step": current_step,
                            "thread_id": thread_id,
                        },
                    }
                    yield f"data: {json.dumps(waiting_state_evt, ensure_ascii=False, cls=WorkflowJSONEncoder)}\n\n"
                    await safe_publish_workflow_event(
                        EventType.WORKFLOW_STATE_UPDATE,
                        thread_id,
                        {
                            "step": current_step,
                            "state": "waiting_for_answers",
                            "message": "等待用户提交答案",
                        },
                        user_id=user.id,
                    )
                    complete_evt = {"type": "workflow_completed", "data": {"thread_id": thread_id}}
                    yield f"data: {json.dumps(complete_evt, ensure_ascii=False)}\n\n"
                    await safe_publish_workflow_event(
                        EventType.WORKFLOW_COMPLETED,
                        thread_id,
                        {"step": current_step},
                        user_id=user.id,
                    )
                    return

                if current_step in ("completed", "end", "feedback_completed"):
                    completed_state_evt = {
                        "type": "workflow_state_update",
                        "data": {
                            "step": current_step,
                            "score": state.get("score"),
                            "feedback": state.get("feedback"),
                            "score_details": state.get("score_details"),
                        },
                    }
                    yield f"data: {json.dumps(completed_state_evt, ensure_ascii=False, cls=WorkflowJSONEncoder)}\n\n"
                    await safe_publish_workflow_event(
                        EventType.WORKFLOW_STATE_UPDATE,
                        thread_id,
                        {
                            "step": current_step,
                            "score": state.get("score"),
                            "feedback": state.get("feedback"),
                            "score_details": state.get("score_details"),
                        },
                        user_id=user.id,
                    )
                    complete_evt = {"type": "workflow_completed", "data": {"thread_id": thread_id}}
                    yield f"data: {json.dumps(complete_evt, ensure_ascii=False)}\n\n"
                    await safe_publish_workflow_event(
                        EventType.WORKFLOW_COMPLETED,
                        thread_id,
                        {"step": current_step},
                        user_id=user.id,
                    )
                    return

                study_flow = _get_study_flow(thread_id)
                config = {"configurable": {"thread_id": thread_id}}

                try:
                    async for event in astream_with_resilience(
                        study_flow.graph, None, config, stream_mode="values"
                    ):
                        if event:
                            step = event.get("current_step", "unknown")
                            state_update_evt = {
                                "type": "workflow_state_update",
                                "data": {
                                    "step": step,
                                    "state": step,
                                    "payload": event,
                                },
                            }
                            state_update_data = json.dumps(
                                state_update_evt, ensure_ascii=False, cls=WorkflowJSONEncoder
                            )
                            yield f"data: {state_update_data}\n\n"
                            await safe_publish_workflow_event(
                                EventType.WORKFLOW_STATE_UPDATE,
                                thread_id,
                                {"step": step, "state": step},
                                user_id=user.id,
                            )

                            if step == "waiting_for_answers":
                                waiting_evt = {
                                    "type": "workflow_state_update",
                                    "data": {
                                        "step": step,
                                        "state": "waiting_for_answers",
                                        "message": "等待用户提交答案",
                                    },
                                }
                                yield f"data: {json.dumps(waiting_evt, ensure_ascii=False)}\n\n"
                                await safe_publish_workflow_event(
                                    EventType.WORKFLOW_STATE_UPDATE,
                                    thread_id,
                                    {
                                        "step": step,
                                        "state": "waiting_for_answers",
                                        "message": "等待用户提交答案",
                                    },
                                    user_id=user.id,
                                )
                                complete_evt = {"type": "workflow_completed", "data": {"thread_id": thread_id}}
                                yield f"data: {json.dumps(complete_evt, ensure_ascii=False)}\n\n"
                                await safe_publish_workflow_event(
                                    EventType.WORKFLOW_COMPLETED,
                                    thread_id,
                                    {"step": step},
                                    user_id=user.id,
                                )
                                return

                    complete_evt = {"type": "workflow_completed", "data": {"thread_id": thread_id}}
                    yield f"data: {json.dumps(complete_evt, ensure_ascii=False)}\n\n"
                    await safe_publish_workflow_event(
                        EventType.WORKFLOW_COMPLETED,
                        thread_id,
                        {"step": "completed"},
                        user_id=user.id,
                    )

                except Exception as stream_error:
                    logger.warning(f"[API] 流式执行失败：{stream_error}")
                    await safe_publish_workflow_event(
                        EventType.WORKFLOW_FAILED,
                        thread_id,
                        {"step": "stream", "error": str(stream_error)},
                        user_id=user.id,
                    )
                    yield sse_error_event(code="50001", message=str(stream_error))
                    complete_evt = {"type": "workflow_completed", "data": {"thread_id": thread_id}}
                    yield f"data: {json.dumps(complete_evt, ensure_ascii=False)}\n\n"

            except Exception as e:
                logger.exception("[API] 流式输出失败：")
                await safe_publish_workflow_event(
                    EventType.WORKFLOW_FAILED,
                    thread_id,
                    {"step": "stream", "error": str(e)},
                    user_id=user.id if user else None,
                )
                yield sse_error_event(code="50001", message=str(e))

        return sse_response(event_stream())

    except Exception as e:
        error_msg = str(e)
        logger.exception(f"[API] 流式输出失败：{error_msg}")

        def error_event():
            yield sse_error_event(code="50001", message=error_msg)

        return StreamingHttpResponse(
            error_event(), content_type="text/event-stream", status=500, headers={"Cache-Control": "no-cache"}
        )