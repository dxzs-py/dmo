from rest_framework import serializers
from .models import ResearchTask


class ResearchStartSerializer(serializers.Serializer):
    """深度研究启动请求序列化器"""
    query = serializers.CharField(
        min_length=1,
        required=True,
        help_text="研究问题"
    )
    thread_id = serializers.CharField(
        required=False,
        allow_null=True,
        help_text="自定义线程 ID（可选）"
    )
    task_id = serializers.CharField(
        required=False,
        allow_null=True,
        help_text="任务 ID（已废弃，使用 thread_id）"
    )
    research_depth = serializers.CharField(
        default='standard',
        help_text="研究深度：basic, standard, comprehensive"
    )
    enable_web_search = serializers.BooleanField(
        default=True,
        required=False,
        help_text="是否启用网络搜索"
    )
    enable_doc_analysis = serializers.BooleanField(
        default=False,
        required=False,
        help_text="是否启用文档分析"
    )


class ResearchTaskSerializer(serializers.ModelSerializer):
    """研究任务序列化器"""
    class Meta:
        model = ResearchTask
        fields = '__all__'


class ResearchResultSerializer(serializers.Serializer):
    """研究结果序列化器"""
    status = serializers.CharField()
    thread_id = serializers.CharField()
    query = serializers.CharField()
    final_report = serializers.CharField(required=False, allow_blank=True)
    plan = serializers.DictField(required=False, allow_null=True)
    steps_completed = serializers.DictField(required=False, allow_null=True)
    metadata = serializers.DictField(required=False, allow_null=True)
