"""SubAgentNestingMiddleware 深度限制与 description 透传单元测试（Task 1 / Task 2.4）。

覆盖：
1. Task 1 深度限制（标记 + 拦截机制，不抛异常，避免破坏主 agent 执行流）：
   - parent_depth < MAX_AGENT_DEPTH 正常放行（subagent_depth = parent_depth + 1，
     不携带 blocked 标记）；
   - parent_depth = MAX_AGENT_DEPTH 时第 4 层被阻止（before_model 写入
     subagent_depth_blocked=True；wrap_model_call / awrap_model_call 拦截模型调用
     并返回含当前/最大深度的错误 AIMessage，真实模型零调用）；
   - 集成验证：create_agent + 真实 SubAgentNestingMiddleware，超限子 agent 正常结束、
     错误消息出现在最终 messages（deepagents task 工具会将其转为 ToolMessage 回传
     主 agent，与超限拒绝派生返回错误语义一致）。
2. Task 2.4 description 透传：before_model 将子 agent 描述写入 state（subagent_description）；
   SubAgentToolEventMiddleware._forward_event 对 depth>0 事件透传 description，
   主 agent（depth=0）不透传。

运行：
    python manage.py test agent_hub
或（不依赖 Django settings/DB）：
    python -m unittest Django_xm.apps.agent_hub.tests.test_subagent_nesting
"""

import asyncio
import unittest
from unittest import mock

from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage

from Django_xm.apps.agent_hub.builders.subagent_support import (
    MAX_AGENT_DEPTH,
    SubAgentNestingMiddleware,
    SubAgentToolEventMiddleware,
    _build_depth_blocked_message,
    _extract_nesting_from_state,
)


def _patch_env(parent_depth: int, agent_name: str = "web-researcher"):
    """mock before_model 依赖的父 configurable 与 agent 名称读取。"""
    patchers = (
        mock.patch(
            "Django_xm.apps.agent_hub.builders.subagent_support._read_configurable",
            return_value={"depth": parent_depth, "agent_path": ["main"]},
        ),
        mock.patch(
            "Django_xm.apps.agent_hub.builders.subagent_support._read_agent_name",
            return_value=agent_name,
        ),
    )
    return patchers


