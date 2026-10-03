from rest_framework import serializers


class UserEventWriteSerializer(serializers.Serializer):
    event_type = serializers.CharField(max_length=50)
    event_category = serializers.CharField(max_length=20)
    session_id = serializers.CharField(max_length=100, required=False, default="")
    resource_id = serializers.CharField(max_length=100, required=False, default="")
    resource_type = serializers.CharField(max_length=50, required=False, default="")
    metadata = serializers.DictField(required=False, default=dict)
    duration_ms = serializers.IntegerField(required=False, allow_null=True)
    is_success = serializers.BooleanField(required=False, default=True)
    error_message = serializers.CharField(required=False, default="")


class PageViewSerializer(serializers.Serializer):
    path = serializers.CharField(max_length=500)
    title = serializers.CharField(max_length=200, required=False, default="")


class FeatureUseSerializer(serializers.Serializer):
    feature = serializers.CharField(max_length=100)
    metadata = serializers.DictField(required=False, default=dict)
