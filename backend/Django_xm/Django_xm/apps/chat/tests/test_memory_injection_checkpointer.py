"""1.2 运行时动态注入测试（fix-review-round2，替代旧 A9 graph_input 前置注入语义）。

旧实现（已废除）：checkpointer 分支把「研究上下文 + 记忆注入」组装为 SystemMessage
并列在 graph_input 的 human_msg 之前——注入消息随 checkpoint state 持久化并逐轮堆积。

新语义：
- 记忆注入：chat_service 获取注入文本（asyncio.to_thread，失败降级空串）→
  AgentService 在 use_checkpointer 分支装配 MemoryInjectionMiddleware →
  wrap_model_call 经 request.override 前插到**送达模型**的消息列表，
  **不写回 checkpoint state**；
- 研究上下文：不经注入通道——AgentService 早已将 data["_research_system_prompt"]
  作为 AgentConfig.system_prompt 注入（模型调用时拼接，不进 state），
  graph_input 前置的 SystemMessage 与 system_prompt 本就重复，已一并移除；
- 非受信内容消毒 / 非 checkpointer 分支语义不在本文件覆盖范围。

测试证明方式：
1. chat_service 接线：checkpointer 分支 graph_input 仅含 human_msg（研究上下文
   与记忆注入均不再进 graph_input/state），注入文本写入
   data["_memory_injection_context"]（带参考提示尾注），query/session_id 正确透传，
   获取在工作线程执行，失败/无记忆降级空串，无 session_id 不触发获取；
2. AgentService 装配：use_checkpointer 且注入文本非空 → AgentConfig.middleware
   含 MemoryInjectionMiddleware；文本为空或非 checkpointer → 不装配；
3. 中间件语义：wrap/awrap_model_call 前插 SystemMessage（request.override），
   空/空白文本透传，不改写原请求消息列表；
4. 验收（spec 1.2）：连续 3 轮带注入对话后，checkpoint state messages 中无任何
   【知识图谱上下文】/【跨会话上下文】SystemMessage。

运行：
    python manage.py test Django_xm.apps.chat.tests.test_memory_injection_checkpointer
"""

import asyncio
import threading
from contextlib import ExitStack
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase
from langchain.agents import create_agent
from langchain.agents.middleware.types import ModelRequest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver

from Django_xm.apps.chat.services.agent_service import AgentService
from Django_xm.apps.chat.services.chat_service import ChatService
from Django_xm.apps.context_manager.middleware import MemoryInjectionMiddleware

_MEMORY_CONTEXT = "【跨会话上下文】\n用户此前决定使用 LangGraph 构建多智能体。"
_INJECTION_NOTE = "（以上为历史会话与知识图谱记忆，供回答时参考；与当前问题无关的内容可忽略。）"
_INJECTION_TEXT = f"{_MEMORY_CONTEXT}\n\n{_INJECTION_NOTE}"

_THREAD_CONFIG = {"configurable": {"thread_id": "t1"}}


def _make_service(get_injection_context_impl) -> ChatService:
    """构造绕过 __init__ 的 ChatService，context_service 换为注入桩"""

    service = ChatService.__new__(ChatService)
    service._context_service = SimpleNamespace(
        apply_context_engineering_for_checkpointer=lambda *args, **kwargs: {},
        get_injection_context=get_injection_context_impl,
    )
    return service


def _run_build(service, data, research_context=None):
    """运行 _build_stream_agent_and_input（checkpointer 分支），返回
    ((agent, graph_input, config, agent_config), human_msg, timeline)。"""

    human_msg = HumanMessage(content=data.get("message", ""))
    timeline = {}

    async def scenario():
        timeline["loop_thread"] = threading.current_thread()
        return await service._build_stream_agent_and_input(data, {}, [], research_context)

    patches = [
        mock.patch.object(
            ChatService,
            "_create_agent_with_memory",
            new=mock.AsyncMock(
                return_value=(SimpleNamespace(), _THREAD_CONFIG, True, SimpleNamespace())
            ),
        ),
        mock.patch.object(
            ChatService, "acreate_human_message", new=mock.AsyncMock(return_value=human_msg)
        ),
        mock.patch.object(
            ChatService, "_auto_reject_pending_interrupts", new=mock.AsyncMock(return_value=None)
        ),
    ]
    with ExitStack() as stack:
        for patcher in patches:
            stack.enter_context(patcher)
        result = asyncio.run(scenario())
    return result, human_msg, timeline


