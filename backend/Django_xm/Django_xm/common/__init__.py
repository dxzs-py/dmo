"""
公共模块

提供项目级别的通用工具，请按需从子模块直接导入：
    from Django_xm.common.responses import success_response
    from Django_xm.common.error_codes import ErrorCode
"""

__all__ = [
    "ErrorCode",
    "IsAdmin",
    "IsAuthenticatedOrQueryParam",
    "api_response",
    "custom_exception_handler",
    "error_response",
    "get_client_ip",
    "get_error_message",
    "get_user_agent",
    "not_found_response",
    "success_response",
    "validation_error_response",
]


def __getattr__(name):
    _MODULE_MAP = {
        "api_response": ".responses",
        "success_response": ".responses",
        "error_response": ".responses",
        "validation_error_response": ".responses",
        "not_found_response": ".responses",
        "ErrorCode": ".error_codes",
        "get_error_message": ".error_codes",
        "custom_exception_handler": ".exceptions",
        "get_client_ip": ".request_utils",
        "get_user_agent": ".request_utils",
        "IsAdmin": ".permissions",
        "IsAuthenticatedOrQueryParam": ".permissions",
    }
    if name in _MODULE_MAP:
        import importlib
        module = importlib.import_module(_MODULE_MAP[name], __package__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
