"""
工作流API视图
使用类视图和服务层实现
"""
import json
from django.http import StreamingHttpResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .serializers import (
    WorkflowStartSerializer,
    WorkflowSubmitSerializer,
    WorkflowResponseSerializer
)
from .services import WorkflowService
from .study_flow import get_workflow_state, _get_study_flow
from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)


class WorkflowStartView(APIView):
    """启动学习工作流视图"""

    def post(self, request):
        """
        启动学习工作流

        工作流会自动执行以下步骤：
        1. 分析用户问题，生成学习计划
        2. 检索相关文档
        3. 生成练习题
        4. 暂停，等待用户提交答案
        """
        serializer = WorkflowStartSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            user_question = serializer.validated_data.get('user_question') or serializer.validated_data.get('query')
            result = WorkflowService.start_workflow(
                user_question=user_question,
                thread_id=serializer.validated_data.get('thread_id')
            )

            response_serializer = WorkflowResponseSerializer(result)
            return Response({
                'code': 0,
                'message': '操作成功',
                'data': response_serializer.data
            })

        except Exception as e:
            logger.error(f"[API] 启动工作流失败：{str(e)}", exc_info=True)
            return Response({
                'code': 500,
                'message': str(e)
            }, status=status.HTTP_500_INTERNAL_ERROR)


class WorkflowSubmitView(APIView):
    """提交用户答案视图"""

    def post(self, request):
        """
        提交用户答案，继续执行工作流
        """
        serializer = WorkflowSubmitSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            result = WorkflowService.submit_user_answers(
                thread_id=serializer.validated_data['thread_id'],
                answers=serializer.validated_data['answers']
            )

            return Response({
                'code': 0,
                'message': '操作成功',
                'data': result
            })

        except Exception as e:
            logger.error(f"[API] 提交答案失败：{str(e)}", exc_info=True)
            return Response({
                'code': 500,
                'message': str(e)
            }, status=status.HTTP_500_INTERNAL_ERROR)


class WorkflowStatusView(APIView):
    """获取工作流状态视图"""

    def get(self, request, thread_id):
        """
        获取工作流的当前状态
        """
        try:
            state = WorkflowService.get_workflow_status(thread_id)
            logger.info(f"[WorkflowStatusView] Got state for thread {thread_id}: {state}")

            if not state:
                logger.warning(f"[WorkflowStatusView] No state found for thread {thread_id}")
                return Response({
                    'code': 404,
                    'message': '工作流会话不存在'
                }, status=status.HTTP_404_NOT_FOUND)

            return Response({
                'code': 0,
                'message': '操作成功',
                'data': {
                    "thread_id": thread_id,
                    "current_step": state.get("current_step"),
                    "status": state.get("current_step"),
                    "user_question": state.get("user_question"),
                    "learning_plan": state.get("learning_plan"),
                    "retrieved_docs": state.get("retrieved_docs"),
                    "quiz": state.get("quiz"),
                    "user_answers": state.get("user_answers"),
                    "score": state.get("score"),
                    "score_details": state.get("score_details"),
                    "feedback": state.get("feedback"),
                    "should_retry": state.get("should_retry", False),
                    "retry_count": state.get("retry_count", 0),
                    "created_at": state.get("created_at", ""),
                    "updated_at": state.get("updated_at", "")
                }
            })

        except Exception as e:
            logger.error(f"[API] 查询状态失败：{str(e)}", exc_info=True)
            return Response({
                'code': 500,
                'message': str(e)
            }, status=status.HTTP_500_INTERNAL_ERROR)


class WorkflowHistoryView(APIView):
    """获取工作流历史视图"""

    def get(self, request, thread_id):
        try:
            history = WorkflowService.get_workflow_history(thread_id)
            return Response({
                'code': 0,
                'message': '操作成功',
                'data': {"thread_id": thread_id, "history": history}
            })

        except Exception as e:
            logger.error(f"[API] 查询历史失败：{str(e)}", exc_info=True)
            return Response({
                'code': 500,
                'message': str(e)
            }, status=status.HTTP_500_INTERNAL_ERROR)


class WorkflowDeleteView(APIView):
    """删除工作流视图"""

    def delete(self, request, thread_id):
        try:
            result = WorkflowService.delete_workflow(thread_id)
            return Response({
                'code': 0,
                'message': '操作成功',
                'data': result
            })

        except Exception as e:
            logger.error(f"[API] 删除工作流失败：{str(e)}", exc_info=True)
            return Response({
                'code': 500,
                'message': str(e)
            }, status=status.HTTP_500_INTERNAL_ERROR)


def workflow_stream(request, thread_id):
    """
    流式获取工作流执行进度（Server-Sent Events）
    使用 LangGraph 的 stream 方法获取真实的事件流
    """
    try:
        logger.info(f"[API] 流式获取工作流，thread_id={thread_id}")

        def event_stream():
            """生成 SSE 事件流"""
            try:
                state = get_workflow_state(thread_id)

                if not state:
                    yield f"data: {json.dumps({'type': 'error', 'message': '工作流不存在'}, ensure_ascii=False)}\n\n"
                    return

                yield f"data: {json.dumps({'type': 'start', 'state': state.get('current_step')}, ensure_ascii=False)}\n\n"

                study_flow = _get_study_flow(thread_id)
                config = {"configurable": {"thread_id": thread_id}}

                try:
                    for event in study_flow.graph.stream(None, config, stream_mode="values"):
                        if event:
                            current_step = event.get('current_step', 'unknown')
                            yield f"data: {json.dumps({'type': 'state_update', 'step': current_step, 'data': event}, ensure_ascii=False)}\n\n"

                    yield f"data: {json.dumps({'type': 'complete'}, ensure_ascii=False)}\n\n"

                except Exception as stream_error:
                    logger.warning(f"[API] 流式执行失败：{stream_error}")
                    yield f"data: {json.dumps({'type': 'stream_error', 'message': str(stream_error)}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'type': 'complete'}, ensure_ascii=False)}\n\n"

            except Exception as e:
                logger.error(f"[API] 流式输出失败：{str(e)}", exc_info=True)
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"

        return StreamingHttpResponse(
            event_stream(),
            content_type='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
            }
        )

    except Exception as e:
        logger.error(f"[API] 流式输出失败：{str(e)}", exc_info=True)
        return Response({
            'code': 500,
            'message': str(e)
        }, status=status.HTTP_500_INTERNAL_ERROR)
