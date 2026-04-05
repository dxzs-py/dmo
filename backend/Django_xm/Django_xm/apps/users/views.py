"""
用户认证和账户管理视图
提供登录、注册、用户信息、验证码、安全登出等功能
统一使用项目标准响应格式 (code=200 表示成功)
"""

import logging
import uuid

from django.core.cache import cache
from django.http import HttpResponse
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from Django_xm.apps.core.throttling import LoginRateThrottle, SensitiveOperationRateThrottle
from Django_xm.utils.responses import (
    success_response,
    error_response,
    validation_error_response,
)
from Django_xm.utils.error_codes import ErrorCode

from .models import User
from .serializers import (
    MyTokenObtainPairSerializer,
    UserRegisterSerializer,
    UserInfoSerializer,
)
from .captcha import CaptchaGenerator

logger = logging.getLogger(__name__)


class MyObtainTokenPairView(TokenObtainPairView):
    """
    自定义登录视图，返回自定义的用户信息
    """
    serializer_class = MyTokenObtainPairSerializer
    permission_classes = [AllowAny]
    throttle_classes = [LoginRateThrottle]

    def post(self, request, *args, **kwargs):
        captcha_key = request.data.get('captcha_key')
        captcha_code = request.data.get('captcha', '').lower()

        if captcha_key and captcha_code:
            stored_code = cache.get(f'captcha:{captcha_key}')

            if not stored_code:
                return error_response(
                    code=ErrorCode.VALIDATION_FAILED,
                    message='验证码已过期，请刷新',
                    http_status=status.HTTP_400_BAD_REQUEST
                )

            if stored_code != captcha_code:
                return error_response(
                    code=ErrorCode.VALIDATION_FAILED,
                    message='验证码错误',
                    http_status=status.HTTP_400_BAD_REQUEST
                )

            cache.delete(f'captcha:{captcha_key}')

        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
            user = serializer.user
            return success_response(
                data={
                    'id': user.id,
                    'username': user.username,
                    'email': user.email,
                    'access': serializer.validated_data['access'],
                    'refresh': serializer.validated_data['refresh']
                },
                message='登录成功'
            )
        except Exception as e:
            logger.error(f"登录失败: {str(e)}")
            return error_response(
                code=ErrorCode.AUTH_FAILED,
                message='用户名或密码错误，请重新输入',
                http_status=status.HTTP_401_UNAUTHORIZED
            )


class UserRegisterView(generics.CreateAPIView):
    """
    用户注册视图
    """
    queryset = User.objects.all()
    serializer_class = UserRegisterSerializer
    permission_classes = [AllowAny]
    throttle_classes = [SensitiveOperationRateThrottle]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(
            success_response(
                data=serializer.data,
                message='注册成功'
            ).data,
            status=status.HTTP_201_CREATED,
            headers=headers
        )


class UserInfoView(APIView):
    """
    获取/更新当前登录用户信息
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        serializer = UserInfoSerializer(request.user)
        return success_response(
            data=serializer.data,
            message='获取成功'
        )

    def put(self, request):
        serializer = UserInfoSerializer(request.user, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return success_response(
                data=serializer.data,
                message='更新成功'
            )
        return validation_error_response(
            errors=serializer.errors,
            message='参数错误'
        )


class CaptchaView(APIView):
    """
    获取图形验证码
    """
    permission_classes = [AllowAny]

    def get(self, request):
        captcha_key = str(uuid.uuid4())
        generator = CaptchaGenerator()
        code, image_buf = generator.generate(captcha_key)

        return HttpResponse(image_buf, content_type='image/png', headers={
            'X-Captcha-Key': captcha_key
        })


class CaptchaVerifyView(APIView):
    """
    验证图形验证码
    """
    permission_classes = [AllowAny]

    def post(self, request):
        captcha_key = request.data.get('captcha_key')
        captcha_code = request.data.get('captcha_code', '').lower()

        if not captcha_key or not captcha_code:
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message='验证码不能为空',
                http_status=status.HTTP_400_BAD_REQUEST
            )

        stored_code = cache.get(f'captcha:{captcha_key}')

        if not stored_code:
            return error_response(
                code=ErrorCode.VALIDATION_FAILED,
                message='验证码已过期，请刷新',
                http_status=status.HTTP_400_BAD_REQUEST
            )

        if stored_code != captcha_code:
            return error_response(
                code=ErrorCode.VALIDATION_FAILED,
                message='验证码错误',
                http_status=status.HTTP_400_BAD_REQUEST
            )

        cache.delete(f'captcha:{captcha_key}')

        return success_response(
            message='验证成功'
        )


class SecureLogoutView(APIView):
    """
    安全登出视图

    功能：
    1. 将JWT token加入黑名单
    2. 清除用户在Redis中的所有会话缓存
    3. 清除服务端session数据
    4. 记录安全日志
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            from rest_framework_simplejwt.tokens import RefreshToken
            from Django_xm.apps.chat.services import SecureSessionCacheService

            user_id = request.user.id
            username = request.user.username

            refresh_token = request.data.get('refresh')

            if refresh_token:
                try:
                    token = RefreshToken(refresh_token)
                    token.blacklist()
                    logger.info(f"Blacklisted refresh token for user {username}")
                except Exception as e:
                    logger.warning(f"Failed to blacklist token: {str(e)}")

            invalidated_count = SecureSessionCacheService.invalidate_all_user_sessions(user_id)

            if hasattr(request, 'session'):
                request.session.flush()

            logger.info(
                f"User {username} (ID: {user_id}) logged out securely. "
                f"Invalidated {invalidated_count} cached sessions."
            )

            return success_response(
                data={
                    'sessions_cleared': invalidated_count,
                    'token_blacklisted': bool(refresh_token)
                },
                message='安全登出成功'
            )

        except Exception as e:
            logger.error(f"Secure logout failed for user {request.user.id}: {str(e)}")
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message='登出过程中出现错误',
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
