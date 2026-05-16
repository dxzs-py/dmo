import logging
import uuid
import json
from datetime import datetime

from rest_framework.views import APIView
from rest_framework import status
from Django_xm.apps.core.throttling import ResearchRateThrottle
from rest_framework.permissions import IsAuthenticated
from django.db import transaction

from Django_xm.apps.common.sse_utils import sse_response
from Django_xm.apps.common.responses import success_response, error_response, not_found_response
from Django_xm.apps.common.error_codes import ErrorCode
from Django_xm.apps.common.sse_utils import authenticate_sse_request, sse_error_response

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

from .views_files import (
    DeepResearchFilesListView,
    DeepResearchFileDownloadView,
    DeepResearchFileContentView,
    DeepResearchGlobalSearchView,
)
from .views_stream import (
    DeepResearchStreamView,
    deep_research_stream,
)

logger = logging.getLogger(__name__)
task_manager = get_task_manager()
file_manager = get_file_manager()


class DeepResearchStartView(APIView):
    throttle_classes = [ResearchRateThrottle]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ResearchStartSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                code=ErrorCode.VALIDATION_FAILED,
                message="数据验证失败",
                data=serializer.errors,
                http_status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        thread_id = data.get('thread_id') or f"research_{uuid.uuid4().hex[:12]}"

        logger.info(f"收到研究请求：{data['query'][:50]}...")

        try:
            if task_manager.task_exists(thread_id, user_id=request.user.id):
                return error_response(
                    code=ErrorCode.DUPLICATE_RESOURCE,
                    message=f'研究任务 {thread_id} 已存在',
                    http_status=status.HTTP_400_BAD_REQUEST,
                )

            knowledge_base_ids = data.get('knowledge_base_ids', [])

            with transaction.atomic():
                task_manager.create_task(
                    thread_id,
                    data['query'],
                    enable_web_search=data.get('enable_web_search', True),
                    enable_doc_analysis=data.get('enable_doc_analysis', False),
                    created_by=request.user,
                )

                ResearchTask.objects.filter(task_id=thread_id).update(
                    knowledge_base_ids=knowledge_base_ids,
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
                enable_doc_analysis=data.get('enable_doc_analysis', False),
                knowledge_base_ids=knowledge_base_ids,
                user_id=request.user.id,
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
                    'knowledge_base_ids': knowledge_base_ids,
                    'estimated_time': estimated_time,
                },
                message='研究任务已创建',
            )

        except Exception as e:
            logger.error(f"启动研究任务失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DeepResearchStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, task_id):
        try:
            cached_status = get_task_status(task_id)

            try:
                task = ResearchTask.objects.select_related('created_by').get(
                    task_id=task_id, created_by=request.user, is_deleted=False,
                )

                response_data = {
                    'task_id': task.task_id,
                    'status': task.status,
                    'query': task.query,
                    'created_at': task.created_at.isoformat() if task.created_at else '',
                    'updated_at': task.updated_at.isoformat() if task.updated_at else '',
                    'enable_web_search': task.enable_web_search,
                    'enable_doc_analysis': task.enable_doc_analysis,
                    'knowledge_base_ids': task.knowledge_base_ids or [],
                    'current_step': cached_status.get('current_step', 'unknown') if cached_status else task.status,
                }

                if task.status == 'completed' and task.final_report:
                    response_data['final_report'] = task.final_report
                    if cached_status:
                        result = cached_status.get('result', {})
                        response_data['plan'] = result.get('plan')
                        response_data['steps_completed'] = result.get('steps_completed')

                if task.status == 'completed':
                    try:
                        from Django_xm.apps.core.services.file_manager import get_file_manager
                        fm = get_file_manager()
                        file_list = fm.list_task_files(task_id, 'research')
                        response_data['files'] = [f.to_dict() for f in file_list]
                    except Exception:
                        response_data['files'] = []

                return success_response(data=response_data)

            except ResearchTask.DoesNotExist:
                return not_found_response(message='研究任务不存在')

        except Exception as e:
            logger.error(f"查询研究状态失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DeepResearchResultView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, task_id):
        try:
            try:
                task = ResearchTask.objects.select_related('created_by').get(
                    task_id=task_id, created_by=request.user, is_deleted=False,
                )

                if task.status != 'completed':
                    status_msg = '研究任务尚未完成' if task.status == 'running' else '研究任务失败'
                    return success_response(
                        data={
                            'status': task.status,
                            'thread_id': task.task_id,
                            'query': task.query,
                        },
                        message=status_msg,
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
                        'knowledge_base_ids': task.knowledge_base_ids or [],
                    }
                )

            except ResearchTask.DoesNotExist:
                return not_found_response(message='研究任务不存在')

        except Exception as e:
            logger.error(f"获取研究结果失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DeepResearchFilesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, task_id):
        try:
            try:
                task = ResearchTask.objects.select_related('created_by').get(
                    task_id=task_id, created_by=request.user, is_deleted=False,
                )

                files = []
                try:
                    from Django_xm.apps.core.services.file_manager import get_file_manager
                    fm = get_file_manager()
                    file_list = fm.list_task_files(task_id, 'research')
                    files = [f.to_dict() for f in file_list]
                except Exception:
                    pass

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
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DeepResearchTaskDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, task_id):
        try:
            logger.info(f"删除研究任务：{task_id}")

            task_manager.delete_task(task_id)

            return success_response(
                data={
                    'status': 'success',
                    'message': f'研究任务 {task_id} 已删除',
                },
                message='删除成功',
            )

        except Exception as e:
            logger.error(f"删除研究任务失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DeepResearchTaskListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            status_filter = request.query_params.get('status')
            search_query = request.query_params.get('search')
            page = int(request.query_params.get('page', 1))
            page_size = min(int(request.query_params.get('page_size', 20)), 100)

            queryset = ResearchTask.objects.filter(
                created_by=request.user,
                is_deleted=False,
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
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
