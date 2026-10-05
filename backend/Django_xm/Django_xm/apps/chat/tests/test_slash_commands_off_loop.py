"""E11（D1 遗留，审计 PF-05/P2-7）：slash 命令链同步上下文工程移出事件循环。

修复前缺口：
- 斜杠命令链两条调用路径均在 FastAPI 执行服务事件循环上**同步**执行
  ``execute_command``（纯分发器，handler 含阻塞路径）：
  1. 流式主链：``run_agent_session`` → ``_dispatch_by_mode`` →
     ``_handle_slash_command``（async gen 内同步调用）；
  2. 非流式链：``process_chat_request`` → ``_precheck_chat_request``（sync def
     内同步调用）。
- 重活根因 ``/compact`` → ``build_structured_context``：``_ensure_store``
  （store 懒加载连接）+ ``store.search``（store IO）+ 跨会话逐条相关性评分的
  同步 ``embed_query``（远程 API——``get_injection_context`` 的 KG/跨会话两个
  分支仅以 ``user_id`` 为闸，``query=""`` **不**跳过，与审计 PF-05 原描述
  "falsy query 跳过"不符）；超预算压缩路径 ``prune_by_relevance`` 每条消息再
  2 次同步 ``embed_query``（最多 2N 次串行远程调用）。命中时同循环其他会话
  执行/WS 广播全部停摆。

修复（仅改调度不改编排，同 D1 aapply_context_engineering 模式）：
- ``slash_commands.aexecute_command``：asyncio.to_thread 调度变体，
  execute_command 保持同步签名（轻命令随重命令一并移出循环，避免逐命令
  维护"是否需要调度"的判定面）；
- 两个调用点统一改走 ``aexecute_command``；``_precheck_chat_request`` 随之
  async 化（唯一生产调用点同步更新，cache/rag 段行为不变）。

测试证明方式（参照 D1 test_context_engineering_off_loop）：
1. 时序死锁式断言——同步重活阻塞在 threading.Event 上，只有事件循环上的
   心跳协程置位后才能结束；若重活直接跑在循环上则心跳永远无法先于重活
   完成（断言必败）；
2. 线程身份断言——命令执行线程 ≠ 事件循环线程；
3. 调用点接线断言——流式/非流式两链均经异步入口调度，返回结构不变；
4. 命令行为回归——7 个命令 aexecute 与 sync 输出逐一相等（仅改调度不改编排）。

附带项（N-6 关联面，本任务顺手修）：``_run_stream_execution`` 的
``FallbackDetectionCallback.expected_provider=plan.provider_id or ""``——请求
未带 provider_id 时落空串，on_llm_end 以 expected_provider 非空为闸 → 运行时
降级检测整体关闭。补 bound_model._provider_id 兜底（与 fallback.py 无工具
回退链同构段对齐），并附检测闸门语义的反向验证（空串必不报降级）。

运行：
    python manage.py test Django_xm.apps.chat.tests.test_slash_commands_off_loop
"""

import asyncio
import threading
import time
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from Django_xm.apps.ai_engine.services import llm_fallback as llm_fallback_module
from Django_xm.apps.ai_engine.services import registry_service as registry_service_module
from Django_xm.apps.ai_engine.services import llm_factory as llm_factory_module
from Django_xm.apps.chat.services import slash_commands as slash_commands_module
from Django_xm.apps.chat.services.chat_service import ChatService
from Django_xm.apps.chat.services.slash_commands import aexecute_command, execute_command


async def _collect(agen):
    return [event async for event in agen]


# ---------------------------------------------------------------------------
# 1. aexecute_command 自身：事件循环不被同步重活阻塞
# ---------------------------------------------------------------------------


