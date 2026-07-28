"""
AI Engine 服务层 - 提供模型管理、用量追踪等核心服务

包含：
- LLM 工厂（模型创建、预设、流式）
- LLM Fallback 机制（运行时降级、结构化输出 fallback）
- LLM 缓存与速率限制
- 用量追踪（Token 用量统计）
- Token 追踪（模型调用 Token 统计）
- 项目上下文检测
- 建议生成

Agent 创建请使用 Django_xm.apps.agent_hub.create()（旧的 BaseAgent / create_base_agent 已删除）。
"""

from .cost_tracker import (
    TokenDetailTracker,
    TokenRecord,
    create_token_detail_tracker,
)
from .llm_factory import (
    get_chat_model,
    get_chat_model_by_provider,
    get_model_by_preset,
    get_model_string,
    get_streaming_model,
    get_structured_model_with_fallback,
    get_structured_output_model,
    test_model_connection,
)

# Fallback 机制相关（Task 19 拆分：从 llm_fallback 重新导出，而非 llm_factory）
from .llm_fallback import (
    FallbackDetectionCallback,
    LazyFallbackChatModel,
    StructuredModelWithFallback,
    get_fallback_candidates,
    is_connection_error,
)
from .project_context import (
    ProjectContext,
    ProjectContextDetector,
    detect_project_context,
)
from .suggestion_service import generate_suggestions
from .token_counter import TokenUsageCallbackHandler
from .tool_usage_guard import (
    ToolUsageDecision,
    ToolUsageGuard,
    ToolUsageStatus,
    get_tool_usage_guard,
    reset_tool_usage_guard,
)
from .usage_tracker import (
    TokenUsage,
    UsageTracker,
    create_usage_tracker,
)

__all__ = [
    "ProjectContext",
    "ProjectContextDetector",
    "TokenDetailTracker",
    "TokenRecord",
    "TokenUsage",
    "TokenUsageCallbackHandler",
    "ToolUsageDecision",
    "ToolUsageGuard",
    "ToolUsageStatus",
    "UsageTracker",
    "create_token_detail_tracker",
    "create_usage_tracker",
    "detect_project_context",
    "generate_suggestions",
    "get_chat_model",
    "get_chat_model_by_provider",
    "get_fallback_candidates",
    "get_model_by_preset",
    "get_model_string",
    "get_streaming_model",
    "get_structured_model_with_fallback",
    "get_structured_output_model",
    "get_tool_usage_guard",
    "reset_tool_usage_guard",
    "test_model_connection",
]
