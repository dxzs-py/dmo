"""
聊天模块序列化器

设计原则：
- 输入验证（Request/Input）：使用 Serializer，不绑定模型，灵活定义字段和验证规则
- 输出序列化（Response/Output）：使用 ModelSerializer，绑定模型，自动处理字段映射
- 创建/更新操作：使用 ModelSerializer，利用 save() 方法简化实例创建
"""

from typing import Any

from rest_framework import serializers

from .models import ChatMessage, ChatSession

# ==================== 输入验证序列化（Serializer） ====================


class MessageSerializer(serializers.Serializer):
    """聊天历史消息项 - 用于请求输入中的 chat_history 字段验证"""

    role = serializers.ChoiceField(choices=["user", "assistant", "system"], help_text="消息角色：user/assistant/system")
    content = serializers.CharField(max_length=50000, help_text="消息内容，最大50000字符", allow_blank=True)
    attachment_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, allow_null=True, help_text="该消息关联的附件ID列表"
    )

    def validate_role(self, value):
        valid_roles = ["user", "assistant", "system"]
        if value not in valid_roles:
            raise serializers.ValidationError(f"无效的角色类型，必须是: {', '.join(valid_roles)}")
        return value

    def validate_content(self, value):
        if value and len(value.strip()) == 0:
            raise serializers.ValidationError("消息内容不能为空")
        return value.strip() if value else value