class CheckpointerBranchGraphInputTests(SimpleTestCase):
    """checkpointer 分支 graph_input 仅含本轮 human 消息（注入不进 state）。"""

    def test_graph_input_contains_only_human_message_with_injection_text(self):
        """注入文本经 data 传递（带尾注），graph_input 仅 human_msg，参数透传"""
        received = {}

        def _fake_injection(query, session_id=None):
            received["query"] = query
            received["session_id"] = session_id
            return _MEMORY_CONTEXT

        service = _make_service(_fake_injection)
        data = {"mode": "agent", "message": "如何继续？", "session_id": "s-1"}
        (_agent, graph_input, config, _agent_config), human_msg, _timeline = _run_build(
            service, data
        )

        self.assertEqual(received["query"], "如何继续？")
        self.assertEqual(received["session_id"], "s-1")
        self.assertEqual(data["_memory_injection_context"], _INJECTION_TEXT)
        self.assertEqual(graph_input, {"messages": [human_msg]})
        self.assertEqual(config, _THREAD_CONFIG)

    def test_research_context_not_injected_into_graph_input(self):
        """研究上下文不再前置进 graph_input（system_prompt 通道承担，避免 state 残留）"""
        service = _make_service(lambda query, session_id=None: _MEMORY_CONTEXT)
        data = {
            "mode": "agent",
            "message": "继续",
            "session_id": "s-1",
            "_research_system_prompt": "【研究上下文】报告全文……",
        }
        (_agent, graph_input, _config, _agent_config), human_msg, _timeline = _run_build(
            service, data, research_context="研究上下文"
        )

        messages = graph_input["messages"]
        self.assertEqual(messages, [human_msg])
        for msg in messages:
            self.assertNotIn("【研究上下文】", str(msg.content))
            self.assertNotIn("【跨会话上下文】", str(msg.content))
            self.assertNotIn("【知识图谱上下文】", str(msg.content))

    def test_injection_failure_degrades_to_no_injection(self):
        """注入失败降级为不注入：注入文本为空串，不抛异常不阻断对话"""

        def _boom(query, session_id=None):
            raise RuntimeError("store unavailable")

        service = _make_service(_boom)
        data = {"mode": "agent", "message": "hi", "session_id": "s-1"}
        (_agent, graph_input, _config, _agent_config), human_msg, _timeline = _run_build(
            service, data
        )

        self.assertEqual(data["_memory_injection_context"], "")
        self.assertEqual(graph_input, {"messages": [human_msg]})

    def test_empty_injection_context_stays_empty(self):
        """注入上下文为空（无 KG/跨会话记忆）时注入文本为空串"""
        service = _make_service(lambda query, session_id=None: "")
        data = {"mode": "agent", "message": "hi", "session_id": "s-1"}
        (_agent, graph_input, _config, _agent_config), human_msg, _timeline = _run_build(
            service, data
        )

        self.assertEqual(data["_memory_injection_context"], "")
        self.assertEqual(graph_input, {"messages": [human_msg]})

    def test_no_session_id_skips_fetch(self):
        """无 session_id（use_checkpointer 必为 False）不触发注入获取"""
        fetch_calls = []

        def _fake_injection(query, session_id=None):
            fetch_calls.append((query, session_id))
            return _MEMORY_CONTEXT

        service = _make_service(_fake_injection)
        data = {"mode": "agent", "message": "hi"}
        (_agent, graph_input, _config, _agent_config), human_msg, _timeline = _run_build(
            service, data
        )

        self.assertEqual(fetch_calls, [])
        self.assertEqual(data["_memory_injection_context"], "")
        self.assertEqual(graph_input, {"messages": [human_msg]})


