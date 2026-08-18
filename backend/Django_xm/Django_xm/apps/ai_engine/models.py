"""
AI 引擎数据模型

SystemConfig: 系统配置键值对，持久化 AI 模型选择等设置
"""

import logging

from django.db import models

logger = logging.getLogger(__name__)

# ─── SystemConfig 缓存 ─────────────────────────────────────────────
_config_cache: dict = {}


def invalidate_system_config_cache(key: str | None = None):
    """清除 SystemConfig 缓存，key=None 时清除全部"""
    if key is None:
        _config_cache.clear()
    else:
        _config_cache.pop(key, None)


def warmup_system_config_cache():
    """预加载 SystemConfig 缓存，应在应用启动时（同步上下文）调用"""
    try:
        for obj in SystemConfig.objects.all():
            _config_cache[obj.key] = obj.value
        logger.debug(f"SystemConfig 缓存预热完成，共 {len(_config_cache)} 项")
    except Exception:
        logger.exception("SystemConfig 缓存预热失败（非致命）")


def _is_async_context() -> bool:
    """检测当前是否在异步上下文中"""
    try:
        import asyncio

        return asyncio.get_running_loop() is not None
    except RuntimeError:
        return False


class SystemConfig(models.Model):
    """系统配置键值对，持久化 AI 模型选择等设置

    预定义 key:
    - default_chat_model: {"provider_id": "openai", "model_name": "gpt-4o-mini"}
    - helper_model: {"provider_id": "", "model_name": ""}
    - embedding_provider: {"provider_id": "openai"}
    """

    key = models.CharField(max_length=100, unique=True, db_index=True, verbose_name="配置键")
    value = models.JSONField(default=dict, verbose_name="配置值")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = "ai_engine_system_config"
        verbose_name = "系统配置"
        verbose_name_plural = "系统配置"

    def __str__(self):
        return f"{self.key}={self.value}"

    @classmethod
    def get_value(cls, key: str, default=None):
        """读取配置值，不存在则返回 default

        异步安全：优先走缓存，异步上下文中不触发 ORM。
        """
        if key in _config_cache:
            return _config_cache[key]
        # 异步上下文中不能直接访问 ORM
        if _is_async_context():
            logger.debug(f"异步上下文中读取 SystemConfig({key})，缓存未命中，返回默认值")
            return default
        try:
            obj = cls.objects.get(key=key)
            _config_cache[key] = obj.value
            return obj.value
        except cls.DoesNotExist:
            return default

    @classmethod
    def set_value(cls, key: str, value: dict) -> "SystemConfig":
        """写入配置值，存在则更新，不存在则创建。value 为 None 时删除记录"""
        if value is None:
            cls.objects.filter(key=key).delete()
            _config_cache.pop(key, None)
            return None
        obj, _created = cls.objects.update_or_create(
            key=key,
            defaults={"value": value},
        )
        _config_cache[key] = value
        return obj


class AIProvider(models.Model):
    """LLM 提供商配置（数据库驱动，替代 config.py 中的 MODEL_REGISTRY）"""

    provider_id = models.CharField(max_length=50, unique=True, db_index=True, verbose_name="提供商ID")
    provider = models.CharField(
        max_length=50,
        verbose_name="LangChain provider类型",
        help_text="传给 init_chat_model 的 model_provider 值，如 openai/anthropic/ollama/deepseek/groq",
    )
    label = models.CharField(max_length=100, verbose_name="显示名称")
    icon = models.CharField(max_length=10, default="", blank=True, verbose_name="图标")
    default_model = models.CharField(max_length=100, default="", verbose_name="默认模型名")
    api_key_env = models.CharField(
        max_length=100,
        default="",
        blank=True,
        null=True,
        verbose_name="API Key 环境变量名",
        help_text="如 OPENAI_API_KEY，本地 provider 留空",
    )
    api_key_attr = models.CharField(
        max_length=100,
        default="",
        blank=True,
        null=True,
        verbose_name="API Key settings 属性名",
        help_text="如 openai_api_key，本地 provider 留空",
    )
    base_url_attr = models.CharField(
        max_length=100, default="", blank=True, null=True, verbose_name="Base URL settings 属性名"
    )
    model_attr = models.CharField(
        max_length=100, default="", blank=True, null=True, verbose_name="模型名 settings 属性名"
    )
    special_params = models.JSONField(
        default=dict, blank=True, verbose_name="特殊参数配置", help_text="DeepSeek thinking/reasoning_effort 等"
    )
    presets = models.JSONField(default=dict, blank=True, verbose_name="预设配置")
    is_enabled = models.BooleanField(default=True, verbose_name="是否启用")
    sort_order = models.IntegerField(default=0, verbose_name="排序权重", help_text="值越小越靠前")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = "ai_engine_provider"
        verbose_name = "LLM 提供商"
        verbose_name_plural = "LLM 提供商"
        ordering = ["sort_order", "provider_id"]

    def __str__(self):
        return f"{self.label} ({self.provider_id})"


class AIModel(models.Model):
    """LLM 模型配置（属于某个 AIProvider）"""

    provider = models.ForeignKey(AIProvider, on_delete=models.CASCADE, related_name="models", verbose_name="所属提供商")
    name = models.CharField(max_length=100, verbose_name="模型名称", help_text="如 gpt-4o-mini, deepseek-v4-flash")
    capabilities = models.JSONField(
        default=list, blank=True, verbose_name="能力列表", help_text="如 ['tool_calling', 'streaming', 'deep_thinking']"
    )
    is_enabled = models.BooleanField(default=True, verbose_name="是否启用")
    sort_order = models.IntegerField(default=0, verbose_name="排序权重")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        db_table = "ai_engine_model"
        verbose_name = "LLM 模型"
        verbose_name_plural = "LLM 模型"
        ordering = ["provider", "sort_order", "name"]
        unique_together = [("provider", "name")]

    def __str__(self):
        return f"{self.provider.label}/{self.name}"


