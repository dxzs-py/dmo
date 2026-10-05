from django.conf import settings
from django.db import models

from Django_xm.apps.core.base_models import BaseModel


class EventCategory(models.TextChoices):
    CHAT = "chat", "智能聊天"
    RAG = "rag", "RAG检索"
    KNOWLEDGE = "knowledge", "知识库"
    WORKFLOW = "workflow", "学习工作流"
    RESEARCH = "research", "深度研究"
    FILE = "file", "文件操作"
    AUTH = "auth", "认证"
    PAGE_VIEW = "page_view", "页面浏览"
    API_CALL = "api_call", "API调用"
    SYSTEM = "system", "系统"


class EventType(models.TextChoices):
    """分析模块的行为事件类型（落库到 UserEvent.event_type 的取值白名单）。

    ⚠️ 命名陷阱（已实证造成误判，改动前必读）：
    本类与 `Django_xm.common.event_schema.EventType` **同名但完全无关**——
    后者是实时事件总线（WebSocket/SSE 推送）的事件名，本类是数据库埋点的事件类型。
    两者成员名高度近似且容易张冠李戴：
      - 本类 `WORKFLOW_COMPLETE = "workflow.complete"`（不带 D）
      - event_schema 的 `WORKFLOW_COMPLETED = "workflow_completed"`（带 D）
    因此在任一模块引用 `EventType` 时，务必先确认 import 来源；
    统计"某成员有无写入点"时也不要只按成员名子串检索（会互相掩盖）。

    **已登记但无任何写入点的成员（13 个，全项目穷举确认）**：
    chat.session.delete / chat.mode.switch / rag.query / rag.index.delete /
    knowledge.base.access / workflow.submit / workflow.complete /
    research.complete / file.download / file.delete /
    auth.login / auth.logout / auth.register

    保留它们是作为埋点 taxonomy（后续接入时无需重新设计枚举），但**不要误以为
    这些事件已在被采集**——查询它们只会得到 0 行。对应行为目前由中间件以
    `api.request`（事件类型）+ 推断出的业务分类间接覆盖。若某天要接入，
    需补信号/视图写入，并同步更新本说明。
    """

    CHAT_MESSAGE_SEND = "chat.message.send", "发送聊天消息"
    CHAT_MESSAGE_RECEIVE = "chat.message.receive", "接收AI回复"
    CHAT_SESSION_CREATE = "chat.session.create", "创建会话"
    CHAT_SESSION_DELETE = "chat.session.delete", "删除会话"
    CHAT_MODE_SWITCH = "chat.mode.switch", "切换对话模式"
    RAG_QUERY = "rag.query", "RAG检索查询"
    RAG_INDEX_CREATE = "rag.index.create", "创建索引"
    RAG_INDEX_DELETE = "rag.index.delete", "删除索引"
    RAG_DOCUMENT_UPLOAD = "rag.document.upload", "上传文档"
    RAG_DOCUMENT_DELETE = "rag.document.delete", "删除文档"
    KNOWLEDGE_BASE_ACCESS = "knowledge.base.access", "访问知识库"
    # 前端 KnowledgeBaseView 跨库检索埋点实际上报的原始值。此前它一直游离于枚举
    # 之外（写入侧是 CharField 而非 ChoiceField，无法拦截），导致功能使用榜的
    # label 查表回退、把英文标识符 cross_kb_search 直接显示给用户。
    CROSS_KB_SEARCH = "cross_kb_search", "跨库检索"
    WORKFLOW_START = "workflow.start", "启动工作流"
    WORKFLOW_SUBMIT = "workflow.submit", "提交工作流答案"
    WORKFLOW_COMPLETE = "workflow.complete", "完成工作流"
    RESEARCH_START = "research.start", "启动研究"
    RESEARCH_COMPLETE = "research.complete", "完成研究"
    FILE_UPLOAD = "file.upload", "文件上传"
    FILE_DOWNLOAD = "file.download", "文件下载"
    FILE_DELETE = "file.delete", "文件删除"
    AUTH_LOGIN = "auth.login", "用户登录"
    AUTH_LOGOUT = "auth.logout", "用户登出"
    AUTH_REGISTER = "auth.register", "用户注册"
    PAGE_VIEW = "page.view", "页面浏览"
    API_REQUEST = "api.request", "API请求"
    FEATURE_USE = "feature.use", "功能使用"


