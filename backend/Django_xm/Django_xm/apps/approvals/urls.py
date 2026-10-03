"""审批子应用路由。

端点清单：
- GET  /api/v1/approvals/metrics/snapshot/: 审批监控指标快照（管理员，Spec D3）
- POST /api/v1/approvals/{interrupt_id}/resume/: 恢复审批（拒绝语义由 approved=false 承载）
"""

from django.urls import path

app_name = "approvals"

from Django_xm.apps.approvals.views import ApprovalMetricsSnapshotView, ApprovalResumeView

urlpatterns = [
    path("metrics/snapshot/", ApprovalMetricsSnapshotView.as_view(), name="approval_metrics_snapshot"),
    path("<str:interrupt_id>/resume/", ApprovalResumeView.as_view(), name="approval_resume"),
]
