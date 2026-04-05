from django.db import models
from django.contrib.auth.models import AbstractUser, UserManager
from django.utils import timezone


class SoftDeleteUserManager(UserManager):
    """用户管理器，自动过滤已软删除的记录"""

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class AllUserManager(UserManager):
    """包含已软删除记录的用户管理器"""


class User(AbstractUser):
    """用户模型类 - 继承AbstractUser并添加软删除功能"""
    mobile = models.CharField(max_length=11, unique=True, verbose_name='手机号', null=True, blank=True)
    avatar = models.ImageField(upload_to='avatar/', null=True, blank=True, verbose_name='头像')
    is_deleted = models.BooleanField(default=False, db_index=True, verbose_name='是否已删除')
    deleted_at = models.DateTimeField(null=True, blank=True, verbose_name='删除时间')

    objects = SoftDeleteUserManager()
    all_objects = AllUserManager()

    class Meta:
        db_table = 'langchain_users'
        verbose_name = '用户'
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.username

    def soft_delete(self, using=None, keep_parents=False):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(using=using)

    def restore(self, using=None):
        self.is_deleted = False
        self.deleted_at = None
        self.save(using=using)