class CheckpointerBranchOffLoopTests(SimpleTestCase):
    def test_injection_runs_off_event_loop(self):
        """get_injection_context（同步重活：KG 检索 + embed_query）在工作线程执行"""

        timeline = {}

        def _fake_injection(query, session_id=None):
            timeline["work_thread"] = threading.current_thread()
            return _MEMORY_CONTEXT

        service = _make_service(_fake_injection)
        data = {"mode": "agent", "message": "hi", "session_id": "s-1"}
        (_result, _human_msg, timeline_from_scenario) = _run_build(service, data)

        self.assertIn("work_thread", timeline)
        self.assertIsNot(
            timeline["work_thread"], timeline_from_scenario["loop_thread"]
        )


class AgentServiceMiddlewareWiringTests(SimpleTestCase):
    """AgentService.create_agent_with_memory 的注入中间件装配测试。"""

    def _make_service(self) -> AgentService:
        service = AgentService.__new__(AgentService)
        service.user_id = 7
        service._checkpointer = None
        service._store = None
        return service

    def _run_create(self, data):
        service = self._make_service()
        with mock.patch(
            "Django_xm.apps.ai_engine.services.checkpointer_factory.get_async_checkpointer",
            new=mock.AsyncMock(return_value=SimpleNamespace()),
        ), mock.patch(
            "Django_xm.apps.agent_hub.create", return_value=SimpleNamespace()
        ) as mock_create:
            _agent, _thread_config, use_checkpointer, config = asyncio.run(
                service.create_agent_with_memory(data)
            )
        return use_checkpointer, config, mock_create

    def test_middleware_attached_when_injection_text_present(self):
        """use_checkpointer 且注入文本非空 → AgentConfig.middleware 含注入中间件"""
        data = {
            "session_id": "s-1",
            "mode": "agent",
            "_memory_injection_context": _INJECTION_TEXT,
        }
        use_checkpointer, config, _mock_create = self._run_create(data)

        self.assertTrue(use_checkpointer)
        injection_middleware = [
            m for m in (config.middleware or []) if isinstance(m, MemoryInjectionMiddleware)
        ]
        self.assertEqual(len(injection_middleware), 1)
        self.assertEqual(injection_middleware[0]._injection_text, _INJECTION_TEXT)

    def test_no_middleware_when_injection_text_empty(self):
        """注入文本为空（无记忆/获取失败降级）→ 不装配注入中间件"""
        data = {"session_id": "s-1", "mode": "agent", "_memory_injection_context": ""}
        use_checkpointer, config, _mock_create = self._run_create(data)

        self.assertTrue(use_checkpointer)
        self.assertFalse(
            any(isinstance(m, MemoryInjectionMiddleware) for m in (config.middleware or []))
        )

    def test_no_middleware_when_checkpointer_unavailable(self):
        """异步 checkpointer 不可用（降级非 checkpointer 链路）→ 不装配（避免双注入）"""
        service = self._make_service()
        data = {
            "session_id": "s-1",
            "mode": "agent",
            "_memory_injection_context": _INJECTION_TEXT,
        }
        with mock.patch(
            "Django_xm.apps.ai_engine.services.checkpointer_factory.get_async_checkpointer",
            new=mock.AsyncMock(return_value=None),
        ), mock.patch(
            "Django_xm.apps.agent_hub.create", return_value=SimpleNamespace()
        ):
            _agent, _thread_config, use_checkpointer, config = asyncio.run(
                service.create_agent_with_memory(data)
            )

        self.assertFalse(use_checkpointer)
        self.assertFalse(
            any(isinstance(m, MemoryInjectionMiddleware) for m in (config.middleware or []))
        )