class ChatRequestSerializer(serializers.Serializer):
    """
    聊天请求输入验证

    用途：验证前端发送的聊天请求数据
    使用 Serializer 原因：输入数据不直接对应单一模型字段，
    需要灵活组合 message/chat_history/mode 等字段
    """

    message = serializers.CharField(min_length=1, max_length=10000, help_text="用户消息内容，1-10000字符")
    client_message_id = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=64,
        help_text="客户端消息幂等ID（可选），同一用户短时间内相同 ID 的重复请求将被去重",
    )
    chat_history = MessageSerializer(many=True, required=False, allow_null=True)
    mode = serializers.CharField(default="agent", max_length=50, help_text="对话模式标识")
    use_tools = serializers.BooleanField(default=True, help_text="是否使用工具")
    use_web_search = serializers.BooleanField(default=False, help_text="是否启用联网搜索")
    use_knowledge_base = serializers.BooleanField(default=False, help_text="是否启用知识库检索")
    use_deep_thinking = serializers.BooleanField(default=False, help_text="是否启用深度思考")
    use_mcp = serializers.BooleanField(default=False, help_text="是否启用 MCP 工具")
    selected_mcp_servers = serializers.ListField(
        child=serializers.CharField(max_length=50),
        required=False,
        allow_null=True,
        help_text="选中的 MCP Server 名称列表（每个名称最长 50 字符），为空则使用所有已配置的 Server",
    )
    selected_tools = serializers.ListField(
        child=serializers.CharField(max_length=50),
        required=False,
        allow_null=True,
        help_text="选中的工具名称列表（每个名称最长 50 字符），为空则根据模式自动选择",
    )
    streaming = serializers.BooleanField(default=False, help_text="是否流式输出")
    session_id = serializers.CharField(required=False, allow_null=True, max_length=100)
    selected_knowledge_base = serializers.CharField(
        required=False, allow_null=True, max_length=200, help_text="选中的知识库ID"
    )
    selected_knowledge_bases = serializers.ListField(
        child=serializers.CharField(max_length=200),
        required=False,
        allow_null=True,
        help_text="选中的知识库ID列表（多选）",
    )
    attachment_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        allow_null=True,
        help_text="附件ID列表，用于基于文件内容回答问题",
    )
    provider_id = serializers.CharField(
        required=False,
        allow_null=True,
        max_length=50,
        help_text="模型提供商 ID（openai/deepseek/groq/baidu_qianfan/anthropic）",
    )
    model_name = serializers.CharField(
        required=False, allow_null=True, max_length=100, help_text="模型名称，如 gpt-4o-mini、deepseek-v4-flash"
    )
    special_params = serializers.DictField(
        required=False, allow_null=True, help_text="提供商专属参数，如 DeepSeek 的 thinking/reasoning_effort"
    )
    temperature = serializers.FloatField(
        required=False, allow_null=True, min_value=0.0, max_value=2.0, help_text="温度参数，0-2之间"
    )
    max_tokens = serializers.IntegerField(
        required=False, allow_null=True, min_value=1, max_value=32768, help_text="最大生成 token 数"
    )
    research_task_id = serializers.CharField(
        required=False, allow_null=True, max_length=100,
        help_text="关联的深度研究任务ID，用于注入研究上下文"
    )
    continue_task_id = serializers.CharField(
        required=False, allow_null=True, max_length=100, help_text="继续研究的任务ID，用于加载先前研究成果"
    )
    regenerate = serializers.BooleanField(
        default=False,
        help_text="是否重新生成：复用已有消息对（不新建 user/assistant），对 assistant 消息追加新版本",
    )
    user_message_id = serializers.CharField(
        required=False, allow_null=True, max_length=100,
        help_text="重新生成目标用户消息 ID（regenerate=True 时必填）",
    )
    assistant_message_id = serializers.CharField(
        required=False, allow_null=True, max_length=100,
        help_text="重新生成目标助手消息 ID（regenerate=True 时必填）",
    )

    def validate_message(self, value):
        """验证消息内容"""
        value = value.strip()
        if not value:
            raise serializers.ValidationError("消息内容不能为空或仅包含空格")
        if len(value) > 10000:
            raise serializers.ValidationError("消息内容不能超过10000个字符")
        return value

    def validate_mode(self, value):
        allowed_modes = ["agent", "deep-research"]
        if value not in allowed_modes:
            raise serializers.ValidationError(f"不支持的模式: {value}")
        return value

    def validate_session_id(self, value):
        """验证会话ID格式"""
        if value:
            import re

            if not re.match(r"^[a-f0-9\-]{36}$", value):
                raise serializers.ValidationError("会话ID格式无效")
        return value

    def validate_chat_history(self, value):
        """验证聊天历史"""
        if value is not None and len(value) > 50:
            raise serializers.ValidationError("聊天历史记录不能超过50条")
        return value

    def validate_attachment_ids(self, value):
        """验证附件ID列表"""
        if value is not None and len(value) > 10:
            raise serializers.ValidationError("单次请求附件数量不能超过10个")
        return value

    def validate(self, attrs):
        """跨字段验证"""
        if attrs.get("streaming") and not attrs.get("message"):
            raise serializers.ValidationError({"message": "流式模式必须提供消息内容"})
        if attrs.get("regenerate"):
            if not attrs.get("user_message_id") or not attrs.get("assistant_message_id"):
                raise serializers.ValidationError(
                    {"message": "重新生成必须提供 user_message_id 与 assistant_message_id"}
                )
        return attrs


class ChatResponseSerializer(serializers.Serializer):
    """
    聊天响应输出格式

    用途：统一非流式聊天的响应结构
    使用 Serializer 原因：响应数据是聚合结果（含 tools_used/success 等），
    不直接对应数据库模型
    """

    message = serializers.CharField()
    mode = serializers.CharField()
    tools_used = serializers.ListField(child=serializers.CharField())
    success = serializers.BooleanField()
    error = serializers.CharField(required=False, allow_null=True)
    session_id = serializers.CharField(required=False, allow_null=True)


# ==================== 模型序列化器（ModelSerializer） ====================


