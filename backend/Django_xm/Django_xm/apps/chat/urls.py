from django.urls import path
from . import views
from .suggestion_views import SuggestionsView

app_name = 'chat'

urlpatterns = [
    path('', views.ChatView.as_view(), name='chat'),
    path('stream/', views.ChatStreamView.as_view(), name='chat-stream'),
    path('modes/', views.ChatModesView.as_view(), name='chat-modes'),
    path('mcp/tools/', views.McpToolsView.as_view(), name='mcp-tools'),
    path('mcp/status/', views.McpStatusView.as_view(), name='mcp-status'),
    path('mcp/test/', views.McpServerTestView.as_view(), name='mcp-test'),
    path('mcp/call-log/', views.McpToolCallLogView.as_view(), name='mcp-call-log'),
    path('mcp/servers/', views.McpServerListView.as_view(), name='mcp-servers'),
    path('mcp/servers/add/', views.McpServerAddView.as_view(), name='mcp-servers-add'),
    path('mcp/servers/delete/', views.McpServerDeleteView.as_view(), name='mcp-servers-delete'),
    path('tools/', views.ToolListView.as_view(), name='tools-list'),
    path('tools/upload/', views.ToolUploadView.as_view(), name='tools-upload'),
    path('sessions/', views.ChatSessionListView.as_view(), name='chat-sessions-list'),
    path('sessions/create/', views.ChatSessionCreateView.as_view(), name='chat-sessions-create'),
    path('sessions/<str:session_id>/', views.ChatSessionDetailView.as_view(), name='chat-sessions-detail'),
    path('sessions/<str:session_id>/messages/', views.ChatMessageCreateView.as_view(), name='chat-messages-create'),
    path('sessions/<str:session_id>/messages/batch/', views.ChatMessageBatchCreateView.as_view(), name='chat-messages-batch-create'),
    path('sessions/<str:session_id>/compact/', views.ChatSessionCompactView.as_view(), name='chat-sessions-compact'),
    path('messages/<int:message_id>/', views.ChatMessageUpdateView.as_view(), name='chat-messages-update'),
    path('commands/', views.ChatCommandsView.as_view(), name='chat-commands'),
    path('commands/execute/', views.ChatCommandExecuteView.as_view(), name='chat-commands-execute'),
    path('permissions/', views.ChatPermissionsView.as_view(), name='chat-permissions'),
    path('tool-confirmation/', views.ToolConfirmationView.as_view(), name='tool-confirmation'),
    path('cost/', views.ChatCostView.as_view(), name='chat-cost'),
    path('project-context/', views.ProjectContextView.as_view(), name='chat-project-context'),
    path('suggestions/', SuggestionsView.as_view(), name='chat-suggestions'),
]
