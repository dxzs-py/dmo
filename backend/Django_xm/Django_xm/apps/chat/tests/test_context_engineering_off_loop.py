"""D1（审计 PF-05/P2-7）：apply_context_engineering 非 checkpointer 分支移出事件循环。

修复前缺口（audit perf PF-05）：
- ``chat_service._build_stream_agent_and_input``（async，use_checkpointer=False 分支）
  与 ``stream/fallback.stream_without_tools`` 在事件循环上
  直接同步调用 ``apply_context_engineering`` → ``build_structured_context`` 同步执行
  KG/store 检索与逐条同步 ``embed_query``（远程 API，最多 10 次串行），
  命中时 FastAPI 执行服务事件循环停摆，同循环其他会话/WS 广播全部被卡。
  （原第三个调用点 ``_invoke_chat_graph`` 属非流式孤儿链，已随 CT-01=A 删除，
  其接线用例一并移除。）

修复：新增 ``ChatService.aapply_context_engineering``（asyncio.to_thread 调度），
两个生产调用点统一改走该入口；仅改调度不改编排，输入输出与同步版一致。

测试证明方式：
1. 时序死锁式断言——同步重活阻塞在 threading.Event 上，只有事件循环上的心跳协程
   置位后才能结束；若重活直接跑在循环上则心跳永远无法先于重活完成（断言必败）；
2. 线程身份断言——同步实现执行线程 ≠ 事件循环线程；
3. 调用点接线断言——均经异步入口调度，同步入口零直接调用。

运行：
    python manage.py test Django_xm.apps.chat.tests.test_context_engineering_off_loop
"""

import asyncio
import threading
import time
from contextlib import ExitStack
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase
from langchain_core.messages import AIMessage, HumanMessage

from Django_xm.apps.ai_engine.services import llm_fallback as llm_fallback_module
from Django_xm.apps.ai_engine.services.cost_tracker import create_token_detail_tracker
from Django_xm.apps.ai_engine.services.usage_tracker import create_usage_tracker
from Django_xm.apps.chat.services import chat_service as chat_service_module
from Django_xm.apps.chat.services.chat_service import ChatService
from Django_xm.apps.chat.services.stream.fallback import FallbackStreamService

_REQUESTED_MODEL = "primary-model"


async def _collect(agen):
    return [event async for event in agen]


class _StubModel:
    """单 chunk 流式模型桩（fallback 链专用）"""

    async def astream(self, messages, config=None):
        yield AIMessage(content="hello")


class _QuietFbCallback:
    """未降级桩回调（替换 llm_fallback.FallbackDetectionCallback）"""

    fallback_detected = False

    def __init__(self, *args, **kwargs):
        pass

    def get_fallback_info(self):
        return None


class _RecordingChatService:
    """记录同步/异步入口调用次数的 ChatService 桩（fallback 接线断言用）"""

    def __init__(self):
        self.sync_calls = 0
        self.async_calls = 0

    def apply_context_engineering(self, chat_history, message, mode=None, model_name=None):
        self.sync_calls += 1
        return chat_history, {}

    async def aapply_context_engineering(self, chat_history, message, mode=None, model_name=None):
        self.async_calls += 1
        return chat_history, {}

    async def acreate_human_message(self, data):
        return HumanMessage(content=data.get("message", ""))


# ---------------------------------------------------------------------------
# 1. aapply_context_engineering 自身：事件循环不被同步重活阻塞
# ---------------------------------------------------------------------------


class AapplyContextEngineeringOffLoopTests(SimpleTestCase):
    def _make_service(self, timeline, release):
        """构造绕过 __init__ 的 ChatService 实例，同步实现替换为阻塞在 release 上的重活桩"""

        def _heavy_apply(self, chat_history, query, mode="agent", model_name=None):
            timeline["work_thread"] = threading.current_thread()
            # 模拟同步 embed/KG/store 重活：等待事件循环侧心跳置位才能结束。
            # 若本函数跑在事件循环线程上，心跳协程永远得不到调度 → 5s 超时兜底。
            release.wait(timeout=5.0)
            timeline["work_finished"] = time.perf_counter()
            return chat_history, {"injection_detected": False, "pruned": False, "budget_over": []}

        service = ChatService.__new__(ChatService)
        patcher = mock.patch.object(ChatService, "apply_context_engineering", new=_heavy_apply)
        patcher.start()
        self.addCleanup(patcher.stop)
        return service

    def test_heavy_sync_work_does_not_block_event_loop(self):
        """重活执行期间事件循环保持调度能力（心跳先于重活完成）+ 重活在非循环线程执行"""
        timeline = {}
        release = threading.Event()
        service = self._make_service(timeline, release)
        history_in = [{"role": "user", "content": "hi"}]
        holder = {}

        async def scenario():
            holder["loop_thread"] = threading.current_thread()

            async def heartbeat():
                await asyncio.sleep(0.05)  # 让 to_thread 先把重活调度到工作线程
                timeline["first_tick"] = time.perf_counter()
                release.set()

            apply_task = asyncio.create_task(
                service.aapply_context_engineering(history_in, "hi", mode="agent", model_name="m")
            )
            await heartbeat()
            result = await asyncio.wait_for(apply_task, timeout=10.0)
            return result

        result = asyncio.run(scenario())

        # 心跳 tick 严格早于重活结束（tick 在 release.set() 之前落表，
        # work_finished 在 release.wait() 返回之后落表，时序无竞态）：
        # 若重活直接跑在循环上，first_tick 只能出现在 work_finished 之后 → 断言必败
        self.assertIn("first_tick", timeline)
        self.assertIn("work_finished", timeline)
        self.assertLess(timeline["first_tick"], timeline["work_finished"])
        # 重活执行线程 ≠ 事件循环线程
        self.assertIsNot(timeline["work_thread"], holder["loop_thread"])
        # 仅改调度不改编排：输入输出与同步版本一致
        self.assertEqual(result[0], history_in)
        self.assertEqual(
            result[1], {"injection_detected": False, "pruned": False, "budget_over": []}
        )


