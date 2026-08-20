"""agent_hub 异常基类统一测试（cq-07）。

覆盖：
- AgentHubError / AgentCreationError / PreflightCheckError（agent_hub）均为
  Django_xm.common.exceptions.BaseAppError 的实例
  （cq-07 异常基类统一：agent_hub 异常体系收敛到 BaseAppError）

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.agent_hub.tests.test_exceptions --settings=Django_xm.settings.test
"""

import unittest

from Django_xm.apps.agent_hub.exceptions import (
    AgentCreationError,
    AgentHubError,
    PreflightCheckError,
)
from Django_xm.common.exceptions import BaseAppError


class AgentHubExceptionInheritanceTests(unittest.TestCase):
    """agent_hub 异常体系统一收敛到 BaseAppError。"""

    def test_agent_hub_exceptions_inherit_base_app_error(self):
        """agent_hub 异常体系实例均为 BaseAppError。"""
        self.assertIsInstance(AgentHubError("boom"), BaseAppError)
        self.assertIsInstance(AgentCreationError("boom"), BaseAppError)
        self.assertIsInstance(PreflightCheckError("boom", issues=["缺少模型配置"]), BaseAppError)


if __name__ == "__main__":
    unittest.main()