class ChatMessageSerializer(serializers.ModelSerializer):
    attachments = serializers.SerializerMethodField()
    attachment_ids = serializers.SerializerMethodField(method_name='get_attachment_ids')
    research_task_id = serializers.CharField(read_only=True)
    research_task_status = serializers.SerializerMethodField(method_name='get_research_task_status')
    research_task_deleted = serializers.SerializerMethodField(method_name='get_research_task_deleted')
    session_id = serializers.PrimaryKeyRelatedField(source='session', read_only=True)
    chain_of_thought = serializers.JSONField(read_only=True)
    tool_calls = serializers.JSONField(read_only=True)
    # 子代理图层正文/思考（Agent 图层嵌套规范 Task 1.5，刷新后恢复子代理图层依据）
    subagent_contents = serializers.JSONField(read_only=True)
    current_version = serializers.IntegerField(required=False)
    is_finalized = serializers.BooleanField(required=False)
    created_at = serializers.DateTimeField(read_only=True)
    token_count = serializers.IntegerField(read_only=True)
    token_detail = serializers.JSONField(read_only=True)
    response_time = serializers.FloatField(read_only=True)

    class Meta:
        model = ChatMessage
        fields = [
            "id",
            "session_id",
            "role",
            "content",
            "sources",
            "plan",
            "chain_of_thought",
            "tool_calls",
            "approval",
            "reasoning",
            "subagent_contents",
            "suggestions",
            "versions",
            "current_version",
            "is_finalized",
            "attachments",
            "attachment_ids",
            "created_at",
            "model",
            "token_count",
            "token_detail",
            "response_time",
            "research_task_id",
            "research_task_status",
            "research_task_deleted",
        ]
        read_only_fields = ["id", "session_id", "created_at"]

    def get_attachments(self, obj) -> list[Any]:
        from Django_xm.apps.attachments.serializers import ChatAttachmentSerializer

        try:
            attachments = obj.attachments.all()
            if not attachments and hasattr(obj, "_prefetched_objects_cache"):
                attachments = obj._prefetched_objects_cache.get("attachments", [])
            return ChatAttachmentSerializer(attachments, many=True).data
        except Exception:
            return []

    def get_attachment_ids(self, obj) -> list[int]:
        try:
            attachments = obj.attachments.all()
            if not attachments and hasattr(obj, "_prefetched_objects_cache"):
                attachments = obj._prefetched_objects_cache.get("attachments", [])
            return [att.id for att in attachments]
        except Exception:
            return []

    def get_research_task_status(self, obj) -> str | None:
        """返回关联研究任务的权威状态（P7 根因修复）

        前端刷新后凭该字段判定"研究进行中/已完成"，避免快照不含状态导致误显。
        必须用 all_objects（默认 objects 过滤了 is_deleted=True）。
        """
        if not obj.research_task_id:
            return None
        try:
            from django.apps import apps

            ResearchTask = apps.get_model("research", "ResearchTask")
            task = ResearchTask.all_objects.filter(task_id=obj.research_task_id).first()
            return task.status if task else None
        except Exception:
            return None

    def get_research_task_deleted(self, obj) -> bool | None:
        """检查关联的研究任务是否已软删除"""
        if not obj.research_task_id:
            return None
        try:
            from django.apps import apps

            ResearchTask = apps.get_model("research", "ResearchTask")
            # 必须用 all_objects，默认 objects 过滤了 is_deleted=True
            task = ResearchTask.all_objects.filter(task_id=obj.research_task_id).first()
            return task.is_deleted if task else True
        except Exception:
            return None

    def validate_content(self, value):
        if value and len(value) > 50000:
            raise serializers.ValidationError("消息内容不能超过50000个字符")
        return value

    def validate_role(self, value):
        from Django_xm.apps.chat.models import MessageRole

        valid_roles = [choice[0] for choice in MessageRole.choices]
        if value not in valid_roles:
            raise serializers.ValidationError(f"无效的角色: {value}")
        return value

    def validate_sources(self, value):
        return value if value is not None else []

    def validate_tool_calls(self, value):
        return value if value is not None else []

    def validate_suggestions(self, value):
        return value if value is not None else []

    def validate_versions(self, value):
        return value if value is not None else []

    def validate_current_version(self, value):
        if value is None:
            return 0
        if value < 0:
            raise serializers.ValidationError("版本索引不能为负数")
        return value


