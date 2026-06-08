from django.db import models
from django.conf import settings
from Django_xm.apps.core.base_models import AuditModel, BaseModel


class DocumentFileType(models.TextChoices):
    PDF = 'pdf', 'PDF'
    TXT = 'txt', 'TXT'
    MD = 'md', 'Markdown'
    CSV = 'csv', 'CSV'
    DOCX = 'docx', 'Word'
    XLSX = 'xlsx', 'Excel'
    HTML = 'html', 'HTML'
    JSON = 'json', 'JSON'
    OTHER = 'other', '其他'


class DocumentIndex(AuditModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='document_indexes',
        verbose_name='所属用户',
        null=True,
        blank=True
    )
    index_name = models.CharField(max_length=100, verbose_name='索引名称')
    description = models.TextField(blank=True, verbose_name='描述')
    document_count = models.IntegerField(default=0, verbose_name='文档数量')

    class Meta:
        db_table = 'rag_document_index'
        verbose_name = '文档索引'
        verbose_name_plural = '文档索引'
        unique_together = ('user', 'index_name')
        indexes = [
            models.Index(fields=['user', '-updated_at']),
        ]

    def __str__(self):
        if self.user and hasattr(self.user, 'username'):
            return f"{self.index_name} (user: {self.user.username})"
        return f"{self.index_name}"

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('knowledge:index-detail', kwargs={'name': self.index_name})


class Document(BaseModel):
    index = models.ForeignKey(DocumentIndex, on_delete=models.CASCADE, related_name='documents', verbose_name='所属索引')
    filename = models.CharField(max_length=255, verbose_name='文件名')
    file_path = models.CharField(max_length=500, verbose_name='文件路径')
    file_type = models.CharField(
        max_length=50,
        choices=DocumentFileType.choices,
        default=DocumentFileType.OTHER,
        verbose_name='文件类型'
    )
    file_size = models.BigIntegerField(verbose_name='文件大小(字节)')
    chunk_count = models.IntegerField(default=0, verbose_name='分块数量')

    class Meta:
        db_table = 'rag_document'
        verbose_name = '文档'
        verbose_name_plural = '文档'
        indexes = [
            models.Index(fields=['index', '-created_at']),
            models.Index(fields=['file_type']),
        ]

    def __str__(self):
        return f"{self.filename} ({self.index.index_name})"


class IndexMetadata(models.Model):
    """索引元数据（替代文件系统 metadata.json）"""

    class IndexStatus(models.TextChoices):
        EMPTY = 'empty', '空索引'
        BUILDING = 'building', '构建中'
        READY = 'ready', '就绪'
        UPDATING = 'updating', '更新中'
        ERROR = 'error', '错误'

    name = models.CharField(max_length=255, unique=True, verbose_name="索引名称")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='index_metadata',
        null=True,
        blank=True,
        verbose_name="所属用户"
    )
    status = models.CharField(
        max_length=20,
        choices=IndexStatus.choices,
        default=IndexStatus.EMPTY,
        verbose_name="索引状态"
    )
    store_type = models.CharField(
        max_length=50,
        default='pgvector',
        verbose_name="存储类型"
    )
    description = models.TextField(blank=True, default='', verbose_name="描述")
    embedding_model = models.CharField(max_length=255, blank=True, default='', verbose_name="Embedding 模型")
    embedding_dimension = models.IntegerField(null=True, blank=True, verbose_name="Embedding 维度")
    num_documents = models.IntegerField(default=0, verbose_name="文档数量")
    error_message = models.TextField(blank=True, default='', verbose_name="错误信息")
    metadata_json = models.JSONField(default=dict, blank=True, verbose_name="扩展元数据")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = 'knowledge_index_metadata'
        verbose_name = "索引元数据"
        verbose_name_plural = "索引元数据"
        indexes = [
            models.Index(fields=['user', 'status'], name='idx_user_status'),
            models.Index(fields=['name'], name='idx_name'),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"

    def transition_to(self, new_status: str) -> None:
        """状态机转换，校验合法性"""
        valid_transitions = {
            'empty': ['building', 'error'],
            'building': ['ready', 'error'],
            'ready': ['updating', 'error', 'building'],
            'updating': ['ready', 'error'],
            'error': ['building', 'empty'],
        }
        if new_status not in valid_transitions.get(self.status, []):
            raise ValueError(
                f"非法状态转换: {self.status} -> {new_status}，"
                f"允许的转换: {valid_transitions.get(self.status, [])}"
            )
        self.status = new_status
        self.save(update_fields=['status', 'updated_at'])
