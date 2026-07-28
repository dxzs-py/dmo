"""审批子应用路由。"""

from django.urls import path

app_name = 'approvals'

from Django_xm.apps.approvals.views import (
    ApprovalDetailView,
    ApprovalListView,
    ApprovalRejectView,
    ApprovalResumeView,
    ApprovalStateView,
)

urlpatterns = [
    path('', ApprovalListView.as_view(), name='approval-list'),
    path('<str:interrupt_id>/', ApprovalDetailView.as_view(), name='approval-detail'),
    path('<str:interrupt_id>/state/', ApprovalStateView.as_view(), name='approval-state'),
    path('<str:interrupt_id>/resume/', ApprovalResumeView.as_view(), name='approval-resume'),
    path('<str:interrupt_id>/reject/', ApprovalRejectView.as_view(), name='approval-reject'),
]
