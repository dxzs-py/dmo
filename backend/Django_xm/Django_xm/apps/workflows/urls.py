from django.urls import path
from . import views

app_name = 'workflows'

urlpatterns = [
    path('start/', views.workflow_start, name='start'),
    path('submit/', views.workflow_submit, name='submit'),
    path('status/<str:thread_id>/', views.workflow_status, name='status'),
    path('history/<str:thread_id>/', views.workflow_history, name='history'),
    path('stream/<str:thread_id>/', views.workflow_stream, name='stream'),
    path('<str:thread_id>/', views.workflow_delete, name='delete'),
]
