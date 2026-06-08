"""
全局错误码定义

每个错误码携带默认消息和 HTTP 状态码，新增错误码只需在此处添加，
无需同步修改 responses.py 的 _infer_http_status。
"""
from enum import IntEnum


class ErrorCode(IntEnum):
    """全局错误码枚举

    用法:
        code = ErrorCode.INVALID_PARAMS
        code.message   -> "请求参数错误"
        code.http_status -> 400
    """

    def __new__(cls, value, message, http_status):
        obj = int.__new__(cls, value)
        obj._value_ = value
        obj._message = message
        obj._http_status = http_status
        return obj

    @property
    def message(self):
        return self._message

    @property
    def http_status(self):
        return self._http_status

    SUCCESS = (200, "操作成功", 200)
    CREATED = (201, "创建成功", 201)

    INVALID_PARAMS = (40001, "请求参数错误", 400)
    VALIDATION_FAILED = (40002, "数据验证失败", 400)
    CAPTCHA_ERROR = (40003, "验证码错误或已过期", 400)
    BAD_REQUEST = (40004, "错误的请求", 400)
    DUPLICATE_ENTRY = (40005, "数据已存在", 400)

    UNAUTHORIZED = (40101, "未认证或认证已过期", 401)
    TOKEN_EXPIRED = (40102, "Token 已过期", 401)
    TOKEN_INVALID = (40103, "Token 无效", 401)
    LOGIN_REQUIRED = (40104, "请先登录", 401)
    AUTH_FAILED = (40105, "认证失败", 401)

    FORBIDDEN = (40301, "无权访问", 403)
    PERMISSION_DENIED = (40302, "权限不足", 403)

    NOT_FOUND = (40401, "资源不存在", 404)
    RESOURCE_NOT_FOUND = (40402, "请求的资源不存在", 404)

    METHOD_NOT_ALLOWED = (40501, "请求方法不允许", 405)

    RATE_LIMITED = (42901, "请求过于频繁", 429)
    TOO_MANY_REQUESTS = (42902, "请求次数超限", 429)

    SERVER_ERROR = (50001, "服务器内部错误", 500)
    INTERNAL_ERROR = (50002, "服务器内部错误", 500)
    SERVICE_UNAVAILABLE = (50301, "服务暂不可用", 503)


def get_error_message(code, default=None):
    """获取错误码对应的默认消息

    Args:
        code: 错误码（int 或 ErrorCode 枚举）
        default: 默认消息

    Returns:
        str: 错误消息
    """
    if isinstance(code, ErrorCode):
        return code.message
    if isinstance(code, int):
        try:
            return ErrorCode(code).message
        except ValueError:
            pass
    return default or "未知错误"