class AexecuteCommandOffLoopTests(SimpleTestCase):
    def test_heavy_sync_command_does_not_block_event_loop(self):
        """重命令执行期间事件循环保持调度能力 + 执行线程身份断言 + 输入输出一致"""
        timeline = {}
        release = threading.Event()
        command_result = {"type": "success", "content": "done"}

        def _heavy_execute(command_name, context):
            # 模拟 /compact 的同步 store/embedding 重活：等待事件循环侧心跳
            # 置位才能结束。若本函数跑在事件循环线程上，心跳协程永远得不到
            # 调度 → 5s 超时兜底后 release.wait 返回 False，时序断言必败。
            timeline["work_thread"] = threading.current_thread()
            release.wait(timeout=5.0)
            timeline["work_finished"] = time.perf_counter()
            return dict(command_result)

        async def scenario():
            holder = {}
            holder["loop_thread"] = threading.current_thread()

            async def heartbeat():
                await asyncio.sleep(0.05)  # 让 to_thread 先把重活调度到工作线程
                timeline["first_tick"] = time.perf_counter()
                release.set()

            with mock.patch.object(
                slash_commands_module, "execute_command", _heavy_execute
            ):
                task = asyncio.create_task(aexecute_command("compact", {}))
                await heartbeat()
                result = await asyncio.wait_for(task, timeout=10.0)
            return result, holder

        result, holder = asyncio.run(scenario())

        # 心跳 tick 严格早于重活结束（tick 在 release.set() 之前落表，
        # work_finished 在 release.wait() 返回之后落表，时序无竞态）：
        # 若命令直接跑在循环上，first_tick 只能出现在 work_finished 之后 → 断言必败
        self.assertIn("first_tick", timeline)
        self.assertIn("work_finished", timeline)
        self.assertLess(timeline["first_tick"], timeline["work_finished"])
        # 命令执行线程 ≠ 事件循环线程
        self.assertIsNot(timeline["work_thread"], holder["loop_thread"])
        # 仅改调度不改编排：输入输出与同步版本一致
        self.assertEqual(result, {"type": "success", "content": "done"})

    def test_unknown_command_returns_error_without_scheduling(self):
        """未知命令 → error 结果（分发语义与同步版一致）"""
        result = asyncio.run(aexecute_command("nope", {}))
        self.assertEqual(result["type"], "error")
        self.assertIn("未知命令", result["content"])


# ---------------------------------------------------------------------------
# 2. 调用点接线：流式链 _handle_slash_command
# ---------------------------------------------------------------------------


class SlashStreamWiringTests(SimpleTestCase):
    def test_stream_slash_branch_executes_off_loop(self):
        """流式链 slash 分支：execute_command 经 to_thread 执行且事件结构不变"""
        timeline = {}
        release = threading.Event()
        service = ChatService.__new__(ChatService)
        service.user_id = 1
        captured = {}

        def _spy_execute(command_name, context):
            timeline["work_thread"] = threading.current_thread()
            captured["command_name"] = command_name
            captured["user_id"] = context.get("user_id")
            captured["session_id"] = context.get("session_id")
            release.set()
            return {"type": "info", "content": "📊 会话状态"}

        data = {
            "message": "/status",
            "session_id": "s1",
            "chat_history": [{"role": "user", "content": "hi"}],
        }

        async def scenario():
            timeline["loop_thread"] = threading.current_thread()
            with mock.patch.object(slash_commands_module, "execute_command", _spy_execute):
                return await _collect(
                    service._handle_slash_command(
                        data, ("status", ""), SimpleNamespace(get_summary=lambda: {})
                    )
                )

        events = asyncio.run(scenario())

        # 命令执行不在事件循环线程上
        self.assertIsNot(timeline["work_thread"], timeline["loop_thread"])
        # 编排不变：命令名/上下文字段按原样传递
        self.assertEqual(captured["command_name"], "status")
        self.assertEqual(captured["user_id"], 1)
        self.assertEqual(captured["session_id"], "s1")
        # 事件结构不变：command 结果 + end 终态
        self.assertEqual(
            [e["type"] for e in events], ["command", "end"]
        )
        self.assertEqual(events[0]["data"], {"type": "info", "content": "📊 会话状态"})
        self.assertEqual(events[0]["content"], "📊 会话状态")

    def test_stream_slash_branch_error_result_shape(self):
        """流式链 slash 分支：未知命令的 error 结果原样进入 command 事件（行为不变）"""
        service = ChatService.__new__(ChatService)
        service.user_id = 1
        data = {"message": "/nope", "session_id": "s1", "chat_history": []}
        events = asyncio.run(
            _collect(
                service._handle_slash_command(data, ("nope", ""), SimpleNamespace(get_summary=lambda: {}))
            )
        )
        self.assertEqual(events[0]["data"]["type"], "error")
        self.assertEqual(events[1]["type"], "end")


