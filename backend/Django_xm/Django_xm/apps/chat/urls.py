from django.urls import path

from . import views
from .suggestion_views import SuggestionsView

app_name = "chat"

urlpatterns = [
    path("", views.ChatView.as_view(), name="chat"),
    path("stream/", views.ChatStreamView.as_view(), name="chat_stream"),
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
    path("messages/<int:message_id>/", views.ChatMessageUpdateView.as_view(), name="chat_messages_update"),
    path("messages/<int:message_id>/delete/", views.ChatMessageDeleteView.as_view(), name="message_delete"),
    path(
        "sessions/<str:session_id>/messages/pair/delete/",
        views.ChatMessagePairDeleteView.as_view(),
        name="message_pair_delete",
    ),
    path("commands/", views.ChatCommandsView.as_view(), name="chat_commands"),
    path("commands/execute/", views.ChatCommandExecuteView.as_view(), name="chat_commands_execute"),
    path("finalize/", views.ChatFinalizeView.as_view(), name="chat_finalize"),
    path("project-context/", views.ProjectContextView.as_view(), name="chat_project_context"),
    path("suggestions/", SuggestionsView.as_view(), name="chat_suggestions"),
]
