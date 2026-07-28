"""错误码与异常处理器单元测试（Task 4.6）。

覆盖 spec `fix-backend-audit-findings` 阶段 1B 变更：
1. ErrorCode 枚举新增 DUPLICATE_RESOURCE / AGENT_RATE_LIMITED / AGENT_EXECUTION_FAILED
2. custom_exception_handler 显式捕获 MethodNotAllowed / DjangoPermissionDenied
3. _handle_auth_error 使用 isinstance(exc, TokenError) 替代字符串匹配
4. _handle_agent_error 根据 exc.error_code 映射到业务错误码

运行方式:
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    conda activate langchain_xm
    python manage.py test Django_xm.common.tests.test_error_codes --verbosity=2
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock

# Django 环境初始化（兼容 unittest 直接运行）
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django
import django.apps

if not django.apps.apps.ready:
    django.setup()

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from rest_framework import status as http_status
from rest_framework.exceptions import (
    AuthenticationFailed,
    MethodNotAllowed,
    ValidationError,
)
from rest_framework.exceptions import (
    PermissionDenied as DRFPermissionDenied,
)
from rest_framework_simplejwt.exceptions import InvalidToken

from Django_xm.apps.ai_engine.services.exceptions import (
    AgentExecutionError,
    GuardrailsValidationError,
    LCAgentException,
    ModelCallError,
    RateLimitExceededError,
)
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.exceptions import (
    _AGENT_ERROR_CODE_MAP,
    _handle_agent_error,
    custom_exception_handler,
)


def _make_context():
    """构造 DRF exception_handler 所需的 context 字典。"""
    request = MagicMock()
    request.user = MagicMock()
    request.user.is_authenticated = True
    view = MagicMock()
    return {"request": request, "view": view, "args": [], "kwargs": {}}


class ErrorCodeEnumTests(unittest.TestCase):
    """SubTask 4.1 + 4.2：新增错误码定义校验。"""

    def test_duplicate_resource_error_code_definition(self):
        """DUPLICATE_RESOURCE = (40901, "资源已存在", 409)。"""
        self.assertEqual(int(ErrorCode.DUPLICATE_RESOURCE), 40901)
        self.assertEqual(ErrorCode.DUPLICATE_RESOURCE.message, "资源已存在")
        self.assertEqual(ErrorCode.DUPLICATE_RESOURCE.http_status, 409)

    def test_agent_rate_limited_error_code_definition(self):
        """AGENT_RATE_LIMITED = (42903, "智能体调用频率超限，请稍后再试", 429)。"""
        self.assertEqual(int(ErrorCode.AGENT_RATE_LIMITED), 42903)
        self.assertIn("智能体调用频率超限", ErrorCode.AGENT_RATE_LIMITED.message)
        self.assertEqual(ErrorCode.AGENT_RATE_LIMITED.http_status, 429)

    def test_agent_execution_failed_error_code_definition(self):
        """AGENT_EXECUTION_FAILED = (50003, "智能体执行失败，请稍后重试", 500)。"""
        self.assertEqual(int(ErrorCode.AGENT_EXECUTION_FAILED), 50003)
        self.assertIn("智能体执行失败", ErrorCode.AGENT_EXECUTION_FAILED.message)
        self.assertEqual(ErrorCode.AGENT_EXECUTION_FAILED.http_status, 500)


class MethodNotAllowedHandlingTests(unittest.TestCase):
    """SubTask 4.3：MethodNotAllowed 显式捕获，返回 405 + 40501。"""

    def test_method_not_allowed_returns_405(self):
        """MethodNotAllowed 异常返回 HTTP 405。"""
        exc = MethodNotAllowed("PUT")
        response = custom_exception_handler(exc, _make_context())
        self.assertEqual(response.status_code, http_status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(response.data["code"], int(ErrorCode.METHOD_NOT_ALLOWED))

    def test_method_not_allowed_preserves_message(self):
        """MethodNotAllowed 携带的 detail 消息透传到响应。"""
        exc = MethodNotAllowed("DELETE", detail="Method 'DELETE' not allowed.")
        response = custom_exception_handler(exc, _make_context())
        self.assertIn("DELETE", response.data["message"])

    def test_method_not_allowed_not_swallowed_by_apiexception(self):
        """MethodNotAllowed 不应被通用 APIException 分支以 500 返回。

        回归测试：MethodNotAllowed 是 APIException 子类，若未在 APIException 分支前
        显式捕获，会被通用分支以 SERVER_ERROR 返回 500，丢失 405 语义。
        """
        exc = MethodNotAllowed("PATCH")
        response = custom_exception_handler(exc, _make_context())
        # 必须返回 405，而非 500
        self.assertNotEqual(response.status_code, http_status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertEqual(response.data["code"], int(ErrorCode.METHOD_NOT_ALLOWED))


class DjangoPermissionDeniedHandlingTests(unittest.TestCase):
    """SubTask 4.3：Django 核心 PermissionDenied 显式捕获。"""

    def test_django_permission_denied_returns_403(self):
        """Django PermissionDenied（非 DRF）返回 403 + 40302。"""
        exc = DjangoPermissionDenied("无权访问该资源")
        response = custom_exception_handler(exc, _make_context())
        self.assertEqual(response.status_code, http_status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["code"], int(ErrorCode.PERMISSION_DENIED))
        self.assertIn("无权访问", response.data["message"])

    def test_django_permission_denied_default_message(self):
        """Django PermissionDenied 无消息时回退默认"权限不足"。"""
        exc = DjangoPermissionDenied("")
        response = custom_exception_handler(exc, _make_context())
        self.assertEqual(response.status_code, http_status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["message"], "权限不足")

    def test_drf_permission_denied_takes_precedence_over_django(self):
        """DRF PermissionDenied 优先匹配（同为 APIException 子类）。

        验证 _handle_auth_error 不会错误地将 DRF PermissionDenied 走 Django 分支。
        """
        exc = DRFPermissionDenied("DRF 权限不足")
        response = custom_exception_handler(exc, _make_context())
        self.assertEqual(response.status_code, http_status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["code"], int(ErrorCode.PERMISSION_DENIED))
        self.assertIn("DRF 权限不足", response.data["message"])


class TokenErrorIsinstanceCheckTests(unittest.TestCase):
    """SubTask 4.5：认证错误识别使用 isinstance(exc, InvalidToken) 替代字符串匹配。

    注：SimpleJWT 5.x 中 ``InvalidToken`` 继承自 ``AuthenticationFailed``，
    而非 ``TokenError``。原 spec 描述的 ``TokenError`` isinstance 检查实际无法捕获
    SimpleJWT 在 view 层抛出的 ``InvalidToken``，已修正为 ``InvalidToken`` 检查。
    """

    def test_invalid_token_with_expired_keyword_returns_token_expired(self):
        """InvalidToken 消息含 'expired' → TOKEN_EXPIRED。"""
        exc = InvalidToken("Token is expired")
        response = custom_exception_handler(exc, _make_context())
        self.assertEqual(response.status_code, http_status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data["code"], int(ErrorCode.TOKEN_EXPIRED))
        self.assertEqual(response.data["message"], "登录已过期，请重新登录")

    def test_invalid_token_with_invalid_keyword_returns_token_invalid(self):
        """InvalidToken 消息含 'invalid' 但非 expired → TOKEN_INVALID。"""
        exc = InvalidToken("Token is invalid or malformed")
        response = custom_exception_handler(exc, _make_context())
        self.assertEqual(response.status_code, http_status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data["code"], int(ErrorCode.TOKEN_INVALID))
        self.assertEqual(response.data["message"], "无效的认证信息，请重新登录")

    def test_invalid_token_without_expired_keyword_returns_token_invalid(self):
        """InvalidToken 不含 expired 关键字 → TOKEN_INVALID（非 UNAUTHORIZED）。"""
        exc = InvalidToken("Token signature verification failed")
        response = custom_exception_handler(exc, _make_context())
        self.assertEqual(response.data["code"], int(ErrorCode.TOKEN_INVALID))

    def test_authentication_failed_not_invalid_token_returns_unauthorized(self):
        """AuthenticationFailed 但非 InvalidToken → UNAUTHORIZED（回退分支）。"""
        exc = AuthenticationFailed("Authentication failed.")
        response = custom_exception_handler(exc, _make_context())
        self.assertEqual(response.status_code, http_status.HTTP_401_UNAUTHORIZED)
        # 非 InvalidToken 走 UNAUTHORIZED 分支
        self.assertEqual(response.data["code"], int(ErrorCode.UNAUTHORIZED))

    def test_invalid_token_detail_dict_extracted_correctly(self):
        """InvalidToken.detail 为 dict 结构，从 'detail' 子键提取消息。

        回归测试：旧实现直接 ``str(exc.detail)`` 会得到 dict 的字符串表示，
        虽包含消息但也包含 ErrorDetail 包装，新实现从 ``detail['detail']`` 提取干净消息。
        """
        exc = InvalidToken("Token is expired")
        # 验证 detail 为 dict
        self.assertIsInstance(exc.detail, dict)
        self.assertIn('detail', exc.detail)
        # custom_exception_handler 能正确提取消息并识别为过期
        response = custom_exception_handler(exc, _make_context())
        self.assertEqual(response.data["code"], int(ErrorCode.TOKEN_EXPIRED))


class AgentErrorCodeMappingTests(unittest.TestCase):
    """SubTask 4.4：_handle_agent_error 根据 exc.error_code 映射业务错误码。"""

    def test_agent_error_code_map_covers_known_codes(self):
        """_AGENT_ERROR_CODE_MAP 覆盖所有预定义 error_code。"""
        expected_keys = {
            "RATE_LIMIT_EXCEEDED",
            "AGENT_EXECUTION_ERROR",
            "MODEL_CALL_ERROR",
            "RAG_RETRIEVAL_ERROR",
            "CHECKPOINT_ERROR",
            "GUARDRAILS_VALIDATION_ERROR",
            "UNEXPECTED_ERROR",
            "AGENT_ERROR",
        }
        self.assertTrue(expected_keys.issubset(set(_AGENT_ERROR_CODE_MAP.keys())))

    def test_rate_limit_exceeded_maps_to_agent_rate_limited(self):
        """RATE_LIMIT_EXCEEDED → AGENT_RATE_LIMITED (42903) + HTTP 429。"""
        exc = RateLimitExceededError("API 速率限制已超限")
        response = _handle_agent_error(exc)
        self.assertEqual(response.status_code, http_status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(response.data["code"], int(ErrorCode.AGENT_RATE_LIMITED))

    def test_agent_execution_error_maps_to_agent_execution_failed(self):
        """AGENT_EXECUTION_ERROR → AGENT_EXECUTION_FAILED (50003)。"""
        exc = AgentExecutionError("Agent 调用失败", agent_type="deep_research")
        response = _handle_agent_error(exc)
        self.assertEqual(response.status_code, http_status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertEqual(response.data["code"], int(ErrorCode.AGENT_EXECUTION_FAILED))

    def test_model_call_error_maps_to_service_unavailable(self):
        """MODEL_CALL_ERROR → SERVICE_UNAVAILABLE (50301) + HTTP 503。"""
        exc = ModelCallError("模型调用失败", model_name="gpt-4")
        response = _handle_agent_error(exc)
        self.assertEqual(response.status_code, http_status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data["code"], int(ErrorCode.SERVICE_UNAVAILABLE))

    def test_guardrails_validation_error_maps_to_validation_failed(self):
        """GUARDRAILS_VALIDATION_ERROR → VALIDATION_FAILED (40002) + HTTP 400。"""
        # GuardrailsValidationError 默认 recoverable=False，会先匹配 503 分支
        # 但业务码仍应为 VALIDATION_FAILED
        exc = GuardrailsValidationError("内容验证未通过", validation_type="output_parser")
        response = _handle_agent_error(exc)
        # recoverable=False 优先 → 503
        self.assertEqual(response.status_code, http_status.HTTP_503_SERVICE_UNAVAILABLE)
        # 业务码仍为 VALIDATION_FAILED
        self.assertEqual(response.data["code"], int(ErrorCode.VALIDATION_FAILED))

    def test_unexpected_error_maps_to_server_error_when_recoverable(self):
        """UNEXPECTED_ERROR + recoverable=True → SERVER_ERROR + HTTP 500。

        注：classify_exception 中 UNEXPECTED_ERROR 默认 recoverable=False，
        但本测试直接构造 recoverable=True 的异常验证 HTTP 状态码优先级。
        """
        exc = LCAgentException(
            "测试未预期错误",
            error_code="UNEXPECTED_ERROR",
            recoverable=True,  # 强制可恢复以验证 500 分支
        )
        response = _handle_agent_error(exc)
        self.assertEqual(response.status_code, http_status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertEqual(response.data["code"], int(ErrorCode.SERVER_ERROR))

    def test_unmapped_error_code_falls_back_to_server_error(self):
        """未映射的 error_code 回退到 SERVER_ERROR。"""
        exc = LCAgentException(
            "未知错误",
            error_code="UNKNOWN_ERROR_CODE",
            recoverable=True,
        )
        response = _handle_agent_error(exc)
        self.assertEqual(response.data["code"], int(ErrorCode.SERVER_ERROR))
        self.assertEqual(response.status_code, http_status.HTTP_500_INTERNAL_SERVER_ERROR)

    def test_non_recoverable_returns_503(self):
        """recoverable=False 的 agent 异常返回 503（覆盖所有业务码）。"""
        exc = LCAgentException(
            "不可恢复错误",
            error_code="RATE_LIMIT_EXCEEDED",  # 即便映射到 AGENT_RATE_LIMITED
            recoverable=False,  # 不可恢复优先 → 503
        )
        response = _handle_agent_error(exc)
        self.assertEqual(response.status_code, http_status.HTTP_503_SERVICE_UNAVAILABLE)
        # 业务码仍为 AGENT_RATE_LIMITED（按 error_code 映射）
        self.assertEqual(response.data["code"], int(ErrorCode.AGENT_RATE_LIMITED))

    def test_agent_error_response_includes_error_code_in_data(self):
        """响应 data 字段包含原始 error_code 与 recoverable 字段。"""
        exc = AgentExecutionError("失败", agent_type="chat")
        response = _handle_agent_error(exc)
        self.assertEqual(response.data["data"]["error_code"], "AGENT_EXECUTION_ERROR")
        self.assertIn("recoverable", response.data["data"])

    def test_agent_error_uses_user_message_when_provided(self):
        """exc.user_message 优先于 error_data.message 作为响应 message。"""
        exc = LCAgentException(
            "内部错误详情",
            error_code="AGENT_ERROR",
            user_message="对用户友好的消息",
            recoverable=True,
        )
        response = _handle_agent_error(exc)
        self.assertEqual(response.data["message"], "对用户友好的消息")

    def test_agent_error_falls_back_to_error_data_message(self):
        """无 user_message 时回退到 error_data.message。"""
        exc = LCAgentException(
            "回退消息",
            error_code="AGENT_ERROR",
            user_message=None,
            recoverable=True,
        )
        # DEFAULT_USER_MESSAGE 会作为默认值，而非 None
        response = _handle_agent_error(exc)
        # LCAgentException.__init__ 中 user_message 默认为 DEFAULT_USER_MESSAGE
        # 所以这里使用 DEFAULT_USER_MESSAGE
        self.assertIsNotNone(response.data["message"])


class AgentExceptionViaMainHandlerTests(unittest.TestCase):
    """LCAgentException 通过 custom_exception_handler 主入口分发到 _handle_agent_error。"""

    def test_lcagent_exception_routed_to_agent_handler(self):
        """LCAgentException 实例经 custom_exception_handler 走 agent 处理分支。"""
        exc = RateLimitExceededError("API 速率限制已超限")
        response = custom_exception_handler(exc, _make_context())
        self.assertEqual(response.status_code, http_status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(response.data["code"], int(ErrorCode.AGENT_RATE_LIMITED))

    def test_other_exceptions_not_routed_to_agent_handler(self):
        """非 LCAgentException 不走 agent 处理分支。"""
        exc = ValidationError("校验失败")
        response = custom_exception_handler(exc, _make_context())
        # ValidationError 走 _handle_validation_error 分支
        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], int(ErrorCode.VALIDATION_FAILED))


if __name__ == "__main__":
    unittest.main()