# ---------------------------------------------------------------------------
# 3. 调用点接线：非流式链 _precheck_chat_request（async 化）
# ---------------------------------------------------------------------------


class PrecheckSlashWiringTests(SimpleTestCase):
    def _make_service(self):
        service = ChatService.__new__(ChatService)
        service.user_id = 1
        return service

    def test_precheck_slash_branch_executes_off_loop(self):
        """非流式链：命令命中时 execute_command 经 to_thread 执行，返回结构不变"""
        timeline = {}
        service = self._make_service()
        data = {
            "message": "/status",
            "session_id": "s1",
            "mode": "agent",
            "chat_history": [{"role": "user", "content": "hi"}],
        }

        def _spy_execute(command_name, context):
            timeline["work_thread"] = threading.current_thread()
            return {"type": "info", "content": "📊 会话状态"}

        async def scenario():
            timeline["loop_thread"] = threading.current_thread()
            with mock.patch.object(slash_commands_module, "execute_command", _spy_execute):
                return await service._precheck_chat_request(data)

        result = asyncio.run(scenario())

        self.assertIsNot(timeline["work_thread"], timeline["loop_thread"])
        # 返回结构与修复前完全一致（is_command 提前返回，不进 cache/rag 分流）
        self.assertEqual(
            result,
            {
                "message": "📊 会话状态",
                "mode": "agent",
                "tools_used": [],
                "success": True,
                "is_command": True,
                "command_type": "info",
            },
        )

    def test_precheck_command_short_circuits_cache_and_rag(self):
        """命令命中 → 直接返回，不再触发模型响应缓存与 RAG 分流（编排顺序不变）"""
        service = self._make_service()
        data = {"message": "/clear", "session_id": "s1", "mode": "agent", "chat_history": []}
        with mock.patch.object(
            slash_commands_module, "execute_command", lambda name, ctx: {"type": "action", "content": "ok"}
        ):
            from Django_xm.apps.cache_manager.services.cache_service import ModelResponseCacheService

            with mock.patch.object(
                ModelResponseCacheService, "get_cached_response", side_effect=AssertionError("不应触达缓存")
            ):
                result = asyncio.run(service._precheck_chat_request(data))
        self.assertTrue(result["is_command"])

    def test_precheck_unknown_command_returns_error_response(self):
        """非流式链：未知命令 → is_command=True 的 error 响应（与同步版一致）"""
        service = self._make_service()
        data = {"message": "/nope", "session_id": "s1", "mode": "agent", "chat_history": []}
        result = asyncio.run(service._precheck_chat_request(data))
        self.assertTrue(result["is_command"])
        self.assertEqual(result["command_type"], "error")
        self.assertIn("未知命令", result["message"])


# ---------------------------------------------------------------------------
# 4. 命令行为回归：aexecute 与 sync 输出逐一相等（仅改调度不改编排）
# ---------------------------------------------------------------------------


