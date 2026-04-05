from django.db import models
from Django_xm.apps.core.base_models import BaseModel


class DocumentIndex(BaseModel):
    index_name = models.CharField(max_length=100, unique=True, verbose_name='索引名称')
    description = models.TextField(blank=True, verbose_name='描述')
    document_count = models.IntegerField(default=0, verbose_name='文档数量')

    class Meta:
        db_table = 'rag_document_index'
        verbose_name = '文档索引'
        verbose_name_plural = '文档索引'
        indexes = [
            models.Index(fields=['-updated_at']),
        ]

    def __str__(self):
        return self.index_name

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('rag:index-detail', kwargs={'name': self.index_name})


class Document(BaseModel):
    index = models.ForeignKey(DocumentIndex, on_delete=models.CASCADE, related_name='documents', verbose_name='所属索引')
    filename = models.CharField(max_length=255, verbose_name='文件名')
    file_path = models.CharField(max_length=500, verbose_name='文件路径')
    file_type = models.CharField(max_length=50, verbose_name='文件类型')
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
