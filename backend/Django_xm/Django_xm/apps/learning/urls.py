from django.urls import path

from . import views

app_name = "learning"

urlpatterns = [
    path("start/", views.WorkflowStartView.as_view(), name="start"),
    path("start/stream/", views.WorkflowStartStreamView.as_view(), name="start_stream"),
    path("submit/", views.WorkflowSubmitView.as_view(), name="submit"),
    path("status/<str:thread_id>/", views.WorkflowStatusView.as_view(), name="status"),
    path("history/<str:thread_id>/", views.WorkflowHistoryView.as_view(), name="history"),
    path("stream/<str:thread_id>/", views.workflow_stream, name="stream"),
    path("task/<str:thread_id>/", views.WorkflowDeleteView.as_view(), name="delete"),
    path("<str:thread_id>/restart/stream/", views.WorkflowRestartStreamView.as_view(), name="restart_stream"),
    path("<str:thread_id>/questions/", views.WorkflowQuestionListView.as_view(), name="questions"),
    path(
        "<str:thread_id>/questions/<str:question_id>/",
        views.WorkflowQuestionUpdateView.as_view(),
        name="question_update",
    ),
    path("<str:thread_id>/attempts/", views.WorkflowAttemptListView.as_view(), name="attempts"),
    # 新增API
    path("tasks/", views.WorkflowListView.as_view(), name="tasks"),
    path("<str:thread_id>/files/", views.WorkflowFilesListView.as_view(), name="task_files"),
    path(
        "<str:thread_id>/file/download/<path:filename>/", views.WorkflowFileDownloadView.as_view(), name="file_download"
    ),
    path("<str:thread_id>/file/content/<path:filename>/", views.WorkflowFileContentView.as_view(), name="file_content"),
]
