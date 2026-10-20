"""
自定义认证后端
支持用户名或手机号码登录，遵循 Django 认证后端协议
"""

import re

from django.contrib.auth.backends import ModelBackend

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
        if re.match(r"^1[3-9]\d{9}$", account):
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
    本后端要"扩展"Django 认证协议，继承基类即可被 authenticate() 框架函数识别。
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
"""
浏览器 POST /login
  └─ MyObtainTokenPairView.post
       └─ serializer.is_valid(raise_exception=True)   # simplejwt 的 TokenObtainPairSerializer
            └─ django.contrib.auth.authenticate(username=..., password=...)   # ← 框架函数
                 └─ 遍历 AUTHENTICATION_BACKENDS：
                      1) UsernameMobileAuthBackend.authenticate()   ← 你注册的，先试
                      2) ModelBackend.authenticate()               ← 兜底
                      第一个返回非 None 的 user 即胜出
"""
