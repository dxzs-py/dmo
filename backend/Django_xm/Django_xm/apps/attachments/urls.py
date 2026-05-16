from django.urls import path
from . import views

app_name = 'attachments'

urlpatterns = [
    path('sessions/<str:session_id>/upload/', views.ChatAttachmentUploadView.as_view(), name='upload'),
    path('sessions/<str:session_id>/list/', views.ChatAttachmentListView.as_view(), name='list'),
    path('<int:attachment_id>/', views.ChatAttachmentDeleteView.as_view(), name='delete'),

    path('admin/', views.AttachmentAdminListView.as_view(), name='admin-list'),
    path('admin/stats/', views.AttachmentAdminStatsView.as_view(), name='admin-stats'),
    path('admin/cleanup/', views.AttachmentAdminCleanupView.as_view(), name='admin-cleanup'),
    path('admin/<int:attachment_id>/', views.AttachmentAdminDetailView.as_view(), name='admin-detail'),
    path('admin/<int:attachment_id>/action/', views.AttachmentAdminActionView.as_view(), name='admin-action'),
    path('admin/batch/', views.AttachmentAdminBatchView.as_view(), name='admin-batch'),
    path('admin/storage-alerts/', views.StorageAlertView.as_view(), name='admin-storage-alerts'),
    path('admin/storage-alerts/<int:alert_id>/', views.StorageAlertView.as_view(), name='admin-storage-alert-detail'),
]
