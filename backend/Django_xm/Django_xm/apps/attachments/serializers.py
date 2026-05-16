from rest_framework import serializers
from .models import ChatAttachment


class ChatAttachmentSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = ChatAttachment
        fields = ['id', 'original_name', 'file_size', 'file_type', 'mime_type', 'url', 'created_at']
        read_only_fields = ['id', 'created_at']

    def get_url(self, obj):
        return obj.file.url if hasattr(obj.file, 'url') else ''
