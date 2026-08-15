"""SubAgentRuntime 统一运行时（子 Agent 唯一对外入口）。

全系统所有子 Agent 仅允许通过 ``SubAgentRuntime`` 创建/运行/恢复/销毁。
"""

from Django_xm.apps.ai_engine.subagent_runtime.lifecycle import (
    SubAgentLifecycleManager,
    get_lifecycle_manager,
)
from Django_xm.apps.ai_engine.subagent_runtime.registry import (
    SUBAGENT_SPECS,
    SubAgentSpec,
    get_subagent_spec,
    resolve_dedicated_tools,
    resolve_system_prompt,
)
from Django_xm.apps.ai_engine.subagent_runtime.runtime import (
    MAX_SUBAGENT_DEPTH,
    MAX_SUBAGENT_ROUNDS,
    SubAgentRuntime,
    get_subagent_runtime,
)

__all__ = [
    "MAX_SUBAGENT_DEPTH",
    "MAX_SUBAGENT_ROUNDS",
    "SUBAGENT_SPECS",
    "SubAgentLifecycleManager",
    "SubAgentRuntime",
    "SubAgentSpec",
    "get_lifecycle_manager",
    "get_subagent_runtime",
    "get_subagent_spec",
    "resolve_dedicated_tools",
    "resolve_system_prompt",
]
