"""知识库模块序列化器。"""

from __future__ import annotations

import re

from rest_framework import serializers

from .models import Document, DocumentIndex

# 索引名称校验正则：字母、数字、下划线、连字符、中文
INDEX_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\u4e00-\u9fa5]+$")


class IndexNameValidationMixin:
    """索引名称共享校验逻辑。

    规则：
        - 仅允许字母、数字、下划线、连字符、中文
        - 长度上限 100
    """

    def validate_name(self, value: str) -> str:
        """校验索引名称格式。

        Args:
            value: 待校验的索引名称。

        Returns:
            校验通过后的索引名称。

        Raises:
            serializers.ValidationError: 名称格式非法时抛出。
        """
        if not INDEX_NAME_PATTERN.match(value):
            raise serializers.ValidationError("索引名称只能包含字母、数字、下划线、连字符和中文")
        return value


class DocumentIndexSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentIndex
        fields = ["id", "index_name", "description", "document_count", "created_at", "updated_at"]
        read_only_fields = ["id", "document_count", "created_at", "updated_at"]


class DocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = ["id", "filename", "file_path", "file_type", "file_size", "chunk_count", "created_at", "updated_at"]
        read_only_fields = ["id", "chunk_count", "created_at", "updated_at"]


class RagQuerySerializer(serializers.Serializer):
    """RAG 查询请求序列化器。"""

    index_name = serializers.CharField(required=True, help_text="索引名称")
    query = serializers.CharField(min_length=1, required=True, help_text="查询问题")
    k = serializers.IntegerField(default=4, required=False, max_value=20, help_text="返回文档数量（最大 20）")
    return_sources = serializers.BooleanField(default=True, required=False, help_text="是否返回来源")


class RagResponseSerializer(serializers.Serializer):
    """RAG 查询响应序列化器。"""

    answer = serializers.CharField()
    sources = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    success = serializers.BooleanField(default=True)
    error = serializers.CharField(required=False, allow_null=True)


class IndexCreateSerializer(IndexNameValidationMixin, serializers.Serializer):
    """创建索引请求序列化器。"""

    name = serializers.CharField(
        min_length=1,
        max_length=100,
        required=True,
        help_text="索引名称（仅允许字母、数字、下划线、连字符、中文，最长 100）",
    )
    directory_path = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, help_text="文档目录路径（可选，不提供则创建空索引）"
    )
    description = serializers.CharField(default="", allow_blank=True, help_text="索引描述")
    chunk_size = serializers.IntegerField(required=False, allow_null=True, help_text="分块大小")
    chunk_overlap = serializers.IntegerField(required=False, allow_null=True, help_text="分块重叠")
    overwrite = serializers.BooleanField(default=False, required=False, help_text="是否覆盖已存在的索引")


class EmptyIndexCreateSerializer(IndexNameValidationMixin, serializers.Serializer):
    """创建空索引请求序列化器。"""

    name = serializers.CharField(
        min_length=1,
        max_length=100,
        required=True,
        help_text="索引名称（仅允许字母、数字、下划线、连字符、中文，最长 100）",
    )
    description = serializers.CharField(
        default="", allow_blank=True, max_length=500, required=False, help_text="索引描述（可选）"
    )
    overwrite = serializers.BooleanField(default=False, required=False, help_text="是否覆盖已存在的索引")


class IndexInfoSerializer(serializers.Serializer):
    name = serializers.CharField()
    description = serializers.CharField(default="")
    created_at = serializers.CharField(default="")
    updated_at = serializers.CharField(default="")
    num_documents = serializers.IntegerField(default=0)
    store_type = serializers.CharField(default="pgvector")
    embedding_model = serializers.CharField(default="")


class SearchRequestSerializer(serializers.Serializer):
    """检索请求序列化器。"""

    index_name = serializers.CharField(required=True, help_text="索引名称")
    query = serializers.CharField(min_length=1, required=True, help_text="检索查询")
    k = serializers.IntegerField(default=4, required=False, max_value=20, help_text="返回文档数量（最大 20）")
    score_threshold = serializers.FloatField(required=False, allow_null=True, help_text="相似度阈值")


class SearchResultSerializer(serializers.Serializer):
    """检索结果序列化器。"""

    content = serializers.CharField()
    metadata = serializers.DictField()
    score = serializers.FloatField(required=False, allow_null=True)


class CreateKnowledgeBaseSerializer(serializers.Serializer):
    """创建知识库请求序列化器（views_kb.KnowledgeBaseListView.post）。"""

    name = serializers.CharField(required=True, max_length=100, help_text="知识库名称")
    description = serializers.CharField(required=False, allow_blank=True, default="", help_text="知识库描述")


class UpdateKnowledgeBaseSerializer(serializers.Serializer):
    """更新知识库请求序列化器（views_kb.KnowledgeBaseDetailView.patch）。"""

    description = serializers.CharField(required=False, allow_blank=True, help_text="知识库描述")


class KnowledgeBaseSearchSerializer(serializers.Serializer):
    """知识库检索测试请求序列化器（views_kb.KnowledgeBaseSearchView.post）。"""

    query = serializers.CharField(required=True, help_text="检索查询")
    top_k = serializers.IntegerField(required=False, default=5, min_value=1, max_value=50, help_text="返回结果数量")
