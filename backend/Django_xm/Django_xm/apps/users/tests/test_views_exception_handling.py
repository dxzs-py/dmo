"""UserRegisterView / SecureLogoutView 异常处理测试

验证 Task 24.1 + 24.2 + 24.4 收窄异常捕获后的行为：
1. UserRegisterView.create：未预期异常冒泡到全局 custom_exception_handler（不再本地吞掉返回 50002）
2. SecureLogoutView.post：未预期异常冒泡到全局 handler（不再伪装成功响应）
3. ValidationError 仍返回 400 + 字段错误（本地捕获语义未变）

运行方式:
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    D:\\anaconda3\\Scripts\\conda.exe run -n langchain_xm python manage.py test \\
        Django_xm.apps.users.tests.test_views_exception_handling --keepdb -v 2
"""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

User = get_user_model()


class UserRegisterViewExceptionHandlingTests(APITestCase):
    """SubTask 24.1 + 24.4：UserRegisterView.create 收窄异常捕获"""

    def test_register_validation_error_returns_400(self):
        """密码不匹配时 ValidationError 本地捕获返回 400 + 字段错误"""
        response = self.client.post(
            "/api/v1/users/register/",
            {
                "username": "newuser",
                "password": "Complex@123",
                "password_confirm": "Different@456",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_register_success(self):
        """正常路径返回 201 + success_response"""
        response = self.client.post(
            "/api/v1/users/register/",
            {
                "username": "newuser_ok",
                "password": "Complex@123",
                "password_confirm": "Complex@123",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["code"], 200)
        self.assertEqual(response.data["data"]["username"], "newuser_ok")

    def test_register_unexpected_exception_bubbles_up(self):
        """perform_create 内部异常冒泡到全局 handler 返回 500

        修改前：本地 except Exception 返回 200 + SERVER_ERROR（状态码 200 错误响应）
        修改后：异常向上传播，由 custom_exception_handler 统一返回 500 + INTERNAL_ERROR
        """
        with patch(
            "Django_xm.apps.users.views.User.objects.create_user",
            side_effect=RuntimeError("DB connection lost"),
        ):
            response = self.client.post(
                "/api/v1/users/register/",
                {
                    "username": "fail_user",
                    "password": "Complex@123",
                    "password_confirm": "Complex@123",
                },
                format="json",
            )

        # RuntimeError 非 APIException 子类，由全局 handler 返回 500 + INTERNAL_ERROR
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.data["code"], 50002)


class SecureLogoutViewExceptionHandlingTests(APITestCase):
    """SubTask 24.2 + 24.4：SecureLogoutView.post 收窄异常捕获"""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="logout_user", password="Complex@123", email="logout@example.com"
        )
        self.client.force_authenticate(user=self.user)

    def test_logout_no_active_session(self):
        """无 refresh token 且无 user_id 时返回 200 + '登出成功（无有效会话）'"""
        # 已认证用户但显式无 refresh token：仍走 access token 分支
        response = self.client.post("/api/v1/users/secure-logout/", {}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertIn("sessions_cleared", response.data["data"])

    def test_logout_invalid_refresh_token_returns_success(self):
        """refresh token 无效时：本地捕获 InvalidToken，返回 200（无有效会话）

        验证本地捕获 InvalidToken/TokenError 的降级路径：
        refresh token 解码失败不阻塞登出，返回 200 + '登出成功（无有效会话）'
        """
        response = self.client.post(
            "/api/v1/users/secure-logout/",
            {"refresh": "invalid-refresh-token"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["token_blacklisted"], False)

    def test_logout_success_with_blacklist(self):
        """携带有效 refresh token 时：黑名单成功 + 缓存清理成功"""
        from rest_framework_simplejwt.tokens import RefreshToken

        refresh = RefreshToken.for_user(self.user)

        response = self.client.post(
            "/api/v1/users/secure-logout/",
            {"refresh": str(refresh)},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], 200)

    def test_logout_unexpected_exception_bubbles_up(self):
        """SecureSessionCacheService.invalidate_all_user_sessions 异常冒泡到全局 handler

        修改前：本地 except Exception 返回 200 + '登出成功（清理完成）'（错误响应伪装成功）
        修改后：异常向上传播，由 custom_exception_handler 返回 500 + INTERNAL_ERROR
        """
        with patch(
            "Django_xm.apps.cache_manager.services.secure_session_cache."
            "SecureSessionCacheService.invalidate_all_user_sessions",
            side_effect=RuntimeError("Redis unreachable"),
        ):
            response = self.client.post("/api/v1/users/secure-logout/", {}, format="json")

        # RuntimeError 非 APIException 子类，由全局 handler 返回 500 + INTERNAL_ERROR
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.data["code"], 50002)

    def test_logout_blacklist_infra_failure_does_not_block_logout(self):
        """token.blacklist() 基础设施故障不阻塞登出流程

        黑名单存储故障（Redis 故障等）只记 warning，不向上传播，
        返回 200 + 'token_blacklisted: False'（与原行为一致）
        """
        from rest_framework_simplejwt.tokens import RefreshToken

        refresh = RefreshToken.for_user(self.user)

        # 模拟 blacklist() 抛出非 Token 异常（如 Redis 故障）
        with patch(
            "rest_framework_simplejwt.tokens.RefreshToken.blacklist",
            side_effect=RuntimeError("Redis connection refused"),
        ):
            response = self.client.post(
                "/api/v1/users/secure-logout/",
                {"refresh": str(refresh)},
                format="json",
            )

        # 黑名单失败仍返回 200（业务语义：登出成功，黑名单基础设施可后续修复）
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["token_blacklisted"], False)
