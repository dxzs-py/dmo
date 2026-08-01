"""ApprovalMiddleware 集成测试（spec: fix-tool-approval-and-cross-browser-sync-integrity Task 2.8）。

验证两项根因修复：
    1. Fix 1 — BaseAgentBuilder 显式注入 ApprovalMiddleware 到 chat 代理模式中间件栈
       （修复 chat 模式 AgentType.BASE 工具调用全部直接执行、无审批流程的根因）
    2. Fix 2 — ApprovalMiddleware.aafter_model 接入 is_auto_approve 白名单预检 +
       修复 ``policy is None: continue`` 缺陷（未知工具默认进入审批批次，安全第一）

测试矩阵：
    - base_builder 注入 ApprovalMiddleware（build_middleware 返回空栈时显式注入）
    - base_builder 不重复注入（build_middleware 已含 ApprovalMiddleware 时保持单一实例）
    - 白名单工具（get_current_time）→ auto_approved_tool_calls，不 interrupt
    - 未知工具（unknown_tool）→ approval_requests（而非原缺陷的 continue 放行）
    - shell_exec "ollama list" → approval_requests（CONTROLLED，因 ollama 不在命令白名单）

运行方式：
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    conda activate langchain_xm
    python -m pytest tests/test_approval_middleware_integration.py -v
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

# Django 环境初始化（middleware / policies 顶层导入 Django 模型与 settings，需 setup）
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django
import django.apps

if not django.apps.apps.ready:
    django.setup()

from langchain_core.messages import AIMessage

from Django_xm.apps.agent_hub.approval.middleware import ApprovalMiddleware
from Django_xm.apps.agent_hub.builders.base_builder import BaseAgentBuilder
from Django_xm.apps.agent_hub.config import AgentType
from Django_xm.common.risk_levels import RiskLevel


def _make_state(tool_calls: list[dict]) -> dict:
    """构建包含单个 AIMessage（带 tool_calls）的 LangGraph state。

    Args:
        tool_calls: tool_call 字典列表，每项含 name/args/id

    Returns:
        ``{"messages": [AIMessage]}`` 状态字典
    """
    return {
        "messages": [
            AIMessage(content="", tool_calls=tool_calls),
        ],
    }


def _make_runtime() -> SimpleNamespace:
    """构建主 agent runtime（无 subagent_context，chat_session_id 为空）。

    Returns:
        SimpleNamespace 模拟 runtime 对象，context 为空字典
    """
    return SimpleNamespace(context={})


class TestBaseBuilderInjectsApprovalMiddleware(unittest.IsolatedAsyncioTestCase):
    """Fix 1：BaseAgentBuilder 必须显式注入 ApprovalMiddleware。

    修复根因：``build_middleware`` → ``CapabilityRegistry.build_middleware_for_agent``
    不一定为 BASE/RAG/SAFE_RAG 注入 ApprovalMiddleware，导致 chat 代理模式工具调用
    全部直接执行、无审批流程。
    """

    @staticmethod
    def _make_config() -> SimpleNamespace:
        """构建最小化 AgentConfig mock，满足 _build_internal 访问需求。"""
        return SimpleNamespace(
            agent_type=AgentType.BASE,
            system_prompt=None,
        )

    async def test_base_builder_injects_approval_middleware_when_absent(self):
        """build_middleware 返回空栈时，_build_internal 显式注入 ApprovalMiddleware。"""
        builder = BaseAgentBuilder()
        config = self._make_config()
        captured: dict = {}

        def fake_create_agent(**kwargs):
            captured["kwargs"] = kwargs
            return "fake_graph"

        with (
            patch(
                "Django_xm.apps.agent_hub.middleware.build_middleware",
                return_value=[],
            ),
            patch(
                "Django_xm.apps.agent_hub.model_resolver.resolve_model",
                return_value="fake_model",
            ),
            patch(
                "Django_xm.apps.agent_hub.tool_resolver.resolve_tools",
                new=AsyncMock(return_value=[]),
            ),
            patch("langchain.agents.create_agent", side_effect=fake_create_agent),
            patch(
                "Django_xm.apps.agent_hub.builders._common._build_common_agent_kwargs",
                side_effect=lambda c, kw: kw,
            ),
            patch.object(
                builder,
                "_build_system_prompt",
                new=AsyncMock(return_value="fake_prompt"),
            ),
        ):
            graph = await builder._build_internal(config)

        # agent 已构建
        self.assertEqual(graph, "fake_graph")

        # middleware 栈传入 create_agent 且包含 ApprovalMiddleware 实例
        middleware = captured["kwargs"].get("middleware")
        self.assertIsNotNone(middleware, "middleware 必须传入 create_agent")
        approval_mw = [m for m in middleware if isinstance(m, ApprovalMiddleware)]
        self.assertEqual(
            len(approval_mw),
            1,
            "栈中必须有且仅有一个 ApprovalMiddleware 实例",
        )

    async def test_base_builder_does_not_duplicate_approval_middleware(self):
        """build_middleware 已含 ApprovalMiddleware 时，不重复注入。"""
        builder = BaseAgentBuilder()
        config = self._make_config()
        existing_mw = ApprovalMiddleware()
        captured: dict = {}

        def fake_create_agent(**kwargs):
            captured["kwargs"] = kwargs
            return "fake_graph"

        with (
            patch(
                "Django_xm.apps.agent_hub.middleware.build_middleware",
                return_value=[existing_mw],
            ),
            patch(
                "Django_xm.apps.agent_hub.model_resolver.resolve_model",
                return_value="fake_model",
            ),
            patch(
                "Django_xm.apps.agent_hub.tool_resolver.resolve_tools",
                new=AsyncMock(return_value=[]),
            ),
            patch("langchain.agents.create_agent", side_effect=fake_create_agent),
            patch(
                "Django_xm.apps.agent_hub.builders._common._build_common_agent_kwargs",
                side_effect=lambda c, kw: kw,
            ),
            patch.object(
                builder,
                "_build_system_prompt",
                new=AsyncMock(return_value="fake_prompt"),
            ),
        ):
            await builder._build_internal(config)

        middleware = captured["kwargs"].get("middleware")
        approval_mw = [m for m in middleware if isinstance(m, ApprovalMiddleware)]
        self.assertEqual(
            len(approval_mw),
            1,
            "已存在 ApprovalMiddleware 时不应重复注入",
        )
        # 应为同一实例（复用而非新建）
        self.assertIs(approval_mw[0], existing_mw)


class TestApprovalMiddlewareAutoApproveAndUnknownTool(unittest.IsolatedAsyncioTestCase):
    """Fix 2：is_auto_approve 白名单预检 + policy is None 缺陷修复。

    修复前：``if policy is None: continue`` 未知工具直接放行（既不审批也不审计）。
    修复后：
        - 白名单工具（is_auto_approve=True）→ auto_approved_tool_calls，仅审计
        - 未知工具（无策略）→ approval_requests，默认 CONTROLLED 进入审批批次
        - 已注册策略工具 → 走原 assess_risk 逻辑
    """

    async def test_whitelist_tool_auto_approved(self):
        """白名单工具 get_current_time 进入 auto_approved_tool_calls，不 interrupt。"""
        middleware = ApprovalMiddleware()
        state = _make_state([{"name": "get_current_time", "args": {}, "id": "tc_wl"}])
        runtime = _make_runtime()

        # 拦截 interrupt（白名单工具不应触发）
        interrupt_mock = MagicMock(return_value={})
        # 拦截 SAFE 审计（避免 tool_call_lifecycle 服务依赖）
        audit_mock = AsyncMock()

        with (
            patch(
                "Django_xm.apps.agent_hub.approval.middleware.interrupt",
                interrupt_mock,
            ),
            patch.object(
                ApprovalMiddleware,
                "_audit_auto_approved_tools",
                audit_mock,
            ),
        ):
            result = await middleware.aafter_model(state, runtime)

        # 白名单工具不触发 interrupt
        interrupt_mock.assert_not_called()
        # 审计被调用（auto_approved_tool_calls 非空）
        audit_mock.assert_awaited_once()
        # 审计参数：第一个位置参数为 auto_approved tool_call 列表
        audit_args = audit_mock.call_args
        auto_calls = audit_args.args[0]
        self.assertEqual(len(auto_calls), 1)
        self.assertEqual(auto_calls[0]["tool_name"], "get_current_time")
        self.assertEqual(auto_calls[0]["tool_call_id"], "tc_wl")
        # 无需审批时返回 None（正常执行）
        self.assertIsNone(result)

    async def test_unknown_tool_enters_approval_batch(self):
        """未知工具（unknown_tool）进入 approval_requests，而非 continue 放行。"""
        middleware = ApprovalMiddleware()
        state = _make_state([{"name": "unknown_tool", "args": {"foo": "bar"}, "id": "tc_unknown"}])
        runtime = _make_runtime()

        captured: dict = {}

        def fake_interrupt(payload):
            captured["payload"] = payload
            # 返回批准决策，让方法正常完成
            return {req["tool_call_id"]: True for req in payload["requests"]}

        with (
            patch(
                "Django_xm.apps.agent_hub.approval.middleware.interrupt",
                side_effect=fake_interrupt,
            ),
            patch.object(
                ApprovalMiddleware,
                "_audit_auto_approved_tools",
                AsyncMock(),
            ),
        ):
            result = await middleware.aafter_model(state, runtime)

        # interrupt 被调用（有审批请求）
        self.assertIn("payload", captured, "interrupt 必须被调用")
        requests = captured["payload"]["requests"]
        self.assertEqual(len(requests), 1, "未知工具必须进入审批批次")
        req = requests[0]
        self.assertEqual(req["tool_name"], "unknown_tool")
        self.assertEqual(req["tool_call_id"], "tc_unknown")
        self.assertEqual(req["risk_level"], RiskLevel.CONTROLLED.value)
        self.assertTrue(req["_approval"])
        # 返回修订后的 messages
        self.assertIsNotNone(result)
        self.assertIn("messages", result)

    async def test_shell_exec_ollama_list_enters_approval(self):
        """shell_exec "ollama list" 进入 approval_requests（CONTROLLED）。

        ollama 不在命令白名单、不命中黑名单、不含 HIGH_RISK_KEYWORDS，
        assess_risk 返回 CONTROLLED，必须进入审批批次。
        """
        middleware = ApprovalMiddleware()
        state = _make_state([{"name": "shell_exec", "args": {"command": "ollama list"}, "id": "tc_ollama"}])
        runtime = _make_runtime()

        captured: dict = {}

        def fake_interrupt(payload):
            captured["payload"] = payload
            return {req["tool_call_id"]: True for req in payload["requests"]}

        with (
            patch(
                "Django_xm.apps.agent_hub.approval.middleware.interrupt",
                side_effect=fake_interrupt,
            ),
            patch.object(
                ApprovalMiddleware,
                "_audit_auto_approved_tools",
                AsyncMock(),
            ),
        ):
            await middleware.aafter_model(state, runtime)

        self.assertIn("payload", captured, "shell_exec ollama list 必须触发审批")
        requests = captured["payload"]["requests"]
        self.assertEqual(len(requests), 1)
        req = requests[0]
        self.assertEqual(req["tool_name"], "shell_exec")
        self.assertEqual(req["tool_call_id"], "tc_ollama")
        self.assertEqual(req["risk_level"], RiskLevel.CONTROLLED.value)
        # operation 应为命令内容
        self.assertIn("ollama list", req["operation"])

    async def test_whitelist_and_unknown_tool_mixed_batch(self):
        """混合场景：白名单工具自动通过 + 未知工具进入审批批次。"""
        middleware = ApprovalMiddleware()
        state = _make_state(
            [
                {"name": "get_current_time", "args": {}, "id": "tc_wl"},
                {"name": "unknown_danger", "args": {"x": 1}, "id": "tc_unk"},
            ]
        )
        runtime = _make_runtime()

        captured: dict = {}

        def fake_interrupt(payload):
            captured["payload"] = payload
            return {req["tool_call_id"]: True for req in payload["requests"]}

        audit_mock = AsyncMock()

        with (
            patch(
                "Django_xm.apps.agent_hub.approval.middleware.interrupt",
                side_effect=fake_interrupt,
            ),
            patch.object(
                ApprovalMiddleware,
                "_audit_auto_approved_tools",
                audit_mock,
            ),
        ):
            await middleware.aafter_model(state, runtime)

        # 白名单工具进入审计
        audit_mock.assert_awaited_once()
        auto_calls = audit_mock.call_args.args[0]
        self.assertEqual(len(auto_calls), 1)
        self.assertEqual(auto_calls[0]["tool_name"], "get_current_time")

        # 未知工具进入审批批次
        requests = captured["payload"]["requests"]
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["tool_name"], "unknown_danger")
        self.assertEqual(requests[0]["risk_level"], RiskLevel.CONTROLLED.value)


if __name__ == "__main__":
    unittest.main()
