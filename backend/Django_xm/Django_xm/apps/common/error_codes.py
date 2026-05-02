"""
统一错误码定义
所有API响应使用统一的 {code, message, data} 格式
"""
from enum import IntEnum


class ErrorCode(IntEnum):
    """全局错误码枚举"""

    SUCCESS = 200

    INVALID_PARAMS = 40001
    VALIDATION_FAILED = 40002
    CAPTCHA_ERROR = 40003
    CAPTCHA_EXPIRED = 40004
    DUPLICATE_RESOURCE = 40005

    UNAUTHORIZED = 40101
    TOKEN_EXPIRED = 40102
    TOKEN_INVALID = 40103
    LOGIN_FAILED = 40104
    ACCOUNT_DISABLED = 40105

    FORBIDDEN = 40301
    PERMISSION_DENIED = 40302
    SESSION_MISMATCH = 40303

    NOT_FOUND = 40401
    RESOURCE_NOT_FOUND = 40402
    SESSION_NOT_FOUND = 40403
    TASK_NOT_FOUND = 40404

    RATE_LIMITED = 42901
    TOO_MANY_REQUESTS = 42902

    SERVER_ERROR = 50001
    INTERNAL_ERROR = 50002
    SERVICE_UNAVAILABLE = 50003
    TASK_EXECUTION_FAILED = 50004
    LLM_SERVICE_ERROR = 50005


ERROR_MESSAGES = {
    ErrorCode.SUCCESS: "操作成功",

    ErrorCode.INVALID_PARAMS: "请求参数错误",
    ErrorCode.VALIDATION_FAILED: "数据验证失败",
    ErrorCode.CAPTCHA_ERROR: "验证码错误",
    ErrorCode.CAPTCHA_EXPIRED: "验证码已过期，请刷新",
    ErrorCode.DUPLICATE_RESOURCE: "资源已存在",

    ErrorCode.UNAUTHORIZED: "未登录或登录已过期",
    ErrorCode.TOKEN_EXPIRED: "Token已过期，请重新登录",
    ErrorCode.TOKEN_INVALID: "无效的Token",
    ErrorCode.LOGIN_FAILED: "用户名或密码错误，请重新输入",
    ErrorCode.ACCOUNT_DISABLED: "账户已被禁用",

    ErrorCode.FORBIDDEN: "无权访问此资源",
    ErrorCode.PERMISSION_DENIED: "权限不足",
    ErrorCode.SESSION_MISMATCH: "会话验证失败，请重新登录",

    ErrorCode.NOT_FOUND: "资源不存在",
    ErrorCode.RESOURCE_NOT_FOUND: "请求的资源不存在",
    ErrorCode.SESSION_NOT_FOUND: "会话不存在",
    ErrorCode.TASK_NOT_FOUND: "任务不存在",

    ErrorCode.RATE_LIMITED: "请求过于频繁，请稍后再试",
    ErrorCode.TOO_MANY_REQUESTS: "操作过于频繁，请稍后再试",

    ErrorCode.SERVER_ERROR: "服务器内部错误",
    ErrorCode.INTERNAL_ERROR: "服务器内部错误，请稍后重试",
    ErrorCode.SERVICE_UNAVAILABLE: "服务暂时不可用",
    ErrorCode.TASK_EXECUTION_FAILED: "任务执行失败",
    ErrorCode.LLM_SERVICE_ERROR: "AI服务暂时不可用",
}


def get_error_message(code: ErrorCode, default: str = None) -> str:
    """获取错误码对应的默认消息"""
    return ERROR_MESSAGES.get(code, default or "未知错误")
