"""
工作流API视图
使用类视图和服务层实现，遵循项目统一的响应格式规范
"""
import json
import uuid
from urllib.parse import quote
from django.http import StreamingHttpResponse, FileResponse, HttpResponse
from rest_framework.views import APIView
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from Django_xm.apps.common.responses import success_response, error_response, not_found_response, validation_error_response
from Django_xm.apps.common.error_codes import ErrorCode

from .serializers import (
    WorkflowStartSerializer,
    WorkflowSubmitSerializer,
    WorkflowResponseSerializer,
    WorkflowSessionSerializer
)
from .services import WorkflowService
from .services.study_flow import get_workflow_state, _get_study_flow
from .models import WorkflowSession
from Django_xm.apps.core.services.file_manager import get_file_manager
from Django_xm.apps.ai_engine.config import get_logger
from Django_xm.apps.core.permissions import IsAuthenticatedOrQueryParam

logger = get_logger(__name__)
file_manager = get_file_manager()


class _WorkflowJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        from langchain_core.messages import BaseMessage
        if isinstance(obj, BaseMessage):
            return {"type": obj.type, "content": obj.content}
        if hasattr(obj, 'model_dump'):
            return obj.model_dump()
        if hasattr(obj, 'dict'):
            return obj.dict()
        if hasattr(obj, '__dict__'):
            return str(obj)
        return super().default(obj)


class WorkflowStartView(APIView):
    """启动学习工作流视图"""
    permission_classes = [IsAuthenticated]

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
            return validation_error_response(
                errors=serializer.errors,
                message="数据验证失败"
            )

        try:
            user_question = serializer.validated_data.get('user_question') or serializer.validated_data.get('query')
            result = WorkflowService.start_workflow(
                user_question=user_question,
                thread_id=serializer.validated_data.get('thread_id'),
                user_id=request.user.id
            )

            try:
                from .services.persistence_service import get_persistence_service
                persistence_service = get_persistence_service()
                persistence_service.save_workflow_state(
                    thread_id=result['thread_id'],
                    state=result,
                    user_id=request.user.id
                )
            except Exception as persist_err:
                logger.warning(f"持久化工作流会话失败: {persist_err}")

            response_serializer = WorkflowResponseSerializer(result)
            return success_response(
                data=response_serializer.data,
                message='操作成功'
            )

        except Exception as e:
            logger.error(f"[API] 启动工作流失败：{str(e)}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class WorkflowStartStreamView(APIView):
    """启动学习工作流（SSE流式输出）"""
    permission_classes = [IsAuthenticated]

    def options(self, request):
        response = HttpResponse(status=200)
        origin = request.headers.get('Origin', '*')
        response['Access-Control-Allow-Origin'] = origin
        response['Access-Control-Allow-Credentials'] = 'true'
        response['Access-Control-Allow-Headers'] = 'authorization,content-type,x-csrftoken,x-requested-with'
        response['Access-Control-Allow-Methods'] = 'GET,POST,OPTIONS'
        response['Access-Control-Max-Age'] = '86400'
        return response

    def post(self, request):
        """
        启动工作流并以SSE流式返回执行进度

        SSE事件类型：
        - start: 工作流开始
        - step: 步骤更新
        - state_update: 状态更新（含完整数据）
        - waiting: 等待用户输入
        - complete: 工作流完成
        - error: 错误
        """
        serializer = WorkflowStartSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(
                errors=serializer.errors,
                message="数据验证失败"
            )

        user_question = serializer.validated_data.get('user_question') or serializer.validated_data.get('query')
        thread_id = serializer.validated_data.get('thread_id') or f"study_{uuid.uuid4().hex[:12]}"
        user_id = request.user.id

        logger.info(f"[API] 流式启动工作流，thread_id={thread_id}")

        def event_stream():
            try:
                yield f"data: {json.dumps({'type': 'start', 'thread_id': thread_id, 'message': '工作流启动中...'}, ensure_ascii=False)}\n\n"

                from .study_flow import _get_study_flow, StudyFlowState
                from datetime import datetime

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

                yield f"data: {json.dumps({'type': 'step', 'step': 'planner', 'message': '正在生成学习计划...'}, ensure_ascii=False)}\n\n"

                for event in study_flow.graph.stream(initial_state, config, stream_mode="values"):
                    if not event:
                        continue

                    current_step = event.get('current_step', 'unknown')

                    step_messages = {
                        'planner': '正在生成学习计划...',
                        'retrieval': '正在检索相关资料...',
                        'quiz_generator': '正在生成练习题...',
                        'waiting_for_answers': '等待您提交答案...',
                        'grading': '正在评分...',
                        'feedback': '正在生成反馈...',
                        'end': '工作流已完成',
                    }

                    step_message = step_messages.get(current_step, f'当前步骤: {current_step}')

                    yield f"data: {json.dumps({'type': 'step', 'step': current_step, 'message': step_message}, ensure_ascii=False)}\n\n"

                    if current_step == 'waiting_for_answers':
                        waiting_data = {
                            'type': 'waiting',
                            'step': current_step,
                            'data': {
                                'thread_id': thread_id,
                                'learning_plan': event.get('learning_plan'),
                                'quiz': event.get('quiz'),
                                'current_step': current_step,
                            }
                        }
                        yield f"data: {json.dumps(waiting_data, ensure_ascii=False, cls=_WorkflowJSONEncoder)}\n\n"
                        break

                    state_data = {
                        'type': 'state_update',
                        'step': current_step,
                        'data': {
                            'learning_plan': event.get('learning_plan'),
                            'retrieved_docs': event.get('retrieved_docs'),
                            'quiz': event.get('quiz'),
                        }
                    }
                    yield f"data: {json.dumps(state_data, ensure_ascii=False, cls=_WorkflowJSONEncoder)}\n\n"

                yield f"data: {json.dumps({'type': 'complete', 'thread_id': thread_id}, ensure_ascii=False)}\n\n"

                try:
                    from .services.persistence_service import get_persistence_service
                    persistence_service = get_persistence_service()
                    persistence_service.save_workflow_state(
                        thread_id=thread_id,
                        state=study_flow.graph.get_state(config).values,
                        user_id=user_id
                    )
                except Exception as persist_err:
                    logger.warning(f"持久化工作流会话失败: {persist_err}")

            except Exception as e:
                logger.error(f"[API] 流式工作流执行失败：{str(e)}", exc_info=True)
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"

        return StreamingHttpResponse(
            event_stream(),
            content_type='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
                'Access-Control-Allow-Origin': request.headers.get('Origin', '*'),
                'Access-Control-Allow-Credentials': 'true',
            }
        )


