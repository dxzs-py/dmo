"""E5-02（W5 票0）：agent/deep-research 主链检索器注入失败 → 用户可见降级事件。

旧行为缺口：``_inject_agent_retriever_tools`` / ``_inject_deep_research_retriever_tools``
中 ``if retriever:`` 无 else——get_rag_retriever 因 embedding/PG 故障或空索引返回
None 时该知识库检索工具静默不注入，LLM 凭参数知识回答，用户误以为答案来自知识库，
且流以 success 终态结束（生产可达 + 直接误导，E5-02 执行位提前依据）。

修复：注入失败时向 data["_stream_degradation_events"] 追加 degradation 事件
（kb_id + message），由 chat_executor_core 逐条 yield 广播；经 stream_broadcast
白名单（STREAM_EVENT + event_subtype='degradation'，对齐 error/retry 既有 A1 帧
模式）送达前端，流形态不受影响（不冲刷 reasoning 窗口，属"流继续"类事件）。

运行：
    python manage.py test Django_xm.apps.chat.tests.test_retriever_injection_degradation
"""

import asyncio
import contextlib
import os
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from Django_xm.apps.chat.services import stream_broadcast
from Django_xm.apps.chat.services.chat_service import ChatService
from Django_xm.common.event_schema import EventType

_RETRIEVER_TOOL = "Django_xm.apps.knowledge.services.retriever_tool"
_REALTIME_EVENTS = "Django_xm.common.realtime_events"


def _stub_retriever_tool(*args, **kwargs):
    return SimpleNamespace(name="knowledge_base_kb-a")


class InjectionDegradationTests(SimpleTestCase):
    """E5-02：检索器注入失败产生用户可见 degradation 事件。"""

    def _patch_env(self, retriever_value):
        service = ChatService.__new__(ChatService)
        service.user_id = 1
        data = {"selected_knowledge_bases": ["kb-a"]}
        patches = [
            mock.patch.object(
                ChatService, "_load_user_knowledge_bases", new=mock.AsyncMock(return_value=[])
            ),
            # sync_to_async 恒等替换：AsyncMock 直接返回协程可 await
            mock.patch(
                "Django_xm.apps.chat.services.chat_service.sync_to_async", side_effect=lambda fn: fn
            ),
            mock.patch(f"{_RETRIEVER_TOOL}.create_retriever_tool", _stub_retriever_tool),
            mock.patch("Django_xm.apps.ai_engine.services.llm_factory.get_helper_model", return_value=None),
            mock.patch.object(
                ChatService,
                "_rag_service",
                new=SimpleNamespace(
                    user_id=1, get_rag_retriever=mock.AsyncMock(return_value=retriever_value)
                ),
                create=True,
            ),
        ]
        return service, data, patches

    def _run_agent(self, retriever_value):
        service, data, patches = self._patch_env(retriever_value)
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            async_to_sync(service._inject_agent_retriever_tools)(data)
        return data

    def _run_deep_research(self, retriever_value):
        service, data, patches = self._patch_env(retriever_value)
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            async_to_sync(service._inject_deep_research_retriever_tools)(data)
        return data

    def test_agent_none_retriever_appends_degradation(self):
        """agent：get_rag_retriever 返回 None → data 追加 degradation 事件（修复前静默）。"""
        data = self._run_agent(None)

        events = data.get("_stream_degradation_events")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kb_id"], "kb-a")
        self.assertIn("kb-a", events[0]["message"])
        # 未注入工具（与旧行为一致），但降级事件可广播（新增面）
        self.assertNotIn("_extra_tools", data)

    def test_agent_success_keeps_no_degradation(self):
        """agent：检索器正常 → 无 degradation 事件，工具照常注入。"""
        data = self._run_agent(SimpleNamespace(name="retriever"))

        self.assertNotIn("_stream_degradation_events", data)
        self.assertEqual(len(data.get("_extra_tools", [])), 1)

    def test_deep_research_none_retriever_appends_degradation(self):
        """deep-research：检索器为 None → 同样追加 degradation 事件。"""
        data = self._run_deep_research(None)

        events = data.get("_stream_degradation_events")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kb_id"], "kb-a")
        self.assertNotIn("_retriever_tool_list", data)

    def test_deep_research_success_keeps_no_degradation(self):
        """deep-research：检索器正常 → 无 degradation 事件，主检索器照常设置。"""
        data = self._run_deep_research(SimpleNamespace(name="retriever"))

        self.assertNotIn("_stream_degradation_events", data)
        self.assertEqual(len(data.get("_retriever_tool_list", [])), 1)
        self.assertIsNotNone(data.get("_retriever_tool"))


