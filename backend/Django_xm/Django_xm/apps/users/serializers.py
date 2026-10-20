"""用户模块序列化器。"""

from __future__ import annotations

import re

from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import User

# 密码复杂度校验正则：至少 8 位，包含大写字母+小写字母+数字+特殊字符
PASSWORD_COMPLEXITY_PATTERN = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*()_+\-=\[\]{}|;:,.<>?]).{8,}$")

# 特殊字符集（用于错误提示）——仅为密码复杂度校验规则中的特殊字符集合常量，非真实口令
PASSWORD_SPECIAL_CHARS = "!@#$%^&*()_+-=[]{}|;:,.<>?"  # noqa: S105


class MyTokenObtainPairSerializer(TokenObtainPairSerializer):
    """自定义 JWT 序列化器，返回额外的用户信息。"""

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["username"] = user.username
        token["email"] = user.email if user.email else ""
        return token

    def validate(self, attrs):
        super().validate(attrs)
        refresh = self.get_token(self.user)
        data = {
            "id": self.user.id,
            "username": self.user.username,
            "email": self.user.email if self.user.email else "",
            "is_staff": self.user.is_staff,
            "refresh": str(refresh),
            "access": str(refresh.access_token),
        }
        return data


class UserRegisterSerializer(serializers.ModelSerializer):
    """用户注册序列化器。

    校验规则：
        - password 与 password_confirm 必须一致（基于 validated_data 比较）
        - password 至少 8 位，必须同时包含大写字母、小写字母、数字、特殊字符
    """

    password_confirm = serializers.CharField(write_only=True, label="确认密码")

    class Meta:
        model = User
        fields = ["username", "password", "password_confirm", "email", "mobile"]
        extra_kwargs = {
            "password": {"write_only": True, "min_length": 8},
            "email": {"required": False},
            "mobile": {"required": False},
        }

    def validate_password(self, value: str) -> str:
        """校验密码复杂度。
        validate_<字段名>(self, value) 形式的方法，DRF 在每次校验该字段时会自动触发
        触发点在 DRF 的 Serializer.to_internal_value() 里：它先跑字段自身的约束
        （min_length 等），再调对应的 validate_<field>。
        POST /register
          └─ UserRegisterView.create(request)        # views.py:134
               ├─ self.verify_captcha(request.data)
               ├─ serializer = UserRegisterSerializer(data=request.data)
               └─ serializer.is_valid(raise_exception=True)   # ← DRF 内部自动调用 validate_password
                    └─ DRF 校验 password 字段：
                         ① 先跑 extra_kwargs 里的 min_length=8
                         ② 再调 validate_password(value)   ← 你这段复杂度校验
                         ③ 全字段过完跑 validate() 比对 password_confirm


        Args:
            value: 待校验的密码。

        Returns:
            校验通过后的密码。

        Raises:
            serializers.ValidationError: 密码复杂度不满足时抛出。
        """
        if not PASSWORD_COMPLEXITY_PATTERN.match(value):
            raise serializers.ValidationError(
                f"密码至少 8 位，必须同时包含大写字母、小写字母、数字和特殊字符（特殊字符集：{PASSWORD_SPECIAL_CHARS}）"
            )
        return value

    def validate(self, attrs):
        """跨字段校验：确认密码必须与密码一致。

        Args:
            attrs: 已通过字段级校验的 validated_data。

        Returns:
            校验通过后的 attrs。

        Raises:
            serializers.ValidationError: 两次密码不一致时抛出。
        """
        password = attrs.get("password")
        password_confirm = attrs.get("password_confirm")
        if password and password_confirm and password != password_confirm:
            raise serializers.ValidationError({"password_confirm": "两次输入的密码不一致"})
        return attrs

    def create(self, validated_data):
        validated_data.pop("password_confirm")
        user = User.objects.create_user(**validated_data)
        return user


class UserInfoSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "email", "mobile", "avatar", "date_joined", "is_staff"]


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(required=True, min_length=1)
    new_password = serializers.CharField(required=True, min_length=8)

    def validate_old_password(self, value):
        user = self.context.get("request").user
        if not user.check_password(value):
            raise serializers.ValidationError("当前密码错误")
        return value

    def validate_new_password(self, value):
        """新密码复杂度与注册规则（PASSWORD_COMPLEXITY_PATTERN）保持一致。"""
        if not PASSWORD_COMPLEXITY_PATTERN.match(value):
            raise serializers.ValidationError(
                f"密码至少 8 位，必须同时包含大写字母、小写字母、数字和特殊字符（特殊字符集：{PASSWORD_SPECIAL_CHARS}）"
            )
        return value


class BindPhoneSerializer(serializers.Serializer):
    mobile = serializers.CharField(required=True, max_length=11)

    def validate_mobile(self, value):
        if not re.match(r"^1[3-9]\d{9}$", value):
            raise serializers.ValidationError("请输入有效的手机号")
        user = self.context.get("request").user
        if User.objects.filter(mobile=value).exclude(pk=user.pk).exists():
            raise serializers.ValidationError("该手机号已被其他用户绑定")
        return value


class UserProfileSerializer(serializers.Serializer):
    username = serializers.CharField(required=False, min_length=1, max_length=150)
    email = serializers.EmailField(required=False, allow_blank=True)

    def validate_username(self, value):
        user = self.context.get("request").user
        if User.objects.filter(username=value).exclude(pk=user.pk).exists():
            raise serializers.ValidationError("用户名已存在")
        return value


class UserPreferencesSerializer(serializers.Serializer):
    theme = serializers.CharField(required=False, max_length=20)
    language = serializers.CharField(required=False, max_length=10)
    notifications_enabled = serializers.BooleanField(required=False)
    auto_save_sessions = serializers.BooleanField(required=False)
