"""
AI Engine 服务层 - 提供模型管理、用量追踪等核心服务

包含：
- LLM 工厂（模型创建、预设、流式）
- 用量追踪（Token 用量统计）
- Token 追踪（模型调用 Token 统计）
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
    get_chat_model_by_provider,
    test_model_connection,
    get_chat_model_with_fallback,
    get_fallback_candidates,
    get_structured_model_with_fallback,
)
from .usage_tracker import (
    TokenUsage,
    UsageTracker,
    create_usage_tracker,
)
from .cost_tracker import (
    TokenRecord,
    TokenDetailTracker,
    create_token_detail_tracker,
)
from .agent_factory import (
    BaseAgent,
)
import warnings
warnings.warn(
    "BaseAgent 已废弃，请使用 Django_xm.apps.agent_hub.create()",
    DeprecationWarning,
    stacklevel=2,
)
from .project_context import (
    ProjectContext,
    ProjectContextDetector,
    detect_project_context,
)
from .suggestion_service import generate_suggestions
from .token_counter import TokenUsageCallbackHandler
from .tool_usage_guard import (
    ToolUsageGuard,
    ToolUsageStatus,
    ToolUsageDecision,
    get_tool_usage_guard,
    reset_tool_usage_guard,
)

__all__ = [
    "get_chat_model",
    "get_streaming_model",
    "get_structured_output_model",
    "get_model_by_preset",
    "get_model_string",
    "get_chat_model_by_provider",
    "test_model_connection",
    "get_chat_model_with_fallback",
    "get_fallback_candidates",
    "get_structured_model_with_fallback",
    "TokenUsage",
    "UsageTracker",
    "create_usage_tracker",
    "TokenRecord",
    "TokenDetailTracker",
    "create_token_detail_tracker",
    "BaseAgent",
    "ProjectContext",
    "ProjectContextDetector",
    "detect_project_context",
    "generate_suggestions",
    "TokenUsageCallbackHandler",
    "ToolUsageGuard",
    "ToolUsageStatus",
    "ToolUsageDecision",
    "get_tool_usage_guard",
    "reset_tool_usage_guard",
]
