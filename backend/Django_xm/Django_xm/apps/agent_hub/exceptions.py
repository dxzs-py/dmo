class AgentHubError(Exception):
    pass


class AgentCreationError(AgentHubError):
    pass


class ConfigValidationError(AgentHubError):
    pass


class ModelResolutionError(AgentHubError):
    pass


class ToolResolutionError(AgentHubError):
    pass


class MiddlewareBuildError(AgentHubError):
    pass


class FrameworkNotAvailableError(AgentHubError):
    pass
