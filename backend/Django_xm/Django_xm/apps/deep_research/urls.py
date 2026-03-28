from django.urls import path
from .views import (
    DeepResearchStartView,
    DeepResearchStatusView,
    DeepResearchResultView,
    DeepResearchFilesView,
    DeepResearchFileDownloadView,
    DeepResearchTaskDeleteView,
)

app_name = 'deep_research'

urlpatterns = [
    # 启动研究
    path('start/', DeepResearchStartView.as_view(), name='start'),
    
    # 查询状态
    path('status/<str:task_id>/', DeepResearchStatusView.as_view(), name='status'),
    
    # 获取结果
    path('result/<str:task_id>/', DeepResearchResultView.as_view(), name='result'),
    
    # 列出文件
    path('files/<str:task_id>/', DeepResearchFilesView.as_view(), name='files'),
    
    # 下载文件
    path('file/<str:task_id>/<path:filename>/', DeepResearchFileDownloadView.as_view(), name='file-download'),
    
    # 删除任务
    path('task/<str:task_id>/', DeepResearchTaskDeleteView.as_view(), name='task-delete'),
]
