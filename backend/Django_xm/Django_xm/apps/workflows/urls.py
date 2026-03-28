from django.urls import path
from . import views

app_name = 'workflows'

urlpatterns = [
    path('start/', views.WorkflowStartView.as_view(), name='start'),
    path('submit/', views.WorkflowSubmitView.as_view(), name='submit'),
    path('status/<str:thread_id>/', views.WorkflowStatusView.as_view(), name='status'),
    path('history/<str:thread_id>/', views.WorkflowHistoryView.as_view(), name='history'),
    path('stream/<str:thread_id>/', views.workflow_stream, name='stream'),
    path('<str:thread_id>/', views.WorkflowDeleteView.as_view(), name='delete'),
]