class CommandBehaviorParityTests(SimpleTestCase):
    """全部 7 个命令：同一上下文下 aexecute_command 与 execute_command 输出相等。

    /model 的 DB 读取路径（SystemConfig 默认模型 + MODEL_REGISTRY）统一打桩，
    其余命令均为纯 CPU 路径。
    """

    _REGISTRY = {
        "p1": {"models": [{"name": "m1"}, {"name": "m2"}], "default_model": "m1"},
        "p2": {"model_names": ["m3"], "default_model": "m3"},
    }

    def _base_context(self, **extra):
        context = {
            "user_id": 1,
            "session_id": "s1",
            "messages": [{"role": "user", "content": "hi"}],
            "session": {"session_id": "s1", "mode": "agent", "title": "t"},
            "args": "",
            "timestamp": "2026-10-05",
        }
        context.update(extra)
        return context

    def _assert_parity(self, command_name, context):
        sync_result = execute_command(command_name, dict(context))
        async_result = asyncio.run(aexecute_command(command_name, dict(context)))
        self.assertEqual(sync_result, async_result)
        return sync_result

    def _patch_model_dependencies(self, stack):
        registry_patcher = mock.patch.object(
            registry_service_module, "get_model_registry", return_value=dict(self._REGISTRY)
        )
        config_patcher = mock.patch.object(
            llm_factory_module, "get_system_default_chat_model", return_value=None
        )
        stack.enter_context(registry_patcher)
        stack.enter_context(config_patcher)

    def test_light_commands_parity(self):
        """轻命令（help/status/clear/export/version）输出与同步版一致"""
        for name in ("help", "status", "clear", "export", "version"):
            with self.subTest(command=name):
                result = self._assert_parity(name, self._base_context())
                self.assertIn(result["type"], ("info", "action", "export"))

    def test_model_query_current_parity(self):
        """/model 无参：显示当前模型（DB 路径打桩后行为一致）"""
        with mock.patch.object(
            llm_factory_module, "get_model_string", return_value="p1:m1"
        ):
            result = self._assert_parity("model", self._base_context())
        self.assertEqual(result["type"], "info")
        self.assertIn("p1:m1", result["content"])

    def test_model_switch_valid_and_invalid_parity(self):
        """/model 带参：合法/非法模型切换输出与同步版一致（registry 打桩）"""
        from contextlib import ExitStack

        with ExitStack() as stack:
            self._patch_model_dependencies(stack)
            valid = self._assert_parity("model", self._base_context(args="p2:m3"))
            invalid = self._assert_parity("model", self._base_context(args="p9:m9"))
        self.assertEqual(valid["type"], "success")
        self.assertEqual(valid["model"], "p2:m3")
        self.assertEqual(invalid["type"], "error")
        self.assertIn("不支持的模型", invalid["content"])

    def test_compact_empty_messages_parity(self):
        """/compact 空消息：info 提示与同步版一致（重路径由 context_manager 自身测试守护）"""
        result = self._assert_parity("compact", self._base_context(messages=[]))
        self.assertEqual(result["type"], "info")
        self.assertIn("没有消息可以压缩", result["content"])

    def test_session_required_commands_without_session(self):
        """requires_session 命令缺会话 → error（前置校验语义不受调度影响）"""
        for name in ("status", "compact", "clear", "export"):
            with self.subTest(command=name):
                context = self._base_context()
                context.pop("session")
                result = self._assert_parity(name, context)
                self.assertEqual(result["type"], "error")
                self.assertIn("需要活跃的会话", result["content"])


# ---------------------------------------------------------------------------
# 5. 附带项：_run_stream_execution expected_provider 兜底（N-6 关联面）
# ---------------------------------------------------------------------------


class _CaptureFbCallback:
    """记录构造参数的 FallbackDetectionCallback 替身"""

    instances: list = []

    def __init__(self, expected_provider="", expected_model=""):
        self.expected_provider = expected_provider
        self.expected_model = expected_model
        _CaptureFbCallback.instances.append(self)

    def get_fallback_info(self):
        return None


