from django.db import models
from Django_xm.apps.core.base_models import AuditModel


class WorkflowExecutionStatus(models.TextChoices):
    PENDING = 'pending', '待执行'
    RUNNING = 'running', '执行中'
    COMPLETED = 'completed', '已完成'
    FAILED = 'failed', '失败'


class WorkflowSessionStatus(models.TextChoices):
    RUNNING = 'running', '执行中'
    WAITING_FOR_ANSWERS = 'waiting_for_answers', '等待答案'
    RETRY = 'retry', '重试'
    COMPLETED = 'completed', '已完成'
    FAILED = 'failed', '失败'


class WorkflowExecution(AuditModel):
    thread_id = models.CharField(
        max_length=100,
        unique=True,
        verbose_name='线程 ID'
    )
    workflow_type = models.CharField(
        max_length=50,
        db_index=True,
        verbose_name='工作流类型'
    )
    query = models.TextField(verbose_name='查询内容')
    status = models.CharField(
        max_length=20,
        choices=WorkflowExecutionStatus.choices,
        default=WorkflowExecutionStatus.PENDING,
        db_index=True,
        verbose_name='状态'
    )
    result = models.JSONField(null=True, blank=True, verbose_name='执行结果')

    class Meta:
        db_table = 'workflow_execution'
        verbose_name = '工作流执行'
        verbose_name_plural = '工作流执行'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['workflow_type', 'status', '-created_at']),
        ]

    def __str__(self):
        return f"WorkflowExecution({self.thread_id}, {self.status})"

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('workflows:status', kwargs={'thread_id': self.thread_id})


class WorkflowSession(AuditModel):
    thread_id = models.CharField(
        max_length=100,
        unique=True,
        verbose_name='线程 ID'
    )
    user_question = models.TextField(verbose_name='用户问题')
    status = models.CharField(
        max_length=20,
        choices=WorkflowSessionStatus.choices,
        default=WorkflowSessionStatus.RUNNING,
        db_index=True,
        verbose_name='状态'
    )
    current_step = models.CharField(
        max_length=50,
        blank=True,
        default='',
        verbose_name='当前步骤'
    )
    learning_plan = models.JSONField(null=True, blank=True, verbose_name='学习计划')
    quiz = models.JSONField(null=True, blank=True, verbose_name='练习题')
    user_answers = models.JSONField(null=True, blank=True, verbose_name='用户答案')
    score = models.IntegerField(null=True, blank=True, verbose_name='得分')
    score_details = models.JSONField(null=True, blank=True, verbose_name='评分详情')
    feedback = models.TextField(blank=True, verbose_name='反馈信息')
    should_retry = models.BooleanField(default=False, verbose_name='是否重试')
    error_message = models.TextField(blank=True, verbose_name='错误信息')

    class Meta:
        db_table = 'workflow_session'
        verbose_name = '工作流会话'
        verbose_name_plural = '工作流会话'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at']),
        ]

    def __str__(self):
        return f"WorkflowSession({self.thread_id}, {self.status})"

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('workflows:status', kwargs={'thread_id': self.thread_id})
