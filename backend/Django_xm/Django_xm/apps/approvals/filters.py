"""审批列表过滤器（dj-15：django-filter 标准化查询参数过滤）。"""

import django_filters

from .models import Approval


class ApprovalFilter(django_filters.FilterSet):
    """GET /approvals/ 查询参数过滤（全部精确等值匹配）。"""

    class Meta:
        model = Approval
        fields = ["source_id", "source", "chat_session_id", "state"]
