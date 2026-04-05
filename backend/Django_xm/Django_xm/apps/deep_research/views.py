import logging
import time
import uuid
from datetime import datetime
from typing import Optional, Dict, Any
from pathlib import Path

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.http import FileResponse

from Django_xm.utils.responses import success_response, error_response, not_found_response
from Django_xm.utils.error_codes import ErrorCode

from .serializers import (
    ResearchStartSerializer,
    ResearchTaskSerializer,
    ResearchResultSerializer,
)
from .models import ResearchTask
from Django_xm.tasks.deep_research import run_research_task
from .task_manager import get_task_manager, get_task_status, update_task_status
from Django_xm.apps.core.tools.filesystem import get_filesystem

logger = logging.getLogger(__name__)
task_manager = get_task_manager()


class DeepResearchStartView(APIView):
    """
    启动深度研究任务
    
    创建一个新的研究任务，在后台执行。
    需要用户认证。
    """
    permission_classes = [IsAuthenticated]
    
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
            if task_manager.task_exists(thread_id):
                return error_response(
                    code=ErrorCode.DUPLICATE_RESOURCE,
                    message=f'研究任务 {thread_id} 已存在',
                    http_status=status.HTTP_400_BAD_REQUEST
                )
            
            task_manager.create_task(
                thread_id,
                data['query'],
                enable_web_search=data.get('enable_web_search', True),
                enable_doc_analysis=data.get('enable_doc_analysis', False)
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
                task = ResearchTask.objects.get(task_id=task_id)
                
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
                task = ResearchTask.objects.get(task_id=task_id)
                
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
                task = ResearchTask.objects.get(task_id=task_id)
                
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


class DeepResearchFileDownloadView(APIView):
    """
    获取研究文件内容
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request, task_id, filename):
        try:
            try:
                task = ResearchTask.objects.get(task_id=task_id)
                
                logger.info(f"读取研究文件：{task_id}/{filename}")
                
                fs = get_filesystem(task_id)
                
                if "/" in filename:
                    parts = filename.split("/")
                    subdirectory = "/".join(parts[:-1])
                    file_name = parts[-1]
                else:
                    subdirectory = None
                    file_name = filename
                
                try:
                    if subdirectory:
                        content = fs.read_file(file_name, subdir=subdirectory)
                    else:
                        content = fs.read_file(file_name)
                except Exception as e:
                    return error_response(
                        code=ErrorCode.NOT_FOUND,
                        message=f'读取文件失败：{str(e)}',
                        http_status=status.HTTP_404_NOT_FOUND
                    )

                return success_response(
                    data={
                        'filename': filename,
                        'content': content,
                    }
                )

            except ResearchTask.DoesNotExist:
                return not_found_response(message='研究任务不存在')

        except Exception as e:
            logger.error(f"读取文件失败：{e}", exc_info=True)
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