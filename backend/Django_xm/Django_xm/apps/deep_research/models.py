from django.db import models

class ResearchTask(models.Model):
    task_id = models.CharField(max_length=100, unique=True, verbose_name='任务ID')
    query = models.TextField(verbose_name='研究主题')
    status = models.CharField(max_length=20, choices=(
        ('pending', '待执行'), ('running', '执行中'),
        ('completed', '已完成'), ('failed', '失败')
    ), default='pending', verbose_name='状态')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')
    final_report = models.TextField(blank=True, verbose_name='最终报告')
    enable_web_search = models.BooleanField(default=True, verbose_name='启用网络搜索')
    enable_doc_analysis = models.BooleanField(default=False, verbose_name='启用文档分析')

    class Meta:
        db_table = 'research_task'
        verbose_name = '研究任务'
        verbose_name_plural = '研究任务'
        ordering = ['-created_at']