class WorkflowSubmitView(APIView):
    """提交用户答案视图"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        """
        提交用户答案，继续执行工作流
        """
        serializer = WorkflowSubmitSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(
                errors=serializer.errors,
                message="数据验证失败"
            )

        try:
            thread_id = serializer.validated_data['thread_id']
            session = WorkflowSession.objects.filter(
                thread_id=thread_id,
                created_by=request.user,
                is_deleted=False
            ).first()
            if not session:
                from .services.persistence_service import get_persistence_service
                persistence_service = get_persistence_service()
                saved_state = persistence_service.load_workflow_state(thread_id)
                if saved_state:
                    persistence_service.save_workflow_state(
                        thread_id=thread_id,
                        state=saved_state,
                        user_id=request.user.id
                    )
                    logger.info(f"[API] 从持久化恢复工作流会话: {thread_id}")
                else:
                    return error_response(
                        code=ErrorCode.NOT_FOUND,
                        message='工作流会话不存在或无权访问',
                        http_status=status.HTTP_404_NOT_FOUND
                    )

            result = WorkflowService.submit_user_answers(
                thread_id=thread_id,
                answers=serializer.validated_data['answers'],
                user_id=request.user.id
            )

            try:
                from .services.persistence_service import get_persistence_service
                persistence_service = get_persistence_service()
                persistence_service.save_workflow_state(
                    thread_id=thread_id,
                    state=result,
                    user_id=request.user.id
                )
            except Exception as persist_err:
                logger.warning(f"持久化工作流会话失败: {persist_err}")

            return success_response(
                data=result,
                message='操作成功'
            )

        except Exception as e:
            logger.error(f"[API] 提交答案失败：{str(e)}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class WorkflowStatusView(APIView):
    """获取工作流状态视图"""
    permission_classes = [IsAuthenticated]

    def get(self, request, thread_id):
        try:
            session = WorkflowSession.objects.filter(
                thread_id=thread_id,
                created_by=request.user,
                is_deleted=False
            ).first()

            state = WorkflowService.get_workflow_status(thread_id)

            if not session and state:
                try:
                    from .services.persistence_service import get_persistence_service
                    persistence_service = get_persistence_service()
                    persistence_service.save_workflow_state(
                        thread_id=thread_id,
                        state=state,
                        user_id=request.user.id
                    )
                except Exception:
                    pass

            if not state:
                return error_response(
                    code=ErrorCode.NOT_FOUND,
                    message='工作流会话不存在或无权访问',
                    http_status=status.HTTP_404_NOT_FOUND
                )

            return success_response(
                data={
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
            )

        except Exception as e:
            logger.error(f"[API] 查询状态失败：{str(e)}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class WorkflowHistoryView(APIView):
    """获取工作流历史视图"""
    permission_classes = [IsAuthenticated]

    def get(self, request, thread_id):
        try:
            history = WorkflowService.get_workflow_history(thread_id)
            return success_response(
                data={"thread_id": thread_id, "history": history}
            )

        except Exception as e:
            logger.error(f"[API] 查询历史失败：{str(e)}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class WorkflowDeleteView(APIView):
    """删除工作流视图"""
    permission_classes = [IsAuthenticated]

    def delete(self, request, thread_id):
        try:
            result = WorkflowService.delete_workflow(thread_id, request.user.id)
            return success_response(
                data=result,
                message='操作成功'
            )

        except Exception as e:
            logger.error(f"[API] 删除工作流失败：{str(e)}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class WorkflowListView(APIView):
    """获取工作流列表视图"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            status_filter = request.query_params.get('status')
            search_query = request.query_params.get('search')
            page = int(request.query_params.get('page', 1))
            page_size = min(int(request.query_params.get('page_size', 20)), 100)

            queryset = WorkflowService.list_user_workflows(
                user_id=request.user.id,
                status=status_filter,
                search=search_query
            )

            total = queryset.count()
            start = (page - 1) * page_size
            end = start + page_size
            sessions = queryset[start:end]

            serializer = WorkflowSessionSerializer(sessions, many=True)

            return success_response(
                data={
                    'items': serializer.data,
                    'total': total,
                    'page': page,
                    'page_size': page_size,
                    'total_pages': (total + page_size - 1) // page_size,
                }
            )

        except Exception as e:
            logger.error(f"[API] 获取工作流列表失败：{str(e)}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class WorkflowFilesListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, thread_id):
        try:
            session = WorkflowSession.objects.filter(
                thread_id=thread_id,
                created_by=request.user,
                is_deleted=False
            ).first()
            if not session:
                return error_response(
                    code=ErrorCode.NOT_FOUND,
                    message='工作流会话不存在或无权访问',
                    http_status=status.HTTP_404_NOT_FOUND
                )

            files = file_manager.list_task_files(thread_id, 'workflow')
            
            from Django_xm.apps.research.serializers import FileInfoSerializer
            serializer = FileInfoSerializer([f.to_dict() for f in files], many=True)

            return success_response(
                data={
                    'thread_id': thread_id,
                    'files': serializer.data,
                    'total': len(files),
                }
            )

        except Exception as e:
            logger.error(f"列出工作流文件失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message='获取文件列表失败',
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class WorkflowFileDownloadView(APIView):
    permission_classes = [IsAuthenticatedOrQueryParam]

    def get(self, request, thread_id, filename):
        try:
            user = request.user if request.user.is_authenticated else None
            if not user:
                return error_response(
                    code=ErrorCode.UNAUTHORIZED,
                    message='未认证',
                    http_status=status.HTTP_401_UNAUTHORIZED
                )
            session = WorkflowSession.objects.filter(
                thread_id=thread_id,
                created_by=user,
                is_deleted=False
            ).first()
            if not session:
                return error_response(
                    code=ErrorCode.NOT_FOUND,
                    message='工作流会话不存在或无权访问',
                    http_status=status.HTTP_404_NOT_FOUND
                )

            file_info = file_manager.get_file_info(thread_id, filename, 'workflow')
            if not file_info:
                return not_found_response(message='文件不存在')

            file_path = file_info.path

            content_type = 'application/octet-stream'
            if file_path.suffix.lower() in ['.md', '.txt']:
                content_type = 'text/plain; charset=utf-8'
            elif file_path.suffix.lower() == '.json':
                content_type = 'application/json'

            response = FileResponse(
                open(file_path, 'rb'),
                content_type=content_type,
                as_attachment=True,
                filename=quote(file_path.name)
            )
            return response

        except Exception as e:
            logger.error(f"下载文件失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message='文件下载失败',
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class WorkflowFileContentView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, thread_id, filename):
        try:
            session = WorkflowSession.objects.filter(
                thread_id=thread_id,
                created_by=request.user,
                is_deleted=False
            ).first()
            if not session:
                return error_response(
                    code=ErrorCode.NOT_FOUND,
                    message='工作流会话不存在或无权访问',
                    http_status=status.HTTP_404_NOT_FOUND
                )

            file_info = file_manager.get_file_info(thread_id, filename, 'workflow')
            if not file_info:
                return not_found_response(message='文件不存在')

            content = file_manager.read_file_content(thread_id, filename, 'workflow')

            from Django_xm.apps.research.serializers import FileInfoSerializer
            return success_response(
                data={
                    'filename': filename,
                    'content': content,
                    'file_info': FileInfoSerializer(file_info.to_dict()).data,
                }
            )

        except Exception as e:
            logger.error(f"读取文件内容失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message='读取文件内容失败',
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


def workflow_stream(request, thread_id):
    """
    流式获取工作流执行进度（Server-Sent Events）
    使用 LangGraph 的 stream 方法获取真实的事件流
    支持Authorization header和查询参数token认证
    """
    from Django_xm.apps.research.views import _authenticate_sse_request, _sse_error_response

    user = _authenticate_sse_request(request)

    if not user:
        return _sse_error_response('未登录或登录已过期', 401)

    try:
        session = WorkflowSession.objects.filter(
            thread_id=thread_id,
            created_by=user,
            is_deleted=False
        ).first()

        if not session:
            state = get_workflow_state(thread_id)
            if not state:
                return _sse_error_response('工作流不存在或无权访问', 404)

            try:
                from .services.persistence_service import get_persistence_service
                persistence_service = get_persistence_service()
                persistence_service.save_workflow_state(
                    thread_id=thread_id,
                    state=state,
                    user_id=user.id
                )
                logger.info(f"[API] 从checkpointer恢复工作流会话: {thread_id}")
            except Exception as persist_err:
                logger.warning(f"[API] 恢复工作流会话失败: {persist_err}")

        logger.info(f"[API] 流式获取工作流，thread_id={thread_id}, user_id={user.id}")

        def event_stream():
            """生成 SSE 事件流"""
            try:
                state = get_workflow_state(thread_id)

                if not state:
                    yield f"data: {json.dumps({'type': 'error', 'message': '工作流不存在'}, ensure_ascii=False)}\n\n"
                    return

                current_step = state.get('current_step', 'unknown')
                yield f"data: {json.dumps({'type': 'start', 'state': current_step}, ensure_ascii=False)}\n\n"

                if current_step == 'waiting_for_answers':
                    yield f"data: {json.dumps({'type': 'state_update', 'step': current_step, 'data': {'learning_plan': state.get('learning_plan'), 'quiz': state.get('quiz'), 'current_step': current_step, 'thread_id': thread_id}}, ensure_ascii=False, cls=_WorkflowJSONEncoder)}\n\n"
                    yield f"data: {json.dumps({'type': 'waiting', 'step': current_step, 'message': '等待用户提交答案'}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'type': 'complete'}, ensure_ascii=False)}\n\n"
                    return

                if current_step in ('completed', 'end', 'feedback_completed'):
                    yield f"data: {json.dumps({'type': 'state_update', 'step': current_step, 'data': {'score': state.get('score'), 'feedback': state.get('feedback'), 'score_details': state.get('score_details')}}, ensure_ascii=False, cls=_WorkflowJSONEncoder)}\n\n"
                    yield f"data: {json.dumps({'type': 'complete'}, ensure_ascii=False)}\n\n"
                    return

                study_flow = _get_study_flow(thread_id)
                config = {"configurable": {"thread_id": thread_id}}

                try:
                    for event in study_flow.graph.stream(None, config, stream_mode="values"):
                        if event:
                            step = event.get('current_step', 'unknown')
                            yield f"data: {json.dumps({'type': 'state_update', 'step': step, 'data': event}, ensure_ascii=False, cls=_WorkflowJSONEncoder)}\n\n"

                            if step == 'waiting_for_answers':
                                yield f"data: {json.dumps({'type': 'waiting', 'step': step, 'message': '等待用户提交答案'}, ensure_ascii=False)}\n\n"
                                yield f"data: {json.dumps({'type': 'complete'}, ensure_ascii=False)}\n\n"
                                return

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
        def error_event():
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
        return StreamingHttpResponse(
            error_event(),
            content_type='text/event-stream',
            status=500,
            headers={'Cache-Control': 'no-cache'}
        )
