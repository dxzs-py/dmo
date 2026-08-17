from django.core.exceptions import ValidationError
from django.db import models

from Django_xm.apps.core.base_models import AuditModel


class ResearchTaskStatus(models.TextChoices):
    PENDING = "pending", "待执行"
    AWAITING_APPROVAL = "awaiting_approval", "等待审批"
    RUNNING = "running", "执行中"
    COMPLETED = "completed", "已完成"
    FAILED = "failed", "失败"


class ResearchDepth(models.TextChoices):
    BASIC = "basic", "基础"
    STANDARD = "standard", "标准"
    COMPREHENSIVE = "comprehensive", "综合"


class ResearchTask(AuditModel):
    task_id = models.CharField(max_length=100, unique=True, verbose_name="任务ID")
    query = models.TextField(verbose_name="研究主题")
    status = models.CharField(
        max_length=20,
        choices=ResearchTaskStatus.choices,
        default=ResearchTaskStatus.PENDING,
        verbose_name="状态",
        db_index=True,
    )
    final_report = models.TextField(blank=True, verbose_name="最终报告")
    enable_web_search = models.BooleanField(default=True, verbose_name="启用网络搜索")
    enable_doc_analysis = models.BooleanField(default=False, verbose_name="启用文档分析")
    knowledge_base_ids = models.JSONField(default=list, blank=True, verbose_name="关联知识库ID列表")
    session_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name="关联会话ID",
        db_index=True,
    )
    research_depth = models.CharField(
        max_length=20, choices=ResearchDepth.choices, default=ResearchDepth.STANDARD, verbose_name="研究深度"
    )
    error_message = models.TextField(blank=True, null=True, verbose_name="错误信息")
    model = models.CharField(max_length=100, blank=True, null=True, verbose_name="使用的模型")
    token_count = models.PositiveIntegerField(default=0, verbose_name="Token 数量")
    token_detail = models.JSONField(default=dict, blank=True, verbose_name="Token 明细")
    response_time = models.FloatField(default=0, verbose_name="响应时间(秒)")
    parent_task = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="child_tasks",
        verbose_name="父级研究任务",
    )
    version = models.IntegerField(default=1, verbose_name="版本号")
    use_mcp = models.BooleanField(default=False, verbose_name="启用MCP")
    selected_mcp_servers = models.JSONField(default=list, blank=True, verbose_name="选中的MCP服务器")
    selected_tools = models.JSONField(default=list, blank=True, verbose_name="选中的工具")
    tool_calls = models.JSONField(
        default=list, blank=True, verbose_name="工具调用历史",
        help_text="数组结构，每个元素为工具调用详情对象，与 ChatMessage.tool_calls 格式一致"
    )
    subagent_contents = models.JSONField(
        default=dict, blank=True, null=True, verbose_name="子代理正文累计",
        help_text="字典结构，key 为 subagent_thread_id，value 为 {content, reasoning_content}，与 ChatMessage.subagent_contents 格式一致"
    )

    class Meta:
        db_table = "research_task"
        verbose_name = "研究任务"
        verbose_name_plural = "研究任务"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["created_by", "status"]),
        ]

    def __str__(self):
        return f"ResearchTask({self.task_id}, {self.status})"

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("research:status", kwargs={"task_id": self.task_id})

    def clean(self):
        super().clean()
        if self.query and len(self.query.strip()) == 0:
            raise ValidationError({"query": "研究主题不能为空"})
        if self.query and len(self.query) > 10000:
            raise ValidationError({"query": "研究主题不能超过10000个字符"})
        if self.error_message and len(self.error_message) > 5000:
            raise ValidationError({"error_message": "错误信息不能超过5000个字符"})

    def save(self, *args, **kwargs):
        if not self.task_id:
            import uuid

            self.task_id = str(uuid.uuid4())
        if self.query:
            self.query = self.query[:10000]
        super().save(*args, **kwargs)

    @property
    def version_chain(self):
        chain = []
        current = self
        while current is not None:
            chain.insert(
                0,
                {
                    "task_id": current.task_id,
                    "query": current.query[:80],
                    "version": current.version,
                    "status": current.status,
                    "created_at": current.created_at.isoformat() if current.created_at else None,
                },
            )
            current = current.parent_task
        return chain
