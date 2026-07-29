"""Analytics 视图异常处理测试

验证 Task 24 收窄异常捕获后的行为：
1. ValidationError 冒泡到全局 custom_exception_handler 返回 400
2. AnalyticsService 内部异常冒泡到全局 handler 返回 500（不被本地 broad except 吞掉）
3. 成功路径返回 200 + success_response

运行方式:
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    D:\\anaconda3\\Scripts\\conda.exe run -n langchain_xm python manage.py test \\
        Django_xm.apps.analytics.tests.test_views_exception_handling --keepdb -v 2
"""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

User = get_user_model()


class _BaseViewTest(APITestCase):
    """通用 setUp 提供已认证 APIClient"""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="tester", password="test-pwd-123", email="t@example.com")
        self.client.force_authenticate(user=self.user)


class DashboardViewExceptionHandlingTests(_BaseViewTest):
    """SubTask 24.3 + 24.4：DashboardView 收窄异常捕获"""

    def test_dashboard_success(self):
        """正常路径返回 200 + success_response"""
        with patch(
            "Django_xm.apps.analytics.views.AnalyticsService.get_dashboard_stats",
            return_value={"visits": 10},
        ):
            response = self.client.get("/api/v1/analytics/dashboard/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], 200)
        self.assertEqual(response.data["data"], {"visits": 10})

    def test_dashboard_unexpected_exception_returns_500(self):
        """AnalyticsService 内部异常由全局 handler 转为 500

        修改前：本地 except Exception 返回 200 + error_response（错误响应但状态码 200）
        修改后：异常向上传播，由 custom_exception_handler 统一返回 500 + INTERNAL_ERROR
        """
        with patch(
            "Django_xm.apps.analytics.views.AnalyticsService.get_dashboard_stats",
            side_effect=RuntimeError("DB connection lost"),
        ):
            response = self.client.get("/api/v1/analytics/dashboard/")

        # RuntimeError 非 APIException 子类，落到全局 handler 的 INTERNAL_ERROR 分支
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.data["code"], 50002)


class PageViewTrackViewExceptionHandlingTests(_BaseViewTest):
    """SubTask 24.3：PageViewTrackView 收窄异常捕获"""

    def test_page_view_validation_error_returns_400(self):
        """缺少 path 字段时 ValidationError 冒泡到全局 handler 返回 400"""
        response = self.client.post("/api/v1/analytics/track/page-view/", {}, format="json")

        self.assertEqual(response.status_code, 400)

    def test_page_view_success(self):
        """正常路径返回 200"""
        with patch("Django_xm.apps.analytics.views.AnalyticsService.record_page_view") as mock_record:
            response = self.client.post(
                "/api/v1/analytics/track/page-view/",
                {"path": "/home", "title": "首页"},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], 200)
        mock_record.assert_called_once()

    def test_page_view_service_exception_returns_500(self):
        """AnalyticsService 异常由全局 handler 转为 500"""
        with patch(
            "Django_xm.apps.analytics.views.AnalyticsService.record_page_view",
            side_effect=RuntimeError("Service unavailable"),
        ):
            response = self.client.post(
                "/api/v1/analytics/track/page-view/",
                {"path": "/home"},
                format="json",
            )

        # RuntimeError 落到全局 handler 的 INTERNAL_ERROR 分支
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.data["code"], 50002)


class FeatureUseTrackViewExceptionHandlingTests(_BaseViewTest):
    """SubTask 24.3：FeatureUseTrackView 收窄异常捕获"""

    def test_feature_use_validation_error_returns_400(self):
        """缺少 feature 字段时 ValidationError 冒泡到全局 handler 返回 400"""
        response = self.client.post("/api/v1/analytics/track/feature-use/", {}, format="json")

        self.assertEqual(response.status_code, 400)

    def test_feature_use_success(self):
        """正常路径返回 200"""
        with patch("Django_xm.apps.analytics.views.AnalyticsService.record_feature_usage") as mock_record:
            response = self.client.post(
                "/api/v1/analytics/track/feature-use/",
                {"feature": "export", "metadata": {"from": "menu"}},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        mock_record.assert_called_once()


class EventTrackViewExceptionHandlingTests(_BaseViewTest):
    """SubTask 24.3：EventTrackView 收窄异常捕获"""

    def test_event_validation_error_returns_400(self):
        """缺少必填字段时 ValidationError 冒泡到全局 handler 返回 400"""
        response = self.client.post("/api/v1/analytics/track/event/", {}, format="json")

        self.assertEqual(response.status_code, 400)

    def test_event_service_exception_returns_500(self):
        """UserEvent.objects.create 异常由全局 handler 转为 500"""
        with patch(
            "Django_xm.apps.analytics.models.UserEvent.objects.create",
            side_effect=RuntimeError("DB connection lost"),
        ):
            response = self.client.post(
                "/api/v1/analytics/track/event/",
                {
                    "event_type": "click",
                    "event_category": "interaction",
                    "metadata": {},
                },
                format="json",
            )

        # RuntimeError 落到全局 handler 的 INTERNAL_ERROR 分支
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.data["code"], 50002)
