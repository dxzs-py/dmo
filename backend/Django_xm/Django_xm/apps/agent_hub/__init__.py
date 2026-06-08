from Django_xm.apps.agent_hub.config import AgentType, AgentConfig
from Django_xm.apps.agent_hub.factory import AgentFactory
from Django_xm.apps.agent_hub.exceptions import (
    AgentHubError,
    AgentCreationError,
    ConfigValidationError,
    ModelResolutionError,
    ToolResolutionError,
    MiddlewareBuildError,
    FrameworkNotAvailableError,
)

__all__ = [
    "create",
    "AgentType",
    "AgentConfig",
    "AgentHubError",
    "AgentCreationError",
    "ConfigValidationError",
    "ModelResolutionError",
    "ToolResolutionError",
    "MiddlewareBuildError",
    "FrameworkNotAvailableError",
]


async def create(config: AgentConfig):
    return await AgentFactory.create(config)
