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
        """
        自定义返回的格式
        """
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
    """
    用户信息序列化器
    """
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'mobile', 'avatar', 'date_joined']