class SubAgentNestingDepthLimitTests(unittest.TestCase):
    """Task 1：deepagents 子代理最大深度限制（标记 + 模型调用拦截）。"""

    def setUp(self):
        self.middleware = SubAgentNestingMiddleware()

    def _call_before_model(self, parent_depth: int, agent_name: str = "web-researcher") -> dict:
        """在 mock 环境下调用 before_model（risk_ceiling/description 解析一并 mock）。"""
        p1, p2 = _patch_env(parent_depth, agent_name)
        with (
            p1,
            p2,
            mock.patch.object(self.middleware, "_resolve_risk_ceiling", return_value="controlled"),
            mock.patch.object(self.middleware, "_resolve_description", return_value="测试描述"),
        ):
            return self.middleware.before_model({}, None)

    def test_below_limit_passes(self):
        """depth < MAX_AGENT_DEPTH 时正常放行，不携带 blocked 标记。"""
        for parent_depth in range(MAX_AGENT_DEPTH):
            result = self._call_before_model(parent_depth)
            self.assertEqual(result["subagent_depth"], parent_depth + 1)
            # 层级字段完整性（path/risk_ceiling/description 均写入）
            self.assertEqual(result["subagent_path"], ["main", "web-researcher"])
            self.assertIn("subagent_risk_ceiling", result)
            self.assertIn("subagent_description", result)
            # 未超限不标记
            self.assertNotIn("subagent_depth_blocked", result)

    def test_at_limit_blocked_marked(self):
        """parent_depth = MAX_AGENT_DEPTH 时第 4 层被阻止：写入 blocked 标记（不抛异常）。"""
        result = self._call_before_model(MAX_AGENT_DEPTH)
        self.assertEqual(result["subagent_depth"], MAX_AGENT_DEPTH + 1)
        self.assertIs(result.get("subagent_depth_blocked"), True)

    def test_negative_parent_depth_normalized(self):
        """负数 parent_depth 归一化为 0，不误拦截（放行为深度 1）。"""
        result = self._call_before_model(-5)
        self.assertEqual(result["subagent_depth"], 1)
        self.assertNotIn("subagent_depth_blocked", result)

    def test_depth_blocked_message_format(self):
        """超限错误消息含当前深度与最大深度（与 agent_context.is_max_depth_reached 语义一致）。"""
        message = _build_depth_blocked_message(MAX_AGENT_DEPTH + 1)
        self.assertIn(str(MAX_AGENT_DEPTH), message)
        self.assertIn(f"当前深度={MAX_AGENT_DEPTH + 1}", message)

    def test_wrap_model_call_intercepts(self):
        """wrap_model_call 拦截超限子 agent 的模型调用，返回错误 AIMessage。"""
        call_should_not_run = {"called": False}

        def fake_call(request):
            call_should_not_run["called"] = True
            return AIMessage(content="真实模型输出")

        request = mock.Mock()
        request.state = {"subagent_depth_blocked": True, "subagent_depth": MAX_AGENT_DEPTH + 1}
        result = self.middleware.wrap_model_call(request, fake_call)
        self.assertFalse(call_should_not_run["called"], "超限时真实模型不应被调用")
        self.assertIsInstance(result, AIMessage)
        self.assertIn("已达到最大子代理嵌套深度", result.content)
        self.assertIn(f"当前深度={MAX_AGENT_DEPTH + 1}", result.content)

    def test_wrap_model_call_passthrough_when_not_blocked(self):
        """未超限时 wrap_model_call 透传真实模型调用。"""
        request = mock.Mock()
        request.state = {}

        def fake_call(req):
            return AIMessage(content="正常回复")

        result = self.middleware.wrap_model_call(request, fake_call)
        self.assertEqual(result.content, "正常回复")

    def test_awrap_model_call_intercepts(self):
        """awrap_model_call 拦截超限子 agent 的模型调用（深度研究 astream 链路）。"""
        call_should_not_run = {"called": False}

        async def fake_call(request):
            call_should_not_run["called"] = True
            return AIMessage(content="真实模型输出")

        async def _run():
            request = mock.Mock()
            request.state = {"subagent_depth_blocked": True, "subagent_depth": MAX_AGENT_DEPTH + 1}
            return await self.middleware.awrap_model_call(request, fake_call)

        result = asyncio.run(_run())
        self.assertFalse(call_should_not_run["called"])
        self.assertIsInstance(result, AIMessage)
        self.assertIn("已达到最大子代理嵌套深度", result.content)

    def test_full_agent_blocked_at_depth(self):
        """集成验证：create_agent + 真实中间件，超限子 agent 正常结束、错误消息可见。

        子 agent（即被拦截的 agent）不执行任何任务，最终 messages 末尾为错误
        AIMessage——deepagents task 工具会将其文本转为 ToolMessage 回传主 agent，
        主 agent 调整策略继续（不破坏执行流）。
        """
        middleware = SubAgentNestingMiddleware()
        p1, p2 = _patch_env(MAX_AGENT_DEPTH, "web-researcher")
        model = GenericFakeChatModel(messages=iter(["真实模型被调用——不应发生"]))
        with (
            p1,
            p2,
            mock.patch.object(middleware, "_resolve_risk_ceiling", return_value="controlled"),
            mock.patch.object(middleware, "_resolve_description", return_value="测试描述"),
        ):
            agent = create_agent(model=model, middleware=[middleware], tools=[])
            result = agent.invoke({"messages": [HumanMessage(content="执行研究任务")]})

        last_message = result["messages"][-1]
        self.assertIsInstance(last_message, AIMessage)
        self.assertIn("已达到最大子代理嵌套深度", last_message.content)
        self.assertIn(f"当前深度={MAX_AGENT_DEPTH + 1}", last_message.content)
        self.assertNotIn("真实模型被调用", last_message.content)

    def test_full_agent_passes_below_depth(self):
        """集成验证：深度未超限时模型正常被调用。"""
        middleware = SubAgentNestingMiddleware()
        p1, p2 = _patch_env(1, "web-researcher")
        model = GenericFakeChatModel(messages=iter(["正常回复"]))
        with (
            p1,
            p2,
            mock.patch.object(middleware, "_resolve_risk_ceiling", return_value="controlled"),
            mock.patch.object(middleware, "_resolve_description", return_value="测试描述"),
        ):
            agent = create_agent(model=model, middleware=[middleware], tools=[])
            result = agent.invoke({"messages": [HumanMessage(content="执行研究任务")]})

        self.assertEqual(result["messages"][-1].content, "正常回复")

    def test_description_written_to_state(self):
        """before_model 将子 agent 任务目标描述写入 state（subagent_description）。"""
        p1, p2 = _patch_env(1, "web-researcher")
        with p1, p2, mock.patch.object(
            self.middleware,
            "_resolve_risk_ceiling",
            return_value="controlled",
        ), mock.patch.object(
            self.middleware,
            "_resolve_description",
            return_value="网络搜索和信息整理专家，负责从互联网搜索和整理研究信息",
        ):
            result = self.middleware.before_model({}, None)
        self.assertEqual(result["subagent_description"], "网络搜索和信息整理专家，负责从互联网搜索和整理研究信息")

    def test_extract_nesting_includes_description(self):
        """_extract_nesting_from_state 提取 description 字段（供事件透传）。"""
        nesting = _extract_nesting_from_state(
            {
                "subagent_depth": 2,
                "subagent_path": ["main", "web-researcher"],
                "subagent_description": "文档分析和知识提取专家",
            }
        )
        self.assertEqual(nesting["depth"], 2)
        self.assertEqual(nesting["description"], "文档分析和知识提取专家")

    def test_extract_nesting_empty_state_defaults(self):
        """空 state 提取出安全的默认值（深度 0 / 空描述）。"""
        nesting = _extract_nesting_from_state(None)
        self.assertEqual(nesting["depth"], 0)
        self.assertEqual(nesting["agent_path"], [])
        self.assertEqual(nesting["description"], "")


