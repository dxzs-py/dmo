"""
统一异常处理器入口

本模块作为 DRF EXCEPTION_HANDLER 的统一入口，
实际逻辑委托给 Django_xm.apps.core.views.custom_exception_handler 实现，
确保全局只有一份异常处理逻辑，避免维护多套重复代码。

注意: 使用函数级延迟导入以避免与 core.views 的循环依赖
"""
import importlib

_custom_exception_handler = None


def custom_exception_handler(exc, context):
    global _custom_exception_handler
    if _custom_exception_handler is None:
        _custom_exception_handler = importlib.import_module(
            "Django_xm.apps.core.views"
        ).custom_exception_handler
    return _custom_exception_handler(exc, context)


__all__ = ['custom_exception_handler']
