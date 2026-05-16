"""
上下文管理子应用配置

独立于 ai_engine.config，管理上下文管理相关的所有配置项。
支持从环境变量和 .env 文件加载。

使用方式:
    from Django_xm.apps.context_manager.config import context_settings
    threshold = context_settings.compression_threshold_ratio
"""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ContextManagerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    compression_enabled: bool = Field(
        default=True,
        description="是否启用上下文自动压缩"
    )

    compression_strategy: str = Field(
        default="hybrid",
        description="压缩策略: summary/sliding_window/hybrid"
    )

    compression_threshold_ratio: float = Field(
        default=0.8,
        ge=0.5,
        le=0.95,
        description="压缩触发阈值（占模型 token 限制的比例）"
    )

    compression_keep_recent: int = Field(
        default=6,
        ge=2,
        le=50,
        description="压缩时保留的最近消息数"
    )

    compression_summary_max_length: int = Field(
        default=1500,
        ge=200,
        le=5000,
        description="压缩摘要最大长度"
    )

    kg_enabled: bool = Field(
        default=True,
        description="是否启用知识图谱上下文管理"
    )

    kg_max_hops: int = Field(
        default=2,
        ge=1,
        le=5,
        description="知识图谱检索最大跳数"
    )

    kg_max_entities: int = Field(
        default=20,
        ge=5,
        le=100,
        description="知识图谱检索最大实体数"
    )

    cross_session_enabled: bool = Field(
        default=True,
        description="是否启用跨会话上下文复用"
    )

    cross_session_max_context_length: int = Field(
        default=2000,
        ge=500,
        le=10000,
        description="跨会话上下文最大长度"
    )


context_settings = ContextManagerSettings()


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    return logger
