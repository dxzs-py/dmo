"""spawn_sub_agent 跨会话并发 configurable 隔离单元测试（Task 2 C1 回归）。

历史缺陷：SpawnSubAgentTool 为进程级单例（agent_hub.apps 注册、注入每个
deep agent），invoke/ainvoke 覆写层将 configurable 原地覆写到实例字段
``parent_configurable``。跨会话并发派生时（深度研究场景正是跨会话并发）
A 捕获 → B 覆盖 → A 的 _arun 读到 B 的 configurable——A 用户可能用 B 的
user_id/session_id/store 创建子代理（串号）。

修复：删除实例字段缓存，configurable 从每次工具调用的 RunnableConfig 读取
（BaseTool 官方 config 注入机制：langchain_core.tools.base.arun 检测
``config: RunnableConfig`` 参数后注入本次调用 config，经 ensure_config 恒非空）。

本测试以 asyncio.gather 并发多次 spawn 调用（不同 config），断言
runtime.spawn 收到的 configurable / parent_thread_id 与各自调用 config
完全一致（无串号），且单例工具不再持有实例缓存字段。

全部 mock 级（runtime / 注册表 / 工具解析打桩，真实模型与子代理图零调用）。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.agent_hub.tests.test_spawn_config_isolation
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase

from Django_xm.apps.agent_hub.subagent_tools.spawn import spawn_sub_agent

_RUNTIME_MODULE = "Django_xm.apps.ai_engine.subagent_runtime"
_REGISTRY_MODULE = f"{_RUNTIME_MODULE}.registry"
_TOOLS_MODULE = "Django_xm.apps.tools"
_AGENT_HUB_MODULE = "Django_xm.apps.agent_hub"


def _make_config(user_id: int, session_id: str, **extra) -> dict:
    """构造 spawn 工具调用 config（chat_service / research_runner 写入的契约字段子集）。"""
    configurable = {
        "thread_id": session_id,
        "user_id": user_id,
        "session_id": session_id,
        "model_name": "test-model",
        "tool_names": ["t1"],
        "enable_deep_thinking": False,
        **extra,
    }
    return {"configurable": configurable}


class SpawnConfigIsolationTests(SimpleTestCase):
    """并发 spawn 工具调用各自读到自身 configurable（无实例字段串号）。"""

    def setUp(self):
        self.fake_runtime = MagicMock()
        self.fake_runtime.spawn = AsyncMock(
            return_value=SimpleNamespace(thread_id="subagent_test", status="running")
        )

    def _patch_stack(self):
        """打桩 _arun 内部依赖（均为调用时局部导入，patch 源模块属性）。"""
        return (
            patch(f"{_RUNTIME_MODULE}.get_subagent_runtime", return_value=self.fake_runtime),
            patch(f"{_REGISTRY_MODULE}.get_subagent_spec", return_value=None),
            patch(
                f"{_TOOLS_MODULE}.get_tools_for_request_async",
                new=AsyncMock(return_value=[SimpleNamespace(name="t1")]),
            ),
            patch(f"{_AGENT_HUB_MODULE}.AgentConfig", MagicMock()),
        )

    async def _gather_spawn(self, configs: list[dict]) -> list[str]:
        async def _spawn(cfg: dict, tag: int) -> str:
            # InjectedToolCallId 约束：必须以完整 model ToolCall 形式调用（ToolNode 生产形态）
            tool_call = {
                "args": {"agent_name": f"worker-{tag}", "task": f"task-{tag}"},
                "name": "spawn_sub_agent",
                "type": "tool_call",
                "id": f"call-{tag}",
            }
            return await spawn_sub_agent.ainvoke(tool_call, config=cfg)

        return await asyncio.gather(*(_spawn(cfg, i) for i, cfg in enumerate(configs)))

    def test_concurrent_spawns_read_own_configurable(self):
        """gather 并发三次 spawn（不同用户/会话）：每次 runtime.spawn 收到调用自身的 config。"""
        configs = [
            _make_config(user_id=1, session_id="sess-A", assistant_message_id="m-A", enable_deep_thinking=True),
            _make_config(user_id=2, session_id="sess-B", assistant_message_id="m-B", enable_deep_thinking=False),
            _make_config(user_id=3, session_id="sess-C", assistant_message_id="m-C"),
        ]
        patchers = self._patch_stack()
        with patchers[0], patchers[1], patchers[2], patchers[3]:
            results = asyncio.run(self._gather_spawn(configs))

        for result in results:
            content = getattr(result, "content", result)
            self.assertIn("子代理已启动", content)
        self.assertEqual(self.fake_runtime.spawn.await_count, len(configs))

        for call, cfg in zip(self.fake_runtime.spawn.await_args_list, configs, strict=True):
            expected = cfg["configurable"]
            passed = call.kwargs["configurable"]
            for key, value in expected.items():
                self.assertEqual(
                    passed.get(key),
                    value,
                    f"runtime.spawn 收到的 configurable.{key} 与调用 config 不符（串号）",
                )
            self.assertEqual(call.kwargs["parent_thread_id"], expected["thread_id"])

    def test_sequential_spawns_no_cross_contamination(self):
        """顺序两次 spawn（不同 config）：第二次调用不得影响第一次已读取的上下文。"""
        first = _make_config(user_id=1, session_id="sess-A")
        second = _make_config(user_id=2, session_id="sess-B")
        patchers = self._patch_stack()
        with patchers[0], patchers[1], patchers[2], patchers[3]:
            asyncio.run(
                spawn_sub_agent.ainvoke(
                    {
                        "args": {"agent_name": "w1", "task": "t1"},
                        "name": "spawn_sub_agent",
                        "type": "tool_call",
                        "id": "call-1",
                    },
                    config=first,
                )
            )
            asyncio.run(
                spawn_sub_agent.ainvoke(
                    {
                        "args": {"agent_name": "w2", "task": "t2"},
                        "name": "spawn_sub_agent",
                        "type": "tool_call",
                        "id": "call-2",
                    },
                    config=second,
                )
            )

        first_call, second_call = self.fake_runtime.spawn.await_args_list
        self.assertEqual(first_call.kwargs["parent_thread_id"], "sess-A")
        self.assertEqual(first_call.kwargs["configurable"]["user_id"], 1)
        self.assertEqual(second_call.kwargs["parent_thread_id"], "sess-B")
        self.assertEqual(second_call.kwargs["configurable"]["user_id"], 2)

    def test_tool_singleton_holds_no_configurable_state(self):
        """单例工具不持有实例 configurable 缓存（防回归：禁止重引入实例字段）。"""
        self.assertFalse(hasattr(spawn_sub_agent, "parent_configurable"))
        self.assertFalse(hasattr(type(spawn_sub_agent), "_capture_configurable"))


if __name__ == "__main__":
    unittest.main()
