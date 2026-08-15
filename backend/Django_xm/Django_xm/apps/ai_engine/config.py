"""
AI 引擎配置模块

管理所有 AI 相关配置项，包括：
- LLM 提供商（OpenAI / Anthropic / DeepSeek / Groq / 百度千帆）
- Agent / RAG / Embedding / Vector Store
- Guardrails / Checkpointer / Store / Summarization
- Agent Cache / LangSmith
- Tavily / 高德地图

项目级配置（DB/Redis/Celery/安全/日志/服务器等）
在 Django_xm.apps.core.config 中管理。

使用方式:
    from Django_xm.apps.ai_engine.config import settings
    api_key = settings.openai_api_key
"""

from __future__ import annotations

import threading
from typing import Any

from pydantic import Field

from Django_xm.apps.core.config import (
    ProjectSettings,
)
from Django_xm.apps.core.config import (
    setup_loguru_logging as _setup_loguru_logging,
)


class Settings(ProjectSettings):
    """
    AI 引擎配置类

    继承 ProjectSettings，在项目级配置基础上添加 AI 专属配置项。
    """

    # ==================== OpenAI 配置 ====================
    openai_api_key: str = Field(default="", description="OpenAI API 密钥")

    openai_api_base: str = Field(default="https://api.openai.com/v1", description="OpenAI API 基础 URL")

    openai_model: str = Field(default="gpt-4o-mini", description="默认 OpenAI 模型")

    openai_temperature: float = Field(default=0.7, ge=0.0, le=1.0, description="模型温度参数")

    openai_max_tokens: int | None = Field(default=None, description="最大生成 token 数")

    openai_streaming: bool = Field(default=True, description="是否默认启用流式输出")

    # ==================== Anthropic 配置 ====================
    anthropic_api_key: str = Field(default="", description="Anthropic API 密钥")

    # ==================== DeepSeek 配置 ====================
    deepseek_api_key: str = Field(default="", description="DeepSeek API 密钥")

    deepseek_api_base: str = Field(default="https://api.deepseek.com", description="DeepSeek API 基础 URL")

    deepseek_model: str = Field(default="deepseek-v4-flash", description="DeepSeek 模型名称")

    # ==================== Groq 配置 ====================
    groq_api_key: str = Field(default="", description="Groq API 密钥")

    groq_api_base: str = Field(default="https://api.groq.com", description="Groq API 基础 URL")

    groq_model: str = Field(default="llama-3.3-70b-versatile", description="Groq 模型名称")

    # ==================== 百度千帆 配置 ====================
    baidu_qianfan_api_key: str = Field(default="", description="百度千帆 API 密钥")

    baidu_qianfan_api_base: str = Field(default="https://qianfan.baidubce.com/v2", description="百度千帆 API 基础 URL")

    baidu_qianfan_model: str = Field(default="ernie-3.5-8k", description="百度千帆模型名称")

    # ==================== Tavily 搜索配置 ====================
    tavily_api_key: str = Field(default="", description="Tavily 搜索 API 密钥")

    tavily_max_results: int = Field(default=5, ge=1, le=20, description="Tavily 最大返回结果数")

    # ==================== 高德地图配置 ====================
    amap_key: str = Field(default="", description="高德地图 API 密钥")

    # ==================== Agent 配置 ====================
    agent_max_iterations: int = Field(default=15, ge=1, le=100, description="Agent 最大迭代次数")

    agent_max_execution_time: float | None = Field(default=None, description="Agent 最大执行时间(秒)")

    AGENT_CAPABILITIES_DEFAULT: dict = Field(
        default={
            "base": ["context_management", "tool_injection", "guardrails", "rate_limit"],
            "deep_research": ["context_management", "tool_injection", "rate_limit"],
            "learning": ["context_management", "rate_limit"],
        },
        description="各 Agent 类型的默认能力列表",
    )

    # ==================== RAG / Embedding 配置 ====================
    embedding_model: str = Field(default="text-embedding-3-small", description="Embedding 模型名称")

    embedding_batch_size: int = Field(default=100, ge=1, le=1000, description="Embedding 批处理大小")

    local_embedding_model: str = Field(
        default="BAAI/bge-small-zh-v1.5", description="本地兜底 Embedding 模型（HuggingFace，无 API 消耗）"
    )

    # ==================== Ollama 配置 ====================
    ollama_base_url: str = Field(default="http://localhost:11435", description="Ollama 服务地址（默认本地 11435）")

    ollama_model: str = Field(default="qwen3:8b", description="Ollama 默认 chat 模型（需先 ollama pull <model>）")

    ollama_embedding_model: str = Field(default="bge-m3", description="Ollama 默认 embedding 模型（推荐 bge-m3）")

    chunk_size: int = Field(default=1000, ge=100, le=10000, description="文本分块大小(字符)")

    chunk_overlap: int = Field(default=200, ge=0, le=1000, description="分块重叠大小(字符)")

    vector_store_type: str = Field(
        default="pgvector",
        description="向量库类型: pgvector/chroma/faiss/inmemory/milvus（推荐 pgvector，支持持久化和增量更新）",
    )

    vector_store_path: str = Field(default="data/indexes", description="向量库存储路径")

    chroma_persist_directory: str = Field(default="data/chroma_db", description="Chroma 持久化目录")

    chroma_collection_name: str = Field(default="langchain_xm", description="Chroma 默认集合名称")

    retriever_search_type: str = Field(
        default="similarity", description="检索类型: similarity/mmr/similarity_score_threshold"
    )

    retriever_k: int = Field(default=4, ge=1, le=20, description="检索返回文档数")

    retriever_score_threshold: float = Field(default=0.5, ge=0.0, le=1.0, description="相似度阈值")

    retriever_fetch_k: int = Field(default=20, ge=1, le=100, description="MMR 候选文档数")

    retriever_comprehensive_k: int = Field(default=6, ge=1, le=30, description="全局分析模式检索文档数")

    retriever_use_multi_query: bool = Field(default=True, description="全局分析模式是否启用 MultiQuery 扩展召回")

    retriever_intent_classification_enabled: bool = Field(default=True, description="是否启用查询意图自动分类")

    retriever_map_reduce_batch_size: int = Field(default=4, ge=2, le=10, description="Map-Reduce 每批文档数")

    # ==================== Unified RAG Pipeline 配置 ====================
    rag_score_threshold: float = Field(default=0.7, ge=0.0, le=1.0, description="RAG Pipeline 初始检索相似度阈值")

    rag_degraded_threshold: float = Field(default=0.4, ge=0.0, le=1.0, description="RAG Pipeline 降级检索相似度阈值")

    rag_initial_k: int = Field(default=6, ge=1, le=50, description="RAG Pipeline 初始检索返回文档数")

    rag_degraded_k: int = Field(default=12, ge=1, le=100, description="RAG Pipeline 降级检索返回文档数")

    rag_keyword_max: int = Field(default=5, ge=1, le=20, description="RAG Pipeline 关键词最大数量")

    rag_keyword_k: int = Field(default=3, ge=1, le=20, description="RAG Pipeline 每个关键词检索文档数")

    rag_rerank_top_n: int = Field(default=6, ge=1, le=50, description="RAG Pipeline FlashRank 重排序保留数")

    rag_fulltext_token_threshold: int = Field(
        default=5000, ge=0, le=100000, description="RAG Pipeline 全文注入 token 阈值"
    )

    rag_max_docs_in_result: int = Field(default=10, ge=1, le=50, description="RAG Pipeline 格式化输出最大文档数")

    rag_max_doc_content_length: int = Field(
        default=800, ge=100, le=10000, description="RAG Pipeline 单文档内容截断长度"
    )

    rag_quality_retrieval_threshold: float = Field(
        default=0.5, ge=0.0, le=1.0, description="RAG Pipeline 检索质量评分阈值"
    )

    rag_quality_generation_threshold: float = Field(
        default=0.6, ge=0.0, le=1.0, description="RAG Pipeline 生成质量评分阈值"
    )

    rag_max_retry_count: int = Field(default=1, ge=0, le=5, description="RAG Pipeline 最大重检索次数")

    rag_rrf_constant: int = Field(default=60, ge=1, le=200, description="RAG Pipeline RRF 融合常数")

    rag_agent_max_iterations: int = Field(default=10, ge=1, le=50, description="RAG Agent 最大迭代数")

    rag_agent_return_source_documents: bool = Field(default=True, description="是否返回来源文档")

    # ==================== Checkpointer 配置 ====================
    checkpointer_backend: str = Field(default="postgres", description="Checkpointer 后端: sqlite/memory/postgres")

    checkpointer_redis_cache_enabled: bool = Field(
        default=False, description="是否启用 Checkpointer Redis 热缓存（装饰器模式，PG 写优先）"
    )

    checkpointer_redis_ttl: int = Field(
        default=1800, ge=60, le=86400, description="Checkpointer Redis 缓存 TTL（秒）"
    )

    checkpointer_redis_lock_ttl: int = Field(
        default=5, ge=1, le=60, description="Checkpointer 回源限流锁 TTL（秒）"
    )

    # ==================== Store 配置 ====================
    store_enabled: bool = Field(default=False, description="是否自动注入 Store（长期记忆）到 Agent")

    store_backend: str = Field(default="postgres", description="Store 后端: memory/postgres")

    # ==================== Summarization 配置 ====================
    summarization_trigger_tokens: int = Field(
        default=4000, ge=500, le=100000, description="SummarizationMiddleware 触发摘要的 token 阈值"
    )

    summarization_keep_messages: int = Field(
        default=20, ge=2, le=100, description="SummarizationMiddleware 保留的最近消息数"
    )

    # ==================== Agent Cache 配置 ====================
    agent_cache_enabled: bool = Field(default=False, description="是否自动注入 Agent 级别缓存（InMemoryCache）")

    llm_cache_enabled: bool = Field(default=False, description="是否启用全局 LLM Cache（langchain_core.llm_cache）")

    llm_cache_type: str = Field(default="memory", description="LLM Cache 类型: memory/semantic")

    # ==================== Guardrails 配置 ====================
    guardrails_enabled: bool = Field(default=False, description="是否全局启用 Guardrails Middleware")

    guardrails_strict_mode: bool = Field(default=False, description="Guardrails 严格模式（验证失败直接抛异常）")

    guardrails_enable_pii: bool = Field(default=False, description="是否启用 PII 检测与脱敏")

    guardrails_enable_human_in_loop: bool = Field(default=False, description="是否启用人工审核中断")

    guardrails_max_message_count: int = Field(default=100, ge=1, description="Guardrails 最大消息数量限制")

    guardrails_blocked_tools: str = Field(default="", description="Guardrails 额外屏蔽的工具名（逗号分隔）")

    # ==================== LangSmith 配置 ====================
    langsmith_api_key: str = Field(default="", description="LangSmith API 密钥")

    langsmith_project: str = Field(default="langchain_xm", description="LangSmith 项目名称")

    langsmith_endpoint: str = Field(default="https://api.smith.langchain.com", description="LangSmith API 端点")

    langsmith_tracing: bool = Field(default=False, description="是否启用 LangSmith 追踪")

    # ==================== 工具调用使用防护配置 ====================
    # 借鉴 Cloud Code / Claude Code 的 PreToolUse 速率限制、PostToolUse
    # 配额追踪、内容去重冷却等机制；避免粗暴"3 次就强制终止"。
    tool_usage_dedup_window_seconds: int = Field(
        default=30,
        ge=1,
        le=600,
        description="文件写入去重窗口（秒）。同一 (thread, path) 在此窗口内若 content_hash 相同，"
        "工具返回无变更响应且不计调用次数。",
    )
    tool_usage_dedup_cache_size: int = Field(default=1000, ge=10, le=100000, description="文件写入去重 LRU 缓存容量。")
    tool_usage_rate_limit_max: int = Field(
        default=30, ge=1, le=1000, description="单 thread_id 滑动窗口内的最大工具调用次数。"
    )
    tool_usage_rate_limit_window: int = Field(default=60, ge=1, le=3600, description="滑动窗口大小（秒）。")
    tool_usage_soft_warning_threshold: float = Field(
        default=0.5, ge=0.0, le=1.0, description="软警告阈值（占 rate_limit_max 的比例）。"
    )
    tool_usage_hard_stop_threshold: float = Field(
        default=0.9, ge=0.0, le=1.0, description="硬阻断阈值（占 rate_limit_max 的比例）。"
    )
    tool_usage_same_path_max: int = Field(
        default=6, ge=1, le=100, description="同一 path 在去重窗口内最多允许的写入次数（含去重命中）。"
    )
    tool_usage_same_path_diff_ratio: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="同 path 连续多次写入的实质增量判定阈值：增量占比 < 此值视为无意义重写（被打磨循环）。",
    )
    tool_usage_blocked_consecutive_max: int = Field(
        default=2, ge=1, le=10, description="连续 blocked 多少次后交由 termination_judge 走 LOOP_DETECTED 流程。"
    )
    # 通用资源防护配置（适用于所有工具，不仅 fs_write_file）
    tool_usage_general_dedup_enabled: bool = Field(
        default=True, description="是否启用通用 dedup + 通用资源循环检测。关闭后仅 fs_write_file 和 rate_limit 仍生效。"
    )
    tool_usage_same_resource_max: int = Field(
        default=6,
        ge=1,
        le=100,
        description="同一资源在去重窗口内累计调用次数超过此值且最近 3 次 payload_hash 全部相同，"
        "判定为无进展循环并 BLOCK。适用于所有工具（按 tool_fingerprint 注册表）。",
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
_settings_lock = threading.Lock()


def get_settings() -> Settings:
    global _settings_instance  # noqa: PLW0603 - 模块级单例惰性初始化（标准双检锁模式）
    if _settings_instance is None:
        with _settings_lock:
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


# ==================== 日志工具（代理到 core.config）====================

setup_loguru_logging = _setup_loguru_logging


# ==================== 模型注册表 ====================

MODEL_REGISTRY: dict[str, dict[str, Any]] = {
    "openai": {
        "provider": "openai",
        "label": "OpenAI",
        "icon": "🔵",
        "models": [
            {"name": "gpt-4o", "capabilities": ["tool_calling", "streaming"]},
            {"name": "gpt-4o-mini", "capabilities": ["tool_calling", "streaming"]},
            {"name": "gpt-4-turbo", "capabilities": ["tool_calling", "streaming"]},
        ],
        "default_model": "gpt-4o-mini",
        "api_key_env": "OPENAI_API_KEY",
        "api_key_attr": "openai_api_key",
        "base_url_attr": "openai_api_base",
        "model_attr": "openai_model",
        "special_params": {},
        "presets": {
            "default": {
                "model_name": "gpt-4o",
                "model_provider": "openai",
                "temperature": 0.7,
                "description": "默认模型，平衡性能和成本",
            },
            "fast": {
                "model_name": "gpt-4o-mini",
                "model_provider": "openai",
                "temperature": 0.7,
                "description": "快速模型，适合简单任务",
            },
            "precise": {
                "model_name": "gpt-4o",
                "model_provider": "openai",
                "temperature": 0.3,
                "description": "精确模型，适合需要准确性的任务",
            },
            "creative": {
                "model_name": "gpt-4o",
                "model_provider": "openai",
                "temperature": 1.0,
                "description": "创意模型，适合需要创造性的任务",
            },
        },
    },
    "deepseek": {
        "provider": "deepseek",
        "label": "DeepSeek",
        "icon": "🤖",
        "models": [
            {"name": "deepseek-v4-flash", "capabilities": ["tool_calling", "deep_thinking", "streaming"]},
            {"name": "deepseek-v4-pro", "capabilities": ["tool_calling", "deep_thinking", "streaming"]},
        ],
        "default_model": "deepseek-v4-flash",
        "api_key_env": "DEEPSEEK_API_KEY",
        "api_key_attr": "deepseek_api_key",
        "base_url_attr": "deepseek_api_base",
        "model_attr": "deepseek_model",
        "special_params": {
            "thinking": {
                "type": "toggle",
                "label": "思考模式",
                "description": "启用 DeepSeek 思考模式（深度思考模式下自动启用，其他模式下默认关闭）",
                "default": False,
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
        "presets": {},
    },
    "groq": {
        "provider": "groq",
        "label": "Groq",
        "icon": "⚡",
        "models": [
            {"name": "llama-3.3-70b-versatile", "capabilities": ["tool_calling", "streaming"]},
        ],
        "default_model": "llama-3.3-70b-versatile",
        "api_key_env": "GROQ_API_KEY",
        "api_key_attr": "groq_api_key",
        "base_url_attr": None,
        "model_attr": "groq_model",
        "special_params": {},
        "presets": {},
    },
    "baidu_qianfan": {
        "provider": "openai",
        "label": "百度千帆",
        "icon": "🟠",
        "models": [
            {"name": "ernie-3.5-8k", "capabilities": ["tool_calling", "streaming"]},
        ],
        "default_model": "ernie-3.5-8k",
        "api_key_env": "BAIDU_QIANFAN_API_KEY",
        "api_key_attr": "baidu_qianfan_api_key",
        "base_url_attr": "baidu_qianfan_api_base",
        "model_attr": "baidu_qianfan_model",
        "special_params": {},
        "presets": {},
    },
    "anthropic": {
        "provider": "anthropic",
        "label": "Anthropic",
        "icon": "🟣",
        "models": [
            {"name": "claude-sonnet-4-20250514", "capabilities": ["tool_calling", "streaming"]},
            {"name": "claude-3-5-haiku-20241022", "capabilities": ["tool_calling", "streaming"]},
        ],
        "default_model": "claude-sonnet-4-20250514",
        "api_key_env": "ANTHROPIC_API_KEY",
        "api_key_attr": "anthropic_api_key",
        "base_url_attr": None,
        "model_attr": None,
        "special_params": {},
        "presets": {
            "anthropic_default": {
                "model_name": "claude-sonnet-4-20250514",
                "model_provider": "anthropic",
                "temperature": 0.7,
                "description": "Anthropic Claude Sonnet 4，平衡性能和成本",
            },
            "anthropic_fast": {
                "model_name": "claude-3-5-haiku-20241022",
                "model_provider": "anthropic",
                "temperature": 0.7,
                "description": "Anthropic Claude Haiku，快速响应",
            },
            "anthropic_precise": {
                "model_name": "claude-sonnet-4-20250514",
                "model_provider": "anthropic",
                "temperature": 0.3,
                "description": "Anthropic Claude Sonnet 4，精确模式",
            },
        },
    },
    "ollama": {
        "provider": "ollama",
        "label": "Ollama 本地",
        "icon": "🦙",
        "models": [
            {"name": "qwen3:8b", "capabilities": ["tool_calling", "streaming", "deep_thinking"]},
            {"name": "qwen3.5:9b", "capabilities": ["tool_calling", "streaming"]},
            {"name": "hermes3:8b", "capabilities": ["tool_calling", "streaming"]},
        ],
        "default_model": "qwen3:8b",
        # Ollama 无 api_key，标记 None 触发"本地可用"分支
        "api_key_env": None,
        "api_key_attr": None,
        "base_url_attr": "ollama_base_url",
        "model_attr": "ollama_model",
        "special_params": {},
        "presets": {
            "ollama_default": {
                "model_name": "qwen3:8b",
                "model_provider": "ollama",
                "temperature": 0.7,
                "description": "Ollama 本地 qwen3 8B，无需 API key",
            },
            "ollama_qwen35": {
                "model_name": "qwen3.5:9b",
                "model_provider": "ollama",
                "temperature": 0.7,
                "description": "Ollama 本地 qwen3.5 9B",
            },
            "ollama_hermes": {
                "model_name": "hermes3:8b",
                "model_provider": "ollama",
                "temperature": 0.7,
                "description": "Ollama 本地 hermes3 8B",
            },
        },
    },
}

HELPER_MODEL_PRIORITY: list[dict[str, str]] = [
    {"provider": "deepseek", "model": "deepseek-v4-flash", "reason": "性价比最高"},
    {"provider": "groq", "model": "llama-3.3-70b-versatile", "reason": "免费快速"},
    {"provider": "openai", "model": "gpt-4o-mini", "reason": "OpenAI 便宜模型"},
    {"provider": "baidu_qianfan", "model": "ernie-3.5-8k", "reason": "百度便宜模型"},
    {"provider": "ollama", "model": "qwen3:8b", "reason": "本地 Ollama 兜底（无需 API key）"},
]


def get_model_presets() -> dict[str, dict[str, Any]]:
    """从数据库汇总所有预设配置，返回扁平化的 preset_name -> config 映射"""
    from Django_xm.apps.ai_engine.services.registry_service import get_model_registry

    presets: dict[str, dict[str, Any]] = {}
    for _provider_id, cfg in get_model_registry().items():
        for preset_name, preset_cfg in cfg.get("presets", {}).items():
            presets[preset_name] = preset_cfg
    return presets


def get_available_providers() -> list[dict[str, Any]]:
    """从数据库获取所有 Provider 列表（包含不可用的，available 字段标记可用性）"""
    from Django_xm.apps.ai_engine.services.registry_service import get_model_registry, is_provider_available

    result = []
    for key, cfg in get_model_registry().items():
        available = is_provider_available(key)
        models = cfg.get("models", [])
        # 将模型列表统一为包含 capabilities 的字典列表格式
        normalized_models = []
        for model_cfg in models:
            if isinstance(model_cfg, dict):
                normalized_models.append(model_cfg)
            else:
                normalized_models.append({"name": model_cfg, "capabilities": []})
        result.append(
            {
                "id": key,
                "provider": cfg["provider"],
                "label": cfg["label"],
                "icon": cfg["icon"],
                "models": normalized_models,
                "default_model": cfg["default_model"],
                "available": available,
                "special_params": cfg.get("special_params", {}),
            }
        )
    return result
