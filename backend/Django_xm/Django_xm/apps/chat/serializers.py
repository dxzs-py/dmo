"""
聊天模块序列化器

设计原则：
- 输入验证（Request/Input）：使用 Serializer，不绑定模型，灵活定义字段和验证规则
- 输出序列化（Response/Output）：使用 ModelSerializer，绑定模型，自动处理字段映射
- 创建/更新操作：使用 ModelSerializer，利用 save() 方法简化实例创建
"""

from rest_framework import serializers
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import ChatSession, ChatMessage, ChatAttachment


# ==================== 输入验证序列化（Serializer） ====================

class MessageSerializer(serializers.Serializer):
    """聊天历史消息项 - 用于请求输入中的 chat_history 字段验证"""
    role = serializers.ChoiceField(
        choices=['user', 'assistant', 'system'],
        help_text='消息角色：user/assistant/system'
    )
    content = serializers.CharField(
        max_length=50000,
        help_text='消息内容，最大50000字符',
        allow_blank=True
    )

    def validate_role(self, value):
        valid_roles = ['user', 'assistant', 'system']
        if value not in valid_roles:
            raise serializers.ValidationError(f'无效的角色类型，必须是: {", ".join(valid_roles)}')
        return value

    def validate_content(self, value):
        if value and len(value.strip()) == 0:
            raise serializers.ValidationError('消息内容不能为空')
        return value.strip() if value else value


class ChatRequestSerializer(serializers.Serializer):
    """
    聊天请求输入验证
    
    用途：验证前端发送的聊天请求数据
    使用 Serializer 原因：输入数据不直接对应单一模型字段，
    需要灵活组合 message/chat_history/mode 等字段
    """
    message = serializers.CharField(
        min_length=1,
        max_length=10000,
        help_text='用户消息内容，1-10000字符'
    )
    chat_history = MessageSerializer(many=True, required=False, allow_null=True)
    mode = serializers.CharField(
        default='default',
        max_length=50,
        help_text='对话模式标识'
    )
    use_tools = serializers.BooleanField(default=True, help_text='是否使用工具')
    use_advanced_tools = serializers.BooleanField(default=False, help_text='是否使用高级工具')
    use_web_search = serializers.BooleanField(default=False, help_text='是否启用联网搜索')
    streaming = serializers.BooleanField(default=False, help_text='是否流式输出')
    session_id = serializers.CharField(required=False, allow_null=True, max_length=100)
    attachment_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        allow_null=True,
        help_text='附件ID列表，用于基于文件内容回答问题'
    )

    def validate_message(self, value):
        """验证消息内容"""
        value = value.strip()
        if not value:
            raise serializers.ValidationError('消息内容不能为空或仅包含空格')
        if len(value) > 10000:
            raise serializers.ValidationError('消息内容不能超过10000个字符')
        return value

    def validate_mode(self, value):
        """验证模式标识"""
        allowed_modes = [
            'default', 'basic-agent', 'advanced-agent',
            'research-agent', 'rag-agent', 'deep-thinking',
            'deep-research', 'workflow', 'guarded',
        ]
        if value not in allowed_modes:
            raise serializers.ValidationError(f'不支持的模式: {value}')
        return value

    def validate_session_id(self, value):
        """验证会话ID格式"""
        if value:
            import re
            if not re.match(r'^[a-f0-9\-]{36}$', value):
                raise serializers.ValidationError('会话ID格式无效')
        return value

    def validate_chat_history(self, value):
        """验证聊天历史"""
        if value is not None and len(value) > 50:
            raise serializers.ValidationError('聊天历史记录不能超过50条')
        return value

    def validate_attachment_ids(self, value):
        """验证附件ID列表"""
        if value is not None and len(value) > 10:
            raise serializers.ValidationError('单次请求附件数量不能超过10个')
        return value

    def validate(self, attrs):
        """跨字段验证"""
        if attrs.get('streaming') and not attrs.get('message'):
            raise serializers.ValidationError({
                'message': '流式模式必须提供消息内容'
            })
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

class ChatAttachmentSerializer(serializers.ModelSerializer):
    """
    聊天附件序列化器
    """
    url = serializers.SerializerMethodField()
    
    class Meta:
        model = ChatAttachment
        fields = ['id', 'original_name', 'file_size', 'file_type', 'mime_type', 'url', 'created_at']
        read_only_fields = ['id', 'created_at']
    
    def get_url(self, obj):
        return obj.file.url if hasattr(obj.file, 'url') else ''


