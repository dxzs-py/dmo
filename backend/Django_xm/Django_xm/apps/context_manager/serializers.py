from rest_framework import serializers


class ContextStatsSerializer(serializers.Serializer):
    user_id = serializers.IntegerField(allow_null=True)
    compression_enabled = serializers.BooleanField()
    knowledge_graph_enabled = serializers.BooleanField()
    cross_session_enabled = serializers.BooleanField()
    kg_entity_count = serializers.IntegerField(required=False, default=0)
    kg_relation_count = serializers.IntegerField(required=False, default=0)


class KnowledgeGraphClearSerializer(serializers.Serializer):
    confirm = serializers.BooleanField(help_text="确认清除知识图谱数据")


# ---------- Token Budget ----------


class TokenBudgetRequestSerializer(serializers.Serializer):
    session_id = serializers.CharField(required=False, help_text="会话ID")


class TokenBudgetResponseSerializer(serializers.Serializer):
    total_budget = serializers.IntegerField()
    used = serializers.IntegerField()
    remaining = serializers.IntegerField()
    utilization_percent = serializers.FloatField()


# ---------- Context Compress ----------


class ContextCompressRequestSerializer(serializers.Serializer):
    session_id = serializers.CharField(required=True, help_text="会话ID")


class ContextCompressResponseSerializer(serializers.Serializer):
    original_tokens = serializers.IntegerField()
    compressed_tokens = serializers.IntegerField()
    compression_ratio = serializers.FloatField()
    messages_removed = serializers.IntegerField()


# ---------- Knowledge Graph Detail ----------


class KnowledgeGraphDetailRequestSerializer(serializers.Serializer):
    session_id = serializers.CharField(required=False, help_text="会话ID")


class EntitySerializer(serializers.Serializer):
    name = serializers.CharField()
    entity_type = serializers.CharField()
    properties = serializers.DictField()
    confidence = serializers.FloatField()
    first_seen = serializers.FloatField()
    last_seen = serializers.FloatField()
    mention_count = serializers.IntegerField()


class RelationSerializer(serializers.Serializer):
    source = serializers.CharField()
    target = serializers.CharField()
    relation_type = serializers.CharField()
    properties = serializers.DictField()
    confidence = serializers.FloatField()
    created_at = serializers.FloatField()


class KnowledgeGraphDetailResponseSerializer(serializers.Serializer):
    entities = EntitySerializer(many=True)
    relations = RelationSerializer(many=True)
    entity_count = serializers.IntegerField()
    relation_count = serializers.IntegerField()
