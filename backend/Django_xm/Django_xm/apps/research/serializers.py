from rest_framework import serializers
from .models import ResearchTask
from Django_xm.apps.common.serializers import FileInfoSerializer


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
    knowledge_base_ids = serializers.ListField(
        child=serializers.CharField(),
        default=list,
        required=False,
        allow_empty=True,
        help_text="关联的知识库 ID 列表"
    )

    def validate(self, data):
        if data.get('enable_doc_analysis') and not data.get('knowledge_base_ids'):
            raise serializers.ValidationError(
                "启用文档分析时，必须选择至少一个知识库"
            )
        return data


class ResearchTaskSerializer(serializers.ModelSerializer):
    """研究任务序列化器"""
    class Meta:
        model = ResearchTask
        fields = [
            'id', 'task_id', 'query', 'status', 'final_report',
            'enable_web_search', 'enable_doc_analysis', 'knowledge_base_ids',
            'research_depth', 'error_message', 'celery_task_id',
            'created_by', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'task_id', 'created_at', 'updated_at',
                           'celery_task_id', 'error_message']


class ResearchResultSerializer(serializers.Serializer):
    """研究结果序列化器"""
    status = serializers.CharField()
    thread_id = serializers.CharField()
    query = serializers.CharField()
    final_report = serializers.CharField(required=False, allow_blank=True)
    plan = serializers.DictField(required=False, allow_null=True)
    steps_completed = serializers.DictField(required=False, allow_null=True)
    metadata = serializers.DictField(required=False, allow_null=True)

