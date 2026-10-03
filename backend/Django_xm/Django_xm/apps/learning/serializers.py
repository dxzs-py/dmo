"""
序列化器模块
提供工作流相关的请求/响应序列化器
"""

from rest_framework import serializers

from .models import WorkflowAttempt, WorkflowQuestion, WorkflowQuestionStatus, WorkflowSession


class WorkflowStartSerializer(serializers.Serializer):
    """启动工作流请求"""

    user_question = serializers.CharField(min_length=1, required=False, help_text="用户的学习问题")
    query = serializers.CharField(min_length=1, required=False, help_text="用户的学习问题 (别名)")
    workflow_type = serializers.CharField(required=False, allow_blank=True, help_text="工作流类型")
    thread_id = serializers.CharField(required=False, allow_null=True, allow_blank=True, help_text="可选的线程ID")
    knowledge_base_ids = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=False,
        default=list,
        help_text="用户选择的知识库名称列表（原始名称，对应 KnowledgeBaseSelector 的 v-model）",
    )
    # ===== 用户运行时模型配置（与深度研究模块 ResearchStartSerializer 字段语义保持一致）=====
    use_deep_thinking = serializers.BooleanField(default=False, required=False, help_text="是否启用深度思考")
    use_web_search = serializers.BooleanField(default=False, required=False, help_text="是否启用网络查询")
    provider_id = serializers.CharField(required=False, allow_null=True, max_length=50, help_text="模型提供商 ID")
    model_name = serializers.CharField(required=False, allow_null=True, max_length=100, help_text="模型名称")
    temperature = serializers.FloatField(
        required=False, allow_null=True, min_value=0, max_value=2, help_text="生成温度"
    )
    max_tokens = serializers.IntegerField(required=False, allow_null=True, min_value=1, help_text="最大生成 token 数")
    special_params = serializers.DictField(
        required=False, allow_null=True, help_text="模型专属参数（如 thinking、reasoning_effort）"
    )

    def validate(self, data):
        if not data.get("user_question") and not data.get("query"):
            raise serializers.ValidationError(
                {"user_question": "必须提供 user_question 或 query", "query": "必须提供 user_question 或 query"}
            )
        return data


class WorkflowSubmitSerializer(serializers.Serializer):
    """提交答案请求"""

    thread_id = serializers.CharField(required=True)
    answers = serializers.DictField(
        child=serializers.CharField(allow_blank=True), required=True, help_text="用户答案，格式：{question_id: answer}"
    )


class WorkflowQuestionUpdateSerializer(serializers.Serializer):
    """修改单题答案请求"""

    user_answer = serializers.CharField(required=True, help_text="用户答案文本")


class WorkflowQuestionSerializer(serializers.ModelSerializer):
    """工作流题目序列化器

    安全策略：PENDING / ANSWERED（提交前或提交中）的题目不返回标准答案与解析，
    防止前端从 /questions/ 接口直接读取正确答案作弊；SCORED / LOCKED（已评分）
    状态才暴露答案字段供结果展示与练习历史使用。
    """

    class Meta:
        model = WorkflowQuestion
        fields = "__all__"

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance.status in (WorkflowQuestionStatus.PENDING, WorkflowQuestionStatus.ANSWERED):
            data.pop("correct_answer", None)
            data.pop("explanation", None)
            data.pop("is_correct", None)
            data.pop("points_earned", None)
        return data


class WorkflowAttemptSerializer(serializers.ModelSerializer):
    """工作流练习轮次序列化器"""

    class Meta:
        model = WorkflowAttempt
        fields = "__all__"


class WorkflowSessionSerializer(serializers.ModelSerializer):
    """工作流会话序列化器"""

    class Meta:
        model = WorkflowSession
        fields = [
            "id",
            "thread_id",
            "current_step",
            "status",
            "user_question",
            "learning_plan",
            "quiz",
            "user_answers",
            "score",
            "score_details",
            "feedback",
            "should_retry",
            "error_message",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "thread_id", "created_at", "updated_at"]
