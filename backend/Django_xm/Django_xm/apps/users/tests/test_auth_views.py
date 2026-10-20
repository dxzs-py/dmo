"""users 认证视图测试（dj-30：安全关键路径回归测试补齐）。

覆盖：
- 登录成功：响应含 access/refresh token 与 is_staff 字段（前端 isAdmin 契约的源头保障——
  后端序列化返回 snake_case is_staff 正确，转换发生在前端 axios 拦截器 toCamelCase）
- 登录失败：错误密码 → 401（AuthenticationFailed 语义）
- 注册成功 / 重复用户名 400
- getUserInfo：已认证返回用户结构 / 未认证 401

环境事实（2026-10-17 更新）：
- 验证码强制：CaptchaMixin.verify_captcha 未携带 captcha_key/captcha 时拒绝
  （400 CAPTCHA_ERROR）；登录/注册请求须携带 make_captcha_payload() 生成的字段
- 登录失败锁定基于 cache（test.py LocMemCache，用例间自动隔离）；密码需满足复杂度
  正则（大写+小写+数字+特殊字符，≥8 位）

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.users.tests --settings=Django_xm.settings.test
"""

import os
import uuid
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from Django_xm.common.error_codes import ErrorCode

User = get_user_model()

LOGIN_URL = "/api/v1/users/login/"
REGISTER_URL = "/api/v1/users/register/"
INFO_URL = "/api/v1/users/info/"
CAPTCHA_URL = "/api/v1/users/captcha/"

# 满足 serializers.PASSWORD_COMPLEXITY_PATTERN 的测试口令
VALID_PASSWORD = "Abcdef1!@#"


def make_captcha_payload():
    """生成一组有效验证码字段（captcha_key + captcha）。

    验证码一次性（校验通过即销毁），因此每个请求现生成而非复用。
    """
    from Django_xm.apps.users.captcha import CaptchaGenerator

    key = str(uuid.uuid4())
    code, _ = CaptchaGenerator().generate(key)
    return {"captcha_key": key, "captcha": code}


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
        resp = self.client.post(
            LOGIN_URL,
            {"username": "loginuser", "password": VALID_PASSWORD, **make_captcha_payload()},
            format="json",
        )
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
        resp = self.client.post(
            LOGIN_URL,
            {"username": "loginuser", "password": "WrongPass!23", **make_captcha_payload()},
            format="json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_login_unknown_user_rejected(self):
        """未知用户名 → 401。"""
        resp = self.client.post(
            LOGIN_URL,
            {"username": "ghost", "password": VALID_PASSWORD, **make_captcha_payload()},
            format="json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_login_no_credentials_rejected(self):
        """携带验证码但缺失凭据 → 401。"""
        resp = self.client.post(LOGIN_URL, {**make_captcha_payload()}, format="json")
        self.assertEqual(resp.status_code, 401)

    def test_login_missing_captcha_rejected(self):
        """未携带验证码 → 400 CAPTCHA_ERROR（缺失即拒绝，不给绕过 UI 的脚本留门）。"""
        resp = self.client.post(
            LOGIN_URL,
            {"username": "loginuser", "password": VALID_PASSWORD},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], int(ErrorCode.CAPTCHA_ERROR))


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
            **make_captcha_payload(),
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
            **make_captcha_payload(),
        }
        resp = self.client.post(REGISTER_URL, payload, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_register_password_mismatch_rejected(self):
        """两次密码不一致 → 400。"""
        payload = {
            "username": "mismatch",
            "password": VALID_PASSWORD,
            "password_confirm": "Different!23",
            **make_captcha_payload(),
        }
        resp = self.client.post(REGISTER_URL, payload, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_register_missing_captcha_rejected(self):
        """未携带验证码 → 400 CAPTCHA_ERROR。"""
        payload = {
            "username": "nocaptcha",
            "password": VALID_PASSWORD,
            "password_confirm": VALID_PASSWORD,
        }
        resp = self.client.post(REGISTER_URL, payload, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], int(ErrorCode.CAPTCHA_ERROR))


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


class CaptchaViewTests(TestCase):
    """验证码接口：正常路径 + 生成链路故障韧性（P1 双层防御的后端侧）。"""

    def setUp(self):
        self.client = APIClient()

    def test_captcha_success_returns_png_with_key_header(self):
        """正常路径：200 + PNG 内容 + X-Captcha-Key 响应头（含缓存写入全链路）。"""
        resp = self.client.get(CAPTCHA_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/png")
        self.assertTrue(resp["X-Captcha-Key"])
        self.assertTrue(resp.content)

    def test_captcha_generation_failure_returns_structured_503(self):
        """生成链路异常 → logger.exception 留痕 + 结构化 503（SERVICE_UNAVAILABLE），非裸 500。"""
        with (
            mock.patch("Django_xm.apps.users.views.CaptchaGenerator") as mock_generator_cls,
            mock.patch("Django_xm.apps.users.views.logger") as mock_logger,
        ):
            mock_generator_cls.return_value.generate.side_effect = RuntimeError("boom")
            resp = self.client.get(CAPTCHA_URL)

        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertEqual(body["code"], int(ErrorCode.SERVICE_UNAVAILABLE))
        self.assertEqual(body["message"], "验证码服务暂时不可用，请重试")
        mock_logger.exception.assert_called_once()


class SoftDeleteMobileReleaseTests(TestCase):
    """软删除释放 mobile 唯一索引（质量报告 H2 回归）。

    修复前：soft_delete() 保留 mobile → DB 唯一索引仍占用 →
    同手机号重新注册时 create_user 触发 IntegrityError（500）。
    修复后：soft_delete() 将 mobile 置 None，唯一索引释放。
    """

    MOBILE = "13800001234"

    def test_soft_delete_releases_mobile_unique_index(self):
        """软删后 mobile 置 None 且不再占用唯一索引。"""
        user = User.objects.create_user(
            username="softdel_user",
            password=VALID_PASSWORD,
            mobile=self.MOBILE,
        )
        user.soft_delete()

        user.refresh_from_db()
        self.assertTrue(user.is_deleted)
        self.assertIsNone(user.mobile)
        self.assertFalse(User.all_objects.filter(mobile=self.MOBILE).exists())

    def test_mobile_reusable_after_soft_delete(self):
        """软删后同手机号可重新注册（不再 IntegrityError）。"""
        User.objects.create_user(
            username="softdel_user2",
            password=VALID_PASSWORD,
            mobile=self.MOBILE,
        ).soft_delete()

        # 同手机号注册新用户：修复前此行抛 IntegrityError
        reborn = User.objects.create_user(
            username="reborn_user",
            password=VALID_PASSWORD,
            mobile=self.MOBILE,
        )
        self.assertEqual(reborn.mobile, self.MOBILE)
        self.assertTrue(User.objects.filter(mobile=self.MOBILE, is_deleted=False).exists())
