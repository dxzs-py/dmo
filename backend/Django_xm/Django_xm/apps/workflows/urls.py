from django.urls import path
from . import views

app_name = 'workflows'

urlpatterns = [
    path('start/', views.WorkflowStartView.as_view(), name='start'),
    path('start/stream/', views.WorkflowStartStreamView.as_view(), name='start-stream'),
    path('submit/', views.WorkflowSubmitView.as_view(), name='submit'),
    path('status/<str:thread_id>/', views.WorkflowStatusView.as_view(), name='status'),
    path('history/<str:thread_id>/', views.WorkflowHistoryView.as_view(), name='history'),
    path('stream/<str:thread_id>/', views.workflow_stream, name='stream'),
    path('task/<str:thread_id>/', views.WorkflowDeleteView.as_view(), name='delete'),
    
    # 新增API
    path('tasks/', views.WorkflowListView.as_view(), name='tasks'),
    path('<str:thread_id>/files/', views.WorkflowFilesListView.as_view(), name='task-files'),
    path('<str:thread_id>/file/download/<path:filename>/', views.WorkflowFileDownloadView.as_view(), name='file-download'),
    path('<str:thread_id>/file/content/<path:filename>/', views.WorkflowFileContentView.as_view(), name='file-content'),
]
