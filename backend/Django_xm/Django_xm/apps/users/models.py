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

    theme = models.CharField(max_length=20, default='light', verbose_name='主题')
    language = models.CharField(max_length=10, default='zh-CN', verbose_name='语言')
    notifications_enabled = models.BooleanField(default=True, verbose_name='通知开关')
    auto_save_sessions = models.BooleanField(default=True, verbose_name='自动保存会话')

    total_messages = models.PositiveIntegerField(default=0, verbose_name='总消息数')
    total_sessions = models.PositiveIntegerField(default=0, verbose_name='总会话数')
    total_tokens = models.PositiveBigIntegerField(default=0, verbose_name='总Token数')
    total_cost = models.DecimalField(max_digits=12, decimal_places=6, default=0, verbose_name='总费用')
    active_days = models.PositiveIntegerField(default=0, verbose_name='活跃天数')

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
