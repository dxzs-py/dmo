"""
统一配置管理模块（Single Source of Truth）

使用 Pydantic Settings 管理所有配置项，支持从环境变量和 .env 文件加载。
本模块是项目唯一的配置真相源，Django settings 从此处注入所需值。

优先级：环境变量 > .env 文件 > Pydantic 默认值

使用方式:
    from Django_xm.apps.ai_engine.config import settings
    api_key = settings.openai_api_key
"""

from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Optional, Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    应用配置类 - 项目唯一配置真相源

    所有配置项通过环境变量或 .env 文件设置，
    Pydantic 自动完成类型校验和转换。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ==================== OpenAI 配置 ====================
    openai_api_key: str = Field(
        default="",
        description="OpenAI API 密钥"
    )

    openai_api_base: str = Field(
        default="https://api.openai.com/v1",
        description="OpenAI API 基础 URL"
    )

    openai_model: str = Field(
        default="gpt-4o",
        description="默认 OpenAI 模型"
    )

    openai_temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="模型温度参数"
    )

    openai_max_tokens: Optional[int] = Field(
        default=None,
        description="最大生成 token 数"
    )

    openai_streaming: bool = Field(
        default=True,
        description="是否默认启用流式输出"
    )

    # ==================== Tavily 搜索配置 ====================
    tavily_api_key: str = Field(
        default="",
        description="Tavily 搜索 API 密钥"
    )

    tavily_max_results: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Tavily 最大返回结果数"
    )

    # ==================== 高德地图配置 ====================
    amap_key: str = Field(
        default="",
        description="高德地图 API 密钥"
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

    # ==================== Agent 配置 ====================
    agent_max_iterations: int = Field(
        default=15,
        ge=1,
        le=100,
        description="Agent 最大迭代次数"
    )

    agent_max_execution_time: Optional[float] = Field(
        default=None,
        description="Agent 最大执行时间(秒)"
    )

    # ==================== RAG / Embedding 配置 ====================
    embedding_model: str = Field(
        default="text-embedding-3-small",
        description="Embedding 模型名称"
    )

    embedding_batch_size: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Embedding 批处理大小"
    )

    chunk_size: int = Field(
        default=1000,
        ge=100,
        le=10000,
        description="文本分块大小(字符)"
    )

    chunk_overlap: int = Field(
        default=200,
        ge=0,
        le=1000,
        description="分块重叠大小(字符)"
    )

    vector_store_type: str = Field(
        default="faiss",
        description="向量库类型: faiss/inmemory/chroma"
    )

    vector_store_path: str = Field(
        default="data/indexes",
        description="向量库存储路径"
    )

    retriever_search_type: str = Field(
        default="similarity",
        description="检索类型: similarity/mmr/similarity_score_threshold"
    )

    retriever_k: int = Field(
        default=4,
        ge=1,
        le=20,
        description="检索返回文档数"
    )

    retriever_score_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="相似度阈值"
    )

    retriever_fetch_k: int = Field(
        default=20,
        ge=1,
        le=100,
        description="MMR 候选文档数"
    )

    rag_agent_max_iterations: int = Field(
        default=10,
        ge=1,
        le=50,
        description="RAG Agent 最大迭代数"
    )

    rag_agent_return_source_documents: bool = Field(
        default=True,
        description="是否返回来源文档"
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

    # ==================== 数据库配置提示 ====================
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

    # ==================== LangSmith 配置 ====================
    langsmith_api_key: str = Field(
        default="",
        description="LangSmith API 密钥"
    )

    langsmith_project: str = Field(
        default="langchain_xm",
        description="LangSmith 项目名称"
    )

    langsmith_endpoint: str = Field(
        default="https://api.smith.langchain.com",
        description="LangSmith API 端点"
    )

    langsmith_tracing: bool = Field(
        default=False,
        description="是否启用 LangSmith 追踪"
    )

    # ---- 校验方法 ----

    def validate_required_keys(self) -> None:
        """验证必需的配置项"""
        if not self.secret_key:
            raise ValueError("SECRET_KEY 未设置！请通过环境变量或 .env 文件配置。")
        if not self.openai_api_key and not self.debug:
            raise ValueError("OPENAI_API_KEY 未设置！非调试模式下为必需项。")

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in valid:
            raise ValueError(f"无效的日志级别: {v}，可选: {valid}")
        return v.upper()

    # ---- 便捷属性 ----

    @property
    def indexes_dir(self) -> str:
        return self.vector_store_path

    @property
    def documents_dir(self) -> str:
        return self.data_documents_path

    @property
    def uploads_dir(self) -> str:
        return self.data_uploads_path

    @property
    def is_production(self) -> bool:
        return not self.debug

    def get_openai_config(self) -> dict[str, Any]:
        """获取 OpenAI 配置字典"""
        config: dict[str, Any] = {
            "api_key": self.openai_api_key,
            "base_url": self.openai_api_base,
            "model": self.openai_model,
            "temperature": self.openai_temperature,
        }
        if self.openai_max_tokens is not None:
            config["max_tokens"] = self.openai_max_tokens
        return config

    def get_tavily_config(self) -> dict[str, Any]:
        """获取 Tavily 配置字典"""
        return {
            "api_key": self.tavily_api_key,
            "max_results": self.tavily_max_results,
        }

    def ensure_data_dirs(self, base_dir: Path | None = None) -> list[Path]:
        """
        确保数据目录存在并返回路径列表

        Args:
            base_dir: 项目根目录，None 则使用 data_dir 的相对路径解析
        """
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

_settings_instance: Settings | None = None


def get_settings() -> Settings:
    """延迟初始化单例，确保只创建一次"""
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance


def validate_settings() -> None:
    """验证必需配置项"""
    s = get_settings()
    try:
        s.validate_required_keys()
    except ValueError as e:
        if s.debug:
            import warnings
            warnings.warn(str(e), stacklevel=2)
        else:
            raise


settings = get_settings()


# ==================== 日志工具 ====================

def get_logger(name: str) -> logging.Logger:
    """
    获取标准 logging.Logger 实例

    Args:
        name: logger 名称，通常使用 __name__
    """
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
    """配置 loguru 日志系统（可选依赖）"""
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
