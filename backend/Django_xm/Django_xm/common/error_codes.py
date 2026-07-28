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
    LOGIN_FAILED = (40106, "登录失败", 401)

    FORBIDDEN = (40301, "无权访问", 403)
    PERMISSION_DENIED = (40302, "权限不足", 403)

    NOT_FOUND = (40401, "资源不存在", 404)
    RESOURCE_NOT_FOUND = (40402, "请求的资源不存在", 404)

    METHOD_NOT_ALLOWED = (40501, "请求方法不允许", 405)

    # 冲突：资源已存在（创建/更新时唯一约束冲突）
    DUPLICATE_RESOURCE = (40901, "资源已存在", 409)

    RATE_LIMITED = (42901, "请求过于频繁", 429)
    TOO_MANY_REQUESTS = (42902, "请求次数超限", 429)
    # Agent 调用频率超限（与 HTTP 429 区分，便于前端针对 Agent 场景提示）
    AGENT_RATE_LIMITED = (42903, "智能体调用频率超限，请稍后再试", 429)
    # 账号因登录失败次数过多被锁定（按 username 维度，5 次/h 失败锁定 1 小时）
    ACCOUNT_LOCKED = (42904, "账号已锁定，请稍后重试", 429)

    SERVER_ERROR = (50001, "服务器内部错误", 500)
    INTERNAL_ERROR = (50002, "服务器内部错误", 500)
    # Agent 执行失败（业务异常，区别于通用服务器错误）
    AGENT_EXECUTION_FAILED = (50003, "智能体执行失败，请稍后重试", 500)
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


# HTTP 状态码 → ErrorCode 反向索引（惰性构建，避免模块加载时遍历枚举）
_HTTP_STATUS_TO_ERROR_CODE: dict[int, "ErrorCode"] | None = None


def infer_error_code_from_http_status(http_status: int) -> "ErrorCode":
    """从 HTTP 状态码反查最合适的 ErrorCode。

    取该 HTTP 状态码下第一个定义的 ErrorCode（通常是最通用的那个），
    未匹配时返回 ``ErrorCode.SERVER_ERROR``。

    用途：SSE 错误响应、统一异常处理器等场景，从 HTTP 状态码推导错误码，
    取代散落各处的 ``_status_code_map`` 硬编码字典，单一真相源在
    ``ErrorCode`` 枚举本身。

    Args:
        http_status: HTTP 状态码（如 400/401/403/404/429/500）

    Returns:
        对应的 ``ErrorCode`` 枚举成员，默认 ``ErrorCode.SERVER_ERROR``
    """
    global _HTTP_STATUS_TO_ERROR_CODE
    if _HTTP_STATUS_TO_ERROR_CODE is None:
        _HTTP_STATUS_TO_ERROR_CODE = {}
        for ec in ErrorCode:
            # 仅记录首次出现的映射，保持"最通用错误码优先"语义
            _HTTP_STATUS_TO_ERROR_CODE.setdefault(ec.http_status, ec)
    return _HTTP_STATUS_TO_ERROR_CODE.get(http_status, ErrorCode.SERVER_ERROR)
