"""
AI 设置 API（ai_engine 侧保留面）

沙箱全局可用性查询。
（任务 eliminate-priority-debts 4.2：环 2 {ai_engine, knowledge} 破除——
AISettingsView/RebuildIndexesView 及其辅助函数已整体迁往
knowledge/views_settings.py，对外 URL 路径不变，见根 urls.py 直挂。）
"""

from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from Django_xm.apps.core.throttling import MetaRateThrottle
from Django_xm.common.responses import success_response


class SandboxAvailabilityView(APIView):
    """沙箱全局可用性（只读）

    普通用户可访问（IsAuthenticated）：返回 SANDBOX_CONFIG.ENABLED，
    供前端"沙箱模式"开关置灰（未启用时提示"沙箱模式不可用（后端未启用）"）。
    不挂在管理员专属的 AISettingsView 上，避免普通用户 403 拿不到该标志。
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [MetaRateThrottle]

    @extend_schema(exclude=True)
    def get(self, request):
        from django.conf import settings as django_settings

        sandbox_enabled = bool(
            getattr(django_settings, "SANDBOX_CONFIG", {}).get("ENABLED", False)
        )
        return success_response(data={"sandbox_enabled": sandbox_enabled})
