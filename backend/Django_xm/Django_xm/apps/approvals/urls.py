"""审批子应用路由。"""

from django.urls import path

app_name = "approvals"

from Django_xm.apps.approvals.views import (
    ApprovalDetailView,
    ApprovalListView,
    ApprovalMetricsView,
    ApprovalRejectView,
    ApprovalResumeView,
    ApprovalStateView,
)

urlpatterns = [
    path("", ApprovalListView.as_view(), name="approval_list"),
    path("metrics/", ApprovalMetricsView.as_view(), name="approval_metrics"),
    path("<str:interrupt_id>/", ApprovalDetailView.as_view(), name="approval_detail"),
    path("<str:interrupt_id>/state/", ApprovalStateView.as_view(), name="approval_state"),
    path("<str:interrupt_id>/resume/", ApprovalResumeView.as_view(), name="approval_resume"),
    path("<str:interrupt_id>/reject/", ApprovalRejectView.as_view(), name="approval_reject"),
]
