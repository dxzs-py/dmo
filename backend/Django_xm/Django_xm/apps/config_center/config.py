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
    from Django_xm.apps.config_center import settings
    db_host = settings.db_host
"""

from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Optional, Any

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

    # ==================== 服务器配置 ====================
    server_host: str = Field(
        default="0.0.0.0",
        description="服务器监听地址"
    )

    server_port: int = Field(
        default=8000,
        ge=1,
        le=65535,
        description="服务器监听端口"
    )

    server_reload: bool = Field(
        default=True,
        description="开发模式热重载"
    )

    # ==================== 调试 / 环境 ====================
    debug: bool = Field(
        default=True,
        description="调试模式"
    )

    app_name: str = Field(
        default="LC-StudyLab",
        description="应用名称"
    )

    app_version: str = Field(
        default="1.0.0",
        description="应用版本"
    )

    # ==================== 日志配置 ====================
    log_level: str = Field(
        default="INFO",
        description="日志级别: DEBUG/INFO/WARNING/ERROR/CRITICAL"
    )

    log_file: str = Field(
        default="logs/app.log",
        description="日志文件路径"
    )

    log_rotation: str = Field(
        default="100 MB",
        description="日志轮转大小"
    )

    log_retention: str = Field(
        default="30 days",
        description="日志保留时间"
    )

    # ==================== 数据目录配置 ====================
    data_dir: str = Field(
        default="data",
        description="数据存储根目录"
    )

    data_documents_path: str = Field(
        default="data/documents",
        description="文档存储路径"
    )

    data_uploads_path: str = Field(
        default="data/uploads",
        description="上传文件路径"
    )

    # ==================== 数据库配置 ====================
    db_host: str = Field(
        default="127.0.0.1",
        description="数据库主机"
    )

    db_port: int = Field(
        default=3306,
        description="数据库端口"
    )

    db_name: str = Field(
        default="langchain_xm",
        description="数据库名称"
    )

    # ==================== Redis / Cache 配置 ====================
    redis_url: str = Field(
        default="redis://127.0.0.1:6379/1",
        description="默认 Redis URL (default cache)"
    )

    redis_chat_url: str = Field(
        default="redis://127.0.0.1:6379/2",
        description="Chat Session Redis URL"
    )

    redis_password: str = Field(
        default="",
        description="Redis 密码"
    )

    redis_default_timeout: int = Field(
        default=300,
        description="默认缓存超时(秒)"
    )

    redis_chat_timeout: int = Field(
        default=3600,
        description="Chat 缓存超时(秒)"
    )

    # ==================== Celery 配置 ====================
    celery_broker_url: str = Field(
        default="redis://127.0.0.1:6379/3",
        description="Celery Broker URL"
    )

    celery_result_backend: str = Field(
        default="redis://127.0.0.1:6379/4",
        description="Celery Result Backend URL"
    )

    celery_task_time_limit: int = Field(
        default=1800,
        ge=60,
        description="Celery 任务超时(秒)"
    )

    celery_worker_max_tasks_per_child: int = Field(
        default=1000,
        ge=1,
        description="Worker 最大任务数后重启"
    )

    # ==================== 安全配置 ====================
    secret_key: str = Field(
        default="",
        description="Django SECRET_KEY (必须通过环境变量设置)"
    )

    allowed_hosts: str = Field(
        default="localhost,127.0.0.1",
        description="ALLOWED_HOSTS (逗号分隔)"
    )

    cors_allowed_origins: str = Field(
        default="http://localhost:3000,http://localhost:8000,http://www.langchain.cn:8080",
        description="CORS 允许的源 (逗号分隔)"
    )

    csrf_trusted_origins: str = Field(
        default="http://localhost:3000,http://localhost:8000,http://www.langchain.cn:8080",
        description="CSRF TRUSTED_ORIGINS (逗号分隔)"
    )

    session_cookie_age: int = Field(
        default=604800,
        description="Session 过期时间(秒), 默认7天"
    )

    # ==================== JWT 配置 ====================
    jwt_access_token_lifetime_days: int = Field(
        default=3,
        description="Access Token 有效期(天，开发环境)"
    )

    jwt_access_token_lifetime_minutes: int = Field(
        default=30,
        description="Access Token 有效期(分钟，生产环境)"
    )

    jwt_refresh_token_lifetime_days: int = Field(
        default=7,
        description="Refresh Token 有效期(天)"
    )

    # ==================== 邮件配置 ====================
    email_backend: str = Field(
        default="django.core.mail.backends.console.EmailBackend",
        description="邮件后端"
    )

    default_from_email: str = Field(
        default="noreply@langchain.cn",
        description="默认发件人"
    )

    # ==================== 文件上传限制 ====================
    upload_max_memory_size_mb: int = Field(
        default=10,
        description="上传文件内存限制(MB)"
    )

    # ==================== 速率限制配置 ====================
    rate_limit_rpm: int = Field(
        default=60,
        ge=1,
        le=10000,
        description="每分钟最大请求数 (Requests Per Minute)"
    )

    rate_limit_rps: float = Field(
        default=1.0,
        ge=0.1,
        le=100.0,
        description="每秒最大请求数 (Requests Per Second)"
    )

    rate_limit_max_concurrency: int = Field(
        default=10,
        ge=1,
        le=100,
        description="最大并发请求数"
    )

    # ---- 校验方法 ----

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in valid:
            raise ValueError(f"无效的日志级别: {v}，可选: {valid}")
        return v.upper()

    # ---- 便捷属性 ----

    @property
    def is_production(self) -> bool:
        return not self.debug

    def ensure_data_dirs(self, base_dir: Path | None = None) -> list[Path]:
        root = base_dir or Path(self.data_dir).resolve()
        dirs = [
            root,
            root / "documents",
            root / "indexes",
            root / "uploads",
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)
        return dirs


# ==================== 单例管理 ====================

_settings_instance: ProjectSettings | None = None


def get_settings() -> ProjectSettings:
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = ProjectSettings()
    return _settings_instance


settings = get_settings()


# ==================== 日志工具 ====================

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, settings.log_level))
    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, settings.log_level))
    fmt = logging.Formatter("%(levelname)s %(asctime)s - %(name)s - %(message)s")
    console_handler.setFormatter(fmt)
    logger.addHandler(console_handler)
    logger.propagate = False
    return logger


def setup_loguru_logging() -> None:
    try:
        from loguru import logger as _logger
        import sys

        _logger.remove()

        _logger.add(
            sys.stderr,
            format=(
                "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
                "<level>{message}</level>"
            ),
            level=settings.log_level,
            colorize=True,
            backtrace=True,
            diagnose=True,
        )

        log_path = Path(settings.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        _logger.add(
            settings.log_file,
            format=(
                "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
                "{level: <8} | "
                "{name}:{function}:{line} | "
                "{message}"
            ),
            level=settings.log_level,
            rotation=settings.log_rotation,
            retention=settings.log_retention,
            compression="zip",
            backtrace=True,
            diagnose=True,
            enqueue=True,
        )
    except ImportError:
        pass
