"""上下文工程降级用户可见性测试（fix-review-round3 Task 4.4/4.8）。

覆盖：
- 4.4 质量门禁降级摘要用户可见：compression 结果 degraded 标记 → manager
  metadata.compression_degraded → context_service 透传 → chat 侧构建
  degradation 事件（前端 type=degradation 提示"上下文已降级保留"）；
- 4.8 budget_trimmed 消费：最终裁剪标记经同一通道转用户可见提示
  （消除"有产出无消费者"状态）；
- 4.8 convert_chat_history 补 role=="tool"：非 checkpointer 路径工具历史
  不灭——转为带工具名前缀的可读文本消息（ToolMessage 配对信息在纯历史
  重放时不可靠，直接转 ToolMessage 会触发 OpenAI 400）。

事件冲刷链断言：_build_stream_agent_and_input 追加 → _process_normal_stream_chat
冲刷广播（模式层冲刷发生在其之前，此处补冲刷）；回退流 fallback 直接 yield。

运行：
    python manage.py test Django_xm.apps.chat.tests.test_context_degradation_visibility
"""

import asyncio
from contextlib import ExitStack
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase
from langchain_core.messages import HumanMessage

from Django_xm.apps.ai_engine.services import llm_fallback as llm_fallback_module
from Django_xm.apps.ai_engine.services.cost_tracker import create_token_detail_tracker
from Django_xm.apps.ai_engine.services.usage_tracker import create_usage_tracker
from Django_xm.apps.chat.services import chat_service as chat_service_module
from Django_xm.apps.chat.services.chat_service import ChatService
from Django_xm.apps.chat.services.chat_stream_mixin import AgentStreamMixin
from Django_xm.apps.chat.services.context_service import build_context_degradation_events
from Django_xm.apps.chat.utils import convert_chat_history

_DEGRADED_METADATA = {
    "injection_detected": False,
    "pruned": False,
    "budget_over": ["history"],
    "compression_degraded": True,
    "budget_trimmed": {"trimmed": True, "removed_count": 4},
}


class BuildContextDegradationEventsTests(SimpleTestCase):
    """metadata → 用户可见降级事件映射（4.4/4.8 单一构建点）。"""

    def test_degraded_metadata_maps_to_events(self):
        events = build_context_degradation_events(_DEGRADED_METADATA)
        self.assertEqual(len(events), 2)
        self.assertIn("降级", events[0]["message"])
        self.assertIn("已降级为关键片段保留", events[0]["message"])
        self.assertIn("4 条", events[1]["message"])

    def test_clean_metadata_maps_to_no_events(self):
        self.assertEqual(build_context_degradation_events({}), [])
        self.assertEqual(
            build_context_degradation_events(
                {"compression_degraded": False, "budget_trimmed": {"trimmed": False, "removed_count": 0}}
            ),
            [],
        )


class BuildStreamAgentInputDegradationTests(SimpleTestCase):
    """_build_stream_agent_and_input 非 checkpointer 分支：metadata → degradation 事件追加。"""

    def test_metadata_degradation_appended_to_stream_events(self):
        service = ChatService.__new__(ChatService)
        service._context_service = SimpleNamespace(check_injection=lambda msg: False)
        data = {
            "mode": "agent",
            "message": "hello",
            "chat_history": [{"role": "user", "content": "hello"}],
            "model_name": "m1",
        }
        human_msg = HumanMessage(content="hi")

        def _apply(self, chat_history, query, mode="agent", model_name=None):
            return chat_history, dict(_DEGRADED_METADATA)

        async def scenario():
            return await service._build_stream_agent_and_input(data, {}, [], None)

        patches = [
            mock.patch.object(
                ChatService,
                "_create_agent_with_memory",
                new=mock.AsyncMock(return_value=(SimpleNamespace(), {}, False, SimpleNamespace())),
            ),
            mock.patch.object(ChatService, "acreate_human_message", new=mock.AsyncMock(return_value=human_msg)),
            mock.patch.object(ChatService, "apply_context_engineering", new=_apply),
            mock.patch.object(chat_service_module, "convert_chat_history", new=lambda history: []),
        ]
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            asyncio.run(scenario())

        events = data.get("_stream_degradation_events")
        self.assertEqual(len(events), 2)
        self.assertIn("已降级为关键片段保留", events[0]["message"])
        self.assertIn("4 条", events[1]["message"])

    def test_clean_metadata_appends_nothing(self):
        service = ChatService.__new__(ChatService)
        service._context_service = SimpleNamespace(check_injection=lambda msg: False)
        data = {
            "mode": "agent",
            "message": "hello",
            "chat_history": [],
            "model_name": "m1",
        }

        def _apply(self, chat_history, query, mode="agent", model_name=None):
            return chat_history, {"injection_detected": False, "pruned": False, "budget_over": []}

        patches = [
            mock.patch.object(
                ChatService,
                "_create_agent_with_memory",
                new=mock.AsyncMock(return_value=(SimpleNamespace(), {}, False, SimpleNamespace())),
            ),
            mock.patch.object(
                ChatService, "acreate_human_message", new=mock.AsyncMock(return_value=HumanMessage(content="hi"))
            ),
            mock.patch.object(ChatService, "apply_context_engineering", new=_apply),
            mock.patch.object(chat_service_module, "convert_chat_history", new=lambda history: []),
        ]
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            asyncio.run(service._build_stream_agent_and_input(data, {}, [], None))

        self.assertNotIn("_stream_degradation_events", data)


