"""工具级执行超时 + Agent 全局超时默认值单元测试（B6/P2-13 不对齐项①②）。

覆盖：
- ResilienceConfig 代码级默认（settings 不参与）：soft=1800 / hard=3600，均非 None
- get_resilience_config：默认生效 / settings 覆盖 / 显式 None 禁用语义
- get_tool_execution_timeout：默认 300s / settings 覆盖
- ToolExecutionTimeoutMiddleware：快工具透传、慢工具超时返回 error ToolMessage
  （不抛异常）、timeout=None 不限制
- ensure_tool_timeout_middleware：收敛点注入 + 幂等跳过

运行：
    python manage.py test Django_xm.apps.ai_engine.tests.test_tool_timeout_middleware
"""

import asyncio
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings
from langchain_core.messages import ToolMessage

from Django_xm.apps.ai_engine.services.agent_resilience import (
    DEFAULT_HARD_TIMEOUT,
    DEFAULT_SOFT_TIMEOUT,
    ResilienceConfig,
    get_resilience_config,
)
from Django_xm.apps.ai_engine.services.tool_timeout import (
    DEFAULT_TOOL_EXECUTION_TIMEOUT,
    ToolExecutionTimeoutMiddleware,
    get_tool_execution_timeout,
)
from Django_xm.apps.agent_hub.middleware import ensure_tool_timeout_middleware


def _tool_request(name: str, call_id: str) -> SimpleNamespace:
    """构造 awrap_tool_call 所需的最小 request（鸭子类型访问面）。"""
    return SimpleNamespace(tool_call={"name": name, "args": {}, "id": call_id})


class AgentGlobalTimeoutDefaultsTests(SimpleTestCase):
    """不对齐项①：Agent 全局 soft/hard 超时默认值生效断言。"""

    def test_code_level_defaults_are_not_none(self):
        """settings 未配置时的代码级兜底（ResilienceConfig 直接实例化，不经 settings）。"""
        cfg = ResilienceConfig()
        self.assertIsNotNone(cfg.soft_timeout)
        self.assertIsNotNone(cfg.hard_timeout)
        self.assertEqual(cfg.soft_timeout, 1800.0)
        self.assertEqual(cfg.hard_timeout, 3600.0)
        self.assertEqual(DEFAULT_SOFT_TIMEOUT, 1800.0)
        self.assertEqual(DEFAULT_HARD_TIMEOUT, 3600.0)

    def test_get_resilience_config_defaults(self):
        """get_resilience_config 默认返回非 None 超时（test settings 未额外覆盖，与代码默认同值）。"""
        cfg = get_resilience_config()
        self.assertEqual(cfg.soft_timeout, 1800.0)
        self.assertEqual(cfg.hard_timeout, 3600.0)

    @override_settings(AGENT_SOFT_TIMEOUT=60.0, AGENT_HARD_TIMEOUT=120.0)
    def test_settings_override(self):
        """settings 显式配置覆盖代码默认。"""
        cfg = get_resilience_config()
        self.assertEqual(cfg.soft_timeout, 60.0)
        self.assertEqual(cfg.hard_timeout, 120.0)

    @override_settings(AGENT_SOFT_TIMEOUT=None, AGENT_HARD_TIMEOUT=None)
    def test_settings_none_disables(self):
        """显式 None = 不限制（禁用语义保留）。"""
        cfg = get_resilience_config()
        self.assertIsNone(cfg.soft_timeout)
        self.assertIsNone(cfg.hard_timeout)


class ToolExecutionTimeoutTests(SimpleTestCase):
    """不对齐项②：通用工具级缺省超时。"""

    def test_default_timeout_value(self):
        """缺省超时为 300s 量级（与 shell_exec MAX_TIMEOUT 对齐）。"""
        self.assertEqual(DEFAULT_TOOL_EXECUTION_TIMEOUT, 300.0)
        self.assertEqual(get_tool_execution_timeout(), 300.0)

    @override_settings(AGENT_TOOL_EXECUTION_TIMEOUT=0.05)
    def test_settings_override_timeout(self):
        self.assertEqual(get_tool_execution_timeout(), 0.05)

    def test_fast_tool_result_passthrough(self):
        """未超时：工具结果原样透传。"""
        mw = ToolExecutionTimeoutMiddleware(timeout=5.0)

        async def handler(request):
            return ToolMessage(content="ok", tool_call_id=request.tool_call["id"])

        result = asyncio.run(mw.awrap_tool_call(_tool_request("web_search", "call-1"), handler))
        self.assertEqual(result.content, "ok")
        self.assertEqual(result.tool_call_id, "call-1")

    def test_slow_tool_times_out_with_error_tool_message(self):
        """超时：返回 error ToolMessage（不抛异常），tool_call_id 匹配。"""
        mw = ToolExecutionTimeoutMiddleware(timeout=0.05)

        async def hanging_handler(request):
            await asyncio.sleep(5)
            return ToolMessage(content="never", tool_call_id=request.tool_call["id"])

        result = asyncio.run(mw.awrap_tool_call(_tool_request("slow_tool", "call-2"), hanging_handler))
        self.assertIsInstance(result, ToolMessage)
        self.assertEqual(result.tool_call_id, "call-2")
        self.assertIn("超时", result.content)

    def test_none_timeout_disables_limit(self):
        """timeout=None：不施加超时限制。"""
        mw = ToolExecutionTimeoutMiddleware(timeout=None)

        async def handler(request):
            await asyncio.sleep(0.1)
            return ToolMessage(content="ok", tool_call_id=request.tool_call["id"])

        result = asyncio.run(mw.awrap_tool_call(_tool_request("web_search", "call-3"), handler))
        self.assertEqual(result.content, "ok")

    @override_settings(AGENT_TOOL_EXECUTION_TIMEOUT=0.05)
    def test_default_constructor_reads_settings(self):
        """默认构造经 get_tool_execution_timeout 读取 settings。"""
        mw = ToolExecutionTimeoutMiddleware()
        self.assertEqual(mw.timeout, 0.05)


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
