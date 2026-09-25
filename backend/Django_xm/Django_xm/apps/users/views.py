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
from rest_framework.exceptions import AuthenticationFailed, NotAuthenticated
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.views import TokenRefreshView as _TokenRefreshView

from Django_xm.apps.core.throttling import LoginRateThrottle, MetaRateThrottle, SensitiveOperationRateThrottle
from Django_xm.common.captcha_mixin import CaptchaMixin
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.exceptions import BaseAppError
from Django_xm.common.responses import success_response
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
        # 校验失败抛 BaseAppError（CAPTCHA_ERROR），冒泡至全局异常处理器
        self.verify_captcha(request.data)

        # 提取 username 用于登录失败锁定（按账号维度，防暴力破解）
        username = (request.data.get("username") or "").strip()

        # 账号锁定检查：即使密码正确也拒绝，必须等待锁定期过
        if LoginSecurityService.is_locked(username):
            remaining = LoginSecurityService.get_remaining_lock_seconds(username)
            minutes = max(1, remaining // 60)
            raise BaseAppError(
                f"账号已锁定，请 {minutes} 分钟后重试",
                business_code=ErrorCode.ACCOUNT_LOCKED,
            )

        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except (AuthenticationFailed, NotAuthenticated) as e:
            # 真正的认证失败（用户名不存在/密码错误）：记录失败次数（可能触发锁定）
            fail_count = LoginSecurityService.record_failure(username)
            remaining_attempts = max(0, LoginSecurityService.MAX_FAIL_COUNT - fail_count)
            message = "用户名或密码错误，请重新输入"
            if remaining_attempts > 0:
                message += f"，剩余尝试次数 {remaining_attempts} 次"
            else:
                message += "，账号已锁定"
            raise BaseAppError(message, business_code=ErrorCode.LOGIN_FAILED) from e
        except serializers.ValidationError as e:
            # 字段校验失败（如字段缺失/格式错误）：不计入失败次数
            raise BaseAppError("用户名或密码错误，请重新输入", business_code=ErrorCode.LOGIN_FAILED) from e

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
            raise BaseAppError("用户不存在，请重新登录", business_code=ErrorCode.TOKEN_INVALID) from None

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
        - CaptchaMixin.verify_captcha 校验失败抛 BaseAppError，由全局处理器统一转换
        - serializer.is_valid(raise_exception=True) 校验失败抛 ValidationError →
          全局处理器返回 400 + 字段错误
        - 其他未预期异常（IntegrityError / 数据库故障等）不本地吞没，
          冒泡到全局 custom_exception_handler 返回标准 500 响应
        """
        self.verify_captcha(request.data)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
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
        serializer = UserInfoSerializer(request.user)
        return success_response(data=serializer.data, message="获取成功")

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def put(self, request):
        serializer = UserInfoSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return success_response(data=serializer.data, message="更新成功")


class CaptchaView(APIView):
    """
    获取图形验证码
    """

    permission_classes = [AllowAny]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request):
        captcha_key = str(uuid.uuid4())
        generator = CaptchaGenerator()
        _code, image_buf = generator.generate(captcha_key)

        return HttpResponse(image_buf, content_type="image/png", headers={"X-Captcha-Key": captcha_key})


class CaptchaVerifyView(APIView):
    """
    验证图形验证码
    """

    permission_classes = [AllowAny]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request):
        captcha_key = request.data.get("captcha_key")
        captcha_code = request.data.get("captcha_code", "").lower()

        if not captcha_key or not captcha_code:
            raise BaseAppError("验证码不能为空", business_code=ErrorCode.INVALID_PARAMS)

        stored_code = cache.get(f"captcha:{captcha_key}")

        if not stored_code:
            raise BaseAppError("验证码已过期，请刷新", business_code=ErrorCode.VALIDATION_FAILED)

        if not hmac.compare_digest(str(stored_code), str(captcha_code)):
            raise BaseAppError("验证码错误", business_code=ErrorCode.VALIDATION_FAILED)

        cache.delete(f"captcha:{captcha_key}")

        return success_response(message="验证成功")


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
        serializer = UserProfileSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        user = request.user
        for field, value in serializer.validated_data.items():
            setattr(user, field, value)
        user.save()
        out = UserInfoSerializer(user)
        return success_response(data=out.data, message="资料更新成功")


class UserAvatarView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request):
        user = request.user
        avatar_file = request.FILES.get("avatar")

        if not avatar_file:
            raise BaseAppError("请选择头像文件", business_code=ErrorCode.INVALID_PARAMS)

        allowed_types = ["image/png", "image/jpeg", "image/gif", "image/webp"]
        if avatar_file.content_type not in allowed_types:
            raise BaseAppError("仅支持 PNG、JPG、GIF、WebP 格式", business_code=ErrorCode.INVALID_PARAMS)

        if avatar_file.size > 2 * 1024 * 1024:
            raise BaseAppError("头像文件不能超过 2MB", business_code=ErrorCode.INVALID_PARAMS)

        if user.avatar:
            try:
                user.avatar.delete(save=False)
            except Exception:  # noqa: S110  # cleanup, 旧头像文件删除失败不影响新头像上传
                pass

        user.avatar = avatar_file
        user.save()
        serializer = UserInfoSerializer(user)
        return success_response(data=serializer.data, message="头像更新成功")


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [SensitiveOperationRateThrottle]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        user = request.user
        user.set_password(serializer.validated_data["new_password"])
        user.save()
        return success_response(message="密码修改成功")


class BindPhoneView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request):
        serializer = BindPhoneSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        user = request.user
        user.mobile = serializer.validated_data["mobile"]
        user.save()
        serializer_out = UserInfoSerializer(user)
        return success_response(data=serializer_out.data, message="手机号绑定成功")


class UserPreferencesView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request):
        user = request.user
        preferences = {
            "theme": user.theme,
            "language": user.language,
            "notifications_enabled": user.notifications_enabled,
            "auto_save_sessions": user.auto_save_sessions,
        }
        return success_response(data=preferences)

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def put(self, request):
        serializer = UserPreferencesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        for field, value in serializer.validated_data.items():
            setattr(user, field, value)
        user.save()
        return success_response(message="偏好设置更新成功")


class UserUsageStatsView(APIView):
    permission_classes = [IsAuthenticated]

    CACHE_KEY_TEMPLATE = "users:usage_stats:user{uid}"

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request):
        from django.apps import apps
        from django.db.models.functions import TruncDate

        from Django_xm.apps.analytics.services.analytics_service import AnalyticsService
        from Django_xm.apps.cache_manager.services.cache_service import CacheService, CacheTTL

        user = request.user
        cache_key = self.CACHE_KEY_TEMPLATE.format(uid=user.id)
        cached = CacheService.get(cache_key)
        if cached is not None:
            return success_response(data=cached)

        # 会话/消息/Token：复用 analytics 的实时聚合，避免维护 users 表冗余计数器
        overview = AnalyticsService._get_overview_stats(user)

        # 活跃天数：该用户消息按创建日期去重计数
        ChatMessage = apps.get_model("chat", "ChatMessage")
        active_days = (
            ChatMessage.objects.filter(session__user=user)
            .annotate(day=TruncDate("created_at"))
            .values("day")
            .distinct()
            .count()
        )

        stats = {
            "total_messages": overview["total_messages"],
            "total_sessions": overview["total_sessions"],
            "total_tokens": overview["total_tokens"],
            "active_days": active_days,
        }
        CacheService.set(cache_key, stats, CacheTTL.QUERY_SHORT)
        return success_response(data=stats)


class UserAccountDeleteView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [SensitiveOperationRateThrottle]

    @extend_schema(responses={200: EmptySerializer})
    def delete(self, request):
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
