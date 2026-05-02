from django.urls import path
from .views import (
    DeepResearchStartView,
    DeepResearchStatusView,
    DeepResearchResultView,
    DeepResearchFilesView,
    DeepResearchTaskDeleteView,
    DeepResearchTaskListView,
    DeepResearchFilesListView,
    DeepResearchFileDownloadView,
    DeepResearchFileContentView,
    DeepResearchGlobalSearchView,
    DeepResearchStreamView,
    deep_research_stream,
)

app_name = 'deep_research'

urlpatterns = [
    # 启动研究
    path('start/', DeepResearchStartView.as_view(), name='start'),
    
    # 任务列表
    path('tasks/', DeepResearchTaskListView.as_view(), name='tasks'),
    
    # 全局搜索
    path('search/', DeepResearchGlobalSearchView.as_view(), name='search'),
    
    # 文件列表（新版）
    path('<str:task_id>/files/', DeepResearchFilesListView.as_view(), name='task-files'),
    
    # 文件下载
    path('<str:task_id>/file/download/<path:filename>/', DeepResearchFileDownloadView.as_view(), name='file-download'),
    
    # 文件内容
    path('<str:task_id>/file/content/<path:filename>/', DeepResearchFileContentView.as_view(), name='file-content'),
    
    # 删除任务
    path('task/<str:task_id>/', DeepResearchTaskDeleteView.as_view(), name='task-delete'),
    
    # 查询状态
    path('status/<str:task_id>/', DeepResearchStatusView.as_view(), name='status'),
    
    # SSE流式进度（使用普通函数视图）
    path('stream/<str:task_id>/', deep_research_stream, name='stream'),
    
    # 获取结果
    path('result/<str:task_id>/', DeepResearchResultView.as_view(), name='result'),
    
    # 列出文件（旧版兼容）
    path('files/<str:task_id>/', DeepResearchFilesView.as_view(), name='files'),
]
