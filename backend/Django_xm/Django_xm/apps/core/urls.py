from django.urls import path
from . import views
from .task_views import (
    TaskStatusView,
    UserTaskListView,
    TaskCancelView,
    TaskStatsView,
)

app_name = 'core'

urlpatterns = [
    path('tasks/stats/', TaskStatsView.as_view(), name='task-stats'),
    path('tasks/<str:task_id>/cancel/', TaskCancelView.as_view(), name='task-cancel'),
    path('tasks/<str:task_id>/', TaskStatusView.as_view(), name='task-status'),
    path('tasks/', UserTaskListView.as_view(), name='user-tasks'),
]
