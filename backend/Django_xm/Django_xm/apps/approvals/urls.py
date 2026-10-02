"""审批子应用路由。

死接口下线后仅保留审批核心端点：
- POST /api/v1/approvals/{interrupt_id}/resume/: 恢复审批（拒绝语义由 approved=false 承载）
"""

from django.urls import path

app_name = "approvals"

from Django_xm.apps.approvals.views import ApprovalResumeView

urlpatterns = [
    path("<str:interrupt_id>/resume/", ApprovalResumeView.as_view(), name="approval_resume"),
]
