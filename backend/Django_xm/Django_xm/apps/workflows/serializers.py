"""
序列化器模块
提供工作流相关的请求/响应序列化器
"""

from rest_framework import serializers
from .models import WorkflowExecution, WorkflowSession


class WorkflowStartSerializer(serializers.Serializer):
    """启动工作流请求"""
    user_question = serializers.CharField(
        min_length=1,
        required=False,
        help_text="用户的学习问题"
    )
    query = serializers.CharField(
        min_length=1,
        required=False,
        help_text="用户的学习问题 (别名)"
    )
    workflow_type = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="工作流类型"
    )
    thread_id = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        help_text="可选的线程ID"
    )

    def validate(self, data):
        if not data.get('user_question') and not data.get('query'):
            raise serializers.ValidationError({
                'user_question': '必须提供 user_question 或 query',
                'query': '必须提供 user_question 或 query'
            })
        return data


class WorkflowSubmitSerializer(serializers.Serializer):
    """提交答案请求"""
    thread_id = serializers.CharField(required=True)
    answers = serializers.DictField(
        child=serializers.CharField(allow_blank=True),
        required=True,
        help_text="用户答案，格式：{question_id: answer}"
    )


class WorkflowStatusSerializer(serializers.Serializer):
    """工作流状态"""
    thread_id = serializers.CharField()
    current_step = serializers.CharField()
    status = serializers.CharField()
    user_question = serializers.CharField(required=False, allow_blank=True)
    learning_plan = serializers.DictField(required=False, allow_null=True)
    quiz = serializers.DictField(required=False, allow_null=True)
    score = serializers.IntegerField(required=False, allow_null=True)
    score_details = serializers.DictField(required=False, allow_null=True)
    feedback = serializers.CharField(required=False, allow_blank=True)
    should_retry = serializers.BooleanField(required=False, default=False)
    created_at = serializers.CharField(required=False, allow_blank=True)
    updated_at = serializers.CharField(required=False, allow_blank=True)


class WorkflowResponseSerializer(serializers.Serializer):
    """工作流通用响应"""
    thread_id = serializers.CharField()
    status = serializers.CharField()
    message = serializers.CharField(required=False, allow_blank=True)
    current_step = serializers.CharField(required=False, allow_blank=True)
    learning_plan = serializers.DictField(required=False, allow_null=True)
    quiz = serializers.DictField(required=False, allow_null=True)
    score = serializers.IntegerField(required=False, allow_null=True)
    score_details = serializers.DictField(required=False, allow_null=True)
    feedback = serializers.CharField(required=False, allow_blank=True)
    should_retry = serializers.BooleanField(required=False, default=False)
    error = serializers.CharField(required=False, allow_blank=True)


class LearningPlanSerializer(serializers.Serializer):
    """学习计划序列化器"""
    topic = serializers.CharField()
    objectives = serializers.ListField(child=serializers.CharField())
    key_points = serializers.ListField(child=serializers.CharField())
    difficulty = serializers.CharField()
    estimated_time = serializers.IntegerField()


class QuizQuestionSerializer(serializers.Serializer):
    """练习题题目序列化器"""
    id = serializers.CharField()
    type = serializers.CharField()
    question = serializers.CharField()
    options = serializers.ListField(child=serializers.CharField(), required=False, allow_null=True)
    answer = serializers.CharField()
    explanation = serializers.CharField()
    points = serializers.IntegerField()


class QuizSerializer(serializers.Serializer):
    """练习题序列化器"""
    questions = QuizQuestionSerializer(many=True)
    total_points = serializers.IntegerField()
    time_limit = serializers.IntegerField(required=False)


class ScoreDetailSerializer(serializers.Serializer):
    """评分详情序列化器"""
    question_id = serializers.CharField()
    is_correct = serializers.BooleanField()
    points_earned = serializers.IntegerField()
    points_possible = serializers.IntegerField()
    feedback = serializers.CharField(required=False, allow_blank=True)


class RetrievedDocumentSerializer(serializers.Serializer):
    """检索文档序列化器"""
    content = serializers.CharField()
    metadata = serializers.DictField(required=False)
    relevance_score = serializers.FloatField(required=False)


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


class WorkflowSessionStatusSerializer(serializers.ModelSerializer):
    """工作流状态序列化器（简化版）"""
    class Meta:
        model = WorkflowSession
        fields = [
            'thread_id', 'current_step', 'created_at', 'updated_at',
            'status'
        ]