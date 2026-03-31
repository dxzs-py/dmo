from django.db import models


class WorkflowExecution(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_RUNNING = 'running'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED = 'failed'
    
    STATUS_CHOICES = [
        (STATUS_PENDING, '待执行'),
        (STATUS_RUNNING, '执行中'),
        (STATUS_COMPLETED, '已完成'),
        (STATUS_FAILED, '失败'),
    ]
    
    thread_id = models.CharField(
        max_length=100, 
        unique=True,
        db_index=True,
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
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
        verbose_name='状态'
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name='创建时间'
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name='更新时间'
    )
    result = models.JSONField(null=True, blank=True, verbose_name='执行结果')

    class Meta:
        db_table = 'workflow_execution'
        verbose_name = '工作流执行'
        verbose_name_plural = '工作流执行'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['workflow_type', 'status', '-created_at']),
            models.Index(fields=['thread_id', 'status']),
        ]

    def __str__(self):
        return f"WorkflowExecution({self.thread_id}, {self.status})"

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('workflows:status', kwargs={'thread_id': self.thread_id})


class WorkflowSession(models.Model):
    """
    学习工作流会话模型
    用于跟踪和管理学习工作流的执行状态
    """
    STATUS_RUNNING = 'running'
    STATUS_WAITING_FOR_ANSWERS = 'waiting_for_answers'
    STATUS_RETRY = 'retry'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED = 'failed'
    
    STATUS_CHOICES = [
        (STATUS_RUNNING, '执行中'),
        (STATUS_WAITING_FOR_ANSWERS, '等待答案'),
        (STATUS_RETRY, '重试'),
        (STATUS_COMPLETED, '已完成'),
        (STATUS_FAILED, '失败'),
    ]
    
    thread_id = models.CharField(
        max_length=100, 
        unique=True,
        db_index=True,
        verbose_name='线程 ID'
    )
    user_question = models.TextField(verbose_name='用户问题')
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_RUNNING,
        db_index=True,
        verbose_name='状态'
    )
    current_step = models.CharField(
        max_length=50, 
        blank=True,
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
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name='创建时间'
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name='更新时间'
    )

    class Meta:
        db_table = 'workflow_session'
        verbose_name = '工作流会话'
        verbose_name_plural = '工作流会话'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['thread_id', 'status']),
        ]

    def __str__(self):
        return f"WorkflowSession({self.thread_id}, {self.status})"

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('workflows:status', kwargs={'thread_id': self.thread_id})

