import re
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView

from .models import User


def get_account_by_mobile(account):
    """
    根据手机号或用户名获取用户账户信息。
    首先尝试根据输入的账户信息判断其是否为手机号（通过正则表达式验证），
    如果是手机号，则通过mobile字段查询User模型以获取用户信息；
    如果不是手机号，则通过username字段查询User模型以获取用户信息。
    如果查询不到对应的用户信息，则返回None。
    """
    try:
        if re.match(r'^1[3-9]\d{9}$', account):
            user = User.objects.get(mobile=account)
        else:
            user = User.objects.get(username=account)
    except User.DoesNotExist:
        return None
    else:
        return user


class UsernameMobileAuthBackend(ModelBackend):
    """
    自定义用户认证后端，支持用户名或手机号码登录
    继承自Django的ModelBackend，重写了authenticate方法
    """
    def authenticate(self, request, username=None, password=None, **kwargs):
        """
        用户认证方法
        :param request: HttpRequest对象
        :param username: 用户输入的用户名或手机号码
        :param password: 用户输入的密码
        :param kwargs: 其他额外的关键字参数
        :return: 返回通过认证的用户对象，如果认证失败则返回None
        """
        user = get_account_by_mobile(username)
        if user is not None and user.check_password(password) and user.is_active:
            return user
        return None


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
        """
        自定义返回的格式
        """
        old_data = super().validate(attrs)
        refresh = self.get_token(self.user)
        data = {
            'id': self.user.id,
            'code': 200,
            'message': '登录成功',
            'username': self.user.username,
            'email': self.user.email if self.user.email else '',
            'refresh': str(refresh),
            'access': str(refresh.access_token)
        }
        return data


class UserRegisterSerializer(serializers.ModelSerializer):
    """
    用户注册序列化器
    """
    password_confirm = serializers.CharField(write_only=True, label='确认密码')

    class Meta:
        model = User
        fields = ['username', 'password', 'password_confirm', 'email', 'mobile']
        extra_kwargs = {
            'password': {'write_only': True, 'min_length': 6},
            'email': {'required': False},
            'mobile': {'required': False}
        }

    def validate(self, attrs):
        if attrs['password'] != attrs['password_confirm']:
            raise serializers.ValidationError({'password': '两次输入的密码不一致'})
        return attrs

    def create(self, validated_data):
        validated_data.pop('password_confirm')
        user = User.objects.create_user(**validated_data)
        return user


class UserInfoSerializer(serializers.ModelSerializer):
    """
    用户信息序列化器
    """
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'mobile', 'avatar', 'date_joined']