class RunStreamExecutionExpectedProviderTests(SimpleTestCase):
    def _run(self, plan, data):
        """跑通 _run_stream_execution 回调装配段（executor.run 立即结束）"""
        service = ChatService.__new__(ChatService)
        service.user_id = 1
        captured_callbacks = {}

        async def _stub_run(loop_fn, agent, graph_input, config, ctx, strategy, run_data):
            captured_callbacks["callbacks"] = config.get("callbacks", [])
            yield {"type": "chunk", "content": "x"}

        executor = SimpleNamespace(
            timeout_mgr=SimpleNamespace(),
            duplicate_detector=SimpleNamespace(),
            run=_stub_run,
        )
        events = asyncio.run(
            _collect(
                service._run_stream_execution(plan, data, executor, None, {})
            )
        )
        return events, captured_callbacks

    def _make_plan(self, provider_id=None, model_name=None, bound_provider="bound-p", bound_model="bound-m"):
        model_instance = SimpleNamespace(
            bound=SimpleNamespace(_provider_id=bound_provider, model_name=bound_model)
        )
        return SimpleNamespace(
            agent=SimpleNamespace(),
            graph_input={"messages": []},
            config={},
            ctx=SimpleNamespace(),
            strategy=SimpleNamespace(),
            tools=[],
            model_instance=model_instance,
            provider_id=provider_id,
            model_name=model_name,
        )

    def setUp(self):
        _CaptureFbCallback.instances = []

    def test_missing_provider_id_falls_back_to_bound_model(self):
        """请求未带 provider_id → expected_provider 兜底 bound_model._provider_id（修复前为空串=检测关闭）"""
        plan = self._make_plan(provider_id=None, model_name=None)
        data = {"message": "hi", "session_id": "s1", "mode": "agent"}
        with mock.patch.object(llm_fallback_module, "FallbackDetectionCallback", _CaptureFbCallback):
            self._run(plan, data)

        fb = _CaptureFbCallback.instances[0]
        self.assertEqual(fb.expected_provider, "bound-p")
        self.assertEqual(fb.expected_model, "bound-m")

    def test_request_provider_id_takes_priority_over_bound(self):
        """请求显式带 provider_id → 请求值优先（兜底不覆盖显式意图）"""
        plan = self._make_plan(provider_id="p1", model_name="m1")
        data = {"message": "hi", "session_id": "s1", "mode": "agent"}
        with mock.patch.object(llm_fallback_module, "FallbackDetectionCallback", _CaptureFbCallback):
            self._run(plan, data)

        fb = _CaptureFbCallback.instances[0]
        self.assertEqual(fb.expected_provider, "p1")
        self.assertEqual(fb.expected_model, "m1")

    def test_none_model_instance_degrades_to_empty(self):
        """model_instance 缺失 → getattr 链安全降级为空串（不炸、语义不变）"""
        plan = self._make_plan()
        plan.model_instance = None
        data = {"message": "hi", "session_id": "s1", "mode": "agent"}
        with mock.patch.object(llm_fallback_module, "FallbackDetectionCallback", _CaptureFbCallback):
            self._run(plan, data)

        fb = _CaptureFbCallback.instances[0]
        self.assertEqual(fb.expected_provider, "")
        self.assertEqual(fb.expected_model, "")

    def test_empty_expected_provider_keeps_detection_off(self):
        """反向验证（检测闸门语义）：expected_provider 空串 → 真实回调必不报降级。

        证明"兜底缺失=运行时降级检测整体关闭"的因果，兜底缺失时本修复的
        回归测试（上一用例 expected_provider='bound-p'）必败。
        """
        fb = llm_fallback_module.FallbackDetectionCallback(expected_provider="", expected_model="")
        # 模拟 on_llm_start（attempt 登记）→ on_llm_end（首次成功，provider 推断结果不同）
        fb.on_llm_start({}, ["prompt"], run_id="r1", invocation_params={"model_name": "other-m"})
        with mock.patch.object(
            llm_fallback_module, "get_model_registry", return_value={}
        ):
            fb.on_llm_end(SimpleNamespace(), run_id="r1")
        self.assertFalse(fb.fallback_detected)