class ChatSessionListSerializer(serializers.ModelSerializer):
    """
    会话列表序列化器（轻量）

    用途：会话列表页展示，仅包含摘要信息
    绑定模型：ChatSession
    """

    message_count = serializers.IntegerField(read_only=True)
    session_id = serializers.CharField(read_only=True)
    selected_knowledge_base = serializers.CharField(read_only=True, allow_null=True)
    selected_knowledge_bases = serializers.JSONField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = ChatSession
        fields = [
            "id",
            "session_id",
            "title",
            "mode",
            "selected_knowledge_base",
            "selected_knowledge_bases",
            "message_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["session_id", "created_at", "updated_at"]

    def validate_title(self, value):
        if value and len(value.strip()) == 0:
            raise serializers.ValidationError("标题不能为空")
        if value and len(value) > 200:
            raise serializers.ValidationError("标题不能超过200个字符")
        return value.strip() if value else value

    def to_representation(self, instance):
        return super().to_representation(instance)


class ChatSessionDetailSerializer(serializers.ModelSerializer):
    """
    会话详情序列化器（完整）

    用途：会话详情页展示，包含完整消息列表
    绑定模型：ChatSession
    注意：messages 字段会触发额外查询，建议配合 prefetch_related('messages') 使用
    """

    messages = ChatMessageSerializer(many=True, read_only=True)
    message_count = serializers.IntegerField(read_only=True)
    session_id = serializers.CharField(read_only=True)
    selected_knowledge_base = serializers.CharField(read_only=True, allow_null=True)
    selected_knowledge_bases = serializers.JSONField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = ChatSession
        fields = [
            "id",
            "session_id",
            "title",
            "mode",
            "selected_knowledge_base",
            "selected_knowledge_bases",
            "messages",
            "message_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["session_id", "created_at", "updated_at"]

    def get_message_count(self, obj) -> int:
        if hasattr(obj, "_message_count"):
            return obj._message_count
        prefetched_cache = getattr(obj, "_prefetched_objects_cache", None)
        if prefetched_cache and "messages" in prefetched_cache:
            return len(prefetched_cache["messages"])
        return obj.messages.count()

    def to_representation(self, instance):
        return super().to_representation(instance)


class ChatSessionCreateSerializer(serializers.ModelSerializer):
    """
    会话创建序列化器

    用途：创建新会话时验证输入并生成实例
    绑定模型：ChatSession
    """

    selected_knowledge_base = serializers.CharField(
        required=False, allow_null=True, max_length=100
    )
    selected_knowledge_bases = serializers.JSONField(
        required=False, allow_null=True
    )

    class Meta:
        model = ChatSession
        fields = ["title", "mode", "selected_knowledge_base", "selected_knowledge_bases"]

    def validate_title(self, value):
        if not value:
            value = "新对话"
        elif len(value.strip()) == 0:
            raise serializers.ValidationError("标题不能为空")
        elif len(value) > 200:
            raise serializers.ValidationError("标题不能超过200个字符")
        return value.strip()

    def validate_mode(self, value):
        allowed_modes = ["agent", "deep-research"]
        if value and value not in allowed_modes:
            raise serializers.ValidationError(f"不支持的模式: {value}")
        if value and len(value) > 50:
            raise serializers.ValidationError("模式标识不能超过50个字符")
        return value


class ChatSessionUpdateSerializer(serializers.ModelSerializer):
    """
    会话更新序列化器

    用途：更新会话标题等字段
    绑定模型：ChatSession
    允许修改 title 和 selected_knowledge_base 字段
    """

    selected_knowledge_base = serializers.CharField(
        required=False, allow_null=True, max_length=100
    )
    selected_knowledge_bases = serializers.JSONField(
        required=False, allow_null=True
    )

    class Meta:
        model = ChatSession
        fields = ["title", "selected_knowledge_base", "selected_knowledge_bases"]

    def validate_title(self, value):
        if value is not None:
            if len(value.strip()) == 0:
                raise serializers.ValidationError("标题不能为空")
            if len(value) > 200:
                raise serializers.ValidationError("标题不能超过200个字符")
            return value.strip()
        return value
