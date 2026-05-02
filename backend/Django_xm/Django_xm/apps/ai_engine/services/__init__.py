"""
AI Engine 服务层 - 提供模型管理、缓存、用量追踪等核心服务

包含：
- LLM 工厂（模型创建、预设、流式）
- 缓存服务（通用缓存、查询缓存、模型响应缓存、工具结果缓存、向量搜索缓存）
- 用量追踪（Token 用量统计）
- 成本追踪（模型调用成本计算）
- Agent 工厂（基础 Agent 创建）
- 项目上下文检测
- 建议生成
"""

from .llm_factory import (
    get_chat_model,
    get_streaming_model,
    get_structured_output_model,
    get_model_by_preset,
    get_model_string,
)
from .cache_service import (
    CacheTTL,
    CacheService,
    QueryCacheService,
    ModelResponseCacheService,
    ToolResultCacheService,
    VectorSearchCacheService,
    generate_query_cache_key,
    generate_model_cache_key,
    generate_embedding_cache_key,
    generate_tool_cache_key,
    generate_vector_search_cache_key,
    cache_result,
    invalidate_cache,
    CacheWarmer,
    CacheHealthChecker,
)
from .usage_tracker import (
    TokenUsage,
    UsageTracker,
    create_usage_tracker,
)
from .cost_tracker import (
    ModelPricing,
    CostRecord,
    CostTracker,
    create_cost_tracker,
    get_model_pricing,
    get_all_model_pricing,
)
from .agent_factory import (
    BaseAgent,
    create_base_agent,
)
from .project_context import (
    ProjectContext,
    ProjectContextDetector,
    detect_project_context,
)
from .suggestion_service import generate_suggestions

__all__ = [
    "get_chat_model",
    "get_streaming_model",
    "get_structured_output_model",
    "get_model_by_preset",
    "get_model_string",
    "CacheTTL",
    "CacheService",
    "QueryCacheService",
    "ModelResponseCacheService",
    "ToolResultCacheService",
    "VectorSearchCacheService",
    "generate_query_cache_key",
    "generate_model_cache_key",
    "generate_embedding_cache_key",
    "generate_tool_cache_key",
    "generate_vector_search_cache_key",
    "cache_result",
    "invalidate_cache",
    "CacheWarmer",
    "CacheHealthChecker",
    "TokenUsage",
    "UsageTracker",
    "create_usage_tracker",
    "ModelPricing",
    "CostRecord",
    "CostTracker",
    "create_cost_tracker",
    "get_model_pricing",
    "get_all_model_pricing",
    "BaseAgent",
    "create_base_agent",
    "ProjectContext",
    "ProjectContextDetector",
    "detect_project_context",
    "generate_suggestions",
]
