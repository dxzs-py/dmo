"""
公共序列化器

跨 app 共用的序列化器定义，避免循环依赖。
"""

from rest_framework import serializers


class FileInfoSerializer(serializers.Serializer):
    name = serializers.CharField()
    relative_path = serializers.CharField()
    size = serializers.IntegerField()
    size_formatted = serializers.CharField()
    created_at = serializers.CharField()
    modified_at = serializers.CharField()
    file_type = serializers.CharField()
    task_id = serializers.CharField(allow_null=True)
    extension = serializers.CharField()
