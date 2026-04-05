from django.db import models
from Django_xm.apps.core.base_models import AuditModel
from django.conf import settings


class ResearchTask(AuditModel):
    task_id = models.CharField(max_length=100, unique=True, verbose_name='任务ID')
    query = models.TextField(verbose_name='研究主题')
    status = models.CharField(max_length=20, choices=(
        ('pending', '待执行'), ('running', '执行中'),
        ('completed', '已完成'), ('failed', '失败')
    ), default='pending', verbose_name='状态', db_index=True)
    final_report = models.TextField(blank=True, verbose_name='最终报告')
    enable_web_search = models.BooleanField(default=True, verbose_name='启用网络搜索')
    enable_doc_analysis = models.BooleanField(default=False, verbose_name='启用文档分析')
    research_depth = models.CharField(
        max_length=20,
        choices=(('basic', '基础'), ('standard', '标准'), ('comprehensive', '综合')),
        default='standard',
        verbose_name='研究深度'
    )
    error_message = models.TextField(blank=True, null=True, verbose_name='错误信息')
    celery_task_id = models.CharField(max_length=100, blank=True, null=True, verbose_name='Celery任务ID')

    class Meta:
        db_table = 'research_task'
        verbose_name = '研究任务'
        verbose_name_plural = '研究任务'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['task_id']),
            models.Index(fields=['created_by', 'status']),
        ]

    def __str__(self):
        return f"ResearchTask({self.task_id}, {self.status})"

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('deep_research:status', kwargs={'task_id': self.task_id})
