"""core 认证类测试（dj-30：QueryParamTokenAuthentication 安全路径回归）。

覆盖 QueryParamTokenAuthentication 三态：
- query 参数携带有效 token → 认证成功 (user, validated_token)
- 无 token / 超长 token → 返回 None（匿名）
- 无效 token → 返回 None（内部捕获 InvalidToken/TokenError，不抛异常）

注意（2026-08-20 实测）：authenticate 内部捕获所有 JWT 异常并返回 None，
故"无效 token"的断言是返回 None 而非抛异常；401 语义由视图层权限判定产生。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.core.tests --settings=Django_xm.settings.test
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from rest_framework_simplejwt.tokens import RefreshToken

from Django_xm.apps.core.authentication import QueryParamTokenAuthentication

User = get_user_model()


class QueryParamTokenAuthenticationTests(TestCase):
    """JWT query 参数认证三态。"""

    def setUp(self):
        self.factory = RequestFactory()
        self.auth = QueryParamTokenAuthentication()
        self.user = User.objects.create_user(username="sseuser", password="pw123456")

    def test_valid_token_in_query_authenticates(self):
        """query 参数携带有效 access token → 返回 (user, validated_token)。"""
        access = str(RefreshToken.for_user(self.user).access_token)
        request = self.factory.get("/api/v1/stream/", {"token": access})
        result = self.auth.authenticate(request)
        self.assertIsNotNone(result)
        auth_user, validated_token = result
        self.assertEqual(auth_user.id, self.user.id)
        # JWT payload 数值以字符串编码（simplejwt 序列化行为）
        self.assertEqual(int(validated_token["user_id"]), self.user.id)

    def test_missing_token_returns_none(self):
        """无 token → 返回 None（匿名）。"""
        request = self.factory.get("/api/v1/stream/")
        self.assertIsNone(self.auth.authenticate(request))

    def test_invalid_token_returns_none(self):
        """无效 token → 返回 None（内部捕获 TokenError）。"""
        request = self.factory.get("/api/v1/stream/", {"token": "not-a-jwt"})
        self.assertIsNone(self.auth.authenticate(request))

    def test_expired_token_returns_none(self):
        """过期 token → 返回 None。"""
        access = str(RefreshToken.for_user(self.user).access_token)
        from datetime import timedelta

        from rest_framework_simplejwt.tokens import AccessToken

        token = AccessToken(access)
        token.set_exp(lifetime=-timedelta(minutes=10))  # 已过期 10 分钟
        request = self.factory.get("/api/v1/stream/", {"token": str(token)})
        self.assertIsNone(self.auth.authenticate(request))

    def test_oversized_token_returns_none(self):
        """token 超长（≥2048）→ 直接返回 None（长度守卫）。"""
        request = self.factory.get("/api/v1/stream/", {"token": "x" * 2048})
        self.assertIsNone(self.auth.authenticate(request))

    def test_authenticate_header_returns_bearer(self):
        """authenticate_header → 'Bearer'（WWW-Authenticate 值）。"""
        request = self.factory.get("/api/v1/stream/")
        self.assertEqual(self.auth.authenticate_header(request), "Bearer")
