from django.db import models
from django.conf import settings
from Django_xm.apps.core.base_models import BaseModel


class PermissionMode(models.TextChoices):
    ALLOW = 'allow', '允许'
    DENY = 'deny', '拒绝'
    PROMPT = 'prompt', '提示确认'


class SessionPermissionMode(models.TextChoices):
    READ_ONLY = 'read-only', '只读'
    WORKSPACE_WRITE = 'workspace-write', '工作区写入'
    DANGER_FULL_ACCESS = 'danger-full-access', '完全访问'


class UserPermissionPolicy(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='permission_policies',
        verbose_name='用户'
    )
    session_id = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        verbose_name='会话ID',
        help_text='为空表示全局策略'
    )
    default_mode = models.CharField(
        max_length=20,
        choices=PermissionMode.choices,
        default=PermissionMode.ALLOW,
        verbose_name='默认权限模式'
    )
    session_mode = models.CharField(
        max_length=30,
        choices=SessionPermissionMode.choices,
        default=SessionPermissionMode.WORKSPACE_WRITE,
        verbose_name='会话权限模式'
    )
    tool_modes = models.JSONField(
        default=dict,
        blank=True,
        verbose_name='工具权限覆盖',
        help_text='格式: {"tool_name": "allow/deny/prompt"}'
    )

    class Meta:
        db_table = 'core_user_permission_policy'
        verbose_name = '用户权限策略'
        verbose_name_plural = '用户权限策略'
        unique_together = [('user', 'session_id')]
        indexes = [
            models.Index(fields=['user', 'session_id']),
        ]

    def __str__(self):
        scope = f"session:{self.session_id}" if self.session_id else "global"
        return f"PermissionPolicy({self.user_id}, {scope})"