async def _collect(agen):
    return [event async for event in agen]


class ModeHandlerYieldWiringTests(SimpleTestCase):
    """两个 mode 处理器将注入失败事件以 degradation 帧 yield（广播链接线）。"""

    def _make_env(self, retriever_value):
        service = ChatService.__new__(ChatService)
        service.user_id = 1
        data = {"selected_knowledge_bases": ["kb-a"], "use_knowledge_base": True, "message": "hi"}
        patches = [
            mock.patch.object(
                ChatService, "_load_user_knowledge_bases", new=mock.AsyncMock(return_value=[])
            ),
            mock.patch(
                "Django_xm.apps.chat.services.chat_service.sync_to_async", side_effect=lambda fn: fn
            ),
            mock.patch(f"{_RETRIEVER_TOOL}.create_retriever_tool", _stub_retriever_tool),
            mock.patch("Django_xm.apps.ai_engine.services.llm_factory.get_helper_model", return_value=None),
            mock.patch.object(
                ChatService,
                "_rag_service",
                new=SimpleNamespace(
                    user_id=1, get_rag_retriever=mock.AsyncMock(return_value=retriever_value)
                ),
                create=True,
            ),
        ]
        return service, data, patches

    def test_agent_mode_yields_degradation_before_stream(self):
        """agent：degradation 帧先于正文流产出（用户先见提示再见回答）。"""
        service, data, patches = self._make_env(None)

        async def _stub_normal(self, _data, _usage, _token, interrupt_handler=None):
            yield {"type": "chunk", "content": "x"}

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(
                mock.patch.object(ChatService, "_process_normal_stream_chat", new=_stub_normal)
            )
            events = asyncio.run(_collect(service._handle_agent_mode(data, None, None)))

        self.assertEqual([e["type"] for e in events], ["degradation", "chunk"])
        self.assertEqual(events[0]["data"]["kb_id"], "kb-a")
        # 事件消费后容器清理（不残留进落库/恢复数据）
        self.assertNotIn("_stream_degradation_events", data)

    def test_deep_research_mode_yields_degradation_first(self):
        """deep-research：degradation 帧为流首帧（先于 deep_research 任务创建事件）。"""
        service, data, patches = self._make_env(None)
        patches.extend(
            [
                mock.patch.object(
                    ChatService,
                    "_tool_service",
                    SimpleNamespace(get_deep_research_tools=mock.AsyncMock(return_value=[])),
                    create=True,
                ),
                mock.patch.object(
                    ChatService,
                    "_deep_service",
                    SimpleNamespace(
                        create_deep_research_task=mock.AsyncMock(return_value="task-1"),
                        start_execution=mock.AsyncMock(),
                    ),
                    create=True,
                ),
            ]
        )

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            events = asyncio.run(_collect(service._handle_deep_research_mode(data, None, None)))

        self.assertEqual(events[0]["type"], "degradation")
        self.assertEqual(events[0]["data"]["kb_id"], "kb-a")
        self.assertIn("deep_research", [e["type"] for e in events[1:]])
        self.assertNotIn("_stream_degradation_events", data)


class StreamBroadcastDegradationTests(SimpleTestCase):
    """degradation 事件经 STREAM_EVENT + event_subtype 帧模式广播（A1 模式对齐）。"""

    def test_degradation_wired_to_stream_event_subtype(self):
        captured = {}

        async def _capture(evt_type, payload, **kwargs):
            captured["evt_type"] = evt_type
            captured["payload"] = payload

        with mock.patch(f"{_REALTIME_EVENTS}.publish_event", _capture):
            asyncio.run(
                stream_broadcast.publish_stream_event(
                    {
                        "type": "degradation",
                        "data": {"kb_id": "kb-a", "message": "知识库 kb-a 暂不可用"},
                    },
                    session_id="s1",
                    message_id=7,
                )
            )

        self.assertIs(captured["evt_type"], EventType.STREAM_EVENT)
        self.assertEqual(captured["payload"]["event_subtype"], "degradation")
        self.assertEqual(
            captured["payload"]["data"], {"kb_id": "kb-a", "message": "知识库 kb-a 暂不可用"}
        )

    def test_degradation_does_not_flush_reasoning_window(self):
        """degradation 属"流继续"类事件：不冲刷 reasoning 节流窗口（对齐 retry 语义）。"""
        self.assertNotIn("degradation", stream_broadcast._REASONING_FLUSH_SUBTYPES)