class EmbeddingProviderConfig(models.Model):
    """Embedding 提供商配置（关联 AIProvider，属于同一 Provider 体系）

    一个 Provider 可以挂多个 Embedding 配置（每条配置对应一个具体的 embedding 模型）。
    这样 Ollama 之类的 Provider 可以同时支持 bge-m3、Qwen3-VL-Embedding 等多个 embedding。
    """

    provider = models.ForeignKey(
        AIProvider,
        on_delete=models.CASCADE,
        related_name="embedding_configs",
        verbose_name="所属 LLM 提供商",
        help_text="Embedding 与 LLM 属于同一个 Provider，如 OpenAI 既提供 LLM 也提供 Embedding",
    )
    name = models.CharField(
        max_length=100,
        verbose_name="配置名称",
        help_text="便于在 Admin 列表中识别，如 'Ollama - bge-m3'、'Ollama - Qwen3-VL-Embedding'",
    )
    default_model = models.CharField(
        max_length=200,
        verbose_name="默认模型名",
        help_text="Ollama 用 'bge-m3'；OpenAI 用 'text-embedding-3-small'；多模态用 'MedAIBase/Qwen3-VL-Embedding:2b'",
    )
    factory_path = models.CharField(
        max_length=200,
        verbose_name="工厂函数路径",
        help_text="创建 Embedding 实例的 Python 函数路径，系统通过 importlib 动态调用。创建后不可修改。",
    )
    dimension = models.IntegerField(
        default=0,
        verbose_name="输出维度",
        help_text=(
            "模型实际输出维度。仅 MRL 模型（nomic-embed-text / qwen3-embedding / "
            "embeddinggemma 等）可调整；非 MRL 模型此字段由原生最大维度锁定，不可修改。"
        ),
    )
    native_max_dimension = models.IntegerField(
        default=0,
        verbose_name="原生最大维度",
        help_text=(
            "模型不传 dimensions 时的输出维度（如 nomic-embed-text 768、bge-m3 1024、"
            "Qwen3-VL-Embedding-2B 2048）。≤ 0 表示未设置。"
        ),
    )
    min_dimension = models.IntegerField(
        default=0,
        verbose_name="最小支持维度",
        help_text="MRL 截断下限，0 表示不支持截断（如 bge-m3 填 0；nomic-embed-text 填 64）。",
    )
    supported_params = models.JSONField(
        default=list, blank=True, verbose_name="支持的参数列表", help_text="如 ['batch_size', 'model']"
    )
    is_enabled = models.BooleanField(default=True, verbose_name="是否启用")
    sort_order = models.IntegerField(default=0, verbose_name="排序权重")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = "ai_engine_embedding_provider"
        verbose_name = "Embedding 配置"
        verbose_name_plural = "Embedding 配置"
        ordering = ["sort_order", "id"]

    def __str__(self):
        if self.provider and self.name:
            return f"{self.provider.label} - {self.name} ({self.provider.provider_id})"
        if self.provider:
            return f"{self.provider.label} Embedding ({self.provider.provider_id})"
        return "Embedding (未关联 Provider)"

    @property
    def provider_id(self):
        return self.provider.provider_id if self.provider else None

    @property
    def label(self):
        if self.name:
            return f"{self.provider.label} - {self.name}" if self.provider else self.name
        return f"{self.provider.label} Embedding" if self.provider else ""

    @property
    def key_attr(self):
        return self.provider.api_key_attr if self.provider else None


class SubAgentStatus(models.TextChoices):
    """子 Agent 全局统一状态枚举（单一权威，SubAgentRuntime 全链路复用）。

    四状态闭环，不引入无生产者的 queued（当前无子 Agent 任务调度器）。
    """

    RUNNING = "running", "执行中"
    COMPLETED = "completed", "已完成"
    FAILED = "failed", "执行失败"
    INTERRUPTED_PENDING_USER_INPUT = "interrupted_pending_user_input", "等待你的确认"


class SubAgentInstance(models.Model):
    """子 Agent 实例元数据（UI 渲染的唯一数据源）。

    只保存实例元数据（thread_id/父子关系/状态/中断信息/结果预览），
    不存 graph/prompt/业务配置；业务配置由上层（spawn 工具）传入。
    checkpoint 由 LangGraph checkpointer 独立持久化（thread_id 为唯一键）。
    """

    thread_id = models.CharField(max_length=200, unique=True, db_index=True, verbose_name="子代理 thread_id")
    parent_thread_id = models.CharField(
        max_length=200, db_index=True, verbose_name="父线程 thread_id", help_text="会话删除时按此字段遍历回收子代理"
    )
    status = models.CharField(
        max_length=50, choices=SubAgentStatus.choices, default=SubAgentStatus.RUNNING, verbose_name="状态"
    )
    pending_interrupt_info = models.JSONField(
        null=True,
        blank=True,
        verbose_name="中断信息",
        help_text=(
            "interrupt 审批 payload（批量结构：interrupt_type/interrupt_id/"
            "requests:[{tool_call_id/tool_name/args/risk_level/reason}]）"
        ),
    )
    result_preview = models.TextField(blank=True, default="", verbose_name="结果预览")
    metadata = models.JSONField(default=dict, blank=True, verbose_name="元数据")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = "ai_engine_subagent_instance"
        verbose_name = "子代理实例"
        verbose_name_plural = "子代理实例"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.thread_id} ({self.status})"
