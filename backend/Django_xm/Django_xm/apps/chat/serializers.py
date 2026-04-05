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
        fields = '__all__'


class ChatSessionSerializer(serializers.ModelSerializer):
    messages = ChatMessageSerializer(many=True, read_only=True)
    message_count = serializers.SerializerMethodField()

    class Meta:
        model = ChatSession
        fields = ['id', 'session_id', 'title', 'mode', 'created_at', 'updated_at', 'messages', 'message_count']
        read_only_fields = ['session_id', 'created_at', 'updated_at']

    def get_message_count(self, obj):
        return obj.messages.count()


class ChatSessionCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatSession
        fields = ['title', 'mode']


class ChatSessionUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatSession
        fields = ['title']
