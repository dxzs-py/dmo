"""中间件栈顺序守护测试（fix-review-round3 2.11）。

把 base_builder.py「顺序约束」6 行注释转为可执行断言（langchain factory
倒序连边：注册 [..., ToolEvent, Content] → aafter_model 实际执行流
Content → ToolEvent，见 factory.py _add_middleware_edge 逆序链接）：

1. SubAgent 注册序：Nesting → ToolEvent → Content（ToolEvent 若注册在
   Content 之后，执行流反转为 ToolEvent 先跑，position 少算本轮正文长度
   → 前端内联错位）；
2. 安全类注册序（语义核定）：guardrails（内容校验）先于 ApprovalMiddleware
   注册 —— 执行流（注册序倒序）先到 Approval 再到 guardrails，审批作为
   工具执行安全边界最外环优先拦截（与 approval/middleware fail-closed 对齐）；
3. 收敛点保底段相对次序：Approval → ContextManager → ToolExecutionTimeout
   （注册序），context/timeout 为非安全类兜底。

经 BaseAgentBuilder._build_internal 真实装配路径断言（create_agent 打桩），
若 builder 装配顺序被改动，本测试失败即守护点。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.agent_hub.tests.test_middleware_ordering
"""

import asyncio
import unittest
from unittest import mock

from django.test import SimpleTestCase

from Django_xm.apps.agent_hub.approval.middleware import ApprovalMiddleware
from Django_xm.apps.agent_hub.builders.base_builder import BaseAgentBuilder
from Django_xm.apps.agent_hub.builders.subagent_support import (
    SubAgentContentMiddleware,
    SubAgentNestingMiddleware,
    SubAgentToolEventMiddleware,
)
from Django_xm.apps.agent_hub.config import AgentConfig, AgentType
from Django_xm.apps.ai_engine.services.tool_timeout import ToolExecutionTimeoutMiddleware
from Django_xm.apps.context_manager.middleware import ContextManagerMiddleware


def _first_index(stack: list, cls: type) -> int:
    for i, m in enumerate(stack):
        if isinstance(m, cls):
            return i
    return -1


class MiddlewareOrderingTests(SimpleTestCase):
    """最终中间件栈顺序依赖约束断言。"""

    def _build_and_capture(self, config: AgentConfig) -> list:
        """走 BaseAgentBuilder._build_internal 真实装配，捕获 create_agent 的 middleware 栈。"""
        captured: dict = {}

        def fake_create_agent(**kwargs):
            captured.update(kwargs)
            return mock.MagicMock(name="agent-graph")

        with (
            mock.patch(
                "Django_xm.apps.agent_hub.tool_resolver.resolve_tools",
                new=mock.AsyncMock(return_value=[mock.MagicMock(name="tool")]),
            ),
            mock.patch(
                "Django_xm.apps.agent_hub.builders._common.build_dynamic_context_prompt",
                new=mock.AsyncMock(return_value=None),
            ),
            mock.patch(
                "Django_xm.apps.ai_engine.prompts.system_prompts.get_system_prompt",
                return_value="system prompt for ordering test",
            ),
            mock.patch("langchain.agents.create_agent", side_effect=fake_create_agent),
        ):
            builder = BaseAgentBuilder()
            asyncio.run(builder._build_internal(config))

        self.assertIn("middleware", captured, "create_agent 未收到 middleware 栈")
        return captured["middleware"]

    def test_subagent_middleware_registration_order(self):
        """注册序必须为 Nesting → ToolEvent → Content（Content 执行流先跑）。"""
        config = AgentConfig(agent_type=AgentType.BASE, model_name="gpt-4o", user_id=1, session_id="t-1")
        stack = self._build_and_capture(config)

        idx_nesting = _first_index(stack, SubAgentNestingMiddleware)
        idx_toolevent = _first_index(stack, SubAgentToolEventMiddleware)
        idx_content = _first_index(stack, SubAgentContentMiddleware)

        self.assertGreaterEqual(idx_nesting, 0)
        # 红线（base_builder 注释）：ToolEvent 注册在 Content 之前——
        # 执行流（倒序）Content 先转发本轮正文、ToolEvent 后发 PENDING，
        # position 含本轮文本长度；反序则前端内联错位。
        self.assertLess(idx_toolevent, idx_content, "ToolEvent 必须注册在 Content 之前（倒序执行语义）")
        self.assertLess(idx_nesting, idx_toolevent, "Nesting 应最先注册（执行流最后、字段先行写入）")

    def test_approval_registered_after_guardrails(self):
        """安全类注册序：guardrails 先注册、Approval 后注册（执行流 Approval 先拦截）。"""
        config = AgentConfig(
            agent_type=AgentType.BASE,
            model_name="gpt-4o",
            user_id=1,
            session_id="t-1",
            capabilities=["context_management", "guardrails", "rate_limit"],
            enable_guardrails=True,
        )
        stack = self._build_and_capture(config)

        idx_guardrails = _first_index(stack, type(_first_guardrails_instance(stack)))
        idx_approval = _first_index(stack, ApprovalMiddleware)

        self.assertGreaterEqual(idx_guardrails, 0, "enable_guardrails=True 时栈中应有 guardrails 中间件")
        self.assertGreaterEqual(idx_approval, 0)
        self.assertEqual(_count(stack, ApprovalMiddleware), 1, "ApprovalMiddleware 恰好注入一次（幂等）")
        self.assertLess(
            idx_guardrails,
            idx_approval,
            "Approval 必须注册在 guardrails 之后：倒序执行流中审批先于内容校验拦截（安全边界最外环）",
        )

    def test_convergence_fallback_order_approval_context_tooldown(self):
        """收敛点保底注册序：Approval → ContextManager → ToolExecutionTimeout。"""
        from Django_xm.apps.agent_hub.middleware import build_middleware

        config = AgentConfig(
            agent_type=AgentType.BASE,
            model_name="gpt-4o",
            user_id=1,
            session_id="t-1",
            capabilities=[],
        )
        stack = build_middleware(config)

        idx_approval = _first_index(stack, ApprovalMiddleware)
        idx_context = _first_index(stack, ContextManagerMiddleware)
        idx_timeout = _first_index(stack, ToolExecutionTimeoutMiddleware)

        self.assertGreaterEqual(idx_approval, 0)
        self.assertLess(idx_approval, idx_context)
        self.assertLess(idx_context, idx_timeout, "栈末应为 ToolExecutionTimeoutMiddleware（保底追加位）")


def _count(stack: list, cls: type) -> int:
    return sum(1 for m in stack if isinstance(m, cls))


def _first_guardrails_instance(stack: list):
    """取栈中第一个 guardrails 家族实例（GuardrailsMiddleware / RateLimit / PII）。

    guardrails 能力产出的具体类经 build_middleware_stack 组装，此处按
    guardrails 模块家族判定，避免对内部类名硬编码。
    """
    from Django_xm.apps.ai_engine.guardrails import middleware as guardrails_middleware_module

    for m in stack:
        if isinstance(type(m), type) and type(m).__module__ == guardrails_middleware_module.__name__:
            return m
    raise AssertionError("栈中未找到 guardrails 家族中间件")


if __name__ == "__main__":
    unittest.main()
