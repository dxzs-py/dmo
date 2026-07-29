"""上下文管理数据模型

提供三个核心数据模型：
- ContextRule: 上下文规则（对应 Claude Code 的 CLAUDE.md 层级）
- AutoMemory: 自动记忆（Agent 自动写入的工作笔记）
- PromptCache: 提示缓存（前缀缓存 + 用户模板库）

所有模型均继承 BaseModel，统一软删除行为；并包含 user 外键，确保用户隔离。
"""

from django.conf import settings
from django.db import models

from Django_xm.apps.core.base_models import BaseModel


class ContextRule(BaseModel):
    """上下文规则（对应 Claude Code 的 CLAUDE.md 层级）

    支持 4 个作用域层级：
    - organization: 组织策略（全局，所有用户共享）
    - user_global: 用户全局规则（跨项目）
    - project: 项目规则（按 project_id 隔离）
    - local: 本地覆盖（最高优先级）

    路径作用域：path_patterns 字段支持 glob 模式，
    仅在读取匹配文件时加载对应规则（延迟加载）。

    继承 BaseModel 获得 created_at/updated_at/is_deleted/deleted_at 字段
    及软删除管理器（objects 过滤 is_deleted=False，all_objects 返回全部）。
    """

    SCOPE_CHOICES = [
        ("organization", "组织策略"),
        ("user_global", "用户全局规则"),
        ("project", "项目规则"),
        ("local", "本地覆盖"),
    ]
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="context_rules",
    )
    scope = models.CharField(max_length=20, choices=SCOPE_CHOICES, default="user_global")
    project_id = models.CharField(max_length=100, blank=True, default="")
    name = models.CharField(max_length=200)
    content = models.TextField()
    path_patterns = models.JSONField(
        default=list,
        blank=True,
        help_text="路径作用域 glob 模式列表，为空表示全局生效",
    )
    priority = models.IntegerField(
        default=0,
        help_text="同层级内优先级，数值越大越优先",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "context_rule"
        ordering = ["-priority", "-updated_at"]
        indexes = [
            models.Index(fields=["user", "scope", "is_active"], name="idx_ctx_rule_user_scope"),
            models.Index(fields=["user", "project_id"], name="idx_ctx_rule_project"),
        ]

    def __str__(self):
        return f"[{self.scope}] {self.name}"

    def save(self, *args, **kwargs):
        if self.path_patterns is None:
            self.path_patterns = []
        super().save(*args, **kwargs)


class AutoMemory(BaseModel):
    """自动记忆（Agent 自动写入的工作笔记）

    Agent 在研究过程中自动记录的关键信息：
    - 构建命令、调试洞察、用户偏好、使用模式等
    - 限制前 200 行 / 25KB，与 Claude Code 的 MEMORY.md 对齐
    - 按 last_accessed_at 排序，支持 LRU 淘汰

    继承 BaseModel 获得 created_at/updated_at/is_deleted/deleted_at 字段
    及软删除管理器（objects 过滤 is_deleted=False，all_objects 返回全部）。
    """

    SOURCE_CHOICES = [
        ("build_command", "构建命令"),
        ("debug_insight", "调试洞察"),
        ("preference", "用户偏好"),
        ("pattern", "使用模式"),
        ("other", "其他"),
    ]
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="auto_memories",
    )
    project_id = models.CharField(max_length=100, blank=True, default="")
    content = models.TextField()
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="other")
    relevance_tags = models.JSONField(default=list, blank=True)
    access_count = models.IntegerField(default=0)
    last_accessed_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "auto_memory"
        ordering = ["-last_accessed_at"]
        indexes = [
            models.Index(fields=["user", "project_id"], name="idx_auto_mem_project"),
            models.Index(fields=["user", "source"], name="idx_auto_mem_source"),
        ]

    def __str__(self):
        return f"[{self.source}] {self.content[:50]}"

    def save(self, *args, **kwargs):
        if self.relevance_tags is None:
            self.relevance_tags = []
        super().save(*args, **kwargs)


class PromptCache(BaseModel):
    """提示缓存（前缀缓存 + 用户模板库）

    三种缓存类型：
    - system_prefix: 系统前缀缓存（自动管理，Redis TTL 1h）
    - context_template: 上下文模板（带变量的模板，支持变量替换）
    - user_template: 用户模板（用户自定义的提示模板）

    变量定义格式：[{name, description, default}]

    继承 BaseModel 获得 created_at/updated_at/is_deleted/deleted_at 字段
    及软删除管理器（objects 过滤 is_deleted=False，all_objects 返回全部）。
    """

    CACHE_TYPE_CHOICES = [
        ("system_prefix", "系统前缀缓存"),
        ("context_template", "上下文模板"),
        ("user_template", "用户模板"),
    ]
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="prompt_caches",
    )
    name = models.CharField(max_length=200)
    cache_type = models.CharField(max_length=20, choices=CACHE_TYPE_CHOICES, default="user_template")
    content = models.TextField()
    variables = models.JSONField(
        default=list,
        blank=True,
        help_text="模板变量定义 [{name, description, default}]",
    )
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    token_count = models.IntegerField(default=0)
    usage_count = models.IntegerField(default=0)

    class Meta:
        db_table = "prompt_cache"
        ordering = ["sort_order", "-updated_at"]
        indexes = [
            models.Index(fields=["user", "cache_type", "is_active"], name="idx_prompt_cache_type"),
        ]

    def __str__(self):
        return f"[{self.cache_type}] {self.name}"

    def save(self, *args, **kwargs):
        if self.variables is None:
            self.variables = []
        super().save(*args, **kwargs)
