from django.db import models
from django.contrib.auth.models import AbstractUser, UserManager
from django.core.validators import RegexValidator
from django.utils import timezone


class SoftDeleteUserManager(UserManager):
    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class AllUserManager(UserManager):
    pass


class User(AbstractUser):
    mobile = models.CharField(
        max_length=11,
        unique=True,
        verbose_name='手机号',
        null=True,
        blank=True,
        validators=[RegexValidator(regex=r'^1[3-9]\d{9}$', message='请输入有效的手机号')]
    )
    avatar = models.ImageField(upload_to='avatar/', null=True, blank=True, verbose_name='头像')

    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')
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

    def soft_delete(self, using=None):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(using=using)

    def restore(self, using=None):
        self.is_deleted = False
        self.deleted_at = None
        self.save(using=using)
