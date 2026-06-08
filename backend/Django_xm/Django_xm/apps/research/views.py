import logging
import uuid
import json
from datetime import datetime

from rest_framework.views import APIView
from rest_framework import status
from Django_xm.apps.core.throttling import ResearchRateThrottle
from rest_framework.permissions import IsAuthenticated
from django.db import transaction, IntegrityError

from Django_xm.common.sse_utils import sse_response
from Django_xm.common.responses import success_response, error_response, not_found_response
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.sse_utils import authenticate_sse_request, sse_error_response

from .serializers import (
    ResearchStartSerializer,
    ResearchContinueSerializer,
    ResearchTaskSerializer,
    ResearchResultSerializer,
    FileInfoSerializer,
)
from .models import ResearchTask, ResearchTaskStatus
from Django_xm.tasks.deep_research import run_research_task
from .services.task_manager import get_task_manager, get_task_status, update_task_status
from Django_xm.apps.core.services.file_manager import get_file_manager
from Django_xm.common.permissions import IsAuthenticatedOrQueryParam

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
from Django_xm.apps.chat.services.cross_app import get_chat_session, soft_delete_session

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
            knowledge_base_ids = data.get('knowledge_base_ids', [])

            with transaction.atomic():
                if ResearchTask.objects.select_for_update().filter(
                    task_id=thread_id, created_by=request.user, is_deleted=False
                ).exists():
                    return error_response(
                        code=ErrorCode.DUPLICATE_RESOURCE,
                        message=f'研究任务 {thread_id} 已存在',
                        http_status=status.HTTP_400_BAD_REQUEST,
                    )

                task_manager.create_task(
                    thread_id,
                    data['query'],
                    enable_web_search=data.get('enable_web_search', True),
                    enable_doc_analysis=data.get('enable_doc_analysis', False),
                    created_by=request.user,
                )

                ResearchTask.objects.filter(task_id=thread_id).update(
                    knowledge_base_ids=knowledge_base_ids,
                    use_mcp=data.get('use_mcp', False),
                    selected_mcp_servers=data.get('selected_mcp_servers', []),
                    selected_tools=data.get('selected_tools', []),
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
                use_mcp=data.get('use_mcp', False),
                selected_mcp_servers=data.get('selected_mcp_servers', []),
                selected_tools=data.get('selected_tools', []),
                provider_id=data.get('provider_id'),
                model_name=data.get('model_name'),
                enable_deep_thinking=data.get('enable_deep_thinking', False),
                temperature=data.get('temperature'),
                max_tokens=data.get('max_tokens'),
                special_params=data.get('special_params'),
            )

            logger.info(f"研究任务已提交到 Celery 队列：{thread_id} (task_id: {celery_result.id})")

            ResearchTask.objects.filter(task_id=thread_id).update(
                celery_task_id=celery_result.id,
            )

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
                    'use_mcp': data.get('use_mcp', False),
                    'selected_mcp_servers': data.get('selected_mcp_servers', []),
                    'selected_tools': data.get('selected_tools', []),
                    'provider_id': data.get('provider_id'),
                    'model_name': data.get('model_name'),
                    'enable_deep_thinking': data.get('enable_deep_thinking', False),
                    'estimated_time': estimated_time,
                },
                message='研究任务已创建',
            )

        except IntegrityError:
            return error_response(
                code=ErrorCode.DUPLICATE_RESOURCE,
                message=f'研究任务 {thread_id} 已存在',
                http_status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.error(f"启动研究任务失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message="研究任务启动失败，请稍后重试",
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DeepResearchContinueView(APIView):
    throttle_classes = [ResearchRateThrottle]
    permission_classes = [IsAuthenticated]

    def post(self, request, task_id):
        serializer = ResearchContinueSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                code=ErrorCode.VALIDATION_FAILED,
                message="数据验证失败",
                data=serializer.errors,
                http_status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data

        try:
            with transaction.atomic():
                parent_task = ResearchTask.objects.select_for_update().get(
                    task_id=task_id,
                    created_by=request.user,
                    is_deleted=False,
                )

                if parent_task.status != ResearchTaskStatus.COMPLETED:
                    return error_response(
                        code=ErrorCode.VALIDATION_FAILED,
                        message='只能继续已完成的研究任务',
                        http_status=status.HTTP_400_BAD_REQUEST,
                    )

                additional_query = data.get('additional_query', '').strip()
                # 续研查询：如果有补充说明则用补充说明，否则用增量指令
                if additional_query:
                    new_query = additional_query
                else:
                    new_query = f"请在之前研究的基础上继续深入，探索未覆盖的方面，补充更多细节和证据"

                new_thread_id = f"research_{uuid.uuid4().hex[:12]}"
                new_version = parent_task.version + 1

                new_task = ResearchTask(
                    task_id=new_thread_id,
                    query=new_query,
                    enable_web_search=data.get('enable_web_search', parent_task.enable_web_search),
                    enable_doc_analysis=data.get('enable_doc_analysis', parent_task.enable_doc_analysis),
                    knowledge_base_ids=data.get('knowledge_base_ids', parent_task.knowledge_base_ids),
                    use_mcp=data.get('use_mcp', parent_task.use_mcp),
                    selected_mcp_servers=data.get('selected_mcp_servers', parent_task.selected_mcp_servers),
                    selected_tools=data.get('selected_tools', parent_task.selected_tools),
                    research_depth=parent_task.research_depth,
                    created_by=request.user,
                    parent_task=parent_task,
                    version=new_version,
                    session_id=parent_task.session_id,
                )
                new_task.save()

            celery_result = run_research_task.delay(
                thread_id=new_thread_id,
                query=new_query,
                enable_web_search=new_task.enable_web_search,
                enable_doc_analysis=new_task.enable_doc_analysis,
                knowledge_base_ids=new_task.knowledge_base_ids,
                user_id=request.user.id,
                use_mcp=new_task.use_mcp,
                selected_mcp_servers=new_task.selected_mcp_servers,
                selected_tools=new_task.selected_tools,
                provider_id=data.get('provider_id'),
                model_name=data.get('model_name'),
                enable_deep_thinking=data.get('enable_deep_thinking', False),
                temperature=data.get('temperature'),
                max_tokens=data.get('max_tokens'),
                special_params=data.get('special_params'),
                continue_task_id=task_id,
            )

            logger.info(f"续研任务已提交：{new_thread_id} (v{new_version}), 父任务: {task_id}")

            ResearchTask.objects.filter(task_id=new_thread_id).update(
                celery_task_id=celery_result.id,
            )

            return success_response(
                data={
                    'task_id': new_thread_id,
                    'celery_task_id': celery_result.id,
                    'status': 'pending',
                    'query': new_query,
                    'parent_task_id': task_id,
                    'version': new_version,
                    'created_at': datetime.now().isoformat(),
                },
                message=f'续研任务已创建（v{new_version}）',
            )

        except ResearchTask.DoesNotExist:
            return not_found_response(message='研究任务不存在')
        except Exception as e:
            logger.error(f"启动续研任务失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message="续研任务启动失败，请稍后重试",
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
                message="查询研究状态失败，请稍后重试",
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
                message="获取研究结果失败，请稍后重试",
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DeepResearchTaskDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, task_id):
        try:
            logger.info(f"删除研究任务：{task_id}")

            with transaction.atomic():
                task_obj = ResearchTask.objects.select_for_update().filter(
                    task_id=task_id, created_by=request.user, is_deleted=False
                ).first()

                if not task_obj:
                    return not_found_response(message='研究任务不存在或无权删除')

                linked_session = None
                if task_obj.session_id:
                    session = get_chat_session(task_obj.session_id, user=request.user)
                    if session:
                        linked_session = {
                            'session_id': session.session_id,
                            'title': session.title,
                        }

                confirm_delete_linked = request.query_params.get('confirm_delete_linked', '').lower() == 'true'

                if linked_session and not confirm_delete_linked:
                    return success_response(
                        data={
                            'has_linked_data': True,
                            'linked_session': linked_session,
                            'message': '该研究任务关联了一个聊天会话',
                        },
                        message='存在关联的聊天会话，请确认是否一并删除',
                    )

                result = task_manager.delete_task(task_id, user_id=request.user.id)

                if not result:
                    return not_found_response(message='研究任务不存在或无权删除')

                if linked_session:
                    soft_delete_session(task_obj.session_id)

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
                message="删除研究任务失败，请稍后重试",
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
                message="获取研究任务列表失败，请稍后重试",
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

