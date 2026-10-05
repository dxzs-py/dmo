"""B3（审计 F2-1）：模型降级后 chat 链必须记录实际执行模型。

修复前缺口（与 research 链语义相反）：
- chat 链 ``usage_tracker.model_id`` 在降级后仍为请求主模型（chat_service.py:441），
  而 context 事件（stream_helpers.build_context_info）与 ChatMessage.model 落库
  （chat_service.py:490 → chat_message_builder.py:124）均读取该值，
  与前端已被 model_fallback 事件同步为 actual 的模型选择器（streamStateHandlers.js）矛盾。
- research 链（token_counter.py ``_current_model``）记的是最后实际模型。

修复目标：三个 model_fallback 事件产生点（finalizer 运行时降级 /
fallback.py 无工具回退 / chat_service 创建级降级）同步 ``usage_tracker.model_id``
为 actual_model。ChatMessage.model 经由同一 usage_tracker 间接获得 actual 值。

运行：
    python manage.py test Django_xm.apps.chat.tests.test_fallback_model_recording
"""

import asyncio
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase
from langchain_core.messages import AIMessage, HumanMessage

from Django_xm.apps.ai_engine.services import llm_fallback as llm_fallback_module
from Django_xm.apps.ai_engine.services.cost_tracker import create_token_detail_tracker
from Django_xm.apps.ai_engine.services.token_counter import TokenUsageCallbackHandler
from Django_xm.apps.ai_engine.services.usage_tracker import create_usage_tracker
from Django_xm.apps.chat.services.chat_service import ChatService
from Django_xm.apps.chat.services.stream.context import StreamContext
from Django_xm.apps.chat.services.stream.fallback import FallbackStreamService
from Django_xm.apps.chat.services.stream.finalizer import finalize_stream
from Django_xm.apps.chat.services.stream_helpers import build_context_info

_REQUESTED_MODEL = "primary-model"
_FALLBACK_INFO = {
    "original_provider": "p1",
    "original_model": _REQUESTED_MODEL,
    "actual_provider": "fb-provider",
    "actual_model": "fb-model",
    "message": "模型 p1/primary-model 运行时失败，已自动切换到 fb-provider/fb-model",
}


# ---------------------------------------------------------------------------
# 测试桩
# ---------------------------------------------------------------------------


class _FallbackDetectedCallback:
    """fallback_detected=True 的桩回调（finalizer 仅消费这两个接口）"""

    fallback_detected = True

    def get_fallback_info(self):
        return dict(_FALLBACK_INFO)


class _NoFallbackCallback:
    fallback_detected = False

    def get_fallback_info(self):
        return None


class _FakeFbCallbackClass:
    """替换 llm_fallback.FallbackDetectionCallback 的桩类（fallback.py 函数内导入）"""

    fallback_detected = True

    def __init__(self, expected_provider="", expected_model="", **kwargs):
        self.expected_provider = expected_provider
        self.expected_model = expected_model

    def get_fallback_info(self):
        return dict(_FALLBACK_INFO)


class _StubStrategy:
    enable_deep_thinking = False

    async def on_loop_success(self, ctx, data):
        return
        yield  # 使本函数成为异步生成器（不可达分支）


class _StubModel:
    async def ainvoke(self, messages):
        return AIMessage(content="")

    async def astream(self, messages, config=None):
        yield AIMessage(content="hello")


class _StubChatService:
    """FallbackStreamService 依赖的 ChatService 桩"""

    def apply_context_engineering(self, chat_history, message, mode=None, model_name=None):
        return chat_history, {}

    async def aapply_context_engineering(self, chat_history, message, mode=None, model_name=None):
        return self.apply_context_engineering(chat_history, message, mode=mode, model_name=model_name)

    async def acreate_human_message(self, data):
        return HumanMessage(content=data.get("message", ""))


async def _collect(agen):
    return [event async for event in agen]


# ---------------------------------------------------------------------------
# 路径 1：finalizer.py 运行时降级（agent 主链）
# ---------------------------------------------------------------------------


