from django.urls import path

from . import views

app_name = "chat"

urlpatterns = [
    path("stream/", views.ChatStreamView.as_view(), name="chat_stream"),
    path("stream/stop/", views.ChatStreamStopView.as_view(), name="chat_stream_stop"),
    path("modes/", views.ChatModesView.as_view(), name="chat_modes"),
    path("sessions/", views.ChatSessionListView.as_view(), name="chat_sessions_list"),
    path("sessions/create/", views.ChatSessionCreateView.as_view(), name="chat_sessions_create"),
    path("sessions/<str:session_id>/", views.ChatSessionDetailView.as_view(), name="chat_sessions_detail"),
    path("sessions/<str:session_id>/messages/", views.ChatMessageCreateView.as_view(), name="chat_messages_create"),
    # 消息资源路由：承载 PATCH 更新（消息单条删除 REST 通道已随 WS 化改造下线，
    # 遵循 tools app dj-06 资源化路由约定：资源明细路径唯一，方法由视图分发）
    path("messages/<int:message_id>/", views.ChatMessageUpdateView.as_view(), name="chat_messages_detail"),
    path(
        "sessions/<str:session_id>/messages/pair/<int:user_message_id>/",
        views.ChatMessagePairDeleteView.as_view(),
        name="chat_messages_pair_delete",
    ),
    path("commands/", views.ChatCommandsView.as_view(), name="chat_commands"),
    path("finalize/", views.ChatFinalizeView.as_view(), name="chat_finalize"),
    path("project-context/", views.ProjectContextView.as_view(), name="chat_project_context"),
]
