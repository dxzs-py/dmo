from django.urls import path
from .views import (
    WorkflowStartView,
    WorkflowSubmitAnswersView,
    WorkflowStatusView,
    WorkflowHistoryView,
    workflow_stream,
)

app_name = 'workflow'

urlpatterns = [
    # 启动工作流
    path('start/', WorkflowStartView.as_view(), name='start'),
    
    # 提交答案
    path('submit-answers/', WorkflowSubmitAnswersView.as_view(), name='submit_answers'),
    
    # 查询状态
    path('status/<str:thread_id>/', WorkflowStatusView.as_view(), name='status'),
    
    # 查询历史
    path('history/<str:thread_id>/', WorkflowHistoryView.as_view(), name='history'),
    
    # 流式输出
    path('stream/<str:thread_id>/', workflow_stream, name='stream'),
]
