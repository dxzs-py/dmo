from rest_framework import serializers
from Django_xm.apps.analytics.models import UserEvent, DailyAggregation, EventType, EventCategory


class UserEventWriteSerializer(serializers.Serializer):
    event_type = serializers.CharField(max_length=50)
    event_category = serializers.CharField(max_length=20)
    session_id = serializers.CharField(max_length=100, required=False, default='')
    resource_id = serializers.CharField(max_length=100, required=False, default='')
    resource_type = serializers.CharField(max_length=50, required=False, default='')
    metadata = serializers.DictField(required=False, default=dict)
    duration_ms = serializers.IntegerField(required=False, allow_null=True)
    is_success = serializers.BooleanField(required=False, default=True)
    error_message = serializers.CharField(required=False, default='')


class PageViewSerializer(serializers.Serializer):
    path = serializers.CharField(max_length=500)
    title = serializers.CharField(max_length=200, required=False, default='')


class FeatureUseSerializer(serializers.Serializer):
    feature = serializers.CharField(max_length=100)
    metadata = serializers.DictField(required=False, default=dict)


class RecentActivitySerializer(serializers.ModelSerializer):
    event_type_label = serializers.SerializerMethodField()
    event_category_label = serializers.SerializerMethodField()

    class Meta:
        model = UserEvent
        fields = [
            'id', 'event_type', 'event_type_label',
            'event_category', 'event_category_label',
            'resource_type', 'metadata',
            'is_success', 'duration_ms', 'created_at',
        ]

    def get_event_type_label(self, obj):
        return EventType(obj.event_type).label if obj.event_type in [t.value for t in EventType] else obj.event_type

    def get_event_category_label(self, obj):
        return EventCategory(obj.event_category).label if obj.event_category in [c.value for c in EventCategory] else obj.event_category


class DailyAggregationSerializer(serializers.ModelSerializer):
    class Meta:
        model = DailyAggregation
        fields = '__all__'