class ProcessNormalStreamChatFlushTests(SimpleTestCase):
    """_process_normal_stream_chat 冲刷 _build_stream_agent_and_input 追加的降级事件。"""

    def test_degradation_events_yielded_before_stream(self):
        service = ChatService.__new__(ChatService)
        data = {"mode": "agent", "message": "hi"}

        async def _build(self, data, tool_config, tools, research_context):
            data.setdefault("_stream_degradation_events", []).append({"message": "上下文已降级保留"})
            return SimpleNamespace(), {"messages": []}, {}, SimpleNamespace(model=SimpleNamespace())

        async def _empty_events(self, *args, **kwargs):
            return
            yield  # pragma: no cover - 使其成为 async generator

        patches = [
            mock.patch.object(ChatService, "_get_tools", new=mock.AsyncMock(return_value=[])),
            mock.patch.object(
                AgentStreamMixin,
                "_prepare_stream_tool_and_research",
                new=mock.AsyncMock(return_value=({}, None)),
            ),
            mock.patch.object(ChatService, "_build_stream_agent_and_input", new=_build),
            mock.patch.object(
                AgentStreamMixin,
                "_detect_model_creation_fallback",
                new=mock.AsyncMock(return_value=(None, None, None, [])),
            ),
            mock.patch.object(AgentStreamMixin, "_init_stream_context", new=lambda self, data: SimpleNamespace()),
            mock.patch.object(AgentStreamMixin, "_precreate_degraded_agent", new=mock.AsyncMock(return_value={})),
            mock.patch.object(AgentStreamMixin, "_make_rebuild_agent_fn", new=lambda self, holder: None),
            mock.patch.object(
                AgentStreamMixin, "_build_stream_executor", new=lambda self, *args: SimpleNamespace()
            ),
            mock.patch.object(AgentStreamMixin, "_run_stream_execution", new=_empty_events),
            mock.patch.object(AgentStreamMixin, "_finalize_stream_or_skip", new=_empty_events),
        ]
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)

            async def _collect():
                return [event async for event in service._process_normal_stream_chat(data, None, None)]

            events = asyncio.run(_collect())

        degradation_events = [e for e in events if e.get("type") == "degradation"]
        self.assertEqual(len(degradation_events), 1)
        self.assertEqual(degradation_events[0]["data"], {"message": "上下文已降级保留"})
        # 冲刷即弹出，事件不残留（不会重复广播）
        self.assertNotIn("_stream_degradation_events", data)


class FallbackStreamDegradationTests(SimpleTestCase):
    """回退流（无工具纯对话）同样透出上下文工程降级提示。"""

    def test_stream_without_tools_yields_degradation_events(self):
        from Django_xm.apps.chat.services.stream.fallback import FallbackStreamService

        class _StubChatService:
            async def aapply_context_engineering(self, chat_history, message, mode=None, model_name=None):
                return chat_history, dict(_DEGRADED_METADATA)

            async def acreate_human_message(self, data):
                return HumanMessage(content=data.get("message", ""))

        class _StubModel:
            async def astream(self, messages, config=None):
                yield HumanMessage(content="hello")

        class _QuietFbCallback:
            fallback_detected = False

            def __init__(self, *args, **kwargs):
                pass

            def get_fallback_info(self):
                return None

        service = FallbackStreamService(_StubChatService())
        data = {"chat_history": [], "message": "hi", "mode": "agent", "provider_id": "p1", "model_name": "m1"}
        with mock.patch.object(llm_fallback_module, "FallbackDetectionCallback", _QuietFbCallback), mock.patch(
            "Django_xm.apps.ai_engine.models.SystemConfig.set_value"
        ):
            events = asyncio.run(
                _collect_events(
                    service.stream_without_tools(_StubModel(), data, create_usage_tracker(model_id="m1"), create_token_detail_tracker())
                )
            )

        degradation_events = [e for e in events if e.get("type") == "degradation"]
        self.assertEqual(len(degradation_events), 2)
        self.assertIn("已降级为关键片段保留", degradation_events[0]["data"]["message"])


async def _collect_events(agen):
    return [event async for event in agen]


class ConvertChatHistoryToolRoleTests(SimpleTestCase):
    """4.8：role=="tool" 历史消息转为可读文本消息（不灭失、不触发配对 400）。"""

    def test_tool_message_converted_to_readable_human_message(self):
        history = [
            {"role": "user", "content": "查下天气"},
            {"role": "assistant", "content": "我来查询。"},
            {"role": "tool", "name": "web_search", "content": "北京晴，25 度"},
        ]
        messages = convert_chat_history(history)

        self.assertEqual(len(messages), 3)
        tool_msg = messages[-1]
        self.assertIsInstance(tool_msg, HumanMessage)
        self.assertIn("[工具 web_search 结果]", tool_msg.content)
        self.assertIn("北京晴，25 度", tool_msg.content)

    def test_tool_message_without_name_uses_generic_label(self):
        messages = convert_chat_history([{"role": "tool", "content": "结果文本"}])
        self.assertEqual(len(messages), 1)
        self.assertIsInstance(messages[0], HumanMessage)
        self.assertIn("[工具结果]", messages[0].content)
        self.assertIn("结果文本", messages[0].content)


if __name__ == "__main__":
    unittest.main()
