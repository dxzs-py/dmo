"""深度研究模块序列化器。"""

from __future__ import annotations

from rest_framework import serializers

from .models import ResearchTask

# 允许的研究深度枚举
RESEARCH_DEPTH_CHOICES: tuple[str, ...] = ("basic", "standard", "comprehensive")


class ResearchStartSerializer(serializers.Serializer):
    """深度研究启动请求序列化器。"""

    query = serializers.CharField(
        min_length=1, max_length=10000, required=True, help_text="研究问题（最长 10000 字符）"
    )
    thread_id = serializers.CharField(required=False, allow_null=True, help_text="自定义线程 ID（可选）")
    task_id = serializers.CharField(required=False, allow_null=True, help_text="任务 ID（已废弃，使用 thread_id）")
    research_depth = serializers.CharField(default="standard", help_text="研究深度：basic / standard / comprehensive")
    enable_web_search = serializers.BooleanField(default=True, required=False, help_text="是否启用网络搜索")
    enable_doc_analysis = serializers.BooleanField(default=False, required=False, help_text="是否启用文档分析")
    enable_sandbox = serializers.BooleanField(
        default=False, required=False, help_text="是否启用沙箱执行（HIGH 级命令在容器内隔离）"
    )
    knowledge_base_ids = serializers.ListField(
        child=serializers.CharField(), default=list, required=False, allow_empty=True, help_text="关联的知识库 ID 列表"
    )
    use_mcp = serializers.BooleanField(default=False, required=False, help_text="是否启用 MCP 工具")
    selected_mcp_servers = serializers.ListField(
        child=serializers.CharField(),
        default=list,
        required=False,
        allow_empty=True,
        help_text="选中的 MCP 服务器名称列表",
    )
    selected_tools = serializers.ListField(
        child=serializers.CharField(), default=list, required=False, allow_empty=True, help_text="选中的工具名称列表"
    )
    provider_id = serializers.CharField(required=False, allow_null=True, help_text="模型提供商 ID")
    model_name = serializers.CharField(required=False, allow_null=True, help_text="模型名称")
    enable_deep_thinking = serializers.BooleanField(default=False, required=False, help_text="是否启用深度思考")
    temperature = serializers.FloatField(
        required=False, allow_null=True, min_value=0, max_value=2, help_text="生成温度"
    )
    max_tokens = serializers.IntegerField(required=False, allow_null=True, min_value=1, help_text="最大生成 token 数")
    special_params = serializers.DictField(
        required=False, allow_null=True, help_text="模型专属参数（如 thinking、reasoning_effort）"
    )

    def validate_research_depth(self, value: str) -> str:
        """校验研究深度枚举值。

        Args:
            value: 待校验的研究深度字符串。

        Returns:
            校验通过后的研究深度字符串。

        Raises:
            serializers.ValidationError: 值不在允许枚举内时抛出。
        """
        if value not in RESEARCH_DEPTH_CHOICES:
            raise serializers.ValidationError(
                f"不支持的研究深度: {value}，必须是 {', '.join(RESEARCH_DEPTH_CHOICES)} 之一"
            )
        return value

    def validate(self, data):
        if data.get("enable_doc_analysis") and not data.get("knowledge_base_ids"):
            raise serializers.ValidationError("启用文档分析时，必须选择至少一个知识库")
        return data


class ResearchContinueSerializer(serializers.Serializer):
    additional_query = serializers.CharField(
        required=False, allow_blank=True, help_text="补充说明（可选，追加到原研究主题后）"
    )
    enable_web_search = serializers.BooleanField(default=True, required=False)
    enable_doc_analysis = serializers.BooleanField(default=False, required=False)
    enable_sandbox = serializers.BooleanField(default=False, required=False)
    knowledge_base_ids = serializers.ListField(
        child=serializers.CharField(),
        default=list,
        required=False,
        allow_empty=True,
    )
    use_mcp = serializers.BooleanField(default=False, required=False)
    selected_mcp_servers = serializers.ListField(
        child=serializers.CharField(),
        default=list,
        required=False,
        allow_empty=True,
    )
    selected_tools = serializers.ListField(
        child=serializers.CharField(),
        default=list,
        required=False,
        allow_empty=True,
    )
    provider_id = serializers.CharField(required=False, allow_null=True)
    model_name = serializers.CharField(required=False, allow_null=True)
    enable_deep_thinking = serializers.BooleanField(default=False, required=False)
    temperature = serializers.FloatField(required=False, allow_null=True, min_value=0, max_value=2)
    max_tokens = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    special_params = serializers.DictField(required=False, allow_null=True)


class ResearchTaskSerializer(serializers.ModelSerializer):
    source = serializers.SerializerMethodField()
    parent_task_id = serializers.SerializerMethodField()
    version_chain = serializers.SerializerMethodField()

    def get_source(self, obj):
        return "chat" if obj.session_id else "standalone"

    def get_parent_task_id(self, obj):
        return obj.parent_task_id

    def get_version_chain(self, obj):
        return obj.version_chain

    class Meta:
        model = ResearchTask
        fields = [
            "id",
            "task_id",
            "query",
            "status",
            "final_report",
            "enable_web_search",
            "enable_doc_analysis",
            "enable_sandbox",
            "knowledge_base_ids",
            "research_depth",
            "error_message",
            "created_by",
            "created_at",
            "updated_at",
            "session_id",
            "source",
            "parent_task_id",
            "version",
            "version_chain",
        ]
        read_only_fields = ["id", "task_id", "created_at", "updated_at", "error_message"]


class ResearchRetrySubagentSerializer(serializers.Serializer):
    """单独重启失败子代理请求序列化器（Task 3）。

    字段：
        agent_path: 目标子代理完整调用链路（如 ["main", "web-researcher"]）
        tool_call_id: 失败子代理工具调用 ID（LLM tool_call.id）
    """

    agent_path = serializers.ListField(
        child=serializers.CharField(max_length=100),
        min_length=1,
        max_length=10,
        required=True,
        help_text='目标子代理完整调用链路（如 ["main", "web-researcher"]）',
    )
    tool_call_id = serializers.CharField(
        required=True, allow_blank=False, max_length=200, help_text="失败子代理工具调用 ID"
    )

    def validate_agent_path(self, value: list[str]) -> list[str]:
        """校验 agent_path 元素非空（首个元素应为 main，末位为子代理名称）。"""
        cleaned = [str(item).strip() for item in value]
        if any(not item for item in cleaned):
            raise serializers.ValidationError("agent_path 各元素不能为空")
        return cleaned


class ResearchResultSerializer(serializers.Serializer):
    """研究结果序列化器"""

    status = serializers.CharField()
    thread_id = serializers.CharField()
    query = serializers.CharField()
    final_report = serializers.CharField(required=False, allow_blank=True)
    plan = serializers.DictField(required=False, allow_null=True)
    steps_completed = serializers.DictField(required=False, allow_null=True)
    metadata = serializers.DictField(required=False, allow_null=True)
