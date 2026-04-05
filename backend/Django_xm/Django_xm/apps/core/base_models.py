"""
抽象基类模型
提供软删除、审计字段等通用功能
所有业务模型应继承此类
"""
from django.db import models
from django.conf import settings


class SoftDeleteManager(models.Manager):
    """默认管理器，自动过滤已软删除的记录"""

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class AllObjectsManager(models.Manager):
    """包含已软删除记录的管理器"""

    def get_queryset(self):
        return super().get_queryset()


class BaseModel(models.Model):
    """基础抽象模型 - 包含时间戳和软删除"""

    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name='创建时间'
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name='更新时间'
    )
    is_deleted = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name='是否已删除'
    )
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='删除时间'
    )

    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()

    class Meta:
        abstract = True

    def soft_delete(self, using=None, keep_parents=False):
        self.is_deleted = True
        from django.utils import timezone
        self.deleted_at = timezone.now()
        self.save(using=using)

    def restore(self, using=None):
        self.is_deleted = False
        self.deleted_at = None
        self.save(using=using)


class AuditModel(BaseModel):
    """审计模型 - 在BaseModel基础上增加创建/更新人追踪"""

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_%(class)ss',
        verbose_name='创建人'
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='updated_%(class)ss',
        verbose_name='更新人'
    )

    class Meta(BaseModel.Meta):
        abstract = True

    def save(self, *args, **kwargs):
        from django.contrib.auth import get_user_model
        request = kwargs.pop('request', None)
        if request and request.user and request.user.is_authenticated:
            if not self.pk:
                self.created_by = request.user
            self.updated_by = request.user
        super().save(*args, **kwargs)
