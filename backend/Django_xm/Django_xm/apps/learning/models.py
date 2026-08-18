from django.db import models

from Django_xm.apps.core.base_models import AuditModel


class WorkflowExecutionStatus(models.TextChoices):
    PENDING = "pending", "待执行"
    RUNNING = "running", "执行中"
    COMPLETED = "completed", "已完成"
    FAILED = "failed", "失败"


class WorkflowSessionStatus(models.TextChoices):
    RUNNING = "running", "执行中"
    WAITING_FOR_ANSWERS = "waiting_for_answers", "等待答案"
    RETRY = "retry", "重试"
    COMPLETED = "completed", "已完成"
    FAILED = "failed", "失败"


class WorkflowQuestionStatus(models.TextChoices):
    """工作流题目状态"""

    PENDING = "pending", "待答"
    ANSWERED = "answered", "已答"
    SCORED = "scored", "已评分"
    LOCKED = "locked", "已锁定"


class WorkflowQuestionType(models.TextChoices):
    """工作流题目类型"""

    MULTIPLE_CHOICE = "multiple_choice", "选择题"
    FILL_BLANK = "fill_blank", "填空题"
    SHORT_ANSWER = "short_answer", "简答题"


class WorkflowExecution(AuditModel):
    thread_id = models.CharField(max_length=100, unique=True, verbose_name="线程 ID")
    workflow_type = models.CharField(max_length=50, db_index=True, verbose_name="工作流类型")
    query = models.TextField(verbose_name="查询内容")
    status = models.CharField(
        max_length=20,
        choices=WorkflowExecutionStatus.choices,
        default=WorkflowExecutionStatus.PENDING,
        db_index=True,
        verbose_name="状态",
    )
    result = models.JSONField(null=True, blank=True, verbose_name="执行结果")

    class Meta:
        db_table = "workflow_execution"
        verbose_name = "工作流执行"
        verbose_name_plural = "工作流执行"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["workflow_type", "status", "-created_at"]),
        ]

    def __str__(self):
        return f"WorkflowExecution({self.thread_id}, {self.status})"

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("learning:status", kwargs={"thread_id": self.thread_id})


class WorkflowSession(AuditModel):
    thread_id = models.CharField(max_length=100, unique=True, verbose_name="线程 ID")
    user_question = models.TextField(verbose_name="用户问题")
    status = models.CharField(
        max_length=20,
        choices=WorkflowSessionStatus.choices,
        default=WorkflowSessionStatus.RUNNING,
        db_index=True,
        verbose_name="状态",
    )
    current_step = models.CharField(max_length=50, blank=True, default="", verbose_name="当前步骤")
    learning_plan = models.JSONField(null=True, blank=True, verbose_name="学习计划")
    quiz = models.JSONField(null=True, blank=True, verbose_name="练习题")
    user_answers = models.JSONField(null=True, blank=True, verbose_name="用户答案")
    score = models.IntegerField(null=True, blank=True, verbose_name="得分")
    score_details = models.JSONField(null=True, blank=True, verbose_name="评分详情")
    feedback = models.TextField(null=True, blank=True, verbose_name="反馈信息")
    should_retry = models.BooleanField(default=False, verbose_name="是否重试")
    error_message = models.TextField(null=True, blank=True, verbose_name="错误信息")
    model = models.CharField(max_length=100, blank=True, null=True, verbose_name="使用的模型")
    token_count = models.PositiveIntegerField(default=0, verbose_name="Token 数量")
    token_detail = models.JSONField(default=dict, blank=True, verbose_name="Token 明细")
    response_time = models.FloatField(default=0, verbose_name="响应时间(秒)")
    retry_count = models.IntegerField(default=0, verbose_name="重试次数")
    phase = models.CharField(max_length=20, default="planner", verbose_name="当前阶段")
    root_thread_id = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        db_index=True,
        verbose_name="根工作流ID",
    )

    # ===== 用户运行时配置（与 ResearchTask 字段语义保持一致）=====
    knowledge_base_ids = models.JSONField(blank=True, default=list, verbose_name="关联知识库ID列表")
    provider_id = models.CharField(max_length=50, null=True, blank=True, verbose_name="模型提供商ID")
    model_name = models.CharField(max_length=100, null=True, blank=True, verbose_name="用户选择的模型名称")
    temperature = models.FloatField(null=True, blank=True, verbose_name="温度参数")
    max_tokens = models.IntegerField(null=True, blank=True, verbose_name="最大生成token数")
    special_params = models.JSONField(blank=True, default=dict, verbose_name="提供商专属参数")
    enable_deep_thinking = models.BooleanField(default=False, verbose_name="是否启用深度思考")
    use_web_search = models.BooleanField(default=False, verbose_name="是否启用网络查询")

    class Meta:
        db_table = "workflow_session"
        verbose_name = "工作流会话"
        verbose_name_plural = "工作流会话"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
        ]

    def save(self, *args, **kwargs):
        if self.knowledge_base_ids is None:
            self.knowledge_base_ids = []
        if self.special_params is None:
            self.special_params = {}
        super().save(*args, **kwargs)

    def __str__(self):
        return f"WorkflowSession({self.thread_id}, {self.status})"

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("learning:status", kwargs={"thread_id": self.thread_id})


