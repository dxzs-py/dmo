from rest_framework import serializers
from .models import WorkflowExecution

class WorkflowStartSerializer(serializers.Serializer):
    query = serializers.CharField(min_length=1)
    thread_id = serializers.CharField(required=False, allow_null=True)
    workflow_type = serializers.CharField(default='study_flow')

class WorkflowStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkflowExecution
        fields = '__all__'
