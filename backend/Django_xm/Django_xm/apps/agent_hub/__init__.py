from Django_xm.apps.agent_hub.config import AgentConfig, AgentType
from Django_xm.apps.agent_hub.exceptions import (
    AgentCreationError,
    AgentHubError,
    ConfigValidationError,
    FrameworkNotAvailableError,
    MiddlewareBuildError,
    ModelResolutionError,
    ToolResolutionError,
)
from Django_xm.apps.agent_hub.factory import AgentFactory

__all__ = [
    "AgentConfig",
    "AgentCreationError",
    "AgentHubError",
    "AgentType",
    "ConfigValidationError",
    "FrameworkNotAvailableError",
    "MiddlewareBuildError",
    "ModelResolutionError",
    "ToolResolutionError",
    "create",
]


async def create(config: AgentConfig):
    return await AgentFactory.create(config)
