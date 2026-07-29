"""统一审批数据模型。

持久化工具审批的完整生命周期，替代散落在 ChatMessage.approval JSON 字段
和 Redis List 中的数据。按 interrupt_id 唯一索引。
"""

from django.conf import settings
from django.db import models


class Approval(models.Model):
    """统一审批记录模型。

    持久化工具审批的完整生命周期，替代散落在 ChatMessage.approval JSON 字段
    和 Redis List 中的数据。按 interrupt_id 唯一索引。
    """

    # 审批来源
    SOURCE_CHAT = "chat"
    SOURCE_DEEP_RESEARCH = "deep_research"
    # SOURCE_LEARNING 当前未启用：learning 模块是纯学习评测工作流（StateGraph），
    # 无工具调用、无 agent，不接入 ApprovalMiddleware（架构不匹配）。
    # 枚举值保留以避免数据库迁移，若未来 learning 引入 agent 化改造可启用。
    SOURCE_LEARNING = "learning"
    SOURCE_CHOICES = [
        (SOURCE_CHAT, "Chat"),
        (SOURCE_DEEP_RESEARCH, "Deep Research"),
        (SOURCE_LEARNING, "Learning"),
    ]

    # 审批状态
    STATE_PENDING = "pending"
    STATE_PROCESSING = "processing"
    STATE_WAITING = "waiting"  # 已审批但同批次还有其他 pending（批量审批场景）
    STATE_APPROVED = "approved"
    STATE_REJECTED = "rejected"
    STATE_TIMEOUT = "timeout"
    STATE_CHOICES = [
        (STATE_PENDING, "Pending"),
        (STATE_PROCESSING, "Processing"),
        (STATE_WAITING, "Waiting"),
        (STATE_APPROVED, "Approved"),
        (STATE_REJECTED, "Rejected"),
        (STATE_TIMEOUT, "Timeout"),
    ]

    # 审批动作
    ACTION_CONFIRM = "confirm"
    ACTION_CONFIRM_WITH_INPUT = "confirm_with_input"

    interrupt_id = models.CharField(max_length=128, unique=True, db_index=True)
    source = models.CharField(max_length=32, choices=SOURCE_CHOICES)
    source_id = models.CharField(max_length=128, db_index=True)  # session_id 或 task_id
    chat_session_id = models.CharField(max_length=128, null=True, blank=True, db_index=True)
    tool_name = models.CharField(max_length=128)
    title = models.CharField(max_length=256, default="")
    description = models.TextField(default="")
    action = models.CharField(max_length=32, default=ACTION_CONFIRM)
    operation = models.TextField(blank=True, default="")
    danger_level = models.CharField(max_length=32, default="medium")
    parameters = models.JSONField(default=dict, blank=True)
    state = models.CharField(max_length=32, choices=STATE_CHOICES, default=STATE_PENDING)
    user_input = models.TextField(null=True, blank=True)
    # 审批归属用户（根本性越权修复：替代通过 chat_session/source_id 跨表反查）
    # 历史数据通过 00XX_add_user_field 迁移回填；剩余 NULL 数据走 _user_owns_approval 三路 fallback
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="owned_approvals",
        verbose_name="审批归属用户",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approvals",
        verbose_name="审批人",
    )
    extra = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="审批过期时间",
        help_text="创建时设置为 created_at + APPROVAL_TIMEOUT_SECONDS，供 Celery 任务判断",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["source", "source_id"]),
            models.Index(fields=["chat_session_id", "state"]),
            models.Index(fields=["state", "created_at"]),
            models.Index(fields=["state", "expires_at"]),
            # 越权修复：列表查询常按 (user, state) 过滤并按 created_at 倒序
            models.Index(fields=["user", "state", "created_at"]),
            # 按来源筛选待处理审批的复合索引（覆盖 source+state+created_at 查询路径）
            models.Index(
                fields=["source", "state", "created_at"],
                name="approval_src_state_created_idx",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.parameters is None:
            self.parameters = {}
        if self.extra is None:
            self.extra = {}
        super().save(*args, **kwargs)

    def __str__(self):
        return f"[{self.source}] {self.tool_name} ({self.interrupt_id[:8]}) - {self.state}"


class ApprovalOutboxEntry(models.Model):
    """审批事件发件箱（事务性事件投递保障，Phase C）。

    确保审批状态变更（DB save）与事件发布（Redis pub/sub）的最终一致性。
    采用"双写 + 补偿"模式：
    1. _persist_and_broadcast 中创建 outbox 条目 + 尝试直接发布
    2. 直接发布成功 → 标记 delivered（实时性不受影响）
    3. 直接发布失败 → 保持 pending，由 process_approval_outbox Celery 任务重试

    与纯异步发件箱的区别：保留直接发布的实时性，outbox 仅作补偿，
    避免引入 1s 轮询延迟影响审批事件的跨浏览器实时同步。
    """

    STATE_PENDING = "pending"
    STATE_DELIVERED = "delivered"
    STATE_FAILED = "failed"
    STATE_CHOICES = [
        (STATE_PENDING, "Pending"),
        (STATE_DELIVERED, "Delivered"),
        (STATE_FAILED, "Failed"),
    ]

    approval = models.ForeignKey(
        Approval,
        on_delete=models.CASCADE,
        related_name="outbox_entries",
        verbose_name="关联审批",
    )
    event_type = models.CharField(max_length=64, verbose_name="事件类型")
    payload = models.JSONField(default=dict, verbose_name="事件载荷")
    state = models.CharField(
        max_length=16,
        choices=STATE_CHOICES,
        default=STATE_PENDING,
        verbose_name="投递状态",
    )
    attempts = models.IntegerField(default=0, verbose_name="重试次数")
    max_attempts = models.IntegerField(default=3, verbose_name="最大重试次数")
    next_retry_at = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name="下次重试时间")
    created_at = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True, verbose_name="投递时间")
    error_message = models.TextField(blank=True, default="", verbose_name="错误信息")

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["state", "next_retry_at"]),
            models.Index(fields=["approval", "event_type"]),
        ]
        verbose_name = "审批发件箱条目"
        verbose_name_plural = "审批发件箱条目"

    def __str__(self):
        return f"Outbox[{self.event_type}] {self.approval.interrupt_id[:8]} - {self.state}"
