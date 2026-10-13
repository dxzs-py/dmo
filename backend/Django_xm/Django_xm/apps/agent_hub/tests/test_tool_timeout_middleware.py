"""ensure_tool_timeout_middleware 收敛点注入单元测试（B6/P2-13 不对齐项②收敛点，自 ai_engine 迁入）。

覆盖：
- ensure_tool_timeout_middleware：收敛点注入 + 幂等跳过

运行：
    python manage.py test Django_xm.apps.agent_hub.tests.test_tool_timeout_middleware
"""

from django.test import SimpleTestCase

from Django_xm.apps.agent_hub.middleware import ensure_tool_timeout_middleware
from Django_xm.apps.ai_engine.services.tool_timeout import ToolExecutionTimeoutMiddleware


class EnsureToolTimeoutMiddlewareTests(SimpleTestCase):
    """收敛点注入（base/deep/subagent 三 builder 公共路径）。"""

    def test_injects_when_missing(self):
        stack: list = []
        self.assertTrue(ensure_tool_timeout_middleware(stack))
        self.assertEqual(len(stack), 1)
        self.assertIsInstance(stack[0], ToolExecutionTimeoutMiddleware)

    def test_idempotent_when_present(self):
        mw = ToolExecutionTimeoutMiddleware()
        stack: list = [mw]
        self.assertFalse(ensure_tool_timeout_middleware(stack))
        self.assertEqual(len(stack), 1)
        self.assertIs(stack[0], mw)
