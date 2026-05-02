from django.db import models
from django.conf import settings
from Django_xm.apps.core.base_models import AuditModel


class CeleryTaskRecord(AuditModel):
    """
    Celery 任务持久化记录

    将 Celery 任务状态同步到数据库，实现：
    - 任务历史查询
    - 任务统计分析
    - 故障排查
    - 与用户关联
    """

    class TaskStatus(models.TextChoices):
        PENDING = 'pending', '等待中'
        STARTED = 'started', '已开始'
        PROGRESS = 'progress', '执行中'
        SUCCESS = 'success', '成功'
        FAILURE = 'failure', '失败'
        REVOKED = 'revoked', '已撤销'
        RETRY = 'retry', '重试中'

    class TaskType(models.TextChoices):
        DEEP_RESEARCH = 'deep_research', '深度研究'
        RAG_INDEX = 'rag_index', 'RAG索引创建'
        RAG_ADD_DOCS = 'rag_add_docs', 'RAG文档添加'
        RAG_DELETE_INDEX = 'rag_delete_index', 'RAG索引删除'
        WORKFLOW = 'workflow', '工作流执行'
        EMBEDDING = 'embedding', '向量化处理'
        OTHER = 'other', '其他'

    celery_task_id = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        verbose_name='Celery 任务ID',
    )
    task_name = models.CharField(
        max_length=255,
        verbose_name='任务名称',
    )
    task_type = models.CharField(
        max_length=30,
        choices=TaskType.choices,
        default=TaskType.OTHER,
        db_index=True,
        verbose_name='任务类型',
    )
    status = models.CharField(
        max_length=20,
        choices=TaskStatus.choices,
        default=TaskStatus.PENDING,
        db_index=True,
        verbose_name='任务状态',
    )
    task_args = models.JSONField(
        default=dict,
        blank=True,
        verbose_name='任务参数',
    )
    task_kwargs = models.JSONField(
        default=dict,
        blank=True,
        verbose_name='任务关键字参数',
    )
    result = models.JSONField(
        null=True,
        blank=True,
        verbose_name='任务结果',
    )
    error_message = models.TextField(
        blank=True,
        default='',
        verbose_name='错误信息',
    )
    progress = models.IntegerField(
        default=0,
        verbose_name='进度百分比',
    )
    progress_message = models.CharField(
        max_length=500,
        blank=True,
        default='',
        verbose_name='进度消息',
    )
    started_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='开始时间',
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='完成时间',
    )
    runtime_seconds = models.FloatField(
        null=True,
        blank=True,
        verbose_name='运行时长(秒)',
    )
    retry_count = models.IntegerField(
        default=0,
        verbose_name='重试次数',
    )
    worker_name = models.CharField(
        max_length=255,
        blank=True,
        default='',
        verbose_name='Worker 名称',
    )

    class Meta:
        db_table = 'core_celery_task_record'
        verbose_name = 'Celery 任务记录'
        verbose_name_plural = 'Celery 任务记录'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'task_type']),
            models.Index(fields=['created_by', 'status']),
            models.Index(fields=['-created_at']),
        ]

    def __str__(self):
        return f"CeleryTask({self.task_name}, {self.status})"

    def mark_started(self, worker_name: str = ''):
        from django.utils import timezone
        self.status = self.TaskStatus.STARTED
        self.started_at = timezone.now()
        if worker_name:
            self.worker_name = worker_name
        self.save(update_fields=['status', 'started_at', 'worker_name', 'updated_at'])

    def mark_progress(self, progress: int, message: str = ''):
        self.status = self.TaskStatus.PROGRESS
        self.progress = min(max(progress, 0), 100)
        if message:
            self.progress_message = message
        self.save(update_fields=['status', 'progress', 'progress_message', 'updated_at'])

    def mark_success(self, result=None):
        from django.utils import timezone
        self.status = self.TaskStatus.SUCCESS
        self.completed_at = timezone.now()
        self.progress = 100
        if result is not None:
            self.result = result
        if self.started_at:
            self.runtime_seconds = (self.completed_at - self.started_at).total_seconds()
        self.save(update_fields=[
            'status', 'completed_at', 'progress', 'result',
            'runtime_seconds', 'updated_at',
        ])

    def mark_failure(self, error_message: str = ''):
        from django.utils import timezone
        self.status = self.TaskStatus.FAILURE
        self.completed_at = timezone.now()
        self.error_message = error_message
        if self.started_at:
            self.runtime_seconds = (self.completed_at - self.started_at).total_seconds()
        self.save(update_fields=[
            'status', 'completed_at', 'error_message',
            'runtime_seconds', 'updated_at',
        ])

    def mark_revoked(self):
        from django.utils import timezone
        self.status = self.TaskStatus.REVOKED
        self.completed_at = timezone.now()
        if self.started_at:
            self.runtime_seconds = (self.completed_at - self.started_at).total_seconds()
        self.save(update_fields=[
            'status', 'completed_at', 'runtime_seconds', 'updated_at',
        ])

    def mark_retry(self, retry_count: int = 0):
        self.status = self.TaskStatus.RETRY
        self.retry_count = retry_count
        self.save(update_fields=['status', 'retry_count', 'updated_at'])
