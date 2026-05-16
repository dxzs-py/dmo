"""
AI 引擎配置模块

管理所有 AI 相关配置项，包括：
- LLM 提供商（OpenAI / Anthropic / DeepSeek / Groq / 百度千帆）
- Agent / RAG / Embedding / Vector Store
- Guardrails / Checkpointer / Store / Summarization
- Agent Cache / LangSmith
- Tavily / 高德地图

项目级配置（DB/Redis/Celery/安全/日志/服务器等）
在 Django_xm.apps.config_center.config 中管理。

使用方式:
    from Django_xm.apps.ai_engine.config import settings
    api_key = settings.openai_api_key
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Any

from pydantic import Field, model_validator

from Django_xm.apps.config_center.config import (
    ProjectSettings,
    get_logger as _get_logger,
    setup_loguru_logging as _setup_loguru_logging,
)


class Settings(ProjectSettings):
    """
    AI 引擎配置类

    继承 ProjectSettings，在项目级配置基础上添加 AI 专属配置项。
    """

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
        le=1.0,
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

    # ==================== Anthropic 配置 ====================
    anthropic_api_key: str = Field(
        default="",
        description="Anthropic API 密钥"
    )

    # ==================== DeepSeek 配置 ====================
    deepseek_api_key: str = Field(
        default="",
        description="DeepSeek API 密钥"
    )

    deepseek_api_base: str = Field(
        default="https://api.deepseek.com",
        description="DeepSeek API 基础 URL"
    )

    deepseek_model: str = Field(
        default="deepseek-v4-flash",
        description="DeepSeek 模型名称"
    )

    # ==================== Groq 配置 ====================
    groq_api_key: str = Field(
        default="",
        description="Groq API 密钥"
    )

    groq_api_base: str = Field(
        default="https://api.groq.com",
        description="Groq API 基础 URL"
    )

    groq_model: str = Field(
        default="llama-3.3-70b-versatile",
        description="Groq 模型名称"
    )

    # ==================== 百度千帆 配置 ====================
    baidu_qianfan_api_key: str = Field(
        default="",
        description="百度千帆 API 密钥"
    )

    baidu_qianfan_api_base: str = Field(
        default="https://qianfan.baidubce.com/v2",
        description="百度千帆 API 基础 URL"
    )

    baidu_qianfan_model: str = Field(
        default="ernie-3.5-8k",
        description="百度千帆模型名称"
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
        default="chroma",
        description="向量库类型: chroma/faiss/inmemory/milvus（推荐 chroma，支持持久化和增量更新）"
    )

    vector_store_path: str = Field(
        default="data/indexes",
        description="向量库存储路径"
    )

    chroma_persist_directory: str = Field(
        default="data/chroma_db",
        description="Chroma 持久化目录"
    )

    chroma_collection_name: str = Field(
        default="langchain_xm",
        description="Chroma 默认集合名称"
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

    # ==================== Checkpointer 配置 ====================
    checkpointer_backend: str = Field(
        default="sqlite",
        description="Checkpointer 后端: sqlite/memory/postgres"
    )

    # ==================== Store 配置 ====================
    store_enabled: bool = Field(
        default=False,
        description="是否自动注入 Store（长期记忆）到 Agent"
    )

    store_backend: str = Field(
        default="memory",
        description="Store 后端: memory/postgres"
    )

    # ==================== Summarization 配置 ====================
    summarization_trigger_tokens: int = Field(
        default=4000,
        ge=500,
        le=100000,
        description="SummarizationMiddleware 触发摘要的 token 阈值"
    )

    summarization_keep_messages: int = Field(
        default=20,
        ge=2,
        le=100,
        description="SummarizationMiddleware 保留的最近消息数"
    )

    # ==================== Agent Cache 配置 ====================
    agent_cache_enabled: bool = Field(
        default=False,
        description="是否自动注入 Agent 级别缓存（InMemoryCache）"
    )

    llm_cache_enabled: bool = Field(
        default=False,
        description="是否启用全局 LLM Cache（langchain_core.llm_cache）"
    )

    llm_cache_type: str = Field(
        default="memory",
        description="LLM Cache 类型: memory/semantic"
    )

    # ==================== Guardrails 配置 ====================
    guardrails_enabled: bool = Field(
        default=False,
        description="是否全局启用 Guardrails Middleware"
    )

    guardrails_strict_mode: bool = Field(
        default=False,
        description="Guardrails 严格模式（验证失败直接抛异常）"
    )

    guardrails_enable_pii: bool = Field(
        default=False,
        description="是否启用 PII 检测与脱敏"
    )

    guardrails_enable_human_in_loop: bool = Field(
        default=False,
        description="是否启用人工审核中断"
    )

    guardrails_max_message_count: int = Field(
        default=100,
        ge=1,
        description="Guardrails 最大消息数量限制"
    )

    guardrails_blocked_tools: str = Field(
        default="",
        description="Guardrails 额外屏蔽的工具名（逗号分隔）"
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
        if not self.secret_key:
            raise ValueError("SECRET_KEY 未设置！请通过环境变量或 .env 文件配置。")
        if not self.openai_api_key and not self.debug:
            raise ValueError("OPENAI_API_KEY 未设置！非调试模式下为必需项。")

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

    def get_openai_config(self) -> dict[str, Any]:
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
        return {
            "api_key": self.tavily_api_key,
            "max_results": self.tavily_max_results,
        }


# ==================== 单例管理 ====================

_settings_instance: Settings | None = None


def get_settings() -> Settings:
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance


def validate_settings() -> None:
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


# ==================== 日志工具（代理到 config_center）====================

get_logger = _get_logger
setup_loguru_logging = _setup_loguru_logging


# ==================== 模型注册表 ====================

MODEL_REGISTRY: dict[str, dict[str, Any]] = {
    "openai": {
        "provider": "openai",
        "label": "OpenAI",
        "icon": "🔵",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"],
        "default_model": "gpt-4o-mini",
        "api_key_attr": "openai_api_key",
        "base_url_attr": "openai_api_base",
        "model_attr": "openai_model",
        "special_params": {},
    },
    "deepseek": {
        "provider": "deepseek",
        "label": "DeepSeek",
        "icon": "🤖",
        "models": ["deepseek-v4-flash"],
        "default_model": "deepseek-v4-flash",
        "api_key_attr": "deepseek_api_key",
        "base_url_attr": "deepseek_api_base",
        "model_attr": "deepseek_model",
        "special_params": {
            "thinking": {
                "type": "toggle",
                "label": "思考模式",
                "description": "启用 DeepSeek 思考模式（DeepSeek V4 默认启用，关闭后模型将不输出思维链）",
                "default": True,
                "model_kwarg": "thinking",
                "pass_mode": "extra_body",
                "enabled_value": {"type": "enabled"},
                "disabled_value": {"type": "disabled"},
            },
            "reasoning_effort": {
                "type": "select",
                "label": "思考强度",
                "description": "推理努力程度（high: 适合大多数场景, max: 适合复杂推理任务）",
                "options": ["high", "max"],
                "default": "high",
                "model_kwarg": "reasoning_effort",
                "pass_mode": "top_level",
            },
        },
    },
    "groq": {
        "provider": "groq",
        "label": "Groq",
        "icon": "⚡",
        "models": ["llama-3.3-70b-versatile"],
        "default_model": "llama-3.3-70b-versatile",
        "api_key_attr": "groq_api_key",
        "base_url_attr": None,
        "model_attr": "groq_model",
        "special_params": {},
    },
    "baidu_qianfan": {
        "provider": "openai",
        "label": "百度千帆",
        "icon": "🟠",
        "models": ["ernie-3.5-8k"],
        "default_model": "ernie-3.5-8k",
        "api_key_attr": "baidu_qianfan_api_key",
        "base_url_attr": "baidu_qianfan_api_base",
        "model_attr": "baidu_qianfan_model",
        "special_params": {},
    },
    "anthropic": {
        "provider": "anthropic",
        "label": "Anthropic",
        "icon": "🟣",
        "models": ["claude-sonnet-4-20250514", "claude-3-5-haiku-20241022"],
        "default_model": "claude-sonnet-4-20250514",
        "api_key_attr": "anthropic_api_key",
        "base_url_attr": None,
        "model_attr": None,
        "special_params": {},
    },
}


def get_available_providers() -> list[dict[str, Any]]:
    result = []
    for key, cfg in MODEL_REGISTRY.items():
        api_key = getattr(settings, cfg["api_key_attr"], "")
        available = bool(api_key and api_key.strip())
        result.append({
            "id": key,
            "provider": cfg["provider"],
            "label": cfg["label"],
            "icon": cfg["icon"],
            "models": cfg["models"],
            "default_model": cfg["default_model"],
            "available": available,
            "special_params": cfg["special_params"],
        })
    return result