class WorkflowQuestion(models.Model):
    """工作流题目

    记录每一轮练习中的单道题目，包含题目内容、标准答案、用户作答与评分结果。
    通过 attempt_index 与 question_index 定位题目在练习中的位置。
    """

    session = models.ForeignKey(
        WorkflowSession,
        on_delete=models.CASCADE,
        related_name="questions",
        verbose_name="所属会话",
    )
    question_id = models.CharField(max_length=50, verbose_name="题目 ID", help_text="如 q1_r1 表示第1轮第1题")
    attempt_index = models.IntegerField(default=0, verbose_name="第几轮练习")
    question_index = models.IntegerField(default=0, verbose_name="该轮第几题")
    type = models.CharField(
        max_length=20,
        choices=WorkflowQuestionType.choices,
        default=WorkflowQuestionType.MULTIPLE_CHOICE,
        verbose_name="题目类型",
    )
    question = models.TextField(verbose_name="题目内容")
    options = models.JSONField(null=True, blank=True, verbose_name="选项列表")
    correct_answer = models.TextField(verbose_name="标准答案")
    explanation = models.TextField(null=True, blank=True, verbose_name="答案解析")
    points = models.IntegerField(default=0, verbose_name="分值")
    user_answer = models.TextField(null=True, blank=True, verbose_name="用户答案")
    is_correct = models.BooleanField(null=True, default=None, verbose_name="是否正确")
    points_earned = models.IntegerField(null=True, blank=True, verbose_name="得分")
    status = models.CharField(
        max_length=20,
        choices=WorkflowQuestionStatus.choices,
        default=WorkflowQuestionStatus.PENDING,
        verbose_name="题目状态",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    scored_at = models.DateTimeField(null=True, blank=True, verbose_name="评分时间")
    modified_at = models.DateTimeField(auto_now=True, verbose_name="修改时间")

    class Meta:
        db_table = "workflow_question"
        verbose_name = "工作流题目"
        verbose_name_plural = "工作流题目"
        ordering = ["attempt_index", "question_index"]
        unique_together = [["session", "question_id"]]
        indexes = [
            models.Index(fields=["session", "attempt_index"]),
        ]

    def __str__(self) -> str:
        return f"WorkflowQuestion({self.question_id}, attempt={self.attempt_index})"


class WorkflowAttempt(models.Model):
    """工作流练习轮次

    记录一次完整练习轮次的执行信息，包括该轮的 thread_id、总分与反馈。
    一个会话可包含多个轮次，用于支持重试练习场景。
    """

    session = models.ForeignKey(
        WorkflowSession,
        on_delete=models.CASCADE,
        related_name="attempts",
        verbose_name="所属会话",
    )
    attempt_index = models.IntegerField(default=0, verbose_name="第几轮")
    thread_id = models.CharField(max_length=100, verbose_name="该轮执行的 thread_id")
    total_score = models.IntegerField(null=True, blank=True, verbose_name="该轮总分")
    feedback = models.TextField(null=True, blank=True, verbose_name="反馈")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        db_table = "workflow_attempt"
        verbose_name = "工作流练习轮次"
        verbose_name_plural = "工作流练习轮次"
        ordering = ["attempt_index"]
        unique_together = [["session", "attempt_index"]]

    def __str__(self) -> str:
        return f"WorkflowAttempt(session={self.session_id}, index={self.attempt_index})"
