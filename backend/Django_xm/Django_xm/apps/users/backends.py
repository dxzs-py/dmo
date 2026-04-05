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
