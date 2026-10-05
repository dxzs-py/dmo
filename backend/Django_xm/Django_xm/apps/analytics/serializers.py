from rest_framework import serializers

from .models import EventCategory, EventType


class UserEventWriteSerializer(serializers.Serializer):
    # 枚举内校验：此前用 CharField，前端硬编码的 event_type/category 一旦拼错即可
    # 静默写入"枚举外的新事件类型"，无任何报错（历史遗留值见 EventType.CROSS_KB_SEARCH
    # 注释）。改为 ChoiceField 后拼错立即 400，不再污染分析口径。
    # 注意：新增前端埋点事件时，必须先在 models.EventType / EventCategory 登记。
    event_type = serializers.ChoiceField(choices=EventType.choices)
    event_category = serializers.ChoiceField(choices=EventCategory.choices)
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
