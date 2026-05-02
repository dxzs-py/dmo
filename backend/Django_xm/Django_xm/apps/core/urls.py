"""
核心应用的 URL 配置
包含任务管理、系统监控等功能的路由
"""
from django.urls import path
from . import views
from .task_views import (
    TaskStatusView,
    UserTaskListView,
    TaskCancelView,
    TaskStatsView,
)
from .cache_views import (
    CacheHealthView,
    CacheStatsView,
    CacheInvalidateView,
    CacheResetStatsView,
)

app_name = 'core'

urlpatterns = [
    path('tasks/<str:task_id>/', TaskStatusView.as_view(), name='task-status'),
    path('tasks/', UserTaskListView.as_view(), name='user-tasks'),
    path('tasks/<str:task_id>/cancel/', TaskCancelView.as_view(), name='task-cancel'),
    path('tasks/stats/', TaskStatsView.as_view(), name='task-stats'),
    path('cache/health/', CacheHealthView.as_view(), name='cache-health'),
    path('cache/stats/', CacheStatsView.as_view(), name='cache-stats'),
    path('cache/invalidate/', CacheInvalidateView.as_view(), name='cache-invalidate'),
    path('cache/reset-stats/', CacheResetStatsView.as_view(), name='cache-reset-stats'),
]
