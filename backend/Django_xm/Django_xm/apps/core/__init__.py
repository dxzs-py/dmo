"""
Core模块 - Django基础设施

职责：
- Django基础模型类（BaseModel, AuditModel）
- 中间件
- 视图和异常处理
- 节流和权限控制
"""


def __getattr__(name):
    """延迟导入，避免 Django app registry 未就绪时触发循环导入"""
    if name in ("BaseModel", "AuditModel"):
        # 延迟导入实现（模块级 __all__ 已声明导出，运行时由 __getattr__ 返回）
        from .base_models import AuditModel, BaseModel  # noqa: F401

        return locals()[name]
    if name == "get_logger":
        from .logging_utils import get_logger

        return get_logger
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AuditModel",
    "BaseModel",
    "get_logger",
]
