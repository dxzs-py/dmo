"""异常基类统一测试（cq-07）。

覆盖：
- LCAgentException / ModelCallError（ai_engine）与 AgentHubError /
  AgentCreationError / PreflightCheckError（agent_hub）均为
  Django_xm.apps.core.exceptions.BaseAppError 的实例
- LCAgentException 构造签名与 to_dict() 字段零变化锁定
  （显式传参与全默认两条路径）

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.ai_engine.services.tests.test_exceptions --settings=Django_xm.settings.test
"""

import unittest

from Django_xm.apps.agent_hub.exceptions import (
    AgentCreationError,
    AgentHubError,
    PreflightCheckError,
)
from Django_xm.apps.ai_engine.services.exceptions import (
    LCAgentException,
    ModelCallError,
)
from Django_xm.apps.core.exceptions import BaseAppError


class BaseAppErrorInheritanceTests(unittest.TestCase):
    """各 app 异常体系统一收敛到 BaseAppError。"""

    def test_ai_engine_exceptions_inherit_base_app_error(self):
        """ai_engine 异常体系实例均为 BaseAppError。"""
        self.assertIsInstance(LCAgentException("boom"), BaseAppError)
        self.assertIsInstance(ModelCallError("boom", model_name="test-model"), BaseAppError)

    def test_agent_hub_exceptions_inherit_base_app_error(self):
        """agent_hub 异常体系实例均为 BaseAppError。"""
        self.assertIsInstance(AgentHubError("boom"), BaseAppError)
        self.assertIsInstance(AgentCreationError("boom"), BaseAppError)
        self.assertIsInstance(PreflightCheckError("boom", issues=["缺少模型配置"]), BaseAppError)


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


if __name__ == "__main__":
    unittest.main()
