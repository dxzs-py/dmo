"""users 认证视图测试（dj-30：安全关键路径回归测试补齐）。

覆盖：
- 登录成功：响应含 access/refresh token 与 is_staff 字段（前端 isAdmin 契约的源头保障——
  后端序列化返回 snake_case is_staff 正确，转换发生在前端 axios 拦截器 toCamelCase）
- 登录失败：错误密码 → 401（AuthenticationFailed 语义）
- 注册成功 / 重复用户名 400
- getUserInfo：已认证返回用户结构 / 未认证 401

环境事实（2026-08-20 实测）：
- 验证码为可选：CaptchaMixin.verify_captcha 在未传 captcha_key/captcha 时直接放行
- 登录失败锁定基于 cache（test.py LocMemCache，用例间自动隔离）；密码需满足复杂度
  正则（大写+小写+数字+特殊字符，≥8 位）

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.users.tests --settings=Django_xm.settings.test
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()

LOGIN_URL = "/api/v1/users/login/"
REGISTER_URL = "/api/v1/users/register/"
INFO_URL = "/api/v1/users/info/"

# 满足 serializers.PASSWORD_COMPLEXITY_PATTERN 的测试口令
VALID_PASSWORD = "Abcdef1!@#"


class LoginViewTests(TestCase):
    """登录主流程与 is_staff 契约。"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="loginuser",
            password=VALID_PASSWORD,
            email="login@example.com",
            is_staff=True,
        )

    def test_login_success_returns_tokens_and_is_staff(self):
        """登录成功：响应含 access/refresh 与 is_staff 字段（前端 isAdmin 源头契约）。"""
        resp = self.client.post(LOGIN_URL, {"username": "loginuser", "password": VALID_PASSWORD}, format="json")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["code"], 200)
        # 响应为统一信封：业务字段位于 body["data"]（视图手工构造，含 is_staff）
        data = body["data"]
        self.assertIn("access", data)
        self.assertIn("refresh", data)
        self.assertEqual(data["is_staff"], True)
        self.assertEqual(data["username"], "loginuser")

    def test_login_wrong_password_rejected(self):
        """错误密码 → 401 认证失败语义（不泄漏账号状态）。"""
        resp = self.client.post(LOGIN_URL, {"username": "loginuser", "password": "WrongPass!23"}, format="json")
        self.assertEqual(resp.status_code, 401)

    def test_login_unknown_user_rejected(self):
        """未知用户名 → 401。"""
        resp = self.client.post(LOGIN_URL, {"username": "ghost", "password": VALID_PASSWORD}, format="json")
        self.assertEqual(resp.status_code, 401)

    def test_login_no_credentials_rejected(self):
        """缺失凭据 → 401。"""
        resp = self.client.post(LOGIN_URL, {}, format="json")
        self.assertEqual(resp.status_code, 401)


class RegisterViewTests(TestCase):
    """注册主流程。"""

    def setUp(self):
        self.client = APIClient()

    def test_register_success(self):
        """注册成功：新用户可登录。"""
        payload = {
            "username": "newuser",
            "password": VALID_PASSWORD,
            "password_confirm": VALID_PASSWORD,
            "email": "new@example.com",
        }
        resp = self.client.post(REGISTER_URL, payload, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(User.objects.filter(username="newuser").exists())

    def test_register_duplicate_username_rejected(self):
        """重复用户名 → 400。"""
        User.objects.create_user(username="dupuser", password=VALID_PASSWORD)
        payload = {
            "username": "dupuser",
            "password": VALID_PASSWORD,
            "password_confirm": VALID_PASSWORD,
        }
        resp = self.client.post(REGISTER_URL, payload, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_register_password_mismatch_rejected(self):
        """两次密码不一致 → 400。"""
        payload = {
            "username": "mismatch",
            "password": VALID_PASSWORD,
            "password_confirm": "Different!23",
        }
        resp = self.client.post(REGISTER_URL, payload, format="json")
        self.assertEqual(resp.status_code, 400)


class UserInfoViewTests(TestCase):
    """用户信息接口认证边界。"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="infouser", password=VALID_PASSWORD)

    def test_user_info_requires_auth(self):
        """未认证访问 /users/info/ → 401。"""
        resp = self.client.get(INFO_URL)
        self.assertEqual(resp.status_code, 401)

    def test_user_info_returns_structure(self):
        """已认证：返回用户信息结构（含 is_staff 契约字段）。"""
        self.client.force_authenticate(user=self.user)
        resp = self.client.get(INFO_URL)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(data["username"], "infouser")
        self.assertIn("is_staff", data)
        self.assertFalse(data["is_staff"])
