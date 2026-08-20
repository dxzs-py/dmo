"""
用户认证和账户管理视图
提供登录、注册、用户信息、验证码、安全登出等功能
统一使用项目标准响应格式 (code=200 表示成功)
"""

import hmac
import logging
import uuid
from typing import Any

from django.core.cache import cache
from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework import generics, serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.views import TokenRefreshView as _TokenRefreshView

from Django_xm.apps.core.throttling import LoginRateThrottle, MetaRateThrottle, SensitiveOperationRateThrottle
from Django_xm.common.captcha_mixin import CaptchaMixin
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.responses import (
    error_response,
    success_response,
    validation_error_response,
)
from Django_xm.common.serializers import EmptySerializer

from .captcha import CaptchaGenerator
from .models import User
from .serializers import (
    BindPhoneSerializer,
    ChangePasswordSerializer,
    MyTokenObtainPairSerializer,
    UserInfoSerializer,
    UserPreferencesSerializer,
    UserProfileSerializer,
    UserRegisterSerializer,
)
from .services import LoginSecurityService

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

        # 提取 username 用于登录失败锁定（按账号维度，防暴力破解）
        username = (request.data.get("username") or "").strip()

        # 账号锁定检查：即使密码正确也拒绝，必须等待锁定期过
        if LoginSecurityService.is_locked(username):
            remaining = LoginSecurityService.get_remaining_lock_seconds(username)
            minutes = max(1, remaining // 60)
            return error_response(
                code=ErrorCode.ACCOUNT_LOCKED,
                message=f"账号已锁定，请 {minutes} 分钟后重试",
                http_status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except Exception as e:
            from rest_framework.exceptions import AuthenticationFailed as DRFAuthFailed
            from rest_framework.exceptions import NotAuthenticated as DRFNotAuthenticated

            if isinstance(e, (DRFAuthFailed, DRFNotAuthenticated)):
                # 真正的认证失败（用户名不存在/密码错误）：记录失败次数（可能触发锁定）
                fail_count = LoginSecurityService.record_failure(username)
                remaining_attempts = max(0, LoginSecurityService.MAX_FAIL_COUNT - fail_count)
                message = "用户名或密码错误，请重新输入"
                if remaining_attempts > 0:
                    message += f"，剩余尝试次数 {remaining_attempts} 次"
                else:
                    message += "，账号已锁定"
                return error_response(
                    code=ErrorCode.LOGIN_FAILED, message=message, http_status=status.HTTP_401_UNAUTHORIZED
                )
            if isinstance(e, serializers.ValidationError):
                # 字段校验失败（如字段缺失/格式错误）：不计入失败次数
                return error_response(
                    code=ErrorCode.LOGIN_FAILED,
                    message="用户名或密码错误，请重新输入",
                    http_status=status.HTTP_401_UNAUTHORIZED,
                )
            logger.exception(f"登录异常: {type(e).__name__}")
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message="服务器错误，请稍后重试",
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        user = serializer.user
        # 登录成功：清除失败计数（避免历史失败影响）
        LoginSecurityService.record_success(username)
        return success_response(
            data={
                "id": user.id,
                "username": user.username,
                "email": user.email,
                # is_staff 为前端 isAdmin 契约源头（snake_case 正确，camelCase 转换在 axios 层）
                "is_staff": user.is_staff,
                "access": serializer.validated_data["access"],
                "refresh": serializer.validated_data["refresh"],
            },
            message="登录成功",
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
            raise InvalidToken(e.args[0]) from e
        except User.DoesNotExist:
            return error_response(
                code=ErrorCode.TOKEN_INVALID, message="用户不存在，请重新登录", http_status=status.HTTP_401_UNAUTHORIZED
            )
        except Exception as e:
            logger.exception(f"Token刷新异常: {type(e).__name__}")
            return error_response(
                code=ErrorCode.TOKEN_INVALID,
                message="Token刷新失败，请重新登录",
                http_status=status.HTTP_401_UNAUTHORIZED,
            )

        return success_response(data=serializer.validated_data, message="Token刷新成功")


class UserRegisterView(CaptchaMixin, generics.CreateAPIView):
    """
    用户注册视图
    """

    queryset = User.objects.all()
    serializer_class = UserRegisterSerializer
    permission_classes = [AllowAny]
    authentication_classes: list[Any] = []
    throttle_classes = [SensitiveOperationRateThrottle]

    def create(self, request, *args, **kwargs):
        """用户注册

        异常处理策略（与全局 custom_exception_handler 协同）：
        - CaptchaMixin.verify_captcha 内部已返回结构化错误响应，不抛异常
        - serializer.is_valid 抛 ValidationError → 本地捕获返回 400 + 字段错误
        - 其他未预期异常（IntegrityError / 数据库故障等）不本地吞没，
          冒泡到全局 custom_exception_handler 返回标准 500 响应
        """
        captcha_result = self.verify_captcha(request.data)
        if captcha_result is not None:
            return captcha_result

        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except serializers.ValidationError as e:
            logger.warning(f"注册验证失败: {e!s}")
            return validation_error_response(errors=serializer.errors, message="注册失败，请检查输入")
        self.perform_create(serializer)
        return success_response(
            data=serializer.data,
            message="注册成功",
            http_status=status.HTTP_201_CREATED,
        )


class UserInfoView(APIView):
    """
    获取/更新当前登录用户信息
    """

    permission_classes = [IsAuthenticated]
    # 页面加载即请求的只读接口，独立 meta 额度（Task 3.2）
    throttle_classes = [MetaRateThrottle]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request):
        try:
            serializer = UserInfoSerializer(request.user)
            return success_response(data=serializer.data, message="获取成功")
        except Exception:
            logger.exception("获取用户信息失败")
            return error_response(ErrorCode.SERVER_ERROR, message="操作失败，请稍后重试")

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def put(self, request):
        try:
            serializer = UserInfoSerializer(request.user, data=request.data, partial=True)
            if serializer.is_valid():
                serializer.save()
                return success_response(data=serializer.data, message="更新成功")
            return validation_error_response(errors=serializer.errors, message="参数错误")
        except Exception:
            logger.exception("更新用户信息失败")
            return error_response(ErrorCode.SERVER_ERROR, message="操作失败，请稍后重试")


class CaptchaView(APIView):
    """
    获取图形验证码
    """

    permission_classes = [AllowAny]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request):
        try:
            captcha_key = str(uuid.uuid4())
            generator = CaptchaGenerator()
            _code, image_buf = generator.generate(captcha_key)

            return HttpResponse(image_buf, content_type="image/png", headers={"X-Captcha-Key": captcha_key})
        except Exception:
            logger.exception("获取验证码失败")
            return error_response(ErrorCode.SERVER_ERROR, message="操作失败，请稍后重试")


class CaptchaVerifyView(APIView):
    """
    验证图形验证码
    """

    permission_classes = [AllowAny]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request):
        try:
            captcha_key = request.data.get("captcha_key")
            captcha_code = request.data.get("captcha_code", "").lower()

            if not captcha_key or not captcha_code:
                return error_response(
                    code=ErrorCode.INVALID_PARAMS, message="验证码不能为空", http_status=status.HTTP_400_BAD_REQUEST
                )

            stored_code = cache.get(f"captcha:{captcha_key}")

            if not stored_code:
                return error_response(
                    code=ErrorCode.VALIDATION_FAILED,
                    message="验证码已过期，请刷新",
                    http_status=status.HTTP_400_BAD_REQUEST,
                )

            if not hmac.compare_digest(str(stored_code), str(captcha_code)):
                return error_response(
                    code=ErrorCode.VALIDATION_FAILED, message="验证码错误", http_status=status.HTTP_400_BAD_REQUEST
                )

            cache.delete(f"captcha:{captcha_key}")

            return success_response(message="验证成功")
        except Exception:
            logger.exception("验证验证码失败")
            return error_response(ErrorCode.SERVER_ERROR, message="操作失败，请稍后重试")


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

    限流：登出为公开接口（permission_classes=[]），与登录接口访问模式类似，
    采用 LoginRateThrottle（按 IP，5/min）防止滥用导致 token 黑名单膨胀。
    """

    permission_classes = []
    throttle_classes = [LoginRateThrottle]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request):
        """安全登出

        异常处理策略（与全局 custom_exception_handler 协同）：
        - RefreshToken 解码失败：本地捕获（InvalidToken/TokenError），降级为"无有效会话"返回成功
        - token.blacklist() 失败：本地捕获（黑名单基础设施故障不阻塞登出流程），日志记录后继续
        - 其他未预期异常（Redis 不可达 / 数据库故障等）冒泡到全局 custom_exception_handler
          返回标准 500 响应，避免"清理失败仍返回成功"造成前端误判
        """
        from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
        from rest_framework_simplejwt.tokens import RefreshToken

        from Django_xm.apps.cache_manager.services.secure_session_cache import SecureSessionCacheService

        user_id = None
        username = None

        if request.user and request.user.is_authenticated:
            user_id = request.user.id
            username = request.user.username
        else:
            refresh_token = request.data.get("refresh")
            if refresh_token:
                try:
                    token = RefreshToken(refresh_token)
                    user_id = token["user_id"]
                    user = User.objects.filter(id=user_id).first()
                    if user:
                        username = user.username
                except (InvalidToken, TokenError) as e:
                    logger.warning(f"Failed to decode refresh token for logout: {e!s}")

        if not user_id:
            return success_response(
                data={"sessions_cleared": 0, "token_blacklisted": False}, message="登出成功（无有效会话）"
            )

        refresh_token = request.data.get("refresh")
        token_blacklisted = False

        if refresh_token:
            try:
                token = RefreshToken(refresh_token)
                token.blacklist()
                token_blacklisted = True
                logger.info(f"Blacklisted refresh token for user {username}")
            except (InvalidToken, TokenError) as e:
                # Token 无效/已过期不阻塞登出（用户本就要求登出）
                logger.warning(f"Failed to blacklist token (invalid): {e!s}")
            except Exception as e:
                # 黑名单基础设施故障（Redis 不可达等）不阻塞登出流程，
                # 但记录 warning 便于运维定位
                logger.warning(f"Failed to blacklist token: {e!s}")

        invalidated_count = SecureSessionCacheService.invalidate_all_user_sessions(user_id)

        if hasattr(request, "session"):
            request.session.flush()

        logger.info(
            f"User {username} (ID: {user_id}) logged out securely. Invalidated {invalidated_count} cached sessions."
        )

        return success_response(
            data={"sessions_cleared": invalidated_count, "token_blacklisted": token_blacklisted}, message="安全登出成功"
        )


class UserProfileView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def put(self, request):
        try:
            serializer = UserProfileSerializer(data=request.data, context={"request": request})
            if not serializer.is_valid():
                return validation_error_response(errors=serializer.errors, message="参数错误")

            user = request.user
            for field, value in serializer.validated_data.items():
                setattr(user, field, value)
            user.save()
            out = UserInfoSerializer(user)
            return success_response(data=out.data, message="资料更新成功")
        except Exception:
            logger.exception("更新用户资料失败")
            return error_response(ErrorCode.SERVER_ERROR, message="操作失败，请稍后重试")


class UserAvatarView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request):
        try:
            user = request.user
            avatar_file = request.FILES.get("avatar")

            if not avatar_file:
                return error_response(
                    code=ErrorCode.INVALID_PARAMS, message="请选择头像文件", http_status=status.HTTP_400_BAD_REQUEST
                )

            allowed_types = ["image/png", "image/jpeg", "image/gif", "image/webp"]
            if avatar_file.content_type not in allowed_types:
                return error_response(
                    code=ErrorCode.INVALID_PARAMS,
                    message="仅支持 PNG、JPG、GIF、WebP 格式",
                    http_status=status.HTTP_400_BAD_REQUEST,
                )

            if avatar_file.size > 2 * 1024 * 1024:
                return error_response(
                    code=ErrorCode.INVALID_PARAMS,
                    message="头像文件不能超过 2MB",
                    http_status=status.HTTP_400_BAD_REQUEST,
                )

            if user.avatar:
                try:
                    user.avatar.delete(save=False)
                except Exception:  # noqa: S110  # cleanup, 旧头像文件删除失败不影响新头像上传
                    pass

            user.avatar = avatar_file
            user.save()
            serializer = UserInfoSerializer(user)
            return success_response(data=serializer.data, message="头像更新成功")
        except Exception:
            logger.exception("上传头像失败")
            return error_response(ErrorCode.SERVER_ERROR, message="操作失败，请稍后重试")


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [SensitiveOperationRateThrottle]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request):
        try:
            serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
            if not serializer.is_valid():
                return validation_error_response(errors=serializer.errors, message="参数错误")

            user = request.user
            user.set_password(serializer.validated_data["new_password"])
            user.save()
            return success_response(message="密码修改成功")
        except Exception:
            logger.exception("修改密码失败")
            return error_response(ErrorCode.SERVER_ERROR, message="操作失败，请稍后重试")


class BindPhoneView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request):
        try:
            serializer = BindPhoneSerializer(data=request.data, context={"request": request})
            if not serializer.is_valid():
                return validation_error_response(errors=serializer.errors, message="参数错误")

            user = request.user
            user.mobile = serializer.validated_data["mobile"]
            user.save()
            serializer_out = UserInfoSerializer(user)
            return success_response(data=serializer_out.data, message="手机号绑定成功")
        except Exception:
            logger.exception("绑定手机号失败")
            return error_response(ErrorCode.SERVER_ERROR, message="操作失败，请稍后重试")


class UserPreferencesView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request):
        try:
            user = request.user
            preferences = {
                "theme": user.theme,
                "language": user.language,
                "notifications_enabled": user.notifications_enabled,
                "auto_save_sessions": user.auto_save_sessions,
            }
            return success_response(data=preferences)
        except Exception:
            logger.exception("获取用户偏好设置失败")
            return error_response(ErrorCode.SERVER_ERROR, message="操作失败，请稍后重试")

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def put(self, request):
        try:
            serializer = UserPreferencesSerializer(data=request.data)
            if not serializer.is_valid():
                return validation_error_response(errors=serializer.errors, message="参数错误")

            user = request.user
            for field, value in serializer.validated_data.items():
                setattr(user, field, value)
            user.save()
            return success_response(message="偏好设置更新成功")
        except Exception:
            logger.exception("更新用户偏好设置失败")
            return error_response(ErrorCode.SERVER_ERROR, message="操作失败，请稍后重试")


class UserUsageStatsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request):
        try:
            user = request.user
            stats = {
                "total_messages": getattr(user, "total_messages", 0),
                "total_sessions": getattr(user, "total_sessions", 0),
                "total_tokens": getattr(user, "total_tokens", 0),
                "active_days": getattr(user, "active_days", 0),
            }
            return success_response(data=stats)
        except Exception:
            logger.exception("获取用户使用统计失败")
            return error_response(ErrorCode.SERVER_ERROR, message="操作失败，请稍后重试")


class UserAccountDeleteView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [SensitiveOperationRateThrottle]

    @extend_schema(responses={200: EmptySerializer})
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

            return success_response(message="账户已成功注销")
        except Exception:
            logger.exception("注销账户失败")
            return error_response(ErrorCode.SERVER_ERROR, message="操作失败，请稍后重试")
