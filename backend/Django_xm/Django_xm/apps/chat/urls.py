from django.urls import path

from . import views
from .suggestion_views import SuggestionsView

app_name = "chat"

urlpatterns = [
    path("", views.ChatView.as_view(), name="chat"),
    path("stream/", views.ChatStreamView.as_view(), name="chat_stream"),
    path("stream/stop/", views.ChatStreamStopView.as_view(), name="chat_stream_stop"),
    path("modes/", views.ChatModesView.as_view(), name="chat_modes"),
    path("sessions/", views.ChatSessionListView.as_view(), name="chat_sessions_list"),
    path("sessions/create/", views.ChatSessionCreateView.as_view(), name="chat_sessions_create"),
    path("sessions/<str:session_id>/", views.ChatSessionDetailView.as_view(), name="chat_sessions_detail"),
    path("sessions/<str:session_id>/messages/", views.ChatMessageCreateView.as_view(), name="chat_messages_create"),
    path(
        "sessions/<str:session_id>/messages/batch/",
        views.ChatMessageBatchCreateView.as_view(),
        name="chat_messages_batch_create",
    ),
    path("sessions/<str:session_id>/compact/", views.ChatSessionCompactView.as_view(), name="chat_sessions_compact"),
    # 消息资源路由：PATCH 更新 / DELETE 删除共用同一视图（ChatMessageDeleteView
    # 继承 ChatMessageUpdateView，同时承载 patch 与 delete 方法，遵循 tools app
    # dj-06 资源化路由约定：资源明细路径唯一，方法由视图分发）
    path("messages/<int:message_id>/", views.ChatMessageDeleteView.as_view(), name="chat_messages_delete"),
    path(
        "sessions/<str:session_id>/messages/pair/<int:user_message_id>/",
        views.ChatMessagePairDeleteView.as_view(),
        name="chat_messages_pair_delete",
    ),
    path("commands/", views.ChatCommandsView.as_view(), name="chat_commands"),
    path("commands/execute/", views.ChatCommandExecuteView.as_view(), name="chat_commands_execute"),
    path("finalize/", views.ChatFinalizeView.as_view(), name="chat_finalize"),
    path("project-context/", views.ProjectContextView.as_view(), name="chat_project_context"),
    path("suggestions/", SuggestionsView.as_view(), name="chat_suggestions"),
]
