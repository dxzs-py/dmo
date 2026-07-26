"""审批序列化器。"""

from rest_framework import serializers

from Django_xm.apps.approvals.models import Approval


class ApprovedBySerializer(serializers.Serializer):
    """审批人简要信息。"""
    id = serializers.IntegerField()
    username = serializers.CharField()


class ApprovalSerializer(serializers.ModelSerializer):
    """审批记录序列化器。"""

    approved_by = ApprovedBySerializer(read_only=True)

    class Meta:
        model = Approval
        fields = [
            'interrupt_id', 'source', 'source_id', 'chat_session_id',
            'tool_name', 'title', 'description', 'action', 'operation',
            'danger_level', 'parameters', 'state',
            'user_input', 'approved_by', 'extra', 'created_at', 'resolved_at',
        ]
        read_only_fields = ['created_at', 'resolved_at', 'approved_by']

    def to_internal_value(self, data):
        if isinstance(data, dict):
            for field_name, default in [('parameters', {}), ('extra', {})]:
                if field_name in data and data[field_name] is None:
                    data[field_name] = default
        return super().to_internal_value(data)
