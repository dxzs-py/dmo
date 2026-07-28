

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


class PreflightCheckError(AgentHubError):
    """预检快速失败错误

    当 AgentConfig.fail_fast_on_preflight=True 且预检未通过时抛出。
    携带 issues 列表供调用方展示具体问题。

    Attributes:
        issues: 预检发现的问题列表（非空）
    """

    def __init__(self, message: str, issues: list[str] | None = None):
        super().__init__(message)
        self.issues = issues or []

    def __str__(self) -> str:
        if self.issues:
            return f"{super().__str__()} (issues: {self.issues})"
        return super().__str__()

