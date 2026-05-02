import logging
import time
import uuid
import json
from datetime import datetime
from typing import Optional, Dict, Any
from urllib.parse import quote

from rest_framework.views import APIView
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.db import transaction

from Django_xm.apps.common.responses import success_response, error_response, not_found_response
from Django_xm.apps.common.error_codes import ErrorCode

from .serializers import (
    ResearchStartSerializer,
    ResearchTaskSerializer,
    ResearchResultSerializer,
    FileInfoSerializer,
)
from .models import ResearchTask
from Django_xm.tasks.deep_research import run_research_task
from .services.task_manager import get_task_manager, get_task_status, update_task_status
from Django_xm.apps.core.services.file_manager import get_file_manager
from Django_xm.apps.core.permissions import IsAuthenticatedOrQueryParam

logger = logging.getLogger(__name__)
task_manager = get_task_manager()
file_manager = get_file_manager()


class DeepResearchStartView(APIView):
    """
    启动深度研究任务
    
    创建一个新的研究任务，在后台执行。
    需要用户认证。
    """
    permission_classes = [IsAuthenticated]
    
    @transaction.atomic
    def post(self, request):
        serializer = ResearchStartSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                code=ErrorCode.VALIDATION_FAILED,
                message="数据验证失败",
                details=serializer.errors,
                http_status=status.HTTP_400_BAD_REQUEST
            )

        data = serializer.validated_data
        thread_id = data.get('thread_id') or f"research_{uuid.uuid4().hex[:12]}"

        logger.info(f"收到研究请求：{data['query'][:50]}...")

        try:
            if task_manager.task_exists(thread_id, user_id=request.user.id):
                return error_response(
                    code=ErrorCode.DUPLICATE_RESOURCE,
                    message=f'研究任务 {thread_id} 已存在',
                    http_status=status.HTTP_400_BAD_REQUEST
                )
            
            task_manager.create_task(
                thread_id,
                data['query'],
                enable_web_search=data.get('enable_web_search', True),
                enable_doc_analysis=data.get('enable_doc_analysis', False),
                created_by=request.user
            )
            
            research_depth = data.get('research_depth', 'standard')
            if research_depth == 'basic':
                estimated_time = '3-5 分钟'
            elif research_depth == 'comprehensive':
                estimated_time = '10-15 分钟'
            else:
                estimated_time = '5-10 分钟'

            celery_result = run_research_task.delay(
                thread_id=thread_id,
                query=data['query'],
                enable_web_search=data.get('enable_web_search', True),
                enable_doc_analysis=data.get('enable_doc_analysis', False)
            )

            logger.info(f"研究任务已提交到 Celery 队列：{thread_id} (task_id: {celery_result.id})")

            return success_response(
                data={
                    'task_id': thread_id,
                    'celery_task_id': celery_result.id,
                    'status': 'pending',
                    'query': data['query'],
                    'created_at': datetime.now().isoformat(),
                    'updated_at': datetime.now().isoformat(),
                    'enable_web_search': data.get('enable_web_search', True),
                    'enable_doc_analysis': data.get('enable_doc_analysis', False),
                    'estimated_time': estimated_time,
                },
                message='研究任务已创建'
            )

        except Exception as e:
            logger.error(f"启动研究任务失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class DeepResearchStatusView(APIView):
    """
    查询研究状态
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request, task_id):
        try:
            cached_status = get_task_status(task_id)
            
            try:
                task = ResearchTask.objects.select_related('created_by').get(task_id=task_id, created_by=request.user, is_deleted=False)
                
                response_data = {
                    'task_id': task.task_id,
                    'status': task.status,
                    'query': task.query,
                    'created_at': task.created_at.isoformat() if task.created_at else '',
                    'updated_at': task.updated_at.isoformat() if task.updated_at else '',
                    'enable_web_search': task.enable_web_search,
                    'enable_doc_analysis': task.enable_doc_analysis,
                    'current_step': cached_status.get('current_step', 'unknown') if cached_status else task.status,
                }
                
                if task.status == 'completed' and task.final_report:
                    response_data['final_report'] = task.final_report
                    if cached_status:
                        result = cached_status.get('result', {})
                        response_data['plan'] = result.get('plan')
                        response_data['steps_completed'] = result.get('steps_completed')
                
                return success_response(data=response_data)

            except ResearchTask.DoesNotExist:
                return not_found_response(message='研究任务不存在')

        except Exception as e:
            logger.error(f"查询研究状态失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class DeepResearchResultView(APIView):
    """
    获取研究结果
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request, task_id):
        try:
            try:
                task = ResearchTask.objects.select_related('created_by').get(task_id=task_id, created_by=request.user, is_deleted=False)
                
                if task.status != 'completed':
                    status_msg = '研究任务尚未完成' if task.status == 'running' else '研究任务失败'
                    return success_response(
                        data={
                            'status': task.status,
                            'thread_id': task.task_id,
                            'query': task.query,
                        },
                        message=status_msg
                    )

                cached_status = get_task_status(task_id)
                result = cached_status.get('result', {}) if cached_status else {}

                return success_response(
                    data={
                        'status': 'completed',
                        'task_id': task.task_id,
                        'query': task.query,
                        'report': task.final_report,
                        'plan': result.get('plan'),
                        'steps_completed': result.get('steps_completed'),
                        'created_at': task.created_at.isoformat() if task.created_at else '',
                        'updated_at': task.updated_at.isoformat() if task.updated_at else '',
                        'enable_web_search': task.enable_web_search,
                        'enable_doc_analysis': task.enable_doc_analysis,
                    }
                )

            except ResearchTask.DoesNotExist:
                return not_found_response(message='研究任务不存在')

        except Exception as e:
            logger.error(f"获取研究结果失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class DeepResearchFilesView(APIView):
    """
    列出研究文件
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request, task_id):
        try:
            try:
                task = ResearchTask.objects.select_related('created_by').get(task_id=task_id, created_by=request.user, is_deleted=False)
                
                cached_status = get_task_status(task_id)
                result = cached_status.get('result', {}) if cached_status else {}
                
                files = []
                if result and 'sources' in result:
                    for source in result.get('sources', []):
                        if isinstance(source, dict) and 'url' in source:
                            files.append(source['url'])
                        elif isinstance(source, str):
                            files.append(source)
                
                return success_response(
                    data={
                        'thread_id': task.task_id,
                        'files': files,
                        'total': len(files),
                    }
                )

            except ResearchTask.DoesNotExist:
                return not_found_response(message='研究任务不存在')

        except Exception as e:
            logger.error(f"列出研究文件失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class DeepResearchTaskDeleteView(APIView):
    """
    删除研究任务
    """
    permission_classes = [IsAuthenticated]
    
    def delete(self, request, task_id):
        try:
            logger.info(f"删除研究任务：{task_id}")

            task_manager.delete_task(task_id)

            return success_response(
                data={
                    'status': 'success',
                    'message': f'研究任务 {task_id} 已删除'
                },
                message='删除成功'
            )

        except Exception as e:
            logger.error(f"删除研究任务失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class DeepResearchTaskListView(APIView):
    """
    获取研究任务列表
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        try:
            status_filter = request.query_params.get('status')
            search_query = request.query_params.get('search')
            page = int(request.query_params.get('page', 1))
            page_size = min(int(request.query_params.get('page_size', 20)), 100)

            queryset = ResearchTask.objects.filter(
                created_by=request.user,
                is_deleted=False
            ).select_related('created_by')

            if status_filter:
                queryset = queryset.filter(status=status_filter)

            if search_query:
                queryset = queryset.filter(query__icontains=search_query)

            queryset = queryset.order_by('-created_at')

            total = queryset.count()
            start = (page - 1) * page_size
            end = start + page_size
            tasks = queryset[start:end]

            serializer = ResearchTaskSerializer(tasks, many=True)

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
            logger.error(f"获取研究任务列表失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class DeepResearchFilesListView(APIView):
    permission_classes = [IsAuthenticated]
    
    def get(self, request, task_id):
        try:
            task = ResearchTask.objects.filter(
                task_id=task_id, created_by=request.user, is_deleted=False
            ).first()
            if not task:
                return error_response(
                    code=ErrorCode.NOT_FOUND,
                    message='研究任务不存在或无权访问',
                    http_status=status.HTTP_404_NOT_FOUND
                )
            
            subdirectory = request.query_params.get('subdirectory')
            files = file_manager.list_task_files(task_id, 'research', subdirectory)
            
            serializer = FileInfoSerializer([f.to_dict() for f in files], many=True)
            
            return success_response(
                data={
                    'task_id': task_id,
                    'files': serializer.data,
                    'total': len(files),
                }
            )

        except Exception as e:
            logger.error(f"列出研究文件失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message='获取文件列表失败',
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class DeepResearchFileDownloadView(APIView):
    permission_classes = [IsAuthenticatedOrQueryParam]
    
    def get(self, request, task_id, filename):
        try:
            user = request.user if request.user.is_authenticated else None
            if not user:
                return error_response(
                    code=ErrorCode.UNAUTHORIZED,
                    message='未认证',
                    http_status=status.HTTP_401_UNAUTHORIZED
                )
            task = ResearchTask.objects.filter(
                task_id=task_id, created_by=user, is_deleted=False
            ).first()
            if not task:
                return error_response(
                    code=ErrorCode.NOT_FOUND,
                    message='研究任务不存在或无权访问',
                    http_status=status.HTTP_404_NOT_FOUND
                )
            
            file_info = file_manager.get_file_info(task_id, filename, 'research')
            if not file_info:
                return not_found_response(message='文件不存在')

            file_path = file_info.path
            
            content_type = 'application/octet-stream'
            if file_path.suffix.lower() in ['.md', '.txt']:
                content_type = 'text/plain; charset=utf-8'
            elif file_path.suffix.lower() == '.json':
                content_type = 'application/json'
            elif file_path.suffix.lower() == '.pdf':
                content_type = 'application/pdf'

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


class DeepResearchFileContentView(APIView):
    permission_classes = [IsAuthenticated]
    
    def get(self, request, task_id, filename):
        try:
            task = ResearchTask.objects.filter(
                task_id=task_id, created_by=request.user, is_deleted=False
            ).first()
            if not task:
                return error_response(
                    code=ErrorCode.NOT_FOUND,
                    message='研究任务不存在或无权访问',
                    http_status=status.HTTP_404_NOT_FOUND
                )
            
            file_info = file_manager.get_file_info(task_id, filename, 'research')
            if not file_info:
                return not_found_response(message='文件不存在')

            content = file_manager.read_file_content(task_id, filename, 'research')
            
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


class DeepResearchGlobalSearchView(APIView):
    """
    全局搜索文件
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        try:
            keyword = request.query_params.get('keyword', '')
            if not keyword:
                return error_response(
                    code=ErrorCode.VALIDATION_FAILED,
                    message='搜索关键词不能为空',
                    http_status=status.HTTP_400_BAD_REQUEST
                )

            task_type = request.query_params.get('task_type')
            file_types = request.query_params.getlist('file_type')

            files = file_manager.search_files(
                keyword=keyword,
                task_type=task_type,
                file_types=file_types if file_types else None,
            )

            serializer = FileInfoSerializer([f.to_dict() for f in files], many=True)

            return success_response(
                data={
                    'keyword': keyword,
                    'files': serializer.data,
                    'total': len(files),
                }
            )

        except Exception as e:
            logger.error(f"搜索文件失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


def _authenticate_sse_request(request):
    """SSE端点统一认证：支持session、Authorization header、query param token"""
    from rest_framework_simplejwt.authentication import JWTAuthentication
    from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

    if request.user and request.user.is_authenticated:
        return request.user

    auth_header = request.META.get('HTTP_AUTHORIZATION', '')
    if auth_header.startswith('Bearer '):
        token_str = auth_header.split(' ', 1)[1]
        try:
            raw_token = JWTAuthentication().get_raw_token(
                type('Request', (), {'META': {'HTTP_AUTHORIZATION': f'Bearer {token_str}'}})()
            )
            validated_token = JWTAuthentication().get_validated_token(raw_token)
            user = JWTAuthentication().get_user(validated_token)
            if user and user.is_authenticated:
                return user
        except (InvalidToken, TokenError, Exception) as auth_err:
            logging.warning(f"[Auth] SSE端点Header Token验证失败: {auth_err}")

    token = request.GET.get('token')
    if token:
        try:
            validated_token = JWTAuthentication().get_validated_token(
                JWTAuthentication().get_raw_token(
                    type('Request', (), {'META': {'HTTP_AUTHORIZATION': f'Bearer {token}'}})()
                )
            )
            user = JWTAuthentication().get_user(validated_token)
            if user and user.is_authenticated:
                return user
        except (InvalidToken, TokenError, Exception) as auth_err:
            logging.warning(f"[Auth] SSE端点QueryParam Token验证失败: {auth_err}")

    return None


def _sse_error_response(message, status_code=401):
    """生成SSE错误响应"""
    def error_event():
        yield f"data: {json.dumps({'type': 'error', 'message': message}, ensure_ascii=False)}\n\n"
    return StreamingHttpResponse(
        error_event(),
        content_type='text/event-stream',
        status=status_code,
        headers={'Cache-Control': 'no-cache'}
    )


def deep_research_stream(request, task_id):
    """
    深度研究SSE流式进度端点（使用普通Django函数视图，避免DRF内容协商问题）

    前端通过此端点实时监听研究任务的进度更新，
    避免轮询，提升用户体验。
    支持Authorization header和查询参数token认证
    """
    user = _authenticate_sse_request(request)

    if not user:
        return _sse_error_response('未登录或登录已过期', 401)

    try:
        task = ResearchTask.objects.get(
            task_id=task_id,
            created_by=user,
            is_deleted=False
        )
    except ResearchTask.DoesNotExist:
        return _sse_error_response('研究任务不存在', 404)

    logging.info(f"[API] SSE流式监听研究进度，task_id={task_id}, user_id={user.id}")

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
            logging.info(f"[API] SSE连接关闭，task_id={task_id}")
        except Exception as e:
            logging.error(f"[API] SSE流式输出异常：{e}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"

    response = StreamingHttpResponse(
        event_stream(),
        content_type='text/event-stream',
    )
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


# 保持向后兼容的类视图
class DeepResearchStreamView(APIView):
    """
    深度研究SSE流式进度端点（保持向后兼容）

    前端通过此端点实时监听研究任务的进度更新，
    避免轮询，提升用户体验。
    支持查询参数 token 认证（用于 SSE 场景）
    """
    permission_classes = [IsAuthenticatedOrQueryParam]

    def get(self, request, task_id):
        return deep_research_stream(request, task_id)