class UserEvent(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="analytics_events",
        db_index=False,
        verbose_name="用户",
    )
    event_type = models.CharField(max_length=50, choices=EventType.choices, verbose_name="事件类型")
    event_category = models.CharField(
        max_length=20, choices=EventCategory.choices, verbose_name="事件分类"
    )
    session_id = models.CharField(max_length=100, blank=True, default="", verbose_name="会话ID")
    resource_id = models.CharField(max_length=100, blank=True, default="", verbose_name="资源ID")
    resource_type = models.CharField(max_length=50, blank=True, default="", verbose_name="资源类型")
    metadata = models.JSONField(default=dict, blank=True, verbose_name="事件元数据")
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name="IP地址")
    user_agent = models.TextField(blank=True, default="", verbose_name="用户代理")
    duration_ms = models.IntegerField(null=True, blank=True, verbose_name="耗时(毫秒)")
    is_success = models.BooleanField(default=True, verbose_name="是否成功")
    error_message = models.TextField(blank=True, default="", verbose_name="错误信息")

    class Meta:
        db_table = "analytics_user_event"
        verbose_name = "用户事件"
        verbose_name_plural = "用户事件"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["event_category", "-created_at"]),
            models.Index(fields=["event_type", "-created_at"]),
            models.Index(fields=["event_category", "event_type"]),
        ]

    def __str__(self):
        return f"UserEvent({self.event_type}, user={self.user_id})"


# ApiDailyStat.path_stats 单行保留的路径数上限（按当日计数降序，超出置 path_stats_truncated）。
# 取 50 的依据见 migrations/0007_backfill_apidailystat.py 顶部注释（路径内嵌资源 ID，
# 基数无界：单用户单日去重路径中位数 574、最大 12,393，长尾全是一次性资源详情路径；
# Top-50 对当日请求量覆盖率均值 87.6%）。注意该迁移内有一份**冻结副本**，修改此处
# 不会影响历史迁移行为。
API_STAT_MAX_PATHS_PER_DAY = 50


class ApiDailyStat(BaseModel):
    """按 (user, date) 聚合的 API 调用统计——`api.request` 明细的指标承载者。

    存在理由：中间件对每个已认证 `/api/` 请求都会记一行 `UserEvent`，实测该类型
    占全表 97%（217,481/223,782），且 Top 路径全是元数据/配置类 GET（无分析价值）。
    明细收敛后（成功的 GET 不再落明细），仪表盘的 `api_requests` / `api_errors` /
    平均耗时 / 错误率改由本表承载，历史值通过数据迁移回填。

    设计要点：
    - 粒度为 user × date（非 user × date × path 的独立行）：行数 = 用户数 × 天数，
      体量可忽略；路径维度收进 `path_stats` JSON，保留"Top 路径"分析能力。
    - `duration_sum_ms` + `duration_count` 配对使用，相除得平均耗时；只存和无法还原均值。
    - 写入必须用 `F()` 表达式原子累加，**不可**"先读后写再 save"，否则并发丢计数。
    - 保留期独立于 UserEvent 明细（默认 365 天），因本表体量小、可长期回溯。
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="analytics_api_daily_stats",
        db_index=False,
        verbose_name="用户",
    )
    date = models.DateField(verbose_name="日期")
    api_requests = models.PositiveIntegerField(default=0, verbose_name="请求数")
    api_errors = models.PositiveIntegerField(default=0, verbose_name="错误数")
    duration_sum_ms = models.BigIntegerField(default=0, verbose_name="耗时总和(毫秒)")
    duration_count = models.PositiveIntegerField(default=0, verbose_name="耗时样本数")
    # 形如 {"/api/v1/chat/sessions/": {"count": 12, "errors": 1, "duration_sum": 3450}}
    path_stats = models.JSONField(default=dict, blank=True, verbose_name="路径维度统计")
    path_stats_truncated = models.BooleanField(default=False, verbose_name="路径统计是否被截断")

    class Meta:
        db_table = "analytics_api_daily_stat"
        verbose_name = "API每日统计"
        verbose_name_plural = "API每日统计"
        # 幂等键：聚合写入以 (user, date) 做 upsert，天然防止同日重复建行。
        unique_together = ("user", "date")
        indexes = [
            # 说明：不额外建 (user, -date) 索引——unique_together 已在 PostgreSQL 上
            # 生成 (user_id, date) 唯一索引，足以服务"某用户按日期区间/排序"查询
            # （单列第二键的方向升降序不改变可用性），再建一个 DESC 索引纯属写放大。
            # date 单列索引用于保留期清理任务的全表日期扫描。
            models.Index(fields=["-date"]),
        ]

    def __str__(self):
        return f"ApiDailyStat({self.user_id}, {self.date})"
