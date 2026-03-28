"""
workflow模块

提供工作流执行功能：
- 工作流视图和API
- 工作流数据模型
"""

__all__ = [
    "WorkflowExecution",
    "WorkflowStartView",
    "WorkflowStatusView",
]


def __getattr__(name):
    if name == "WorkflowExecution":
        from .models import WorkflowExecution
        return WorkflowExecution
    if name == "WorkflowStartView":
        from .views import WorkflowStartView
        return WorkflowStartView
    if name == "WorkflowStatusView":
        from .views import WorkflowStatusView
        return WorkflowStatusView
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")