"""
Agent factory 单元测试

测试核心数据处理逻辑（不依赖 Django ORM 和外部 API）。
"""
from unittest import TestCase
from unittest.mock import MagicMock, patch
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from Django_xm.apps.ai_engine.services.agent_factory import BaseAgent


class ExtractAiResponseTest(TestCase):
    """测试 _extract_ai_response 静态方法"""

    def test_finds_last_ai_message(self):
        result = {
            "messages": [
                HumanMessage(content="hi"),
                AIMessage(content="hello!"),
            ]
        }
        self.assertEqual(BaseAgent._extract_ai_response(result), "hello!")

    def test_returns_empty_string_when_no_ai_message(self):
        result = {"messages": [HumanMessage(content="hi")]}
        self.assertEqual(BaseAgent._extract_ai_response(result), "")

    def test_returns_empty_string_when_empty_messages(self):
        result = {"messages": []}
        self.assertEqual(BaseAgent._extract_ai_response(result), "")

    def test_skips_tool_messages_before_ai(self):
        result = {
            "messages": [
                HumanMessage(content="hi"),
                ToolMessage(content='{"result": "ok"}', tool_call_id="t1"),
                AIMessage(content="done"),
            ]
        }
        self.assertEqual(BaseAgent._extract_ai_response(result), "done")

    def test_uses_last_ai_message_in_reverse(self):
        result = {
            "messages": [
                AIMessage(content="first"),
                HumanMessage(content="again"),
                AIMessage(content="final"),
            ]
        }
        self.assertEqual(BaseAgent._extract_ai_response(result), "final")


class PrepareGraphInputTest(TestCase):
    """测试 _prepare_graph_input 方法"""

    def setUp(self):
        with patch.object(BaseAgent, '_build_config', return_value={"recursion_limit": 50}):
            self.agent = BaseAgent.__new__(BaseAgent)
            self.agent.user_id = None
            self.agent.session_id = None
            self.agent.model = "openai:gpt-4o"
            self.agent.tools = []
            self.agent.system_prompt = "test"
            self.agent.debug = False
            self.agent.graph = MagicMock()

    def test_builds_input_with_text_only(self):
        with patch.object(self.agent, '_build_config', return_value={"recursion_limit": 50}):
            graph_input, config = self.agent._prepare_graph_input("hello")
            msgs = graph_input["messages"]
            self.assertEqual(len(msgs), 1)
            self.assertIsInstance(msgs[0], HumanMessage)
            self.assertEqual(msgs[0].content, "hello")

    def test_builds_input_with_history(self):
        history = [AIMessage(content="previous")]
        with patch.object(self.agent, '_build_config', return_value={"recursion_limit": 50}):
            graph_input, config = self.agent._prepare_graph_input("hello", chat_history=history)
            msgs = graph_input["messages"]
            self.assertEqual(len(msgs), 2)
            self.assertIsInstance(msgs[0], AIMessage)
            self.assertIsInstance(msgs[1], HumanMessage)

    def test_merges_config_override(self):
        with patch.object(self.agent, '_build_config', return_value={"recursion_limit": 50}):
            _, config = self.agent._prepare_graph_input("hello", config_override={"recursion_limit": 100})
            self.assertEqual(config["recursion_limit"], 100)

    def test_passes_extra_kwargs_to_graph_input(self):
        with patch.object(self.agent, '_build_config', return_value={"recursion_limit": 50}):
            graph_input, _ = self.agent._prepare_graph_input("hello", extra_field="value")
            self.assertEqual(graph_input["extra_field"], "value")

    def test_empty_history_produces_single_message(self):
        with patch.object(self.agent, '_build_config', return_value={"recursion_limit": 50}):
            graph_input, _ = self.agent._prepare_graph_input("hello", chat_history=[])
            self.assertEqual(len(graph_input["messages"]), 1)