# ---------------------------------------------------------------------------
# 2. 调用点接线：_build_stream_agent_and_input 非 checkpointer 分支
# ---------------------------------------------------------------------------


class BuildStreamAgentInputOffLoopWiringTests(SimpleTestCase):
    def test_non_checkpointer_branch_schedules_off_loop(self):
        """_build_stream_agent_and_input 非 checkpointer 分支：同步实现经 to_thread 执行"""
        timeline = {}
        release = threading.Event()
        service = ChatService.__new__(ChatService)
        service._context_service = SimpleNamespace(check_injection=lambda msg: False)
        converted = {}

        def _spy_apply(self, chat_history, query, mode="agent", model_name=None):
            timeline["work_thread"] = threading.current_thread()
            release.set()
            processed = list(chat_history) + [{"role": "assistant", "content": "ctx"}]
            return processed, {"pruned": True}

        def _fake_convert(history):
            converted["args"] = history
            return []

        human_msg = HumanMessage(content="hi")
        data = {
            "mode": "agent",
            "message": "hello",
            "chat_history": [{"role": "user", "content": "hello"}],
            "model_name": _REQUESTED_MODEL,
        }

        async def scenario():
            timeline["loop_thread"] = threading.current_thread()
            return await service._build_stream_agent_and_input(data, {}, [], None)

        patches = [
            mock.patch.object(
                ChatService,
                "_create_agent_with_memory",
                new=mock.AsyncMock(return_value=(SimpleNamespace(), {}, False, SimpleNamespace())),
            ),
            mock.patch.object(
                ChatService, "acreate_human_message", new=mock.AsyncMock(return_value=human_msg)
            ),
            mock.patch.object(ChatService, "apply_context_engineering", new=_spy_apply),
            mock.patch.object(chat_service_module, "convert_chat_history", new=_fake_convert),
        ]
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            agent, graph_input, config, agent_config = asyncio.run(scenario())

        self.assertIsNot(timeline["work_thread"], timeline["loop_thread"])
        self.assertEqual(
            converted["args"],
            [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "ctx"}],
        )
        self.assertIs(graph_input["messages"][-1], human_msg)
        self.assertEqual(config, {"recursion_limit": chat_service_module.AGENT_RECURSION_LIMIT_CHAT})


# ---------------------------------------------------------------------------
# 3. 调用点接线：fallback.stream_without_tools
# ---------------------------------------------------------------------------


class FallbackStreamOffLoopWiringTests(SimpleTestCase):
    def test_stream_without_tools_uses_off_loop_entry_only(self):
        """无工具回退流：只经 aapply_context_engineering 调度，同步入口零调用"""
        chat_service_stub = _RecordingChatService()
        service = FallbackStreamService(chat_service_stub)
        usage_tracker = create_usage_tracker(model_id=_REQUESTED_MODEL)
        data = {
            "chat_history": [],
            "message": "hi",
            "mode": "agent",
            "provider_id": "p1",
            "model_name": _REQUESTED_MODEL,
        }
        with mock.patch.object(
            llm_fallback_module, "FallbackDetectionCallback", _QuietFbCallback
        ), mock.patch("Django_xm.apps.ai_engine.models.SystemConfig.set_value"):
            events = asyncio.run(
                _collect(
                    service.stream_without_tools(
                        _StubModel(), data, usage_tracker, create_token_detail_tracker()
                    )
                )
            )

        self.assertEqual(chat_service_stub.async_calls, 1)
        self.assertEqual(chat_service_stub.sync_calls, 0)
        self.assertTrue([e for e in events if e.get("type") == "chunk"])
