"""
Core模块 - Django基础设施

职责：
- Django基础模型类（BaseModel, AuditModel）
- 权限模型（UserPermissionPolicy）
- 中间件
- 视图和异常处理
- 节流和权限控制

注意：LangChain/LLM相关功能已移至 Django_xm.apps.llm
"""

__all__ = [
    "BaseModel",
    "AuditModel",
    "UserPermissionPolicy",
    "health_check",
    "request_monitor",
    "custom_exception_handler",
]


def __getattr__(name):
    if name in ("BaseModel", "AuditModel"):
        from .base_models import BaseModel, AuditModel
        if name == "BaseModel":
            return BaseModel
        return AuditModel
    if name == "UserPermissionPolicy":
        from .permission_models import UserPermissionPolicy
        return UserPermissionPolicy
    if name in ("health_check", "request_monitor", "custom_exception_handler"):
        from .views import health_check, request_monitor, custom_exception_handler
        if name == "health_check":
            return health_check
        if name == "request_monitor":
            return request_monitor
        return custom_exception_handler
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


