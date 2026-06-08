"""
用户认证和账户管理视图
提供登录、注册、用户信息、验证码、安全登出等功能
统一使用项目标准响应格式 (code=200 表示成功)
"""

import hmac
import logging
import uuid

from django.core.cache import cache
from django.http import HttpResponse
from rest_framework import generics, status, serializers
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView as _TokenRefreshView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

from Django_xm.apps.core.throttling import LoginRateThrottle, SensitiveOperationRateThrottle
from Django_xm.common.responses import (
    success_response,
    error_response,
    validation_error_response,
)
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.captcha_mixin import CaptchaMixin

from .models import User
from .serializers import (
    MyTokenObtainPairSerializer,
    UserRegisterSerializer,
    UserInfoSerializer,
    ChangePasswordSerializer,
    BindPhoneSerializer,
    UserProfileSerializer,
    UserPreferencesSerializer,
)
from .captcha import CaptchaGenerator

logger = logging.getLogger(__name__)


class MyObtainTokenPairView(CaptchaMixin, TokenObtainPairView):
    """
    自定义登录视图，返回自定义的用户信息
    """
    serializer_class = MyTokenObtainPairSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [LoginRateThrottle]

    def post(self, request, *args, **kwargs):
        captcha_result = self.verify_captcha(request.data)
        if captcha_result is not None:
            return captcha_result

        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except Exception as e:
            from rest_framework.exceptions import AuthenticationFailed as DRFAuthFailed
            from rest_framework.exceptions import NotAuthenticated as DRFNotAuthenticated
            if isinstance(e, (DRFAuthFailed, DRFNotAuthenticated)):
                return error_response(
                    code=ErrorCode.LOGIN_FAILED,
                    message='用户名或密码错误，请重新输入',
                    http_status=status.HTTP_401_UNAUTHORIZED
                )
            if isinstance(e, serializers.ValidationError):
                return error_response(
                    code=ErrorCode.LOGIN_FAILED,
                    message='用户名或密码错误，请重新输入',
                    http_status=status.HTTP_401_UNAUTHORIZED
                )
            logger.error(f"登录异常: {type(e).__name__}: {str(e)}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message='服务器错误，请稍后重试',
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

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


class MyTokenRefreshView(_TokenRefreshView):
    """
    自定义Token刷新视图，捕获用户不存在等异常，返回统一格式响应
    """
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as e:
            raise InvalidToken(e.args[0])
        except User.DoesNotExist:
            return error_response(
                code=ErrorCode.TOKEN_INVALID,
                message='用户不存在，请重新登录',
                http_status=status.HTTP_401_UNAUTHORIZED
            )
        except Exception as e:
            logger.error(f"Token刷新异常: {type(e).__name__}: {str(e)}", exc_info=True)
            return error_response(
                code=ErrorCode.TOKEN_INVALID,
                message='Token刷新失败，请重新登录',
                http_status=status.HTTP_401_UNAUTHORIZED
            )

        return success_response(
            data=serializer.validated_data,
            message='Token刷新成功'
        )


class UserRegisterView(CaptchaMixin, generics.CreateAPIView):
    """
    用户注册视图
    """
    queryset = User.objects.all()
    serializer_class = UserRegisterSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [SensitiveOperationRateThrottle]

    def create(self, request, *args, **kwargs):
        try:
            captcha_result = self.verify_captcha(request.data)
            if captcha_result is not None:
                return captcha_result

            serializer = self.get_serializer(data=request.data)
            try:
                serializer.is_valid(raise_exception=True)
            except serializers.ValidationError as e:
                logger.warning(f"注册验证失败: {str(e)}")
                return validation_error_response(
                    errors=serializer.errors,
                    message='注册失败，请检查输入'
                )
            self.perform_create(serializer)
            return success_response(
                data=serializer.data,
                message='注册成功',
                http_status=status.HTTP_201_CREATED,
            )
        except Exception as e:
            logger.exception("用户注册失败")
            return error_response(ErrorCode.SERVER_ERROR, message='操作失败，请稍后重试')


class UserInfoView(APIView):
    """
    获取/更新当前登录用户信息
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            serializer = UserInfoSerializer(request.user)
            return success_response(
                data=serializer.data,
                message='获取成功'
            )
        except Exception as e:
            logger.exception("获取用户信息失败")
            return error_response(ErrorCode.SERVER_ERROR, message='操作失败，请稍后重试')

    def put(self, request):
        try:
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
        except Exception as e:
            logger.exception("更新用户信息失败")
            return error_response(ErrorCode.SERVER_ERROR, message='操作失败，请稍后重试')


class CaptchaView(APIView):
    """
    获取图形验证码
    """
    permission_classes = [AllowAny]

    def get(self, request):
        try:
            captcha_key = str(uuid.uuid4())
            generator = CaptchaGenerator()
            code, image_buf = generator.generate(captcha_key)

            return HttpResponse(image_buf, content_type='image/png', headers={
                'X-Captcha-Key': captcha_key
            })
        except Exception as e:
            logger.exception("获取验证码失败")
            return error_response(ErrorCode.SERVER_ERROR, message='操作失败，请稍后重试')


class CaptchaVerifyView(APIView):
    """
    验证图形验证码
    """
    permission_classes = [AllowAny]

    def post(self, request):
        try:
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

            if not hmac.compare_digest(str(stored_code), str(captcha_code)):
                return error_response(
                    code=ErrorCode.VALIDATION_FAILED,
                    message='验证码错误',
                    http_status=status.HTTP_400_BAD_REQUEST
                )

            cache.delete(f'captcha:{captcha_key}')

            return success_response(
                message='验证成功'
            )
        except Exception as e:
            logger.exception("验证验证码失败")
            return error_response(ErrorCode.SERVER_ERROR, message='操作失败，请稍后重试')


class SecureLogoutView(APIView):
    """
    安全登出视图

    功能：
    1. 将JWT token加入黑名单
    2. 清除用户在Redis中的所有会话缓存
    3. 清除服务端session数据
    4. 记录安全日志

    支持两种认证方式：
    - access token（通过 request.user）
    - refresh token（即使 access token 已过期）
    """
    permission_classes = []

    def post(self, request):
        try:
            from rest_framework_simplejwt.tokens import RefreshToken
            from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
            from Django_xm.apps.cache_manager.services.secure_session_cache import SecureSessionCacheService

            user_id = None
            username = None

            if request.user and request.user.is_authenticated:
                user_id = request.user.id
                username = request.user.username
            else:
                refresh_token = request.data.get('refresh')
                if refresh_token:
                    try:
                        token = RefreshToken(refresh_token)
                        user_id = token['user_id']
                        user = User.objects.filter(id=user_id).first()
                        if user:
                            username = user.username
                    except (InvalidToken, TokenError, Exception) as e:
                        logger.warning(f"Failed to decode refresh token for logout: {str(e)}")

            if not user_id:
                return success_response(
                    data={
                        'sessions_cleared': 0,
                        'token_blacklisted': False
                    },
                    message='登出成功（无有效会话）'
                )

            refresh_token = request.data.get('refresh')
            token_blacklisted = False

            if refresh_token:
                try:
                    token = RefreshToken(refresh_token)
                    token.blacklist()
                    token_blacklisted = True
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
                    'token_blacklisted': token_blacklisted
                },
                message='安全登出成功'
            )

        except Exception as e:
            logger.error(f"Secure logout failed: {str(e)}")
            return success_response(
                data={
                    'sessions_cleared': 0,
                    'token_blacklisted': False
                },
                message='登出成功（清理完成）'
            )


class UserProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def put(self, request):
        try:
            serializer = UserProfileSerializer(
                data=request.data, context={'request': request}
            )
            if not serializer.is_valid():
                return validation_error_response(
                    errors=serializer.errors,
                    message='参数错误'
                )

            user = request.user
            for field, value in serializer.validated_data.items():
                setattr(user, field, value)
            user.save()
            out = UserInfoSerializer(user)
            return success_response(data=out.data, message='资料更新成功')
        except Exception as e:
            logger.exception("更新用户资料失败")
            return error_response(ErrorCode.SERVER_ERROR, message='操作失败，请稍后重试')


class UserAvatarView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            user = request.user
            avatar_file = request.FILES.get('avatar')

            if not avatar_file:
                return error_response(
                    code=ErrorCode.INVALID_PARAMS,
                    message='请选择头像文件',
                    http_status=status.HTTP_400_BAD_REQUEST
                )

            allowed_types = ['image/png', 'image/jpeg', 'image/gif', 'image/webp']
            if avatar_file.content_type not in allowed_types:
                return error_response(
                    code=ErrorCode.INVALID_PARAMS,
                    message='仅支持 PNG、JPG、GIF、WebP 格式',
                    http_status=status.HTTP_400_BAD_REQUEST
                )

            if avatar_file.size > 2 * 1024 * 1024:
                return error_response(
                    code=ErrorCode.INVALID_PARAMS,
                    message='头像文件不能超过 2MB',
                    http_status=status.HTTP_400_BAD_REQUEST
                )

            if user.avatar:
                try:
                    user.avatar.delete(save=False)
                except Exception:
                    pass

            user.avatar = avatar_file
            user.save()
            serializer = UserInfoSerializer(user)
            return success_response(data=serializer.data, message='头像更新成功')
        except Exception as e:
            logger.exception("上传头像失败")
            return error_response(ErrorCode.SERVER_ERROR, message='操作失败，请稍后重试')


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [SensitiveOperationRateThrottle]

    def post(self, request):
        try:
            serializer = ChangePasswordSerializer(
                data=request.data, context={'request': request}
            )
            if not serializer.is_valid():
                return validation_error_response(
                    errors=serializer.errors,
                    message='参数错误'
                )

            user = request.user
            user.set_password(serializer.validated_data['new_password'])
            user.save()
            return success_response(message='密码修改成功')
        except Exception as e:
            logger.exception("修改密码失败")
            return error_response(ErrorCode.SERVER_ERROR, message='操作失败，请稍后重试')


class BindPhoneView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            serializer = BindPhoneSerializer(
                data=request.data, context={'request': request}
            )
            if not serializer.is_valid():
                return validation_error_response(
                    errors=serializer.errors,
                    message='参数错误'
                )

            user = request.user
            user.mobile = serializer.validated_data['mobile']
            user.save()
            serializer_out = UserInfoSerializer(user)
            return success_response(data=serializer_out.data, message='手机号绑定成功')
        except Exception as e:
            logger.exception("绑定手机号失败")
            return error_response(ErrorCode.SERVER_ERROR, message='操作失败，请稍后重试')


class UserPreferencesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            user = request.user
            preferences = {
                'theme': user.theme,
                'language': user.language,
                'notifications_enabled': user.notifications_enabled,
                'auto_save_sessions': user.auto_save_sessions,
            }
            return success_response(data=preferences)
        except Exception as e:
            logger.exception("获取用户偏好设置失败")
            return error_response(ErrorCode.SERVER_ERROR, message='操作失败，请稍后重试')

    def put(self, request):
        try:
            serializer = UserPreferencesSerializer(data=request.data)
            if not serializer.is_valid():
                return validation_error_response(
                    errors=serializer.errors,
                    message='参数错误'
                )

            user = request.user
            for field, value in serializer.validated_data.items():
                setattr(user, field, value)
            user.save()
            return success_response(message='偏好设置更新成功')
        except Exception as e:
            logger.exception("更新用户偏好设置失败")
            return error_response(ErrorCode.SERVER_ERROR, message='操作失败，请稍后重试')


class UserUsageStatsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            user = request.user
            stats = {
                'total_messages': getattr(user, 'total_messages', 0),
                'total_sessions': getattr(user, 'total_sessions', 0),
                'total_tokens': getattr(user, 'total_tokens', 0),
                'active_days': getattr(user, 'active_days', 0),
            }
            return success_response(data=stats)
        except Exception as e:
            logger.exception("获取用户使用统计失败")
            return error_response(ErrorCode.SERVER_ERROR, message='操作失败，请稍后重试')


class UserAccountDeleteView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [SensitiveOperationRateThrottle]

    def delete(self, request):
        try:
            user = request.user
            user_id = user.id
            user.soft_delete()

            from Django_xm.apps.core.signals import ai_data_cleanup_needed
            ai_data_cleanup_needed.send(
                sender=self.__class__,
                user_id=user_id,
                session_id=None,
            )

            return success_response(message='账户已成功注销')
        except Exception as e:
            logger.exception("注销账户失败")
            return error_response(ErrorCode.SERVER_ERROR, message='操作失败，请稍后重试')