class FinalizeStreamRuntimeFallbackTests(SimpleTestCase):
    def _run_finalize(self, fb_callback):
        usage_tracker = create_usage_tracker(model_id=_REQUESTED_MODEL)
        detail_tracker = create_token_detail_tracker()
        ctx = StreamContext()
        data = {"mode": "agent", "message": "hi"}
        with mock.patch("Django_xm.apps.ai_engine.models.SystemConfig.set_value"):
            events = asyncio.run(
                _collect(
                    finalize_stream(
                        ctx,
                        data,
                        [],
                        _StubModel(),
                        _StubStrategy(),
                        TokenUsageCallbackHandler(),
                        fb_callback,
                        usage_tracker,
                        detail_tracker,
                    )
                )
            )
        return events, usage_tracker

    def test_fallback_syncs_usage_tracker_to_actual_model(self):
        """运行时降级 → usage_tracker.model_id 同步为 actual_model（修复前为 primary-model）"""
        events, usage_tracker = self._run_finalize(_FallbackDetectedCallback())
        fallback_events = [e for e in events if e.get("type") == "model_fallback"]
        self.assertEqual(len(fallback_events), 1)
        self.assertEqual(usage_tracker.model_id, "fb-model")

    def test_fallback_context_info_reports_actual_model(self):
        """降级后 context 事件的 model / model_id 均为 actual（与前端 modelStore 同步值一致）"""
        _events, usage_tracker = self._run_finalize(_FallbackDetectedCallback())
        context_info = build_context_info(usage_tracker, create_token_detail_tracker())
        self.assertEqual(context_info["model"], "fb-model")
        self.assertEqual(context_info["model_id"], "fb-model")

    def test_no_fallback_keeps_requested_model(self):
        """未降级 → 不产生 model_fallback 事件，model_id 保持请求主模型"""
        events, usage_tracker = self._run_finalize(_NoFallbackCallback())
        self.assertFalse([e for e in events if e.get("type") == "model_fallback"])
        self.assertEqual(usage_tracker.model_id, _REQUESTED_MODEL)


# ---------------------------------------------------------------------------
# 路径 2：fallback.py 无工具纯对话回退链
# ---------------------------------------------------------------------------


class NoToolsFallbackSyncsActualModelTests(SimpleTestCase):
    def test_fallback_syncs_usage_tracker_to_actual_model(self):
        usage_tracker = create_usage_tracker(model_id=_REQUESTED_MODEL)
        service = FallbackStreamService(_StubChatService())
        data = {
            "chat_history": [],
            "message": "hi",
            "mode": "agent",
            "provider_id": "p1",
            "model_name": _REQUESTED_MODEL,
        }
        with mock.patch.object(
            llm_fallback_module, "FallbackDetectionCallback", _FakeFbCallbackClass
        ), mock.patch("Django_xm.apps.ai_engine.models.SystemConfig.set_value"):
            events = asyncio.run(
                _collect(
                    service.stream_without_tools(
                        _StubModel(), data, usage_tracker, create_token_detail_tracker()
                    )
                )
            )
        fallback_events = [e for e in events if e.get("type") == "model_fallback"]
        self.assertEqual(len(fallback_events), 1)
        self.assertEqual(usage_tracker.model_id, "fb-model")

    def test_no_fallback_keeps_requested_model(self):
        """未检测到降级 → model_id 保持请求主模型（桩类可切换 detected 标志）"""
        usage_tracker = create_usage_tracker(model_id=_REQUESTED_MODEL)
        service = FallbackStreamService(_StubChatService())
        data = {
            "chat_history": [],
            "message": "hi",
            "mode": "agent",
            "provider_id": "p1",
            "model_name": _REQUESTED_MODEL,
        }

        class _QuietFb(_FakeFbCallbackClass):
            fallback_detected = False

        with mock.patch.object(
            llm_fallback_module, "FallbackDetectionCallback", _QuietFb
        ), mock.patch("Django_xm.apps.ai_engine.models.SystemConfig.set_value"):
            events = asyncio.run(
                _collect(
                    service.stream_without_tools(
                        _StubModel(), data, usage_tracker, create_token_detail_tracker()
                    )
                )
            )
        self.assertFalse([e for e in events if e.get("type") == "model_fallback"])
        self.assertEqual(usage_tracker.model_id, _REQUESTED_MODEL)


# ---------------------------------------------------------------------------
# 路径 3：chat_service._detect_model_creation_fallback 创建级降级
# ---------------------------------------------------------------------------


class CreationLevelFallbackSyncsActualModelTests(SimpleTestCase):
    def _detect(self, requested_provider, bound_provider, bound_model):
        data = {"provider_id": requested_provider, "model_name": _REQUESTED_MODEL}
        model_instance = SimpleNamespace(
            bound=SimpleNamespace(_provider_id=bound_provider, model_name=bound_model)
        )
        usage_tracker = create_usage_tracker(model_id=_REQUESTED_MODEL)
        with mock.patch("Django_xm.apps.ai_engine.models.SystemConfig.set_value"):
            _, _, _, events = asyncio.run(
                ChatService._detect_model_creation_fallback(None, data, model_instance, usage_tracker)
            )
        return events, usage_tracker

    def test_creation_fallback_syncs_usage_tracker_to_actual_model(self):
        """创建级降级（用户所选模型不可用已提升候选）→ usage_tracker.model_id 记 actual"""
        events, usage_tracker = self._detect("p1", "fb-provider", "fb-model")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "model_fallback")
        self.assertEqual(usage_tracker.model_id, "fb-model")

    def test_same_provider_no_fallback_keeps_requested_model(self):
        """实际主模型与请求一致 → 无降级事件，model_id 不变"""
        events, usage_tracker = self._detect("p1", "p1", _REQUESTED_MODEL)
        self.assertEqual(events, [])
        self.assertEqual(usage_tracker.model_id, _REQUESTED_MODEL)
