from django.urls import path
from . import views

urlpatterns = [
    path('start/', views.WorkflowStartView.as_view(), name='workflow-start'),
    path('status/<str:thread_id>/', views.WorkflowStatusView.as_view(), name='workflow-status'),
]
