"""异常基类统一测试（cq-07）。

覆盖：
- LCAgentException / ModelCallError（ai_engine）均为
  Django_xm.common.exceptions.BaseAppError 的实例
- LCAgentException 构造签名与 to_dict() 字段零变化锁定
  （显式传参与全默认两条路径）

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.ai_engine.services.tests.test_exceptions --settings=Django_xm.settings.test
"""

import unittest

from Django_xm.apps.ai_engine.services.exceptions import (
    LCAgentException,
    ModelCallError,
)
from Django_xm.common.exceptions import BaseAppError


class BaseAppErrorInheritanceTests(unittest.TestCase):
    """ai_engine 异常体系统一收敛到 BaseAppError。"""

    def test_ai_engine_exceptions_inherit_base_app_error(self):
        """ai_engine 异常体系实例均为 BaseAppError。"""
        self.assertIsInstance(LCAgentException("boom"), BaseAppError)
        self.assertIsInstance(ModelCallError("boom", model_name="test-model"), BaseAppError)


class LCAgentExceptionContractTests(unittest.TestCase):
    """LCAgentException 构造签名与 to_dict 字段零变化锁定。"""

    def test_to_dict_with_explicit_fields(self):
        """显式传参：to_dict 原样返回五个字段。"""
        exc = LCAgentException(
            "模型调用失败",
            error_code="MODEL_CALL_ERROR",
            details={"model_name": "test-model"},
            recoverable=False,
            user_message="请稍后重试",
        )
        self.assertEqual(
            exc.to_dict(),
            {
                "error_code": "MODEL_CALL_ERROR",
                "message": "模型调用失败",
                "user_message": "请稍后重试",
                "details": {"model_name": "test-model"},
                "recoverable": False,
            },
        )

    def test_to_dict_with_defaults(self):
        """默认构造：error_code/details/recoverable/user_message 取默认值。"""
        exc = LCAgentException("boom")
        self.assertEqual(
            exc.to_dict(),
            {
                "error_code": "AGENT_ERROR",
                "message": "boom",
                "user_message": LCAgentException.DEFAULT_USER_MESSAGE,
                "details": {},
                "recoverable": True,
            },
        )


class BuildUserFacingErrorTests(unittest.TestCase):
    """build_user_facing_error 异常→(error_code, 用户文案) 映射（整改 A1.4）。

    消费方：chat_executor_core 失败终态广播 + session_executor research 失败
    处理。锁定映射源唯一（classify_exception 体系），文案必须中文可行动、
    不含异常原文。
    """

    def test_timeout_maps_to_model_call_error_with_actionable_message(self):
        from Django_xm.apps.ai_engine.services.exceptions import build_user_facing_error

        error_code, user_message = build_user_facing_error(TimeoutError("request timed out"))
        self.assertEqual(error_code, "MODEL_CALL_ERROR")
        self.assertEqual(user_message, "请求超时，请稍后重试")

    def test_connection_error_maps_to_actionable_message(self):
        from Django_xm.apps.ai_engine.services.exceptions import build_user_facing_error

        error_code, user_message = build_user_facing_error(ConnectionError("connection reset"))
        self.assertEqual(error_code, "MODEL_CALL_ERROR")
        self.assertEqual(user_message, "网络连接失败，请检查网络后重试")

    def test_openai_402_maps_to_payment_message(self):
        """openai 402（余额不足）→ 余额文案（审计错误映射表 #3 场景）。"""
        import httpx
        from openai import APIStatusError

        from Django_xm.apps.ai_engine.services.exceptions import build_user_facing_error

        request = httpx.Request("POST", "https://api.test/v1/chat/completions")
        response = httpx.Response(402, request=request)
        exc = APIStatusError("insufficient_quota", response=response, body=None)
        error_code, user_message = build_user_facing_error(exc)
        self.assertEqual(error_code, "MODEL_CALL_ERROR")
        self.assertIn("余额", user_message)
        self.assertIn("切换模型", user_message)

    def test_generic_exception_maps_to_unexpected_error(self):
        from Django_xm.apps.ai_engine.services.exceptions import build_user_facing_error

        error_code, user_message = build_user_facing_error(
            RuntimeError("主模型不可用且无可用 fallback 模型")
        )
        self.assertEqual(error_code, "UNEXPECTED_ERROR")
        self.assertEqual(user_message, "服务暂时不可用，请稍后重试")
        self.assertNotIn("fallback", user_message)

    def test_lc_agent_exception_passes_through(self):
        """LCAgentException 体系原样透传自身 error_code/user_message。"""
        from Django_xm.apps.ai_engine.services.exceptions import (
            GuardrailsValidationError,
            build_user_facing_error,
        )

        error_code, user_message = build_user_facing_error(
            GuardrailsValidationError("长度超限", validation_type="input")
        )
        self.assertEqual(error_code, "GUARDRAILS_VALIDATION_ERROR")
        self.assertEqual(user_message, "内容验证未通过，请调整后重试")


if __name__ == "__main__":
    unittest.main()
