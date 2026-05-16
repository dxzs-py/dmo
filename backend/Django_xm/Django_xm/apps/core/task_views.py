"""
异步任务管理 API 视图
提供任务状态查询、任务列表等功能
"""
import logging
from typing import Optional

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, IsAdminUser

from Django_xm.apps.common.responses import success_response, error_response
from Django_xm.apps.common.error_codes import ErrorCode
from Django_xm.apps.common.task_manager import (
    get_task_manager,
    get_task_status,
    TaskStatus,
    TaskType,
    format_task_duration,
)

logger = logging.getLogger(__name__)


class TaskStatusView(APIView):
    """
    任务状态查询视图
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request, task_id):
        """
        获取指定任务的状态
        """
        try:
            task_data = get_task_status(task_id)
            
            if not task_data:
                return error_response(
                    code=ErrorCode.NOT_FOUND,
                    message="任务不存在",
                    http_status=status.HTTP_404_NOT_FOUND
                )
            
            # 验证用户权限（如果任务属于特定用户）
            user_id = task_data.get('user_id')
            if user_id and user_id != request.user.id and not request.user.is_staff:
                return error_response(
                    code=ErrorCode.PERMISSION_DENIED,
                    message="无权访问此任务",
                    http_status=status.HTTP_403_FORBIDDEN
                )
            
            # 格式化响应
            response_data = {
                **task_data,
                'duration': format_task_duration(task_data)
            }
            
            return success_response(data=response_data)
            
        except Exception as e:
            logger.error(f"[TaskStatusView] 查询任务状态失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message="查询任务状态失败",
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class UserTaskListView(APIView):
    """
    用户任务列表视图
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        """
        获取当前用户的任务列表
        """
        try:
            task_type = request.query_params.get('task_type')
            status_filter = request.query_params.get('status')
            limit = min(int(request.query_params.get('limit', 50)), 100)
            
            # 转换枚举类型
            task_type_enum = None
            if task_type:
                try:
                    task_type_enum = TaskType(task_type)
                except ValueError:
                    pass
            
            status_enum = None
            if status_filter:
                try:
                    status_enum = TaskStatus(status_filter)
                except ValueError:
                    pass
            
            manager = get_task_manager()
            tasks = manager.get_user_tasks(
                user_id=request.user.id,
                task_type=task_type_enum,
                status=status_enum,
                limit=limit
            )
            
            # 格式化任务数据
            formatted_tasks = []
            for task in tasks:
                formatted_task = {
                    **task,
                    'duration': format_task_duration(task)
                }
                formatted_tasks.append(formatted_task)
            
            return success_response(data={
                'tasks': formatted_tasks,
                'total': len(formatted_tasks)
            })
            
        except Exception as e:
            logger.error(f"[UserTaskListView] 获取任务列表失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message="获取任务列表失败",
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class TaskCancelView(APIView):
    """
    任务取消视图
    """
    permission_classes = [IsAuthenticated]
    
    def post(self, request, task_id):
        """
        取消指定任务
        """
        try:
            from celery import current_app
            
            task_data = get_task_status(task_id)
            
            if not task_data:
                return error_response(
                    code=ErrorCode.NOT_FOUND,
                    message="任务不存在",
                    http_status=status.HTTP_404_NOT_FOUND
                )
            
            # 验证用户权限
            user_id = task_data.get('user_id')
            if user_id and user_id != request.user.id and not request.user.is_staff:
                return error_response(
                    code=ErrorCode.PERMISSION_DENIED,
                    message="无权操作此任务",
                    http_status=status.HTTP_403_FORBIDDEN
                )
            
            # 尝试撤销Celery任务
            try:
                celery_task_id = task_data.get('celery_task_id', task_id)
                current_app.control.revoke(celery_task_id, terminate=True)
            except Exception as e:
                logger.warning(f"[TaskCancelView] 撤销Celery任务失败：{e}")
            
            # 更新任务状态
            manager = get_task_manager()
            manager.update_task_status(task_id, {
                'status': TaskStatus.REVOKED.value,
                'current_step': 'revoked',
                'error': '用户主动取消',
            })

            # 同步更新数据库记录
            try:
                from Django_xm.apps.core.task_models import CeleryTaskRecord
                celery_task_id = task_data.get('celery_task_id')
                if celery_task_id:
                    record = CeleryTaskRecord.objects.filter(celery_task_id=celery_task_id).first()
                    if record:
                        record.mark_revoked()
            except Exception as e:
                logger.debug(f"[TaskCancelView] 同步数据库记录失败: {e}")
            
            return success_response(message="任务已取消")
            
        except Exception as e:
            logger.error(f"[TaskCancelView] 取消任务失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message="取消任务失败",
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class TaskStatsView(APIView):
    """
    任务统计视图（管理员）
    """
    permission_classes = [IsAdminUser]

    def get(self, request):
        """
        获取任务统计信息
        """
        try:
            from django.db.models import Count, Avg
            from Django_xm.apps.core.task_models import CeleryTaskRecord

            status_stats = dict(
                CeleryTaskRecord.objects
                .values_list('status')
                .annotate(count=Count('id'))
            )
            status_stats.setdefault('pending', 0)
            status_stats.setdefault('started', 0)
            status_stats.setdefault('progress', 0)
            status_stats.setdefault('success', 0)
            status_stats.setdefault('failure', 0)
            status_stats.setdefault('revoked', 0)
            status_stats.setdefault('retry', 0)

            type_stats = dict(
                CeleryTaskRecord.objects
                .values_list('task_type')
                .annotate(count=Count('id'))
            )

            avg_runtime = (
                CeleryTaskRecord.objects
                .filter(runtime_seconds__isnull=False)
                .aggregate(avg=Avg('runtime_seconds'))
            )['avg'] or 0

            total = sum(status_stats.values())

            return success_response(data={
                'status_stats': status_stats,
                'type_stats': type_stats,
                'total': total,
                'avg_runtime_seconds': round(avg_runtime, 2),
            })

        except Exception as e:
            logger.error(f"[TaskStatsView] 获取任务统计失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message="获取任务统计失败",
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
