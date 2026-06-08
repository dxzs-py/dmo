"""
验证码校验公共 Mixin

封装验证码获取、校验、删除逻辑，供登录和注册视图复用。
"""
import hmac
import logging
from django.core.cache import cache
from rest_framework import status
from Django_xm.common.responses import error_response
from Django_xm.common.error_codes import ErrorCode

logger = logging.getLogger(__name__)


class CaptchaMixin:
    """
    验证码校验 Mixin

    在视图的 post() 方法中调用 verify_captcha() 进行验证码校验。
    校验成功返回 None，校验失败返回错误 Response。
    """

    def verify_captcha(self, request_data):
        """
        校验验证码

        Args:
            request_data: 请求数据（dict 或 QueryDict）

        Returns:
            None: 校验成功或未提供验证码（验证码为可选）
            Response: 校验失败时的错误响应
        """
        captcha_key = request_data.get('captcha_key')
        captcha_code = request_data.get('captcha', '').lower()

        if not captcha_key or not captcha_code:
            return None

        stored_code = cache.get(f'captcha:{captcha_key}')

        if not stored_code:
            return error_response(
                code=ErrorCode.VALIDATION_FAILED,
                message='验证码已过期，请刷新',
                http_status=status.HTTP_400_BAD_REQUEST
            )

        if not hmac.compare_digest(str(stored_code), str(captcha_code)):
            return error_response(
                code=ErrorCode.VALIDATION_FAILED,
                message='验证码错误',
                http_status=status.HTTP_400_BAD_REQUEST
            )

        cache.delete(f'captcha:{captcha_key}')
        return None
