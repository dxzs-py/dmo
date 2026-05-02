from django.db import models
from django.conf import settings
from Django_xm.apps.core.base_models import BaseModel


class EventCategory(models.TextChoices):
    CHAT = 'chat', '智能聊天'
    RAG = 'rag', 'RAG检索'
    KNOWLEDGE = 'knowledge', '知识库'
    WORKFLOW = 'workflow', '学习工作流'
    RESEARCH = 'research', '深度研究'
    FILE = 'file', '文件操作'
    AUTH = 'auth', '认证'
    PAGE_VIEW = 'page_view', '页面浏览'
    API_CALL = 'api_call', 'API调用'
    SYSTEM = 'system', '系统'


class EventType(models.TextChoices):
    CHAT_MESSAGE_SEND = 'chat.message.send', '发送聊天消息'
    CHAT_MESSAGE_RECEIVE = 'chat.message.receive', '接收AI回复'
    CHAT_SESSION_CREATE = 'chat.session.create', '创建会话'
    CHAT_SESSION_DELETE = 'chat.session.delete', '删除会话'
    CHAT_MODE_SWITCH = 'chat.mode.switch', '切换对话模式'
    RAG_QUERY = 'rag.query', 'RAG检索查询'
    RAG_INDEX_CREATE = 'rag.index.create', '创建索引'
    RAG_INDEX_DELETE = 'rag.index.delete', '删除索引'
    RAG_DOCUMENT_UPLOAD = 'rag.document.upload', '上传文档'
    RAG_DOCUMENT_DELETE = 'rag.document.delete', '删除文档'
    KNOWLEDGE_BASE_ACCESS = 'knowledge.base.access', '访问知识库'
    WORKFLOW_START = 'workflow.start', '启动工作流'
    WORKFLOW_SUBMIT = 'workflow.submit', '提交工作流答案'
    WORKFLOW_COMPLETE = 'workflow.complete', '完成工作流'
    RESEARCH_START = 'research.start', '启动研究'
    RESEARCH_COMPLETE = 'research.complete', '完成研究'
    FILE_UPLOAD = 'file.upload', '文件上传'
    FILE_DOWNLOAD = 'file.download', '文件下载'
    FILE_DELETE = 'file.delete', '文件删除'
    AUTH_LOGIN = 'auth.login', '用户登录'
    AUTH_LOGOUT = 'auth.logout', '用户登出'
    AUTH_REGISTER = 'auth.register', '用户注册'
    PAGE_VIEW = 'page.view', '页面浏览'
    API_REQUEST = 'api.request', 'API请求'
    FEATURE_USE = 'feature.use', '功能使用'


class UserEvent(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='analytics_events',
        db_index=True,
        verbose_name='用户'
    )
    event_type = models.CharField(
        max_length=50,
        choices=EventType.choices,
        db_index=True,
        verbose_name='事件类型'
    )
    event_category = models.CharField(
        max_length=20,
        choices=EventCategory.choices,
        db_index=True,
        verbose_name='事件分类'
    )
    session_id = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name='会话ID'
    )
    resource_id = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name='资源ID'
    )
    resource_type = models.CharField(
        max_length=50,
        blank=True,
        default='',
        verbose_name='资源类型'
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        verbose_name='事件元数据'
    )
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        verbose_name='IP地址'
    )
    user_agent = models.TextField(
        blank=True,
        default='',
        verbose_name='用户代理'
    )
    duration_ms = models.IntegerField(
        null=True,
        blank=True,
        verbose_name='耗时(毫秒)'
    )
    is_success = models.BooleanField(
        default=True,
        verbose_name='是否成功'
    )
    error_message = models.TextField(
        blank=True,
        default='',
        verbose_name='错误信息'
    )

    class Meta:
        db_table = 'analytics_user_event'
        verbose_name = '用户事件'
        verbose_name_plural = '用户事件'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['event_category', '-created_at']),
            models.Index(fields=['event_type', '-created_at']),
            models.Index(fields=['created_at']),
            models.Index(fields=['event_category', 'event_type']),
        ]

    def __str__(self):
        return f"UserEvent({self.event_type}, user={self.user_id})"


class DailyAggregation(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='analytics_daily_aggregations',
        verbose_name='用户'
    )
    date = models.DateField(
        db_index=True,
        verbose_name='日期'
    )
    chat_sessions = models.PositiveIntegerField(default=0, verbose_name='聊天会话数')
    chat_messages = models.PositiveIntegerField(default=0, verbose_name='聊天消息数')
    chat_tokens = models.PositiveIntegerField(default=0, verbose_name='聊天Token数')
    chat_cost = models.DecimalField(max_digits=12, decimal_places=6, default=0, verbose_name='聊天成本')
    rag_queries = models.PositiveIntegerField(default=0, verbose_name='RAG查询数')
    documents_uploaded = models.PositiveIntegerField(default=0, verbose_name='上传文档数')
    documents_deleted = models.PositiveIntegerField(default=0, verbose_name='删除文档数')
    workflow_started = models.PositiveIntegerField(default=0, verbose_name='启动工作流数')
    workflow_completed = models.PositiveIntegerField(default=0, verbose_name='完成工作流数')
    research_started = models.PositiveIntegerField(default=0, verbose_name='启动研究数')
    research_completed = models.PositiveIntegerField(default=0, verbose_name='完成研究数')
    file_uploads = models.PositiveIntegerField(default=0, verbose_name='文件上传数')
    file_downloads = models.PositiveIntegerField(default=0, verbose_name='文件下载数')
    page_views = models.PositiveIntegerField(default=0, verbose_name='页面浏览数')
    api_requests = models.PositiveIntegerField(default=0, verbose_name='API请求数')
    api_errors = models.PositiveIntegerField(default=0, verbose_name='API错误数')
    avg_response_time_ms = models.FloatField(default=0, verbose_name='平均响应时间(ms)')
    login_count = models.PositiveIntegerField(default=0, verbose_name='登录次数')
    feature_usage = models.JSONField(default=dict, blank=True, verbose_name='功能使用详情')

    class Meta:
        db_table = 'analytics_daily_aggregation'
        verbose_name = '每日统计汇总'
        verbose_name_plural = '每日统计汇总'
        unique_together = ('user', 'date')
        indexes = [
            models.Index(fields=['date']),
            models.Index(fields=['user', '-date']),
        ]

    def __str__(self):
        return f"DailyAggregation({self.user_id}, {self.date})"
