import uuid
import os
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from Django_xm.apps.core.base_models import AuditModel


def chat_attachment_upload_path(instance, filename):
    return f'chat_attachments/{instance.session.session_id}/{uuid.uuid4().hex[:8]}_{filename}'


class MessageRole(models.TextChoices):
    USER = 'user', '用户'
    ASSISTANT = 'assistant', '助手'
    SYSTEM = 'system', '系统'


class ChatMode(models.TextChoices):
    BASIC_AGENT = 'basic-agent', '基础代理'
    DEEP_RESEARCH = 'deep-research', '深度研究'
    RAG = 'rag', 'RAG检索'
    WORKFLOW = 'workflow', '工作流'


class ChatSession(AuditModel):
    session_id = models.CharField(
        max_length=100,
        unique=True,
        verbose_name='会话ID'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='chat_sessions',
        db_index=True,
        verbose_name='用户'
    )
    title = models.CharField(
        max_length=200,
        default='新对话',
        verbose_name='会话标题'
    )
    mode = models.CharField(
        max_length=50,
        choices=ChatMode.choices,
        default=ChatMode.BASIC_AGENT,
        verbose_name='对话模式'
    )
    selected_knowledge_base = models.CharField(
        max_length=200,
        blank=True,
        null=True,
        verbose_name='选中的知识库'
    )

    class Meta:
        db_table = 'chat_session'
        verbose_name = '聊天会话'
        verbose_name_plural = '聊天会话'
        indexes = [
            models.Index(fields=['user', 'created_at']),
            models.Index(fields=['mode']),
        ]

    def __str__(self):
        return f"ChatSession {self.session_id} - {self.title}"

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('chat:chat')

    def clean(self):
        super().clean()
        if self.title and len(self.title.strip()) == 0:
            raise ValidationError({'title': '会话标题不能为空'})
        if self.mode and len(self.mode) > 50:
            raise ValidationError({'mode': '模式标识不能超过50个字符'})

    def save(self, *args, **kwargs):
        if not self.session_id:
            self.session_id = str(uuid.uuid4())
        super().save(*args, **kwargs)


