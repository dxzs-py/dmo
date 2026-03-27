from django.db import models

class WorkflowExecution(models.Model):
    thread_id = models.CharField(max_length=100, unique=True, verbose_name='线程ID')
    workflow_type = models.CharField(max_length=50, verbose_name='工作流类型')
    query = models.TextField(verbose_name='查询内容')
    status = models.CharField(max_length=20, choices=(
        ('pending', '待执行'), ('running', '执行中'),
        ('completed', '已完成'), ('failed', '失败')
    ), default='pending', verbose_name='状态')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')
    result = models.JSONField(null=True, blank=True, verbose_name='执行结果')

    class Meta:
        db_table = 'workflow_execution'
        verbose_name = '工作流执行'
        verbose_name_plural = '工作流执行'
        ordering = ['-created_at']
