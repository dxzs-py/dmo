from django.urls import path
from . import views

app_name = 'chat'

urlpatterns = [
    path('', views.ChatView.as_view(), name='chat'),
    path('stream/', views.ChatStreamView.as_view(), name='chat-stream'),
    path('modes/', views.ChatModesView.as_view(), name='chat-modes'),
    path('sessions/', views.ChatSessionListView.as_view(), name='chat-sessions-list'),
    path('sessions/create/', views.ChatSessionCreateView.as_view(), name='chat-sessions-create'),
    path('sessions/<str:session_id>/', views.ChatSessionDetailView.as_view(), name='chat-sessions-detail'),
    path('sessions/<str:session_id>/messages/', views.ChatMessageCreateView.as_view(), name='chat-messages-create'),
    path('sessions/<str:session_id>/messages/batch/', views.ChatMessageBatchCreateView.as_view(), name='chat-messages-batch-create'),
    path('sessions/<str:session_id>/attachments/', views.ChatAttachmentUploadView.as_view(), name='chat-attachments-upload'),
    path('sessions/<str:session_id>/attachments/list/', views.ChatAttachmentListView.as_view(), name='chat-attachments-list'),
    path('messages/<int:message_id>/', views.ChatMessageUpdateView.as_view(), name='chat-messages-update'),
]
