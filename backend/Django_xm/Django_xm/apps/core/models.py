"""
Core models - Django基础设施

提供基础模型类：
- BaseModel：带软删除的抽象基类
- AuditModel：带创建人和更新人的审计模型

注意：使用 __getattr__ 延迟导入，避免 Django app registry 未就绪时触发循环导入。
调用方应直接从 Django_xm.apps.core.base_models 导入。
"""


def __getattr__(name):
    """延迟导入，避免 Django app registry 未就绪时触发循环导入"""
    if name in ("BaseModel", "AuditModel"):
        from .base_models import BaseModel, AuditModel
        return locals()[name]
    if name == "CeleryTaskRecord":
        from .task_models import CeleryTaskRecord
        return CeleryTaskRecord
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BaseModel",
    "AuditModel",
    "CeleryTaskRecord",
]
