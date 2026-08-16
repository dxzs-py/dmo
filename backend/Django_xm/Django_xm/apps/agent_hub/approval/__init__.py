"""统一审批中间件模块

提供基于 AgentMiddleware 的统一审批拦截能力，
将分散在各工具内部的审批逻辑收敛到独立策略类。

使用方式：
    from Django_xm.apps.agent_hub.approval import (
        ApprovalMiddleware,
        ApprovalPolicy,
        ShellExecApprovalPolicy,
        FileReaderApprovalPolicy,
        FsWriteFileApprovalPolicy,
        ApprovalTimeoutHandler,
        TIMEOUT_DECISION,
    )

    middleware = ApprovalMiddleware()  # 使用默认策略集
"""

from .middleware import ApprovalMiddleware
from .policies import (
    ApprovalPolicy,
    FileReaderApprovalPolicy,
    FsWriteFileApprovalPolicy,
    ShellExecApprovalPolicy,
)
from .timeout_handler import (
    TIMEOUT_DECISION,
    ApprovalTimeoutHandler,
    build_timeout_tool_message,
    get_timeout_handler,
)

__all__ = [
    "TIMEOUT_DECISION",
    "ApprovalMiddleware",
    "ApprovalPolicy",
    "ApprovalTimeoutHandler",
    "FileReaderApprovalPolicy",
    "FsWriteFileApprovalPolicy",
    "ShellExecApprovalPolicy",
    "build_timeout_tool_message",
    "get_timeout_handler",
]
