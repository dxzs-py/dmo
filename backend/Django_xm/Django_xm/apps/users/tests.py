"""
用户应用测试
覆盖模型、认证后端、序列化器和视图
"""
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from .models import User
from .backends import UsernameMobileAuthBackend, get_account_by_mobile

User = get_user_model()


class UserModelTests(TestCase):
    """用户模型测试"""

    def test_create_user(self):
        """测试创建用户"""
        user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        self.assertEqual(user.username, 'testuser')
        self.assertEqual(user.email, 'test@example.com')
        self.assertTrue(user.check_password('testpass123'))
        self.assertFalse(user.is_deleted)

    def test_create_user_with_mobile(self):
        """测试创建带手机号的用户"""
        user = User.objects.create_user(
            username='mobileuser',
            mobile='13800138000',
            password='testpass123'
        )
        self.assertEqual(user.mobile, '13800138000')

    def test_user_str(self):
        """测试User的__str__方法"""
        user = User.objects.create_user(
            username='struser',
            password='testpass123'
        )
        self.assertEqual(str(user), 'struser')

    def test_soft_delete(self):
        """测试软删除功能"""
        user = User.objects.create_user(
            username='deleteuser',
            password='testpass123'
        )
        user_id = user.id

        user.soft_delete()
        self.assertTrue(user.is_deleted)
        self.assertIsNotNone(user.deleted_at)

        self.assertIn(user, User.all_objects.filter(id=user_id))
        self.assertNotIn(user, User.objects.filter(id=user_id))

    def test_restore_user(self):
        """测试恢复软删除的用户"""
        user = User.objects.create_user(
            username='restoreuser',
            password='testpass123'
        )
        user.soft_delete()
        user.restore()

        self.assertFalse(user.is_deleted)
        self.assertIsNone(user.deleted_at)
        self.assertIn(user, User.objects.all())


class AuthenticationBackendTests(TestCase):
    """认证后端测试"""

    def setUp(self):
        """创建测试用户"""
        self.user_by_username = User.objects.create_user(
            username='auth_test',
            email='auth@test.com',
            password='correctpass'
        )
        self.user_by_mobile = User.objects.create_user(
            username='mobile_auth',
            mobile='13900139000',
            password='mobilepass'
        )
        self.backend = UsernameMobileAuthBackend()

    def test_authenticate_by_username(self):
        """测试通过用户名认证"""
        result = self.backend.authenticate(
            None,
            username='auth_test',
            password='correctpass'
        )
        self.assertEqual(result, self.user_by_username)

    def test_authenticate_by_mobile(self):
        """测试通过手机号认证"""
        result = self.backend.authenticate(
            None,
            username='13900139000',
            password='mobilepass'
        )
        self.assertEqual(result, self.user_by_mobile)

    def test_authenticate_wrong_password(self):
        """测试错误密码"""
        result = self.backend.authenticate(
            None,
            username='auth_test',
            password='wrongpass'
        )
        self.assertIsNone(result)

    def test_authenticate_nonexistent_user(self):
        """测试不存在的用户"""
        result = self.backend.authenticate(
            None,
            username='nonexistent',
            password='anypass'
        )
        self.assertIsNone(result)

    def test_authenticate_disabled_user(self):
        """测试禁用用户无法认证"""
        self.user_by_username.is_active = False
        self.user_by_username.save()
        result = self.backend.authenticate(
            None,
            username='auth_test',
            password='correctpass'
        )
        self.assertIsNone(result)

    def test_get_account_by_mobile_with_phone(self):
        """测试通过手机号获取账户"""
        result = get_account_by_mobile('13900139000')
        self.assertEqual(result, self.user_by_mobile)

    def test_get_account_by_mobile_with_username(self):
        """测试通过用户名获取账户（非手机号格式）"""
        result = get_account_by_mobile('auth_test')
        self.assertEqual(result, self.user_by_username)

    def test_get_account_by_mobile_not_found(self):
        """测试不存在的账户返回None"""
        result = get_account_by_mobile('99999999999')
        self.assertIsNone(result)


class UserSerializerTests(TestCase):
    """用户序列化器测试"""

    from .serializers import (
        UserRegisterSerializer,
        UserInfoSerializer
    )

    def test_register_serializer_valid_data(self):
        """测试注册序列化器有效数据"""
        data = {
            'username': 'newuser',
            'password': 'validpass123',
            'password_confirm': 'validpass123',
            'email': 'new@test.com'
        }
        serializer = self.UserRegisterSerializer(data=data)
        self.assertTrue(serializer.is_valid())

    def test_register_serializer_password_mismatch(self):
        """测试注册序列化器密码不匹配"""
        data = {
            'username': 'baduser',
            'password': 'pass123',
            'password_confirm': 'different456',
            'email': 'bad@test.com'
        }
        serializer = self.UserRegisterSerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn('password_confirm', serializer.errors)

    def test_register_serializer_short_password(self):
        """测试注册序列化器密码过短"""
        data = {
            'username': 'shortpass',
            'password': 'short',
            'password_confirm': 'short',
        }
        serializer = self.UserRegisterSerializer(data=data)
        self.assertFalse(serializer.is_valid())

    def test_user_info_serializer(self):
        """测试用户信息序列化器"""
        user = User.objects.create_user(
            username='infouser',
            email='info@test.com',
            mobile='13700137000',
            password='testpass123'
        )
        serializer = self.UserInfoSerializer(user)
        data = serializer.data

        self.assertEqual(data['username'], 'infouser')
        self.assertEqual(data['email'], 'info@test.com')
        self.assertEqual(data['mobile'], '13700137000')
        self.assertIn('id', data)
        self.assertIn('date_joined', data)


class UserViewTests(TestCase):
    """用户视图测试"""

    def setUp(self):
        """准备测试客户端和用户"""
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='viewtest',
            email='view@test.com',
            password='viewpass123'
        )

    def test_login_success(self):
        """测试登录成功"""
        url = reverse('token_obtain_pair')
        data = {
            'username': 'viewtest',
            'password': 'viewpass123'
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data.get('data', {}))
        self.assertIn('refresh', response.data.get('data', {}))

    def test_login_wrong_password(self):
        """测试登录失败-错误密码"""
        url = reverse('token_obtain_pair')
        data = {
            'username': 'viewtest',
            'password': 'wrongpassword'
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_register_success(self):
        """测试注册成功"""
        url = reverse('user_register')
        data = {
            'username': 'regtestuser',
            'password': 'regpass123',
            'password_confirm': 'regpass123',
            'email': 'reg@new.com'
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(User.objects.filter(username='regtestuser').exists())

    def test_get_user_info_unauthorized(self):
        """测试未授权访问用户信息"""
        url = reverse('user_info')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_get_user_info_authorized(self):
        """测试已授权访问用户信息"""
        url = reverse('token_obtain_pair')
        login_resp = self.client.post(url, {
            'username': 'viewtest',
            'password': 'viewpass123'
        }, format='json')

        token = login_resp.data.get('data', {}).get('access', '')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

        info_url = reverse('user_info')
        response = self.client.get(info_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get('data', {}).get('username'), 'viewtest')


class CaptchaViewTests(TestCase):
    """验证码视图测试"""

    def setUp(self):
        self.client = APIClient()

    def test_captcha_view_returns_image(self):
        """测试验证码接口返回图片"""
        url = reverse('captcha')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'image/png')
        self.assertIn('X-Captcha-Key', response)
