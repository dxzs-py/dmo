"""
验证码校验公共 Mixin

封装验证码获取、校验、删除逻辑，供登录和注册视图复用。
校验失败抛出 BaseAppError（business_code=CAPTCHA_ERROR），由全局
custom_exception_handler 统一转换为标准错误响应。
"""

import hmac
import logging

from django.core.cache import cache

from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.exceptions import BaseAppError

logger = logging.getLogger(__name__)


class CaptchaMixin:
    """
    验证码校验 Mixin

    在视图的 post() 方法中调用 verify_captcha() 进行验证码校验。
    校验成功或未提供验证码（验证码为可选）时静默返回；
    校验失败抛出 BaseAppError，冒泡至全局异常处理器。
    """

    def verify_captcha(self, request_data):
        """
        校验验证码

        Args:
            request_data: 请求数据（dict 或 QueryDict）

        Raises:
            BaseAppError: 验证码已过期或错误（code=40003，HTTP 400）
        """
        captcha_key = request_data.get("captcha_key")
        captcha_code = request_data.get("captcha", "").lower()

        if not captcha_key or not captcha_code:
            return

        stored_code = cache.get(f"captcha:{captcha_key}")

        if not stored_code:
            raise BaseAppError("验证码已过期，请刷新", business_code=ErrorCode.CAPTCHA_ERROR)

        if not hmac.compare_digest(str(stored_code), str(captcha_code)):
            raise BaseAppError("验证码错误", business_code=ErrorCode.CAPTCHA_ERROR)

        cache.delete(f"captcha:{captcha_key}")
