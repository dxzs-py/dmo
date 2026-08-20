from django.conf import settings
from django.db import models

from Django_xm.apps.core.base_models import BaseModel


class DocumentFileType(models.TextChoices):
    PDF = "pdf", "PDF"
    TXT = "txt", "TXT"
    MD = "md", "Markdown"
    CSV = "csv", "CSV"
    DOCX = "docx", "Word"
    XLSX = "xlsx", "Excel"
    HTML = "html", "HTML"
    JSON = "json", "JSON"
    OTHER = "other", "其他"


class IndexMetadata(BaseModel):
    """知识库索引唯一实体（dj-13：合并原 DocumentIndex 归属/软删除职责）

    承载：user 归属、状态机、向量元数据（store_type/embedding/块数镜像）、软删除墓碑。
    - name 为向量索引全名（如 user_1_test2），全局唯一
    - num_documents 为向量块数镜像（IndexManager 按向量库 stats 维护）
    - 文件数不存储，由 Document 表派生查询（单一权威）
    """

    class IndexStatus(models.TextChoices):
        EMPTY = "empty", "空索引"
        BUILDING = "building", "构建中"
        READY = "ready", "就绪"
        UPDATING = "updating", "更新中"
        ERROR = "error", "错误"

    name = models.CharField(max_length=255, unique=True, verbose_name="索引名称")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="index_metadata",
        null=True,
        blank=True,
        verbose_name="所属用户",
    )
    status = models.CharField(
        max_length=20, choices=IndexStatus.choices, default=IndexStatus.EMPTY, verbose_name="索引状态"
    )
    store_type = models.CharField(max_length=50, default="pgvector", verbose_name="存储类型")
    description = models.TextField(blank=True, default="", verbose_name="描述")
    embedding_model = models.CharField(max_length=255, blank=True, default="", verbose_name="Embedding 模型")
    embedding_dimension = models.IntegerField(null=True, blank=True, verbose_name="Embedding 维度")
    num_documents = models.IntegerField(default=0, verbose_name="向量块数")
    error_message = models.TextField(blank=True, default="", verbose_name="错误信息")
    metadata_json = models.JSONField(default=dict, blank=True, verbose_name="扩展元数据")

    class Meta:
        db_table = "knowledge_index_metadata"
        verbose_name = "索引元数据"
        verbose_name_plural = "索引元数据"
        indexes = [
            models.Index(fields=["user", "status"], name="idx_user_status"),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"

    def transition_to(self, new_status: str) -> None:
        """状态机转换，校验合法性"""
        valid_transitions = {
            "empty": ["building", "error"],
            "building": ["ready", "error"],
            "ready": ["updating", "error", "building"],
            "updating": ["ready", "error"],
            "error": ["building", "empty"],
        }
        if new_status not in valid_transitions.get(self.status, []):
            raise ValueError(
                f"非法状态转换: {self.status} -> {new_status}，允许的转换: {valid_transitions.get(self.status, [])}"
            )
        self.status = new_status
        self.save(update_fields=["status", "updated_at"])


class Document(BaseModel):
    index = models.ForeignKey(
        IndexMetadata, on_delete=models.CASCADE, related_name="documents", verbose_name="所属索引"
    )
    filename = models.CharField(max_length=255, verbose_name="文件名")
    file_path = models.CharField(max_length=500, verbose_name="文件路径")
    file_type = models.CharField(
        max_length=50, choices=DocumentFileType.choices, default=DocumentFileType.OTHER, verbose_name="文件类型"
    )
    file_size = models.BigIntegerField(verbose_name="文件大小(字节)")
    chunk_count = models.IntegerField(default=0, verbose_name="分块数量")

    class Meta:
        db_table = "rag_document"
        verbose_name = "文档"
        verbose_name_plural = "文档"
        indexes = [
            models.Index(fields=["index", "-created_at"]),
            models.Index(fields=["file_type"]),
        ]

    def __str__(self):
        return f"{self.filename} ({self.index.name})"
