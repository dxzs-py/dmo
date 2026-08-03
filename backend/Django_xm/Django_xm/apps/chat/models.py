import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from Django_xm.apps.core.base_models import AuditModel


class MessageRole(models.TextChoices):
    USER = "user", "用户"
    ASSISTANT = "assistant", "助手"
    SYSTEM = "system", "系统"


class ChatMode(models.TextChoices):
    AGENT = "agent", "代理"
    DEEP_RESEARCH = "deep-research", "深度研究"


class ChatSession(AuditModel):
    session_id = models.CharField(max_length=100, unique=True, verbose_name="会话ID")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chat_sessions",
        db_index=True,
        verbose_name="用户",
    )
    title = models.CharField(max_length=200, default="新对话", verbose_name="会话标题")
    mode = models.CharField(max_length=50, choices=ChatMode.choices, default=ChatMode.AGENT, verbose_name="对话模式")
    selected_knowledge_base = models.CharField(max_length=200, blank=True, null=True, verbose_name="选中的知识库")
    selected_knowledge_bases = models.JSONField(default=list, blank=True, null=True, verbose_name="选中的知识库列表")

    class Meta:
        db_table = "chat_session"
        verbose_name = "聊天会话"
        verbose_name_plural = "聊天会话"
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["mode"]),
        ]

    def __str__(self):
        return f"ChatSession {self.session_id} - {self.title}"

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("chat:chat")

    def clean(self):
        super().clean()
        if self.title and len(self.title.strip()) == 0:
            raise ValidationError({"title": "会话标题不能为空"})
        if self.mode and len(self.mode) > 50:
            raise ValidationError({"mode": "模式标识不能超过50个字符"})

    def save(self, *args, **kwargs):
        if not self.session_id:
            self.session_id = str(uuid.uuid4())
        super().save(*args, **kwargs)


class ChatMessage(AuditModel):
    CONTENT_MAX_LENGTH = 50000

    session = models.ForeignKey(
        ChatSession, on_delete=models.CASCADE, related_name="messages", db_index=True, verbose_name="会话"
    )
    role = models.CharField(
        max_length=20, choices=MessageRole.choices, default=MessageRole.USER, db_index=True, verbose_name="角色"
    )
    content = models.TextField(blank=True, max_length=50000, verbose_name="内容")
    sources = models.JSONField(default=list, blank=True, null=True, verbose_name="来源")
    plan = models.JSONField(default=dict, blank=True, null=True, verbose_name="计划")
    chain_of_thought = models.JSONField(default=list, blank=True, null=True, verbose_name="思维链")
    tool_calls = models.JSONField(default=list, blank=True, verbose_name="工具调用")
    approval = models.JSONField(default=dict, blank=True, null=True, verbose_name="审批数据")
    reasoning = models.JSONField(default=dict, blank=True, null=True, verbose_name="推理")
    suggestions = models.JSONField(default=list, blank=True, null=True, verbose_name="建议问题")
    versions = models.JSONField(default=list, blank=True, null=True, verbose_name="消息版本")
    current_version = models.PositiveIntegerField(
        default=0, help_text="当前展示的版本索引，指向 versions 数组的位置", verbose_name="当前版本索引"
    )
    model = models.CharField(max_length=100, blank=True, null=True, verbose_name="使用的模型")
    token_count = models.PositiveIntegerField(default=0, verbose_name="Token 数量")
    token_detail = models.JSONField(default=dict, blank=True, verbose_name="Token 明细")
    response_time = models.FloatField(default=0, verbose_name="响应时间(秒)")
    research_task_id = models.CharField(
        max_length=100, blank=True, null=True, verbose_name="关联深度研究任务ID", db_index=True
    )
    is_streaming = models.BooleanField(
        default=False,
        verbose_name="是否正在流式输出",
        help_text="标记该消息是否正在进行流式输出，用于前端刷新后判断是否需要恢复",
    )

    class Meta:
        db_table = "chat_message"
        verbose_name = "聊天消息"
        verbose_name_plural = "聊天消息"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["session", "created_at"]),
            models.Index(fields=["role"]),
            models.Index(fields=["session", "role", "created_at"]),
        ]

    def __str__(self):
        content_preview = self.content[:50] if self.content else ""
        return f"{self.role}: {content_preview}..."

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("chat:chat")

    def clean(self):
        super().clean()
        if self.content and len(self.content) > self.CONTENT_MAX_LENGTH:
            raise ValidationError({"content": f"内容长度不能超过{self.CONTENT_MAX_LENGTH}个字符"})
        if self.role not in [choice[0] for choice in MessageRole.choices]:
            raise ValidationError({"role": "无效的角色类型"})
        if self.current_version < 0:
            raise ValidationError({"current_version": "版本索引不能为负数"})

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
