from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import User


class MyTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    自定义JWT序列化器，返回额外的用户信息
    """
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token['username'] = user.username
        token['email'] = user.email if user.email else ''
        return token

    def validate(self, attrs):
        old_data = super().validate(attrs)
        refresh = self.get_token(self.user)
        data = {
            'id': self.user.id,
            'username': self.user.username,
            'email': self.user.email if self.user.email else '',
            'refresh': str(refresh),
            'access': str(refresh.access_token)
        }
        return data


class UserRegisterSerializer(serializers.ModelSerializer):
    password_confirm = serializers.CharField(write_only=True, label='确认密码')

    class Meta:
        model = User
        fields = ['username', 'password', 'password_confirm', 'email', 'mobile']
        extra_kwargs = {
            'password': {'write_only': True, 'min_length': 6},
            'email': {'required': False},
            'mobile': {'required': False}
        }

    def validate_password_confirm(self, value):
        password = self.initial_data.get('password')
        if password and value and password != value:
            raise serializers.ValidationError('两次输入的密码不一致')
        return value

    def create(self, validated_data):
        validated_data.pop('password_confirm')
        user = User.objects.create_user(**validated_data)
        return user


class UserInfoSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'mobile', 'avatar', 'date_joined']


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(required=True, min_length=1)
    new_password = serializers.CharField(required=True, min_length=8)

    def validate_old_password(self, value):
        user = self.context.get('request').user
        if not user.check_password(value):
            raise serializers.ValidationError('当前密码错误')
        return value


class BindPhoneSerializer(serializers.Serializer):
    mobile = serializers.CharField(required=True, max_length=11)

    def validate_mobile(self, value):
        import re
        if not re.match(r'^1[3-9]\d{9}$', value):
            raise serializers.ValidationError('请输入有效的手机号')
        user = self.context.get('request').user
        if User.objects.filter(mobile=value).exclude(pk=user.pk).exists():
            raise serializers.ValidationError('该手机号已被其他用户绑定')
        return value


class UserProfileSerializer(serializers.Serializer):
    username = serializers.CharField(required=False, min_length=1, max_length=150)
    email = serializers.EmailField(required=False, allow_blank=True)

    def validate_username(self, value):
        user = self.context.get('request').user
        if User.objects.filter(username=value).exclude(pk=user.pk).exists():
            raise serializers.ValidationError('用户名已存在')
        return value


class UserPreferencesSerializer(serializers.Serializer):
    theme = serializers.CharField(required=False, max_length=20)
    language = serializers.CharField(required=False, max_length=10)
    notifications_enabled = serializers.BooleanField(required=False)
    auto_save_sessions = serializers.BooleanField(required=False)