class MemoryInjectionMiddlewareTests(SimpleTestCase):
    """MemoryInjectionMiddleware 语义单测（request.override 前插，不写 state）。"""

    @staticmethod
    def _make_request(messages):
        return ModelRequest(
            model=mock.MagicMock(),
            messages=list(messages),
            system_message=None,
            tool_choice=None,
            tools=[],
            response_format=None,
            state=None,
            runtime=None,
        )

    def test_wrap_model_call_prepends_system_message(self):
        request = self._make_request([HumanMessage(content="hi")])
        captured = {}

        def handler(req):
            captured["request"] = req
            return "ok"

        middleware = MemoryInjectionMiddleware(injection_text=_INJECTION_TEXT)
        result = middleware.wrap_model_call(request, handler)

        self.assertEqual(result, "ok")
        overridden_messages = captured["request"].messages
        self.assertEqual(len(overridden_messages), 2)
        self.assertIsInstance(overridden_messages[0], SystemMessage)
        self.assertEqual(overridden_messages[0].content, _INJECTION_TEXT)
        self.assertIs(overridden_messages[1], request.messages[0])
        # 原请求消息列表不被改写（override 返回新请求）
        self.assertEqual(len(request.messages), 1)

    def test_awrap_model_call_prepends_system_message(self):
        request = self._make_request([HumanMessage(content="hi")])
        captured = {}

        async def handler(req):
            captured["request"] = req
            return "ok-async"

        middleware = MemoryInjectionMiddleware(injection_text=_INJECTION_TEXT)
        result = asyncio.run(middleware.awrap_model_call(request, handler))

        self.assertEqual(result, "ok-async")
        overridden_messages = captured["request"].messages
        self.assertIsInstance(overridden_messages[0], SystemMessage)
        self.assertEqual(overridden_messages[0].content, _INJECTION_TEXT)

    def test_empty_text_passthrough(self):
        """空/空白注入文本：完全透传（handler 收到原请求对象，零开销）"""
        for text in ("", "   "):
            request = self._make_request([HumanMessage(content="hi")])
            captured = {}

            def handler(req):
                captured["request"] = req
                return "ok"

            middleware = MemoryInjectionMiddleware(injection_text=text)
            result = middleware.wrap_model_call(request, handler)

            self.assertEqual(result, "ok")
            self.assertIs(captured["request"], request)


class CheckpointStateNoResidueTests(SimpleTestCase):
    """验收（spec 1.2）：连续 3 轮带注入对话，checkpoint state 无注入 SystemMessage。"""

    def test_three_turns_state_has_no_injection_messages(self):
        injection_text = (
            "【知识图谱上下文】\n相关实体：\n  - langchain (提及3次)\n\n"
            "【跨会话上下文】\n用户此前决定使用 LangGraph 构建多智能体。"
        )

        def _infinite_ai_messages():
            # 每次调用产出新实例（唯一 id），保证 add_messages 追加而非按 id 替换
            while True:
                yield AIMessage(content="好的")

        model = GenericFakeChatModel(messages=_infinite_ai_messages())
        agent = create_agent(
            model=model,
            tools=[],
            system_prompt="你是测试助手。",
            middleware=[MemoryInjectionMiddleware(injection_text=injection_text)],
            checkpointer=InMemorySaver(),
        )
        config = {"configurable": {"thread_id": "accept-no-residue"}}

        for turn in range(3):
            agent.invoke({"messages": [HumanMessage(content=f"第{turn}轮问题")]}, config)

        state = agent.get_state(config)
        state_messages = state.values["messages"]

        self.assertEqual(len(state_messages), 6)  # 3 human + 3 ai，无注入消息堆积
        for msg in state_messages:
            self.assertNotIsInstance(msg, SystemMessage)
            self.assertNotIn("【知识图谱上下文】", str(msg.content))
            self.assertNotIn("【跨会话上下文】", str(msg.content))