class ChatMessage(AuditModel):
    CONTENT_MAX_LENGTH = 50000

    session = models.ForeignKey(
        ChatSession,
        on_delete=models.CASCADE,
        related_name='messages',
        db_index=True,
        verbose_name='会话'
    )
    role = models.CharField(
        max_length=20,
        choices=MessageRole.choices,
        default=MessageRole.USER,
        db_index=True,
        verbose_name='角色'
    )
    content = models.TextField(
        blank=True,
        max_length=50000,
        verbose_name='内容'
    )
    sources = models.JSONField(
        default=list,
        blank=True,
        null=True,
        verbose_name='来源'
    )
    plan = models.JSONField(
        default=dict,
        blank=True,
        null=True,
        verbose_name='计划'
    )
    chain_of_thought = models.JSONField(
        default=dict,
        blank=True,
        null=True,
        verbose_name='思维链'
    )
    tool_calls = models.JSONField(
        default=list,
        blank=True,
        null=True,
        verbose_name='工具调用'
    )
    reasoning = models.JSONField(
        default=dict,
        blank=True,
        null=True,
        verbose_name='推理'
    )
    suggestions = models.JSONField(
        default=list,
        blank=True,
        null=True,
        verbose_name='建议问题'
    )
    context = models.JSONField(
        default=dict,
        blank=True,
        null=True,
        verbose_name='上下文信息'
    )
    versions = models.JSONField(
        default=list,
        blank=True,
        null=True,
        verbose_name='消息版本'
    )
    current_version = models.IntegerField(
        default=0,
        verbose_name='当前版本索引'
    )
    model = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name='使用的模型'
    )
    token_count = models.IntegerField(
        default=0,
        verbose_name='Token 数量'
    )
    cost = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        default=0,
        verbose_name='成本(美元)'
    )
    response_time = models.FloatField(
        default=0,
        verbose_name='响应时间(秒)'
    )

    class Meta:
        db_table = 'chat_message'
        verbose_name = '聊天消息'
        verbose_name_plural = '聊天消息'
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['session', 'created_at']),
            models.Index(fields=['role']),
            models.Index(fields=['session', 'role', 'created_at']),
        ]

    def __str__(self):
        content_preview = self.content[:50] if self.content else ''
        return f"{self.role}: {content_preview}..."

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('chat:chat')

    def clean(self):
        super().clean()
        if self.content and len(self.content) > self.CONTENT_MAX_LENGTH:
            raise ValidationError({
                'content': f'内容长度不能超过{self.CONTENT_MAX_LENGTH}个字符'
            })
        if self.role not in [choice[0] for choice in MessageRole.choices]:
            raise ValidationError({'role': '无效的角色类型'})
        if self.current_version < 0:
            raise ValidationError({'current_version': '版本索引不能为负数'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class AttachmentStatus(models.TextChoices):
    ACTIVE = 'active', '活跃'
    INDEXED = 'indexed', '已入库'
    PENDING_DELETE = 'pending_delete', '待删除'
    DELETED = 'deleted', '已删除'


class ChatAttachment(AuditModel):
    session = models.ForeignKey(
        ChatSession,
        on_delete=models.CASCADE,
        related_name='attachments',
        verbose_name='会话'
    )
    message = models.ForeignKey(
        ChatMessage,
        on_delete=models.CASCADE,
        related_name='attachments',
        null=True,
        blank=True,
        verbose_name='消息'
    )
    file = models.FileField(
        upload_to=chat_attachment_upload_path,
        verbose_name='文件'
    )
    original_name = models.CharField(
        max_length=255,
        verbose_name='原始文件名'
    )
    file_size = models.PositiveIntegerField(
        verbose_name='文件大小(字节)'
    )
    file_type = models.CharField(
        max_length=100,
        blank=True,
        verbose_name='文件类型'
    )
    mime_type = models.CharField(
        max_length=255,
        blank=True,
        verbose_name='MIME类型'
    )
    status = models.CharField(
        max_length=20,
        choices=AttachmentStatus.choices,
        default=AttachmentStatus.ACTIVE,
        db_index=True,
        verbose_name='附件状态'
    )
    last_accessed_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='最后访问时间'
    )
    reference_count = models.PositiveIntegerField(
        default=1,
        verbose_name='引用计数'
    )
    indexed_path = models.CharField(
        max_length=500,
        blank=True,
        default='',
        verbose_name='向量索引路径'
    )
    indexed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='入库时间'
    )
    retention_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='自定义保留天数'
    )
    file_hash = models.CharField(
        max_length=64,
        blank=True,
        default='',
        verbose_name='文件SHA256哈希'
    )

    class Meta:
        db_table = 'chat_attachment'
        verbose_name = '聊天附件'
        verbose_name_plural = '聊天附件'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['last_accessed_at']),
        ]

    def __str__(self):
        return f"Attachment: {self.original_name}"

    def touch_access(self):
        from django.utils import timezone
        self.last_accessed_at = timezone.now()
        self.save(update_fields=['last_accessed_at'])

    def increment_reference(self):
        ChatAttachment.objects.filter(pk=self.pk).update(
            reference_count=models.F('reference_count') + 1
        )
        self.refresh_from_db()

    def decrement_reference(self):
        if self.reference_count > 0:
            ChatAttachment.objects.filter(pk=self.pk).update(
                reference_count=models.F('reference_count') - 1
            )
            self.refresh_from_db()

    def is_expired(self):
        from django.utils import timezone
        from django.conf import settings as django_settings
        retention = self.retention_days or getattr(
            django_settings, 'ATTACHMENT_DEFAULT_RETENTION_DAYS', 30
        )
        expiry = self.created_at + timezone.timedelta(days=retention)
        return timezone.now() > expiry

    def can_safely_delete(self):
        return self.reference_count <= 0 or self.is_expired()

    def clean(self):
        super().clean()
        max_size = getattr(settings, 'DATA_UPLOAD_MAX_MEMORY_SIZE', 10 * 1024 * 1024)
        if self.file_size > max_size:
            raise ValidationError({'file_size': f'文件大小不能超过{max_size // (1024*1024)}MB'})


