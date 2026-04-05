import logging
import time
import uuid
from datetime import datetime
from typing import Optional, Dict, Any
from pathlib import Path

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.http import FileResponse

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
    """
    
    def post(self, request):
        serializer = ResearchStartSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        thread_id = data.get('thread_id') or f"research_{uuid.uuid4().hex[:12]}"

        logger.info(f"📥 收到研究请求：{data['query'][:50]}...")

        try:
            if task_manager.task_exists(thread_id):
                return Response({
                    'code': 400,
                    'message': f'研究任务 {thread_id} 已存在'
                }, status=status.HTTP_400_BAD_REQUEST)
            
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

            logger.info(f"✅ 研究任务已提交到 Celery 队列：{thread_id} (task_id: {celery_result.id})")

            return Response({
                'code': 0,
                'message': '操作成功',
                'data': {
                    'task_id': thread_id,
                    'celery_task_id': celery_result.id,
                    'status': 'pending',
                    'query': data['query'],
                    'created_at': datetime.now().isoformat(),
                    'updated_at': datetime.now().isoformat(),
                    'enable_web_search': data.get('enable_web_search', True),
                    'enable_doc_analysis': data.get('enable_doc_analysis', False),
                }
            })

        except Exception as e:
            logger.error(f"❌ 启动研究任务失败：{e}", exc_info=True)
            return Response({
                'code': 500,
                'message': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class DeepResearchStatusView(APIView):
    """
    查询研究状态
    """
    
    def get(self, request, task_id):
        try:
            # 先尝试从缓存获取状态
            cached_status = get_task_status(task_id)
            
            # 从数据库获取任务
            try:
                task = ResearchTask.objects.get(task_id=task_id)
                
                # 构建响应
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
                
                # 如果任务已完成，直接返回报告
                if task.status == 'completed' and task.final_report:
                    response_data['final_report'] = task.final_report
                    # 获取缓存的结果
                    if cached_status:
                        result = cached_status.get('result', {})
                        response_data['plan'] = result.get('plan')
                        response_data['steps_completed'] = result.get('steps_completed')
                
                return Response({
                    'code': 0,
                    'message': '操作成功',
                    'data': response_data
                })

            except ResearchTask.DoesNotExist:
                return Response({
                    'code': 404,
                    'message': '研究任务不存在'
                }, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
            logger.error(f"❌ 查询研究状态失败：{e}", exc_info=True)
            return Response({
                'code': 500,
                'message': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class DeepResearchResultView(APIView):
    """
    获取研究结果
    """
    
    def get(self, request, task_id):
        try:
            try:
                task = ResearchTask.objects.get(task_id=task_id)
                
                if task.status != 'completed':
                    return Response({
                        'code': 200,
                        'message': '研究任务尚未完成' if task.status == 'running' else '研究任务失败',
                        'data': {
                            'status': task.status,
                            'thread_id': task.task_id,
                            'query': task.query,
                        }
                })

                cached_status = get_task_status(task_id)
                result = cached_status.get('result', {}) if cached_status else {}

                return Response({
                    'code': 0,
                    'message': '操作成功',
                    'data': {
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
                })

            except ResearchTask.DoesNotExist:
                return Response({
                    'code': 404,
                    'message': '研究任务不存在'
                }, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
            logger.error(f"❌ 获取研究结果失败：{e}", exc_info=True)
            return Response({
                'code': 500,
                'message': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class DeepResearchFilesView(APIView):
    """
    列出研究文件
    """
    
    def get(self, request, task_id):
        try:
            try:
                task = ResearchTask.objects.get(task_id=task_id)
                
                # 从结果中提取文件列表
                cached_status = get_task_status(task_id)
                result = cached_status.get('result', {}) if cached_status else {}
                
                # 尝试从结果中获取文件
                files = []
                if result and 'sources' in result:
                    for source in result.get('sources', []):
                        if isinstance(source, dict) and 'url' in source:
                            files.append(source['url'])
                        elif isinstance(source, str):
                            files.append(source)
                
                return Response({
                    'code': 0,
                    'message': '操作成功',
                    'data': {
                        'thread_id': task.task_id,
                        'files': files,
                        'total': len(files),
                    }
                })

            except ResearchTask.DoesNotExist:
                return Response({
                    'code': 404,
                    'message': '研究任务不存在'
                }, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
                logger.error(f"❌ 列出研究文件失败：{e}", exc_info=True)
                return Response({
                    'code': 500,
                    'message': str(e)
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class DeepResearchFileDownloadView(APIView):
    """
    获取研究文件内容
    """
    
    def get(self, request, task_id, filename):
        try:
            try:
                task = ResearchTask.objects.get(task_id=task_id)
                
                logger.info(f"📖 读取研究文件：{task_id}/{filename}")
                
                fs = get_filesystem(task_id)
                
                # 解析文件路径
                if "/" in filename:
                    parts = filename.split("/")
                    subdirectory = "/".join(parts[:-1])
                    file_name = parts[-1]
                else:
                    subdirectory = None
                    file_name = filename
                
                # 读取文件内容
                try:
                    if subdirectory:
                        content = fs.read_file(file_name, subdir=subdirectory)
                    else:
                        content = fs.read_file(file_name)
                except Exception as e:
                    return Response({
                        'code': 404,
                        'message': f'读取文件失败：{str(e)}'
                    }, status=status.HTTP_404_NOT_FOUND)

                return Response({
                    'code': 0,
                    'message': '操作成功',
                    'data': {
                        'filename': filename,
                        'content': content,
                    }
                })

            except ResearchTask.DoesNotExist:
                return Response({
                    'code': 404,
                    'message': '研究任务不存在'
                }, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
            logger.error(f"❌ 读取文件失败：{e}", exc_info=True)
            return Response({
                'code': 500,
                'message': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class DeepResearchTaskDeleteView(APIView):
    """
    删除研究任务
    """
    
    def delete(self, request, task_id):
        try:
            logger.info(f"🗑️ 删除研究任务：{task_id}")

            task_manager.delete_task(task_id)

            return Response({
                'code': 0,
                'message': '操作成功',
                'data': {
                    'status': 'success',
                    'message': f'研究任务 {task_id} 已删除'
                }
            })

        except Exception as e:
            logger.error(f"❌ 删除研究任务失败：{e}", exc_info=True)
            return Response({
                'code': 500,
                'message': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)