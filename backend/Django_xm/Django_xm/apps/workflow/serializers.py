from rest_framework import serializers
from .models import WorkflowExecution, WorkflowSession


class WorkflowStartSerializer(serializers.Serializer):
    """工作流启动请求序列化器"""
    user_question = serializers.CharField(
        min_length=1,
        required=True,
        help_text="用户的学习问题"
    )
    thread_id = serializers.CharField(
        required=False,
        allow_null=True,
        help_text="可选的线程 ID，不提供则自动生成"
    )


class WorkflowSubmitSerializer(serializers.Serializer):
    """提交答案请求序列化器"""
    thread_id = serializers.CharField(required=True)
    answers = serializers.DictField(
        child=serializers.CharField(),
        required=True,
        help_text="用户答案，格式：{question_id: answer}"
    )


class WorkflowStatusSerializer(serializers.ModelSerializer):
    """工作流状态序列化器"""
    class Meta:
        model = WorkflowSession
        fields = [
            'thread_id', 'current_step', 'created_at', 'updated_at',
            'status', 'state'
        ]
        extra_kwargs = {
            'state': {'required': False}
        }


class WorkflowResponseSerializer(serializers.Serializer):
    """工作流响应序列化器（通用）"""
    thread_id = serializers.CharField()
    status = serializers.CharField()
    current_step = serializers.CharField(required=False, allow_blank=True)
    learning_plan = serializers.DictField(required=False, allow_null=True)
    quiz = serializers.DictField(required=False, allow_null=True)
    score = serializers.IntegerField(required=False, allow_null=True)
    score_details = serializers.DictField(required=False, allow_null=True)
    feedback = serializers.CharField(required=False, allow_blank=True)
    should_retry = serializers.BooleanField(default=False)
    message = serializers.CharField()


class WorkflowExecutionSerializer(serializers.ModelSerializer):
    """工作流执行记录序列化器"""
    class Meta:
        model = WorkflowExecution
        fields = '__all__'


class WorkflowSessionSerializer(serializers.ModelSerializer):
    """工作流会话序列化器"""
    class Meta:
        model = WorkflowSession
        fields = '__all__'