class AttachmentCleanupLog(models.Model):
    ACTION_CHOICES = [
        ('cleanup', '定时清理'),
        ('index', '入库'),
        ('unindex', '移除入库'),
        ('manual_delete', '手动删除'),
        ('restore', '恢复'),
        ('permanent_delete', '永久删除'),
        ('restore_from_trash', '从回收站恢复'),
    ]

    action = models.CharField(
        max_length=20,
        choices=ACTION_CHOICES,
        verbose_name='操作类型'
    )
    started_at = models.DateTimeField(
        verbose_name='开始时间'
    )
    finished_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='完成时间'
    )
    files_processed = models.PositiveIntegerField(
        default=0,
        verbose_name='处理文件数'
    )
    files_deleted = models.PositiveIntegerField(
        default=0,
        verbose_name='删除文件数'
    )
    files_archived = models.PositiveIntegerField(
        default=0,
        verbose_name='入库文件数'
    )
    files_skipped = models.PositiveIntegerField(
        default=0,
        verbose_name='跳过文件数'
    )
    space_freed = models.PositiveBigIntegerField(
        default=0,
        verbose_name='释放空间(字节)'
    )
    space_archived = models.PositiveBigIntegerField(
        default=0,
        verbose_name='入库空间(字节)'
    )
    errors = models.JSONField(
        default=list,
        blank=True,
        verbose_name='错误列表'
    )
    details = models.JSONField(
        default=dict,
        blank=True,
        verbose_name='详细信息'
    )
    triggered_by = models.CharField(
        max_length=100,
        blank=True,
        default='system',
        verbose_name='触发来源'
    )

    class Meta:
        db_table = 'chat_attachment_cleanup_log'
        verbose_name = '附件清理日志'
        verbose_name_plural = '附件清理日志'
        ordering = ['-started_at']

    def __str__(self):
        return f"CleanupLog {self.action} {self.started_at:%Y-%m-%d %H:%M}"


class StorageAlert(models.Model):
    LEVEL_CHOICES = [
        ('warning', '警告'),
        ('critical', '严重'),
    ]
    STATUS_CHOICES = [
        ('active', '活跃'),
        ('acknowledged', '已确认'),
        ('resolved', '已解决'),
    ]

    level = models.CharField(
        max_length=20,
        choices=LEVEL_CHOICES,
        verbose_name='告警级别'
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='active',
        verbose_name='告警状态'
    )
    storage_path = models.CharField(
        max_length=500,
        verbose_name='存储路径'
    )
    total_space = models.PositiveBigIntegerField(
        verbose_name='总空间(字节)'
    )
    used_space = models.PositiveBigIntegerField(
        verbose_name='已用空间(字节)'
    )
    usage_percent = models.FloatField(
        verbose_name='使用率(%)'
    )
    threshold_percent = models.FloatField(
        verbose_name='阈值(%)'
    )
    message = models.TextField(
        blank=True,
        verbose_name='告警消息'
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='创建时间'
    )
    acknowledged_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='确认时间'
    )
    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='解决时间'
    )

    class Meta:
        db_table = 'chat_storage_alert'
        verbose_name = '存储空间告警'
        verbose_name_plural = '存储空间告警'
        ordering = ['-created_at']

    def __str__(self):
        return f"StorageAlert {self.level} {self.usage_percent:.1f}% {self.created_at:%Y-%m-%d}"