class SubAgentToolEventForwardTests(unittest.TestCase):
    """Task 2.4：_forward_event 透传 description（仅子 agent 事件）。"""

    def test_subagent_event_passes_description(self):
        """depth>0 的子 agent 事件透传 description。"""
        received: dict = {}

        async def on_tool_event(event_type, tool_call_id, tool_name, **kwargs):
            received["event_type"] = event_type
            received["kwargs"] = kwargs

        asyncio.run(
            SubAgentToolEventMiddleware._forward_event(
                on_tool_event,
                "tool_call_pending",
                "tc_sub",
                "search",
                parameters={"q": "langchain"},
                nesting={
                    "depth": 2,
                    "agent_path": ["main", "web-researcher"],
                    "description": "网络搜索和信息整理专家",
                },
            )
        )
        self.assertEqual(received["kwargs"]["description"], "网络搜索和信息整理专家")
        self.assertEqual(received["kwargs"]["depth"], 2)

    def test_main_agent_event_omits_description(self):
        """主 agent（depth=0）事件不透传 description 与嵌套字段。"""
        received: dict = {}

        async def on_tool_event(event_type, tool_call_id, tool_name, **kwargs):
            received["kwargs"] = kwargs

        asyncio.run(
            SubAgentToolEventMiddleware._forward_event(
                on_tool_event,
                "tool_call_completed",
                "tc_main",
                "write_file",
                result="ok",
                nesting={"depth": 0, "agent_path": [], "description": ""},
            )
        )
        self.assertNotIn("description", received["kwargs"])
        self.assertNotIn("depth", received["kwargs"])
        self.assertEqual(received["kwargs"]["result"], "ok")
