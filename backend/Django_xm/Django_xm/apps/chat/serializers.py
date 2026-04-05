from rest_framework import serializers
from .models import ChatSession, ChatMessage


class MessageSerializer(serializers.Serializer):
    role = serializers.CharField()
    content = serializers.CharField()


class ChatRequestSerializer(serializers.Serializer):
    message = serializers.CharField(min_length=1)
    chat_history = MessageSerializer(many=True, required=False, allow_null=True)
    mode = serializers.CharField(default='default')
    use_tools = serializers.BooleanField(default=True)
    use_advanced_tools = serializers.BooleanField(default=False)
    streaming = serializers.BooleanField(default=False)
    session_id = serializers.CharField(required=False, allow_null=True)


class ChatResponseSerializer(serializers.Serializer):
    message = serializers.CharField()
    mode = serializers.CharField()
    tools_used = serializers.ListField(child=serializers.CharField())
    success = serializers.BooleanField()
    error = serializers.CharField(required=False, allow_null=True)
    session_id = serializers.CharField(required=False, allow_null=True)


class ChatMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatMessage
        fields = ['id', 'session', 'role', 'content', 'sources', 'plan',
                  'chain_of_thought', 'tool_calls', 'reasoning',
                  'current_version', 'created_at']


class ChatSessionListSerializer(serializers.ModelSerializer):
    message_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = ChatSession
        fields = ['id', 'session_id', 'title', 'mode', 'message_count',
                  'created_at', 'updated_at']
        read_only_fields = ['session_id', 'created_at', 'updated_at']


class ChatSessionDetailSerializer(serializers.ModelSerializer):
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
    class Meta:
        model = ChatSession
        fields = ['title', 'mode']


class ChatSessionUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatSession
        fields = ['title']
