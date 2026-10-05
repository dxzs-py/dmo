from django.urls import path

from .views import (
    DeepResearchContinueView,
    DeepResearchStartView,
    DeepResearchStatusView,
    DeepResearchTaskCancelView,
    DeepResearchTaskDeleteView,
    DeepResearchTaskListView,
)
from .views_files import (
    DeepResearchFileContentView,
    DeepResearchFileDownloadView,
    DeepResearchFilesListView,
    DeepResearchGlobalSearchView,
)
from .views_stream import (
    deep_research_stream,
)

app_name = "research"

urlpatterns = [
    path("start/", DeepResearchStartView.as_view(), name="start"),
    path("tasks/", DeepResearchTaskListView.as_view(), name="tasks"),
    path("search/", DeepResearchGlobalSearchView.as_view(), name="search"),
    path("<str:task_id>/continue/", DeepResearchContinueView.as_view(), name="continue"),
    path("<str:task_id>/files/", DeepResearchFilesListView.as_view(), name="task_files"),
    path("<str:task_id>/file/download/<path:filename>/", DeepResearchFileDownloadView.as_view(), name="file_download"),
    path("<str:task_id>/file/content/<path:filename>/", DeepResearchFileContentView.as_view(), name="file_content"),
    path("tasks/<str:task_id>/", DeepResearchTaskDeleteView.as_view(), name="tasks_delete"),
    path("tasks/<str:task_id>/cancel/", DeepResearchTaskCancelView.as_view(), name="tasks_cancel"),
    path("status/<str:task_id>/", DeepResearchStatusView.as_view(), name="status"),
    path("stream/<str:task_id>/", deep_research_stream, name="stream"),
]
