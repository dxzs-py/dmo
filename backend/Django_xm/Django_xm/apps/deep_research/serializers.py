from rest_framework import serializers
from .models import ResearchTask

class ResearchStartSerializer(serializers.Serializer):
    query = serializers.CharField(min_length=1)
    task_id = serializers.CharField(required=False, allow_null=True)
    enable_web_search = serializers.BooleanField(default=True)
    enable_doc_analysis = serializers.BooleanField(default=False)

class ResearchTaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResearchTask
        fields = '__all__'
