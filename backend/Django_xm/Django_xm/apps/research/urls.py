from django.urls import path
from .views import (
    DeepResearchStartView,
    DeepResearchContinueView,
    DeepResearchStatusView,
    DeepResearchResultView,
    DeepResearchTaskDeleteView,
    DeepResearchTaskListView,
    DeepResearchFilesListView,
    DeepResearchFileDownloadView,
    DeepResearchFileContentView,
    DeepResearchGlobalSearchView,
    ResearchApprovalView,
)
from .views_stream import (
    DeepResearchStreamView,
    deep_research_stream,
)

app_name = 'research'

urlpatterns = [
    path('start/', DeepResearchStartView.as_view(), name='start'),
    path('approval/', ResearchApprovalView.as_view(), name='approval'),
    path('tasks/', DeepResearchTaskListView.as_view(), name='tasks'),
    path('search/', DeepResearchGlobalSearchView.as_view(), name='search'),
    path('<str:task_id>/continue/', DeepResearchContinueView.as_view(), name='continue'),
    path('<str:task_id>/files/', DeepResearchFilesListView.as_view(), name='task-files'),
    path('<str:task_id>/file/download/<path:filename>/', DeepResearchFileDownloadView.as_view(), name='file-download'),
    path('<str:task_id>/file/content/<path:filename>/', DeepResearchFileContentView.as_view(), name='file-content'),
    path('task/<str:task_id>/', DeepResearchTaskDeleteView.as_view(), name='task-delete'),
    path('status/<str:task_id>/', DeepResearchStatusView.as_view(), name='status'),
    path('stream/<str:task_id>/', deep_research_stream, name='stream'),
    path('result/<str:task_id>/', DeepResearchResultView.as_view(), name='result'),
    path('results/<str:task_id>/', DeepResearchResultView.as_view(), name='results'),
]
