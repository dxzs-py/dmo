"""
项目级配置中心（Single Source of Truth）

管理所有非 AI 相关的项目配置，包括：
- 服务器 / 调试 / 应用信息
- 数据库 / Redis / Celery
- 安全 / CORS / CSRF / JWT
- 日志 / 邮件 / 文件上传
- 速率限制
- 数据目录

AI 相关配置（LLM/Agent/RAG/Embedding/Guardrails/LangSmith 等）
仍在 Django_xm.apps.ai_engine.config 中管理。

优先级：环境变量 > .env 文件 > Pydantic 默认值

使用方式:
    from Django_xm.apps.core.config import settings
    db_host = settings.db_host
"""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProjectSettings(BaseSettings):
    """
    项目级配置类

    仅包含与 AI 引擎无关的通用配置项。
    AI 专属配置在 Django_xm.apps.ai_engine.config.AISettings 中管理。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ==================== 调试 / 环境 ====================
    debug: bool = Field(default=False, description=("调试模式，默认关闭。开发环境需在 .env 中显式设置 DEBUG=True"))

    app_name: str = Field(default="DX-Study-Agent", description="应用名称")

    app_version: str = Field(default="1.0.0", description="应用版本")

    # ==================== 日志配置 ====================
    log_level: str = Field(default="INFO", description="日志级别: DEBUG/INFO/WARNING/ERROR/CRITICAL")

    # ==================== 数据目录配置 ====================
    data_dir: str = Field(default="data", description="数据存储根目录")

    data_uploads_path: str = Field(default="data/uploads", description="上传文件路径")

    # ==================== 数据库配置 ====================
    db_host: str = Field(default="127.0.0.1", description="数据库主机")

    db_port: int = Field(default=5432, description="数据库端口")

    db_name: str = Field(default="langchain_xm", description="数据库名称")

    # ==================== Redis / Cache 配置 ====================
    redis_url: str = Field(default="redis://127.0.0.1:6379/1", description="默认 Redis URL (default cache)")

    redis_chat_url: str = Field(default="redis://127.0.0.1:6379/2", description="Chat Session Redis URL")

    redis_password: str = Field(default="", description="Redis 密码")

    redis_default_timeout: int = Field(default=300, description="默认缓存超时(秒)")

    redis_chat_timeout: int = Field(default=3600, description="Chat 缓存超时(秒)")

    # ==================== Celery 配置 ====================
    celery_broker_url: str = Field(default="redis://127.0.0.1:6379/3", description="Celery Broker URL")

    celery_result_backend: str = Field(default="redis://127.0.0.1:6379/4", description="Celery Result Backend URL")

    celery_task_time_limit: int = Field(default=1800, ge=60, description="Celery 任务超时(秒)")

    celery_worker_max_tasks_per_child: int = Field(default=1000, ge=1, description="Worker 最大任务数后重启")

    # ==================== 安全配置 ====================
    secret_key: str = Field(default="", description="Django SECRET_KEY (必须通过环境变量设置)")

    allowed_hosts: str = Field(default="localhost,127.0.0.1", description="ALLOWED_HOSTS (逗号分隔)")

    cors_allowed_origins: str = Field(
        default="",
        description=("CORS 允许的源 (逗号分隔)。默认空字符串，dev/prod 必须显式配置。生产环境若未配置则启动失败。"),
    )

    csrf_trusted_origins: str = Field(
        default="http://localhost:3000,http://localhost:8000,http://www.langchain.cn:8080",
        description="CSRF TRUSTED_ORIGINS (逗号分隔)",
    )

    session_cookie_age: int = Field(default=604800, description="Session 过期时间(秒), 默认7天")

    # ==================== JWT 配置 ====================
    jwt_access_token_lifetime_days: int = Field(default=3, description="Access Token 有效期(天，开发环境)")

    jwt_access_token_lifetime_minutes: int = Field(default=30, description="Access Token 有效期(分钟，生产环境)")

    jwt_refresh_token_lifetime_days: int = Field(default=7, description="Refresh Token 有效期(天)")

    # ==================== 邮件配置 ====================
    email_backend: str = Field(default="django.core.mail.backends.console.EmailBackend", description="邮件后端")

    default_from_email: str = Field(default="noreply@langchain.cn", description="默认发件人")

    # ==================== 文件上传限制 ====================
    upload_max_memory_size_mb: int = Field(default=10, description="上传文件内存限制(MB)")

    # ==================== 速率限制配置 ====================
    rate_limit_rpm: int = Field(default=60, ge=1, le=10000, description="每分钟最大请求数 (Requests Per Minute)")

    rate_limit_rps: float = Field(default=1.0, ge=0.1, le=100.0, description="每秒最大请求数 (Requests Per Second)")

    rate_limit_max_concurrency: int = Field(default=10, ge=1, le=100, description="最大并发请求数")

    # ==================== Docker 沙箱执行层配置 ====================
    # 原 settings/base.py 直读 os.environ 的 SANDBOX_* 六键收编（ARCH-05）：
    # env 键名与字段名一一对应（pydantic-settings case_sensitive=False 自动映射）。
    # 消费通道：base.py 据此装配 settings.SANDBOX_CONFIG，common/sandbox/executor.py 读取。
    sandbox_enabled: bool = Field(
        default=False, description="沙箱总开关：true 时 HIGH 级工具调用在 Docker 容器内执行（需本机 Docker）"
    )

    sandbox_image: str = Field(default="python:3.11-slim", description="沙箱 Docker 镜像")

    sandbox_memory: str = Field(default="512m", description="沙箱容器内存限制（docker --memory 格式）")

    sandbox_cpus: int = Field(default=1, ge=1, description="沙箱容器 CPU 核数限制")

    sandbox_network: str = Field(default="none", description="沙箱容器网络模式：bridge=可访问外网；none=完全断网隔离")

    sandbox_session_timeout: int = Field(default=3600, ge=1, description="沙箱会话超时（秒）")

    # ==================== 附件治理配置 ====================
    # 原 settings/base.py 直读 os.environ 的 ATTACHMENT_* 六键收编（ARCH-05）。
    # 消费通道：base.py 据此装配 settings.ATTACHMENT_* 属性，
    # apps/attachments/services/* 经 getattr(django_settings, ...) 读取。
    attachment_default_retention_days: int = Field(default=30, ge=1, description="附件默认保留天数")

    attachment_cleanup_hour: int = Field(default=3, ge=0, le=23, description="过期附件每日清理时刻（24 小时制）")

    attachment_storage_warning_threshold: float = Field(default=80, ge=0, le=100, description="存储用量警告阈值（百分比）")

    attachment_storage_critical_threshold: float = Field(
        default=95, ge=0, le=100, description="存储用量严重告警阈值（百分比）"
    )

    attachment_dedup_enabled: bool = Field(default=True, description="是否启用附件内容去重（content_hash）")

    attachment_max_total_size_mb: int = Field(default=5120, ge=1, description="全站附件总容量上限（MB，用户配额依据）")

    # ==================== MCP SSRF 豁免开关 ====================
    mcp_ssrf_allow_private_addresses: bool = Field(
        default=False,
        description=(
            "豁免 MCP SSRF 内网地址校验（common/net/guards.py SSRF_EXEMPT_SETTING）。"
            "开启后 MCP Server URL 与网页抓取目标允许指向内网/回环地址，存在 SSRF 风险，默认必须关闭。"
        ),
    )

    # ---- 校验方法 ----

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in valid:
            raise ValueError(f"无效的日志级别: {v}，可选: {valid}")
        return v.upper()


# ==================== 单例管理 ====================

_settings_instance: ProjectSettings | None = None


def get_settings() -> ProjectSettings:
    global _settings_instance  # noqa: PLW0603  # 单例缓存
    if _settings_instance is None:
        _settings_instance = ProjectSettings()
    return _settings_instance


settings = get_settings()
