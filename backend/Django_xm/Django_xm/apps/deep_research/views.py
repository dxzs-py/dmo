import logging
import time
import uuid
import threading
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
from .deep_agent import create_deep_research_agent
from Django_xm.apps.core.tools.filesystem import get_filesystem

logger = logging.getLogger(__name__)


# ==================== 全局状态管理 ====================

# 存储研究任务状态（内存缓存）
_research_tasks: Dict[str, Dict[str, Any]] = {}

# 存储后台线程引用
_research_threads: Dict[str, threading.Thread] = {}


def get_task_status(thread_id: str) -> Optional[Dict[str, Any]]:
    """获取任务状态"""
    return _research_tasks.get(thread_id)


def update_task_status(thread_id: str, status: Dict[str, Any]) -> None:
    """更新任务状态"""
    if thread_id not in _research_tasks:
        _research_tasks[thread_id] = {}
    _research_tasks[thread_id].update(status)


def run_research_in_background(thread_id: str, query: str, 
                               enable_web_search: bool, enable_doc_analysis: bool):
    """在后台线程中执行研究任务"""
    try:
        logger.info(f"🚀 后台任务开始：{thread_id}")
        
        # 更新状态为运行中
        update_task_status(thread_id, {
            'status': 'running',
            'current_step': 'researching',
            'start_time': datetime.now().isoformat(),
        })
        
        # 更新数据库状态
        try:
            task = ResearchTask.objects.get(task_id=thread_id)
            task.status = 'running'
            task.save()
        except ResearchTask.DoesNotExist:
            pass
        
        # 创建Agent并执行研究
        agent = create_deep_research_agent(
            thread_id=thread_id,
            enable_web_search=enable_web_search,
            enable_doc_analysis=enable_doc_analysis
        )
        
        result = agent.research(query)
        
        # 更新任务状态为完成
        update_task_status(thread_id, {
            'status': 'completed',
            'current_step': 'completed',
            'end_time': datetime.now().isoformat(),
            'result': result,
        })
        
        # 更新数据库
        try:
            task = ResearchTask.objects.get(task_id=thread_id)
            task.status = 'completed'
            task.final_report = result.get('final_report', '')
            task.save()
        except ResearchTask.DoesNotExist:
            pass
        
        logger.info(f"✅ 后台任务完成：{thread_id}")
        
    except Exception as e:
        logger.error(f"❌ 后台任务失败：{thread_id}, 错误：{e}", exc_info=True)
        
        # 更新任务状态为失败
        update_task_status(thread_id, {
            'status': 'failed',
            'current_step': 'failed',
            'end_time': datetime.now().isoformat(),
            'error': str(e),
        })
        
        # 更新数据库
        try:
            task = ResearchTask.objects.get(task_id=thread_id)
            task.status = 'failed'
            task.final_report = f"错误：{str(e)}"
            task.save()
        except ResearchTask.DoesNotExist:
            pass
    finally:
        # 清理线程引用
        if thread_id in _research_threads:
            del _research_threads[thread_id]


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
            # 检查是否已存在
            if thread_id in _research_tasks:
                return Response({
                    'error': f'研究任务 {thread_id} 已存在'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # 初始化任务状态
            update_task_status(thread_id, {
                'status': 'pending',
                'query': data['query'],
                'current_step': 'pending',
                'created_at': datetime.now().isoformat(),
            })
            
            # 创建数据库记录
            task = ResearchTask.objects.create(
                task_id=thread_id,
                query=data['query'],
                status='pending',
                enable_web_search=data.get('enable_web_search', True),
                enable_doc_analysis=data.get('enable_doc_analysis', False)
            )
            
            # 估算完成时间
            research_depth = data.get('research_depth', 'standard')
            if research_depth == 'basic':
                estimated_time = '3-5 分钟'
            elif research_depth == 'comprehensive':
                estimated_time = '10-15 分钟'
            else:
                estimated_time = '5-10 分钟'
            
            # 在后台线程中执行研究任务
            research_thread = threading.Thread(
                target=run_research_in_background,
                args=(thread_id, data['query'], 
                      data.get('enable_web_search', True), 
                      data.get('enable_doc_analysis', False))
            )
            research_thread.daemon = True
            research_thread.start()
            
            # 保存线程引用
            _research_threads[thread_id] = research_thread
            
            logger.info(f"✅ 研究任务已启动（后台执行）：{thread_id}")
            
            return Response({
                'status': 'success',
                'thread_id': thread_id,
                'message': '研究任务已启动，正在后台执行',
                'estimated_time': estimated_time,
            })

        except Exception as e:
            logger.error(f"❌ 启动研究任务失败：{e}", exc_info=True)
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


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
                    'status': task.status,
                    'thread_id': task.task_id,
                    'current_step': cached_status.get('current_step', 'unknown') if cached_status else task.status,
                    'progress': 100 if task.status == 'completed' else 50 if task.status == 'running' else 0,
                    'message': f'研究任务{task.get_status_display()}',
                }
                
                return Response(response_data)
                
            except ResearchTask.DoesNotExist:
                return Response({
                    'error': '研究任务不存在'
                }, status=status.HTTP_404_NOT_FOUND)
                
        except Exception as e:
            logger.error(f"❌ 查询研究状态失败：{e}", exc_info=True)
            return Response({
                'error': str(e)
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
                        'status': task.status,
                        'thread_id': task.task_id,
                        'query': task.query,
                        'error': '研究任务尚未完成' if task.status == 'running' else '研究任务失败',
                    })
                
                # 获取缓存的结果
                cached_status = get_task_status(task_id)
                result = cached_status.get('result', {}) if cached_status else {}
                
                return Response(ResearchResultSerializer({
                    'status': 'completed',
                    'thread_id': task.task_id,
                    'query': task.query,
                    'final_report': task.final_report,
                    'plan': result.get('plan'),
                    'steps_completed': result.get('steps_completed'),
                    'metadata': {
                        'created_at': task.created_at.isoformat(),
                        'completed_at': task.updated_at.isoformat(),
                        'enable_web_search': task.enable_web_search,
                        'enable_doc_analysis': task.enable_doc_analysis,
                    }
                }).data)
                
            except ResearchTask.DoesNotExist:
                return Response({
                    'error': '研究任务不存在'
                }, status=status.HTTP_404_NOT_FOUND)
                
        except Exception as e:
            logger.error(f"❌ 获取研究结果失败：{e}", exc_info=True)
            return Response({
                'error': str(e)
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
                    'thread_id': task.task_id,
                    'files': files,
                    'total': len(files),
                })
                
            except ResearchTask.DoesNotExist:
                return Response({
                    'error': '研究任务不存在'
                }, status=status.HTTP_404_NOT_FOUND)
                
        except Exception as e:
                logger.error(f"❌ 列出研究文件失败：{e}", exc_info=True)
                return Response({
                    'error': str(e)
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
                        'error': f'读取文件失败：{str(e)}'
                    }, status=status.HTTP_404_NOT_FOUND)
                
                # 获取文件信息
                return Response({
                    'filename': filename,
                    'content': content,
                })
                
            except ResearchTask.DoesNotExist:
                return Response({
                    'error': '研究任务不存在'
                }, status=status.HTTP_404_NOT_FOUND)
                
        except Exception as e:
            logger.error(f"❌ 读取文件失败：{e}", exc_info=True)
            return Response({
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class DeepResearchTaskDeleteView(APIView):
    """
    删除研究任务
    """
    
    def delete(self, request, task_id):
        try:
            logger.info(f"🗑️ 删除研究任务：{task_id}")
            
            # 删除缓存的任务状态
            if task_id in _research_tasks:
                del _research_tasks[task_id]
            
            # 删除数据库记录
            try:
                task = ResearchTask.objects.get(task_id=task_id)
                task.delete()
            except ResearchTask.DoesNotExist:
                pass
            
            return Response({
                'status': 'success',
                'message': f'研究任务 {task_id} 已删除'
            })
            
        except Exception as e:
            logger.error(f"❌ 删除研究任务失败：{e}", exc_info=True)
            return Response({
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)