from django.db import models
from django.contrib.auth.models import AbstractUser, UserManager
from Django_xm.apps.core.base_models import BaseModel


class SoftDeleteUserManager(UserManager):
    """用户管理器，自动过滤已软删除的记录"""

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class AllUserManager(UserManager):
    """包含已软删除记录的用户管理器"""


class User(AbstractUser, BaseModel):
    """用户模型类 - 继承AbstractUser和BaseModel，统一软删除功能"""
    mobile = models.CharField(max_length=11, unique=True, verbose_name='手机号', null=True, blank=True)
    avatar = models.ImageField(upload_to='avatar/', null=True, blank=True, verbose_name='头像')

    objects = SoftDeleteUserManager()
    all_objects = AllUserManager()

    class Meta:
        db_table = 'langchain_users'
        verbose_name = '用户'
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.username