class ChatMessageSerializer(serializers.ModelSerializer):
    """
    聊天消息模型序列化器
    
    用途：消息的读取、创建、更新操作
    绑定模型：ChatMessage
    """
    attachments = ChatAttachmentSerializer(many=True, read_only=True)
    
    class Meta:
        model = ChatMessage
        fields = ['id', 'session', 'role', 'content', 'sources', 'plan',
                  'chain_of_thought', 'tool_calls', 'reasoning',
                  'suggestions', 'context', 'versions',
                  'current_version', 'attachments', 'created_at']
        read_only_fields = ['id', 'session', 'created_at']

    def validate_content(self, value):
        """验证内容字段"""
        if value and len(value) > 50000:
            raise serializers.ValidationError('消息内容不能超过50000个字符')
        return value

    def validate_role(self, value):
        """验证角色字段"""
        valid_roles = [choice[0] for choice in ChatMessage.ROLE_CHOICES]
        if value not in valid_roles:
            raise serializers.ValidationError(f'无效的角色: {value}')
        return value

    def validate_current_version(self, value):
        """验证版本索引"""
        if value < 0:
            raise serializers.ValidationError('版本索引不能为负数')
        return value


class ChatSessionListSerializer(serializers.ModelSerializer):
    """
    会话列表序列化器（轻量）
    
    用途：会话列表页展示，仅包含摘要信息
    绑定模型：ChatSession
    """
    message_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = ChatSession
        fields = ['id', 'session_id', 'title', 'mode', 'message_count',
                  'created_at', 'updated_at']
        read_only_fields = ['session_id', 'created_at', 'updated_at']

    def validate_title(self, value):
        if value and len(value.strip()) == 0:
            raise serializers.ValidationError('标题不能为空')
        if value and len(value) > 200:
            raise serializers.ValidationError('标题不能超过200个字符')
        return value.strip() if value else value


class ChatSessionDetailSerializer(serializers.ModelSerializer):
    """
    会话详情序列化器（完整）
    
    用途：会话详情页展示，包含完整消息列表
    绑定模型：ChatSession
    注意：messages 字段会触发额外查询，建议配合 prefetch_related('messages') 使用
    """
    messages = ChatMessageSerializer(many=True, read_only=True)
    message_count = serializers.SerializerMethodField()

    class Meta:
        model = ChatSession
        fields = ['id', 'session_id', 'title', 'mode', 'messages',
                  'message_count', 'created_at', 'updated_at']
        read_only_fields = ['session_id', 'created_at', 'updated_at']

    def get_message_count(self, obj):
        if hasattr(obj, '_message_count'):
            return obj._message_count
        return obj.messages.count()


class ChatSessionCreateSerializer(serializers.ModelSerializer):
    """
    会话创建序列化器
    
    用途：创建新会话时验证输入并生成实例
    绑定模型：ChatSession
    """
    class Meta:
        model = ChatSession
        fields = ['title', 'mode']

    def validate_title(self, value):
        if not value:
            value = '新对话'
        elif len(value.strip()) == 0:
            raise serializers.ValidationError('标题不能为空')
        elif len(value) > 200:
            raise serializers.ValidationError('标题不能超过200个字符')
        return value.strip()

    def validate_mode(self, value):
        allowed_modes = [
            'basic-agent', 'advanced-agent', 'research-agent',
            'rag-agent', 'deep-thinking', 'deep-research',
            'workflow', 'guarded',
        ]
        if value and value not in allowed_modes:
            raise serializers.ValidationError(f'不支持的模式: {value}')
        if value and len(value) > 50:
            raise serializers.ValidationError('模式标识不能超过50个字符')
        return value


class ChatSessionUpdateSerializer(serializers.ModelSerializer):
    """
    会话更新序列化器
    
    用途：更新会话标题等字段
    绑定模型：ChatSession
    仅允许修改 title 字段
    """
    class Meta:
        model = ChatSession
        fields = ['title']

    def validate_title(self, value):
        if value is not None:
            if len(value.strip()) == 0:
                raise serializers.ValidationError('标题不能为空')
            if len(value) > 200:
                raise serializers.ValidationError('标题不能超过200个字符')
            return value.strip()
        return value
