"""知识库模块序列化器。"""

from __future__ import annotations

import re

from rest_framework import serializers

from .models import Document

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


class RagSourceItemSerializer(serializers.Serializer):
    """RAG 查询单条来源（C-4 结构化契约，与流式 sources 事件 data 同构）。

    对应 strict_rag_chain._extract_sources 输出形状：
    {content, source(显示名，C-7 白名单后非服务器路径), metadata, [degraded], [score]}。
    score（R-UX-04）为检索内核归一相似度语义（越大越相关），无分数出口的检索
    路径（MMR/MultiQuery/重排/关键词降级）不携带该键。
    """

    content = serializers.CharField(allow_blank=True, help_text="来源内容片段")
    source = serializers.CharField(required=False, allow_blank=True, help_text="来源显示名")
    metadata = serializers.DictField(required=False, help_text="来源元数据（白名单化）")
    degraded = serializers.BooleanField(required=False, help_text="是否为关键词降级检索结果")
    score = serializers.FloatField(required=False, help_text="相关度分数（归一相似度语义，越大越相关）")


class RagResponseSerializer(serializers.Serializer):
    """RAG 查询响应序列化器。

    C-4：sources 必须是结构化子序列化器——原 ListField(child=CharField()) 会把
    dict 逐元素 str() 成 repr 字符串，与流式端点形状漂移且前端渲染不可用。
    """

    answer = serializers.CharField()
    sources = RagSourceItemSerializer(many=True, required=False, default=list)
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
    # B13：chunk 参数接入 split_documents 调用链（此前为死参数，恒走全局默认 1000/200）
    chunk_size = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=200,
        max_value=4000,
        help_text="分块大小（200-4000，缺省读全局配置）",
    )
    chunk_overlap = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=0,
        help_text="分块重叠（0 ≤ overlap ≤ chunk_size/2，缺省读全局配置）",
    )
    overwrite = serializers.BooleanField(default=False, required=False, help_text="是否覆盖已存在的索引")

    def validate(self, attrs):
        """B13：chunk 参数联动校验——overlap 不得超过 chunk_size 的一半
        （chunk_size 的 200-4000 区间由字段 min/max 保证）。
        """
        chunk_size = attrs.get("chunk_size")
        chunk_overlap = attrs.get("chunk_overlap")
        if chunk_size is not None and chunk_overlap is not None and chunk_overlap > chunk_size // 2:
            raise serializers.ValidationError({"chunk_overlap": "分块重叠不能超过 chunk_size 的一半"})
        return attrs


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


class SearchAllRequestSerializer(serializers.Serializer):
    """跨库聚合检索请求序列化器（views_rag.SearchAllView.post）。"""

    query = serializers.CharField(min_length=1, required=True, help_text="检索查询")
    k = serializers.IntegerField(
        default=4, required=False, min_value=1, max_value=20, help_text="全局返回结果数量（最大 20）"
    )
    index_names = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_null=True,
        help_text="指定检索的索引名称列表（原始名，非全名）；缺省为当前用户全部知识库",
    )
    score_threshold = serializers.FloatField(required=False, allow_null=True, help_text="相似度阈值")


class SearchAllResultItemSerializer(serializers.Serializer):
    """跨库聚合检索结果条目序列化器。"""

    content = serializers.CharField()
    metadata = serializers.DictField()
    score = serializers.FloatField(required=False, allow_null=True)
    source_index = serializers.CharField(help_text="来源知识库原始名")
    fused_rank = serializers.IntegerField(help_text="RRF 融合后全局排名（1 起）")


class SearchAllResponseSerializer(serializers.Serializer):
    """跨库聚合检索响应序列化器。"""

    results = SearchAllResultItemSerializer(many=True)
    searched_indexes = serializers.IntegerField(help_text="成功检索的索引数")
    failed_indexes = serializers.IntegerField(help_text="检索失败被跳过的索引数")


class CreateKnowledgeBaseSerializer(IndexNameValidationMixin, serializers.Serializer):
    """创建知识库请求序列化器（views_kb.KnowledgeBaseListView.post）。

    C-2：name 必须挂 IndexNameValidationMixin——名称经 get_user_index_name 拼接后
    直达磁盘路径（kb_service.upload_dir），无白名单则 `../`/`\\` 可穿越 uploads 根目录。
    """

    name = serializers.CharField(
        required=True,
        max_length=100,
        help_text="知识库名称（仅允许字母、数字、下划线、连字符、中文，最长 100）",
    )
    description = serializers.CharField(required=False, allow_blank=True, default="", help_text="知识库描述")


class UpdateKnowledgeBaseSerializer(serializers.Serializer):
    """更新知识库请求序列化器（views_kb.KnowledgeBaseDetailView.patch）。"""

    description = serializers.CharField(required=False, allow_blank=True, help_text="知识库描述")


class KnowledgeBaseSearchSerializer(serializers.Serializer):
    """知识库检索测试请求序列化器（views_kb.KnowledgeBaseSearchView.post）。"""

    query = serializers.CharField(required=True, help_text="检索查询")
    top_k = serializers.IntegerField(required=False, default=5, min_value=1, max_value=50, help_text="返回结果数量")
