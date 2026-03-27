from rest_framework import serializers
from .models import DocumentIndex, Document

class DocumentIndexSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentIndex
        fields = '__all__'

class DocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = '__all__'

class RagQuerySerializer(serializers.Serializer):
    index_name = serializers.CharField()
    query = serializers.CharField(min_length=1)
    k = serializers.IntegerField(default=4, required=False)
    use_rag_agent = serializers.BooleanField(default=True, required=False)

class RagResponseSerializer(serializers.Serializer):
    answer = serializers.CharField()
    sources = serializers.ListField(child=serializers.DictField())
    success = serializers.BooleanField()
    error = serializers.CharField(required=False, allow_null=True)
