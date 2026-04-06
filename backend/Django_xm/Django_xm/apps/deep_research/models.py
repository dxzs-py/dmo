from django.db import models
from django.core.exceptions import ValidationError
from Django_xm.apps.core.base_models import AuditModel
from django.conf import settings


class ResearchTaskStatus(models.TextChoices):
    PENDING = 'pending', '待执行'
    RUNNING = 'running', '执行中'
    COMPLETED = 'completed', '已完成'
    FAILED = 'failed', '失败'


class ResearchDepth(models.TextChoices):
    BASIC = 'basic', '基础'
    STANDARD = 'standard', '标准'
    COMPREHENSIVE = 'comprehensive', '综合'


class ResearchTask(AuditModel):
    task_id = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        verbose_name='任务ID'
    )
    query = models.TextField(
        verbose_name='研究主题',
        db_index=True
    )
    status = models.CharField(
        max_length=20,
        choices=ResearchTaskStatus.choices,
        default=ResearchTaskStatus.PENDING,
        verbose_name='状态',
        db_index=True
    )
    final_report = models.TextField(
        blank=True,
        verbose_name='最终报告'
    )
    enable_web_search = models.BooleanField(
        default=True,
        verbose_name='启用网络搜索'
    )
    enable_doc_analysis = models.BooleanField(
        default=False,
        verbose_name='启用文档分析'
    )
    research_depth = models.CharField(
        max_length=20,
        choices=ResearchDepth.choices,
        default=ResearchDepth.STANDARD,
        verbose_name='研究深度'
    )
    error_message = models.TextField(
        blank=True,
        null=True,
        verbose_name='错误信息'
    )
    celery_task_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name='Celery任务ID'
    )

    class Meta:
        db_table = 'research_task'
        verbose_name = '研究任务'
        verbose_name_plural = '研究任务'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['task_id']),
            models.Index(fields=['created_by', 'status']),
            models.Index(fields=['research_depth']),
            models.Index(fields=['-created_at']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['task_id'],
                name='unique_research_task_id'
            )
        ]

    def __str__(self):
        return f"ResearchTask({self.task_id}, {self.status})"

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('deep_research:status', kwargs={'task_id': self.task_id})

    def clean(self):
        super().clean()
        if self.query and len(self.query.strip()) == 0:
            raise ValidationError({'query': '研究主题不能为空'})
        if self.query and len(self.query) > 10000:
            raise ValidationError({'query': '研究主题不能超过10000个字符'})
        if self.error_message and len(self.error_message) > 5000:
            raise ValidationError({
                'error_message': '错误信息不能超过5000个字符'
            })

    def save(self, *args, **kwargs):
        if not self.task_id:
            import uuid
            self.task_id = str(uuid.uuid4())
        if self.query:
            self.query = self.query[:10000]
        super().save(*args, **kwargs)
