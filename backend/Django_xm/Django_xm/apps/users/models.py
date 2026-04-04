from django.db import models
from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """用户模型类"""
    mobile = models.CharField(max_length=11, unique=True, verbose_name='手机号', null=True, blank=True)
    avatar = models.ImageField(upload_to='avatar/', null=True, blank=True, verbose_name='头像')
    created_time = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_time = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        db_table = 'langchain_users'
        verbose_name = '用户'
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.username
