"""agent_hub/services — Agent 执行层服务

模块组成：
- agent_resilience : 韧性组件（配置、异常分类、降级、超时、重复调用检测）
- resilient_invoker: 模型调用层降级链与重试管理（CircuitBreaker）
- agent_executor   : 公共 Agent 执行器（重试/降级/超时/回退的流式包装）
"""

from .agent_executor import AgentExecutor
from .agent_resilience import (
    DegradationLevel,
    DuplicateToolCallDetector,
    DuplicateToolCallWarning,
    ErrorAction,
    ExecutionTimeoutManager,
    ResilienceConfig,
    calculate_backoff,
    classify_and_decide,
    get_degraded_tools,
    get_resilience_config,
    retry_with_backoff,
)

__all__ = [
    # agent_executor
    "AgentExecutor",
    # agent_resilience
    "DegradationLevel",
    "DuplicateToolCallDetector",
    "DuplicateToolCallWarning",
    "ErrorAction",
    "ExecutionTimeoutManager",
    "ResilienceConfig",
    "calculate_backoff",
    "classify_and_decide",
    "get_degraded_tools",
    "get_resilience_config",
    "retry_with_backoff",
]
