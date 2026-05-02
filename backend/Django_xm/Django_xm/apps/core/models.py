"""
Core models - Django基础设施

提供基础模型类：
- BaseModel：带软删除的抽象基类
- AuditModel：带创建人和更新人的审计模型
- UserPermissionPolicy：用户权限策略模型
- CeleryTaskRecord：Celery 任务持久化记录

注意：LLM模型封装已移至 Django_xm.apps.llm.llm_models
"""

__all__ = [
    "BaseModel",
    "AuditModel",
    "UserPermissionPolicy",
    "CeleryTaskRecord",
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
    if name == "CeleryTaskRecord":
        from .task_models import CeleryTaskRecord
        return CeleryTaskRecord
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

