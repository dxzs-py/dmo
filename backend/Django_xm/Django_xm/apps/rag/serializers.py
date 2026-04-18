from rest_framework import serializers
from .models import DocumentIndex, Document


class DocumentIndexSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentIndex
        fields = ['id', 'index_name', 'description', 'document_count',
                  'created_at', 'updated_at']
        read_only_fields = ['id', 'document_count', 'created_at', 'updated_at']


class DocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = ['id', 'filename', 'file_path', 'file_type', 'file_size',
                  'chunk_count', 'created_at', 'updated_at']
        read_only_fields = ['id', 'chunk_count', 'created_at', 'updated_at']


class RagQuerySerializer(serializers.Serializer):
    """RAG 查询请求序列化器"""
    index_name = serializers.CharField(
        required=True,
        help_text="索引名称"
    )
    query = serializers.CharField(
        min_length=1,
        required=True,
        help_text="查询问题"
    )
    k = serializers.IntegerField(
        default=4,
        required=False,
        help_text="返回文档数量"
    )
    return_sources = serializers.BooleanField(
        default=True,
        required=False,
        help_text="是否返回来源"
    )
    use_rag_agent = serializers.BooleanField(
        default=True,
        required=False,
        help_text="是否使用 RAG Agent"
    )


class RagResponseSerializer(serializers.Serializer):
    """RAG 查询响应序列化器"""
    answer = serializers.CharField()
    sources = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=list
    )
    success = serializers.BooleanField(default=True)
    error = serializers.CharField(required=False, allow_null=True)


class IndexCreateSerializer(serializers.Serializer):
    """创建索引请求序列化器"""
    name = serializers.CharField(
        min_length=1,
        required=True,
        help_text="索引名称"
    )
    directory_path = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        help_text="文档目录路径（可选，不提供则创建空索引）"
    )
    description = serializers.CharField(
        default="",
        allow_blank=True,
        help_text="索引描述"
    )
    chunk_size = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text="分块大小"
    )
    chunk_overlap = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text="分块重叠"
    )
    overwrite = serializers.BooleanField(
        default=False,
        required=False,
        help_text="是否覆盖已存在的索引"
    )


class EmptyIndexCreateSerializer(serializers.Serializer):
    """创建空索引请求序列化器"""
    name = serializers.CharField(
        min_length=1,
        max_length=100,
        required=True,
        help_text="索引名称（只能包含字母、数字、下划线和连字符）"
    )
    description = serializers.CharField(
        default="",
        allow_blank=True,
        max_length=500,
        required=False,
        help_text="索引描述（可选）"
    )
    overwrite = serializers.BooleanField(
        default=False,
        required=False,
        help_text="是否覆盖已存在的索引"
    )
    
    def validate_name(self, value):
        """验证索引名称格式"""
        import re
        if not re.match(r'^[a-zA-Z0-9_-]+$', value):
            raise serializers.ValidationError("索引名称只能包含字母、数字、下划线和连字符")
        return value


class IndexInfoSerializer(serializers.Serializer):
    """索引信息序列化器"""
    name = serializers.CharField()
    description = serializers.CharField()
    created_at = serializers.CharField()
    updated_at = serializers.CharField()
    num_documents = serializers.IntegerField()
    store_type = serializers.CharField(default="faiss")
    embedding_model = serializers.CharField()


class SearchRequestSerializer(serializers.Serializer):
    """检索请求序列化器"""
    index_name = serializers.CharField(
        required=True,
        help_text="索引名称"
    )
    query = serializers.CharField(
        min_length=1,
        required=True,
        help_text="检索查询"
    )
    k = serializers.IntegerField(
        default=4,
        required=False,
        help_text="返回文档数量"
    )
    score_threshold = serializers.FloatField(
        required=False,
        allow_null=True,
        help_text="相似度阈值"
    )


class SearchResultSerializer(serializers.Serializer):
    """检索结果序列化器"""
    content = serializers.CharField()
    metadata = serializers.DictField()
    score = serializers.FloatField(required=False, allow_null=True)
