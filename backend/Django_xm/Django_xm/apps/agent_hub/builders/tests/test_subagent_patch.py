r"""SubAgentMiddleware 补丁单元测试（Task 6.7）。

覆盖 ``Django_xm.apps.agent_hub.builders.subagent_patch``：

1. ``set_current_checkpointer`` / ``reset_current_checkpointer``
   - contextvar 设置/重置
   - 协程隔离（并发任务互不影响）
2. ``patch_subagent_middleware``
   - 幂等性：多次调用不重复 patch
3. ``_patched_get_subagents``
   - 无 checkpointer：退回原行为
   - 有 checkpointer：注入到 ``create_agent``
   - CompiledSubAgent：直接透传
   - SubAgent 缺少 model / tools：抛 ValueError
   - interrupt_on：追加 HumanInTheLoopMiddleware
4. ``_patched_build_task_tool``
   - task 同步函数：未知 subagent_type 返回错误消息
   - task 同步函数：缺失 tool_call_id 抛 ValueError
   - atask 异步函数：继承父 callbacks
   - atask 异步函数：调用 _on_tool_event 转发事件

mock 策略：
- mock ``deepagents.middleware.subagents`` 模块的结构
- 不依赖真实 LangGraph/LangChain runtime
- mock ``create_agent`` / ``resolve_model`` / ``ToolRuntime``

运行方式：
    cd d:\programming\langchain\langchain_xm\backend\Django_xm
    conda activate langchain_xm
    python -m pytest Django_xm/apps/agent_hub/builders/tests/test_subagent_patch.py -v
"""

from __future__ import annotations

import asyncio
import contextvars
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Django 环境初始化
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django
import django.apps

if not django.apps.apps.ready:
    django.setup()


# ============================================================================
# 测试辅助：构造 fake deepagents.subagents 模块结构
# ============================================================================


def _make_fake_subagents_module():
    """构造一个 fake ``deepagents.middleware.subagents`` 模块。

    用于在测试中替换真实模块，避免依赖 LangGraph runtime。
    包含：
    - ``SubAgentMiddleware`` 类（含 ``_get_subagents`` 方法）
    - ``_build_task_tool`` 函数
    - ``CompiledSubAgent`` TypedDict
    - ``TaskToolSchema`` Pydantic schema（真实 BaseModel，避免 StructuredTool 校验失败）
    - ``_EXCLUDED_STATE_KEYS`` tuple
    - ``TASK_TOOL_DESCRIPTION`` 模板字符串
    """
    from pydantic import BaseModel, Field

    class _TaskToolSchema(BaseModel):
        """模拟 deepagents.middleware.subagents.TaskToolSchema 的最小 schema。

        ``StructuredTool.from_function`` 要求 ``args_schema`` 是 pydantic BaseModel 子类，
        MagicMock 会触发 TypeError: args_schema must be a subclass of pydantic BaseModel。
        """

        description: str = Field(..., description="Task description")
        subagent_type: str = Field(..., description="Subagent type to invoke")

    fake_module = MagicMock()

    class _FakeSubAgentMiddleware:
        """原始 SubAgentMiddleware 的 mock 版本。"""

        def __init__(self, subagents=None):
            self._subagents = subagents or []

        def _get_subagents(self):
            """原始方法：返回空列表（被 patch 后会被替换）。"""
            return []

    fake_module.SubAgentMiddleware = _FakeSubAgentMiddleware
    fake_module.CompiledSubAgent = dict  # TypedDict 退化为 dict
    fake_module.TaskToolSchema = _TaskToolSchema  # 真实 BaseModel，避免 StructuredTool 校验失败
    fake_module._EXCLUDED_STATE_KEYS = ("parent_config",)
    fake_module.TASK_TOOL_DESCRIPTION = "Task tool: {available_agents}"

    # _build_task_tool 默认实现（被 patch 后会替换）
    def _fake_build_task_tool(subagents, task_description=None):
        return MagicMock(name="original_task_tool")

    fake_module._build_task_tool = _fake_build_task_tool
    return fake_module


def _apply_fake_module_to_sys_modules(fake_module):
    """将 fake 模块注册到 sys.modules，使 ``from deepagents.middleware import subagents`` 命中。"""
    # 模拟 deepagents.middleware.subagents 包结构
    if "deepagents" not in sys.modules:
        sys.modules["deepagents"] = MagicMock()
    if "deepagents.middleware" not in sys.modules:
        sys.modules["deepagents.middleware"] = MagicMock()
    sys.modules["deepagents.middleware.subagents"] = fake_module
    if "deepagents._models" not in sys.modules:
        sys.modules["deepagents._models"] = MagicMock()
    if "deepagents.middleware.subagents" in sys.modules:
        # 确保 deepagents.middleware.subagents 属性也指向 fake_module
        sys.modules["deepagents.middleware"].subagents = fake_module


# ============================================================================
# 测试 1: set_current_checkpointer / reset_current_checkpointer
# ============================================================================


class CheckpointerContextVarTests(unittest.IsolatedAsyncioTestCase):
    """checkpointer contextvar 设置/重置/协程隔离。"""

    async def test_set_and_reset_checkpointer(self):
        """set_current_checkpointer 设置后，reset 恢复原值。"""
        from Django_xm.apps.agent_hub.builders.subagent_patch import (
            reset_current_checkpointer,
            set_current_checkpointer,
        )

        checkpointer = MagicMock(name="test_checkpointer")
        token = set_current_checkpointer(checkpointer)

        # 验证 fake token 类型（contextvars.Token）
        self.assertIsInstance(token, contextvars.Token)

        reset_current_checkpointer(token)
        # reset 后再读取应为 None（默认值）
        # 间接验证：通过 patch_subagent_middleware._patched_get_subagents 行为
        # 这里直接检查 contextvar
        from Django_xm.apps.agent_hub.builders.subagent_patch import (
            _CURRENT_CHECKPOINTER,
        )

        self.assertIsNone(_CURRENT_CHECKPOINTER.get())

    async def test_checkpointer_coroutine_isolation(self):
        """contextvar 协程隔离：不同 asyncio 任务互不影响。"""
        from Django_xm.apps.agent_hub.builders.subagent_patch import (
            _CURRENT_CHECKPOINTER,
            reset_current_checkpointer,
            set_current_checkpointer,
        )

        # MagicMock.name 是特殊属性（mock 的标识），用 _mock_name 区分
        # 这里用属性 _test_id 在两个 mock 上标记不同身份
        ck_a = MagicMock()
        ck_a._test_id = "ck_a"
        results = {}

        async def _task_a():
            token = set_current_checkpointer(ck_a)
            await asyncio.sleep(0.01)
            results["a"] = _CURRENT_CHECKPOINTER.get()
            reset_current_checkpointer(token)

        async def _task_b():
            await asyncio.sleep(0.005)  # 让 task_a 先设置
            results["b"] = _CURRENT_CHECKPOINTER.get()  # 应为 None，不受 task_a 影响

        await asyncio.gather(_task_a(), _task_b())

        self.assertIs(results["a"], ck_a, "task_a 应读到自己的 ck_a")
        self.assertIsNone(results["b"], "task_b 应读到 None，不受 task_a 影响")

    async def test_nested_set_returns_correct_token(self):
        """嵌套 set：每次 set 返回不同的 token，依次 reset。"""
        from Django_xm.apps.agent_hub.builders.subagent_patch import (
            _CURRENT_CHECKPOINTER,
            reset_current_checkpointer,
            set_current_checkpointer,
        )

        ck_outer = MagicMock()
        ck_outer._test_id = "outer"
        ck_inner = MagicMock()
        ck_inner._test_id = "inner"

        token_outer = set_current_checkpointer(ck_outer)
        self.assertIs(_CURRENT_CHECKPOINTER.get(), ck_outer)

        token_inner = set_current_checkpointer(ck_inner)
        self.assertIs(_CURRENT_CHECKPOINTER.get(), ck_inner)

        reset_current_checkpointer(token_inner)
        self.assertIs(_CURRENT_CHECKPOINTER.get(), ck_outer)

        reset_current_checkpointer(token_outer)
        self.assertIsNone(_CURRENT_CHECKPOINTER.get())


# ============================================================================
# 测试 2: patch_subagent_middleware 幂等性
# ============================================================================


class PatchIdempotencyTests(unittest.TestCase):
    """patch_subagent_middleware 幂等性测试。"""

    def test_patch_is_idempotent(self):
        """多次调用 patch_subagent_middleware 不重复 patch。

        验证：通过 _patched 标志位检查。
        """
        fake_module = _make_fake_subagents_module()
        _apply_fake_module_to_sys_modules(fake_module)

        # 重置 _patched 标志，确保测试独立
        import Django_xm.apps.agent_hub.builders.subagent_patch as patch_module

        original_patched = patch_module._patched
        original_get_subagents = fake_module.SubAgentMiddleware._get_subagents
        original_build_task_tool = fake_module._build_task_tool

        try:
            patch_module._patched = False

            # 第一次调用：应用 patch
            patch_module.patch_subagent_middleware()
            self.assertTrue(patch_module._patched)
            patched_get_subagents_1 = fake_module.SubAgentMiddleware._get_subagents
            patched_build_task_tool_1 = fake_module._build_task_tool
            self.assertIsNot(
                patched_get_subagents_1,
                original_get_subagents,
                "第一次 patch 后 _get_subagents 应被替换",
            )
            self.assertIsNot(
                patched_build_task_tool_1,
                original_build_task_tool,
                "第一次 patch 后 _build_task_tool 应被替换",
            )

            # 第二次调用：不应再次 patch（_patched=True 时直接 return）
            patch_module.patch_subagent_middleware()
            self.assertIs(
                fake_module.SubAgentMiddleware._get_subagents,
                patched_get_subagents_1,
                "第二次调用不应替换 _get_subagents",
            )
            self.assertIs(
                fake_module._build_task_tool,
                patched_build_task_tool_1,
                "第二次调用不应替换 _build_task_tool",
            )
        finally:
            # 恢复 _patched 标志（避免污染其他测试）
            patch_module._patched = original_patched


# ============================================================================
# 测试 3: _patched_get_subagents
# ============================================================================


class PatchedGetSubagentsTests(unittest.IsolatedAsyncioTestCase):
    """_patched_get_subagents 行为测试。"""

    def setUp(self):
        """每个测试前重置 patch 状态，确保独立。"""
        self.fake_module = _make_fake_subagents_module()
        _apply_fake_module_to_sys_modules(self.fake_module)

        import Django_xm.apps.agent_hub.builders.subagent_patch as patch_module

        self.patch_module = patch_module
        self.original_patched = patch_module._patched
        patch_module._patched = False
        patch_module.patch_subagent_middleware()

    def tearDown(self):
        """恢复 patch 状态。"""
        self.patch_module._patched = self.original_patched

    def test_no_checkpointer_falls_back_to_original(self):
        """无 checkpointer 时退回原 _get_subagents 行为。"""
        # 原方法返回空列表，验证 fall-back
        middleware = self.fake_module.SubAgentMiddleware(subagents=[])
        # contextvar 为 None（默认值）
        result = middleware._get_subagents()
        self.assertEqual(result, [])

    def test_with_checkpointer_injects_into_create_agent(self):
        """有 checkpointer 时注入到 create_agent。"""
        # mock create_agent 与 resolve_model
        fake_create_agent = MagicMock(return_value=MagicMock(name="subagent_runnable"))
        fake_resolve_model = MagicMock(return_value=MagicMock(name="resolved_model"))

        checkpointer = MagicMock(name="test_checkpointer")
        middleware = self.fake_module.SubAgentMiddleware(
            subagents=[
                {
                    "name": "web-researcher",
                    "description": "search expert",
                    "model": "gpt-4o",
                    "tools": [MagicMock()],
                    "system_prompt": "you are a researcher",
                    "middleware": [],
                }
            ]
        )

        with (
            patch(
                "langchain.agents.create_agent",
                fake_create_agent,
            ),
            patch(
                "deepagents._models.resolve_model",
                fake_resolve_model,
            ),
        ):
            from Django_xm.apps.agent_hub.builders.subagent_patch import (
                reset_current_checkpointer,
                set_current_checkpointer,
            )

            token = set_current_checkpointer(checkpointer)
            try:
                result = middleware._get_subagents()
            finally:
                reset_current_checkpointer(token)

        # 验证 create_agent 被调用，且传入 checkpointer
        self.assertEqual(fake_create_agent.call_count, 1)
        call_kwargs = fake_create_agent.call_args.kwargs
        self.assertIs(call_kwargs["checkpointer"], checkpointer)
        self.assertEqual(call_kwargs["name"], "web-researcher")

        # 验证返回的 spec 列表
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "web-researcher")
        self.assertEqual(result[0]["description"], "search expert")

    def test_compiled_subagent_passthrough(self):
        """CompiledSubAgent（含 runnable）直接透传，不调用 create_agent。"""
        fake_create_agent = MagicMock()
        fake_resolve_model = MagicMock()
        compiled_runnable = MagicMock(name="compiled_runnable")

        middleware = self.fake_module.SubAgentMiddleware(
            subagents=[
                {
                    "name": "doc-analyst",
                    "description": "doc analysis",
                    "runnable": compiled_runnable,
                }
            ]
        )

        with (
            patch(
                "langchain.agents.create_agent",
                fake_create_agent,
            ),
            patch(
                "deepagents._models.resolve_model",
                fake_resolve_model,
            ),
        ):
            from Django_xm.apps.agent_hub.builders.subagent_patch import (
                reset_current_checkpointer,
                set_current_checkpointer,
            )

            token = set_current_checkpointer(MagicMock(name="ck"))
            try:
                result = middleware._get_subagents()
            finally:
                reset_current_checkpointer(token)

        # create_agent 不应被调用（已预编译）
        fake_create_agent.assert_not_called()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "doc-analyst")

    def test_subagent_missing_model_raises_value_error(self):
        """SubAgent 缺少 model 字段抛 ValueError。"""
        middleware = self.fake_module.SubAgentMiddleware(
            subagents=[
                {
                    "name": "broken-subagent",
                    "description": "missing model",
                    "tools": [],
                    "system_prompt": "x",
                    "middleware": [],
                }
            ]
        )

        from Django_xm.apps.agent_hub.builders.subagent_patch import (
            reset_current_checkpointer,
            set_current_checkpointer,
        )

        token = set_current_checkpointer(MagicMock(name="ck"))
        try:
            with self.assertRaises(ValueError) as ctx:
                middleware._get_subagents()
            self.assertIn("model", str(ctx.exception))
            self.assertIn("broken-subagent", str(ctx.exception))
        finally:
            reset_current_checkpointer(token)

    def test_subagent_missing_tools_raises_value_error(self):
        """SubAgent 缺少 tools 字段抛 ValueError。"""
        middleware = self.fake_module.SubAgentMiddleware(
            subagents=[
                {
                    "name": "no-tools-agent",
                    "description": "missing tools",
                    "model": "gpt-4o",
                    "system_prompt": "x",
                    "middleware": [],
                }
            ]
        )

        from Django_xm.apps.agent_hub.builders.subagent_patch import (
            reset_current_checkpointer,
            set_current_checkpointer,
        )

        token = set_current_checkpointer(MagicMock(name="ck"))
        try:
            with self.assertRaises(ValueError) as ctx:
                middleware._get_subagents()
            self.assertIn("tools", str(ctx.exception))
            self.assertIn("no-tools-agent", str(ctx.exception))
        finally:
            reset_current_checkpointer(token)

    def test_interrupt_on_appends_human_in_loop_middleware(self):
        """interrupt_on 触发时追加 HumanInTheLoopMiddleware。"""
        fake_create_agent = MagicMock(return_value=MagicMock())
        fake_resolve_model = MagicMock(return_value=MagicMock())

        middleware = self.fake_module.SubAgentMiddleware(
            subagents=[
                {
                    "name": "risky-agent",
                    "description": "needs approval",
                    "model": "gpt-4o",
                    "tools": [],
                    "system_prompt": "x",
                    "middleware": [],
                    "interrupt_on": {"write_file": True},
                }
            ]
        )

        with (
            patch(
                "langchain.agents.create_agent",
                fake_create_agent,
            ),
            patch(
                "deepagents._models.resolve_model",
                fake_resolve_model,
            ),
            patch(
                "langchain.agents.middleware.HumanInTheLoopMiddleware",
            ) as mock_hitl,
        ):
            from Django_xm.apps.agent_hub.builders.subagent_patch import (
                reset_current_checkpointer,
                set_current_checkpointer,
            )

            token = set_current_checkpointer(MagicMock(name="ck"))
            try:
                middleware._get_subagents()
            finally:
                reset_current_checkpointer(token)

        # HumanInTheLoopMiddleware 应被实例化
        mock_hitl.assert_called_once()
        hitl_kwargs = mock_hitl.call_args.kwargs
        self.assertEqual(hitl_kwargs["interrupt_on"], {"write_file": True})


# ============================================================================
# 测试 4: _patched_build_task_tool
# ============================================================================


class PatchedBuildTaskToolTests(unittest.IsolatedAsyncioTestCase):
    """_patched_build_task_tool 行为测试。"""

    def setUp(self):
        """每个测试前重置 patch 状态。"""
        self.fake_module = _make_fake_subagents_module()
        _apply_fake_module_to_sys_modules(self.fake_module)

        import Django_xm.apps.agent_hub.builders.subagent_patch as patch_module

        self.patch_module = patch_module
        self.original_patched = patch_module._patched
        patch_module._patched = False
        patch_module.patch_subagent_middleware()

    def tearDown(self):
        self.patch_module._patched = self.original_patched

    def _make_subagents_spec(self):
        """构造 _build_task_tool 需要的 subagents spec 列表。"""
        return [
            {
                "name": "web-researcher",
                "description": "search expert",
                "runnable": MagicMock(name="web_runnable"),
            },
            {
                "name": "doc-analyst",
                "description": "doc analysis",
                "runnable": MagicMock(name="doc_runnable"),
            },
        ]

    def test_task_unknown_subagent_type_returns_error_message(self):
        """task 同步函数：未知 subagent_type 返回错误消息。

        StructuredTool 通过 ``func`` 暴露原始 task 函数（``from_function(func=task)``）。
        测试通过 ``tool.func`` 直接调用原始 task 函数，绕过 StructuredTool.invoke 的
        args_schema 校验（避免 mock ToolRuntime 与 pydantic 校验冲突）。
        """
        from langchain.tools import ToolRuntime

        tool = self.fake_module._build_task_tool(self._make_subagents_spec())

        # 构造 ToolRuntime mock（缺失 tool_call_id 之前先测未知类型）
        runtime = MagicMock(spec=ToolRuntime)
        runtime.tool_call_id = "tc-1"
        runtime.state = {"messages": []}
        runtime.config = {}

        # tool.func 是原始 task 函数（StructuredTool.from_function(func=task)）
        result = tool.func(
            description="test",
            subagent_type="non-existent-agent",
            runtime=runtime,
        )
        self.assertIsInstance(result, str)
        self.assertIn("non-existent-agent", result)
        self.assertIn("web-researcher", result)
        self.assertIn("doc-analyst", result)

    def test_task_missing_tool_call_id_raises_value_error(self):
        """task 同步函数：缺失 tool_call_id 抛 ValueError。

        通过 ``tool.func`` 直接调用原始 task 函数。
        """
        from langchain.tools import ToolRuntime

        tool = self.fake_module._build_task_tool(self._make_subagents_spec())

        runtime = MagicMock(spec=ToolRuntime)
        runtime.tool_call_id = ""  # 空 tool_call_id
        runtime.state = {"messages": []}
        runtime.config = {}

        with self.assertRaises(ValueError) as ctx:
            tool.func(
                description="test",
                subagent_type="web-researcher",
                runtime=runtime,
            )
        self.assertIn("Tool call ID", str(ctx.exception))

    async def test_atask_inherits_parent_callbacks(self):
        """atask 异步函数：继承父 graph 的 callbacks。"""
        from langchain.tools import ToolRuntime

        subagent_runnable = MagicMock(name="web_runnable")
        subagents_spec = [
            {
                "name": "web-researcher",
                "description": "search",
                "runnable": subagent_runnable,
            }
        ]

        tool = self.fake_module._build_task_tool(subagents_spec)

        parent_callbacks = [MagicMock(name="cb1"), MagicMock(name="cb2")]
        runtime = MagicMock(spec=ToolRuntime)
        runtime.tool_call_id = "tc-async-1"
        runtime.state = {"messages": []}
        runtime.config = {
            "callbacks": parent_callbacks,
            "configurable": {"thread_id": "t-1"},
        }

        # mock 子智能体 ainvoke 返回值（包含 messages 用于 _return_command_with_state_update）
        from langchain_core.messages import AIMessage

        subagent_runnable.ainvoke = AsyncMock(
            return_value={
                "messages": [AIMessage(content="subagent result")],
            }
        )

        # 不传 _on_tool_event，走 ainvoke 路径
        result = await tool.coroutine(
            description="search the web",
            subagent_type="web-researcher",
            runtime=runtime,
        )

        # 验证 ainvoke 被调用（位置参数调用 ainvoke(state, config)）
        self.assertTrue(subagent_runnable.ainvoke.called)
        call_args = subagent_runnable.ainvoke.call_args.args
        # 子智能体 config 应继承父 callbacks（位置参数第 2 个）
        subagent_config = call_args[1]
        self.assertEqual(subagent_config["callbacks"], parent_callbacks)
        self.assertEqual(
            subagent_config["configurable"]["ls_agent_type"],
            "subagent",
        )
        # _on_tool_event 应从子智能体 config 中移除（避免递归）
        self.assertNotIn("_on_tool_event", subagent_config["configurable"])

        # 结果应是 Command（含 ToolMessage update）
        self.assertTrue(hasattr(result, "update"))

    async def test_atask_unknown_subagent_type_returns_error_message(self):
        """atask 异步函数：未知 subagent_type 返回错误消息（字符串）。"""
        from langchain.tools import ToolRuntime

        tool = self.fake_module._build_task_tool(self._make_subagents_spec())

        runtime = MagicMock(spec=ToolRuntime)
        runtime.tool_call_id = "tc-1"
        runtime.state = {"messages": []}
        runtime.config = {}

        result = await tool.coroutine(
            description="test",
            subagent_type="missing-agent",
            runtime=runtime,
        )
        self.assertIsInstance(result, str)
        self.assertIn("missing-agent", result)

    async def test_atask_missing_tool_call_id_raises_value_error(self):
        """atask 异步函数：缺失 tool_call_id 抛 ValueError。"""
        from langchain.tools import ToolRuntime

        tool = self.fake_module._build_task_tool(self._make_subagents_spec())

        runtime = MagicMock(spec=ToolRuntime)
        runtime.tool_call_id = ""
        runtime.state = {"messages": []}
        runtime.config = {}

        with self.assertRaises(ValueError) as ctx:
            await tool.coroutine(
                description="test",
                subagent_type="web-researcher",
                runtime=runtime,
            )
        self.assertIn("Tool call ID", str(ctx.exception))

    async def test_atask_with_on_tool_event_uses_astream(self):
        """atask 异步函数：有 _on_tool_event 时走 astream 路径转发事件。"""
        from langchain.tools import ToolRuntime
        from langchain_core.messages import AIMessageChunk, ToolMessage

        subagent_runnable = MagicMock(name="web_runnable")
        subagents_spec = [
            {
                "name": "web-researcher",
                "description": "search",
                "runnable": subagent_runnable,
            }
        ]

        tool = self.fake_module._build_task_tool(subagents_spec)

        on_tool_event = AsyncMock(name="on_tool_event")

        runtime = MagicMock(spec=ToolRuntime)
        runtime.tool_call_id = "tc-stream-1"
        runtime.state = {"messages": []}
        runtime.config = {
            "callbacks": [MagicMock()],
            "configurable": {
                "thread_id": "t-1",
                "_on_tool_event": on_tool_event,
            },
        }

        # 构造 astream 产生的 chunk 序列：
        # 1. AIMessageChunk 含 tool_calls → 触发 TOOL_CALL_INPUT_READY
        # 2. values 模式产出 final_state
        async def _fake_astream(state, config, stream_mode=None):
            # 模拟 AIMessageChunk 含 tool_calls
            chunk = AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                        "name": "web_search",
                        "args": '{"query": "test"}',
                        "id": "tool-call-1",
                        "index": 0,
                    }
                ],
            )
            yield ("messages", (chunk, {"langgraph_node": "agent"}))
            # values 模式产出最终状态
            yield ("values", {"messages": [chunk, ToolMessage(content="result", tool_call_id="tool-call-1")]})

        subagent_runnable.astream = MagicMock(return_value=_fake_astream({}, {}, stream_mode=[]))

        await tool.coroutine(
            description="search",
            subagent_type="web-researcher",
            runtime=runtime,
        )

        # 验证 astream 被调用
        self.assertTrue(subagent_runnable.astream.called)
        # _on_tool_event 至少被调用一次（INPUT_READY 或 COMPLETED）
        self.assertTrue(on_tool_event.called)


# ============================================================================
# 测试 6: 子 agent 嵌套层级字段透传（Phase E3）
# ============================================================================


class NestedFieldPropagationTests(unittest.IsolatedAsyncioTestCase):
    """_astream_with_tool_events 子 agent 嵌套层级字段透传测试。

    Phase E3 端到端传播链路的第一段：
        atask 在构造 subagent_config 时注入嵌套字段到 configurable →
        _astream_with_tool_events 从 configurable 提取 →
        随每个 tool 事件传递给 on_tool_event 回调（kwargs）→
        adapter._on_tool_event 透传到 _publish_tool_event →
        service.register 注册到 ToolCallContext（test_tool_call_lifecycle.py 覆盖）

    本测试聚焦第一段：验证 _astream_with_tool_events 正确提取并传递嵌套字段。
    """

    def setUp(self):
        """每个测试前重置 patch 状态。"""
        self.fake_module = _make_fake_subagents_module()
        _apply_fake_module_to_sys_modules(self.fake_module)

        import Django_xm.apps.agent_hub.builders.subagent_patch as patch_module

        self.patch_module = patch_module
        self.original_patched = patch_module._patched
        patch_module._patched = False
        patch_module.patch_subagent_middleware()

    def tearDown(self):
        self.patch_module._patched = self.original_patched

    async def test_atask_passes_nested_fields_to_on_tool_event(self):
        """atask 异步函数：嵌套字段从 configurable 提取并传递给 on_tool_event kwargs。

        验证 on_tool_event 收到的 kwargs 含：
        - depth（=1，子 agent）
        - parent_tool_call_id
        - agent_name
        - agent_path
        - risk_ceiling
        """
        from langchain.tools import ToolRuntime
        from langchain_core.messages import AIMessageChunk, ToolMessage

        subagent_runnable = MagicMock(name="web_runnable")
        subagents_spec = [
            {
                "name": "web-researcher",
                "description": "search expert",
                "runnable": subagent_runnable,
            }
        ]

        tool = self.fake_module._build_task_tool(subagents_spec)

        on_tool_event = AsyncMock(name="on_tool_event")

        runtime = MagicMock(spec=ToolRuntime)
        runtime.tool_call_id = "tc-parent-task-call"
        runtime.state = {"messages": []}
        # 父 configurable 不含嵌套字段（主 agent 视角），atask 应构造子 agent 嵌套字段
        runtime.config = {
            "callbacks": [MagicMock()],
            "configurable": {
                "thread_id": "task-1",
                "_on_tool_event": on_tool_event,
            },
        }

        # 构造 astream 产出的 chunk：子 agent 调用 web_search 工具
        async def _fake_astream(state, config, stream_mode=None):
            chunk = AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                        "name": "web_search",
                        "args": '{"query": "langchain"}',
                        "id": "tool-call-sub-1",
                        "index": 0,
                    }
                ],
            )
            yield ("messages", (chunk, {"langgraph_node": "agent"}))
            yield (
                "values",
                {
                    "messages": [chunk, ToolMessage(content="result", tool_call_id="tool-call-sub-1")],
                },
            )

        subagent_runnable.astream = MagicMock(return_value=_fake_astream({}, {}, stream_mode=[]))

        await tool.coroutine(
            description="search the web",
            subagent_type="web-researcher",
            runtime=runtime,
        )

        # on_tool_event 至少被调用一次
        self.assertTrue(on_tool_event.called, "on_tool_event 应被调用")

        # 检查所有调用，至少有一次含嵌套字段
        found_nested = False
        for call in on_tool_event.call_args_list:
            kwargs = call.kwargs
            if (
                kwargs.get("depth") == 1
                and kwargs.get("parent_tool_call_id") == "tc-parent-task-call"
                and kwargs.get("agent_name") == "web-researcher"
                and kwargs.get("agent_path") == ["main", "web-researcher"]
                and kwargs.get("risk_ceiling") is not None
            ):
                found_nested = True
                break

        self.assertTrue(
            found_nested,
            f"on_tool_event 应收到嵌套字段 kwargs, 实际调用: {on_tool_event.call_args_list}",
        )

    async def test_atask_inherits_parent_depth_for_nested_subagent(self):
        """atask 嵌套深度递增：父 agent depth=1 时，子 agent depth=2。

        场景：二级子 agent（子 agent 内部再调用 task 工具）。
        验证 agent_path 从父继承并追加当前子 agent。
        """
        from langchain.tools import ToolRuntime
        from langchain_core.messages import AIMessageChunk, ToolMessage

        subagent_runnable = MagicMock(name="nested_runnable")
        subagents_spec = [
            {
                "name": "doc-analyst",
                "description": "doc analysis",
                "runnable": subagent_runnable,
            }
        ]

        tool = self.fake_module._build_task_tool(subagents_spec)

        on_tool_event = AsyncMock(name="on_tool_event")

        runtime = MagicMock(spec=ToolRuntime)
        runtime.tool_call_id = "tc-level2-task"
        runtime.state = {"messages": []}
        # 父已经是子 agent（depth=1, agent_path=["main", "web-researcher"]）
        runtime.config = {
            "callbacks": [MagicMock()],
            "configurable": {
                "thread_id": "task-1",
                "_on_tool_event": on_tool_event,
                "depth": 1,
                "agent_name": "web-researcher",
                "agent_path": ["main", "web-researcher"],
                "parent_tool_call_id": "tc-level1-task",
                "risk_ceiling": "controlled",
            },
        }

        async def _fake_astream(state, config, stream_mode=None):
            chunk = AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                        "name": "read_file",
                        "args": '{"path": "/sandbox/report.md"}',
                        "id": "tool-call-level2-1",
                        "index": 0,
                    }
                ],
            )
            yield ("messages", (chunk, {"langgraph_node": "agent"}))
            yield (
                "values",
                {
                    "messages": [chunk, ToolMessage(content="doc", tool_call_id="tool-call-level2-1")],
                },
            )

        subagent_runnable.astream = MagicMock(return_value=_fake_astream({}, {}, stream_mode=[]))

        await tool.coroutine(
            description="analyze doc",
            subagent_type="doc-analyst",
            runtime=runtime,
        )

        self.assertTrue(on_tool_event.called)
        # 验证深度递增 + agent_path 追加
        found_level2 = False
        for call in on_tool_event.call_args_list:
            kwargs = call.kwargs
            if (
                kwargs.get("depth") == 2
                and kwargs.get("parent_tool_call_id") == "tc-level2-task"
                and kwargs.get("agent_name") == "doc-analyst"
                and kwargs.get("agent_path") == ["main", "web-researcher", "doc-analyst"]
            ):
                found_level2 = True
                break

        self.assertTrue(
            found_level2,
            f"二级子 agent 应 depth=2 且 agent_path 追加, 实际: {on_tool_event.call_args_list}",
        )

    async def test_atask_exceeds_max_depth_raises_value_error(self):
        """atask 嵌套深度超过上限（默认 3）抛 ValueError，防止无限嵌套。"""
        from langchain.tools import ToolRuntime

        subagents_spec = [
            {
                "name": "web-researcher",
                "description": "search",
                "runnable": MagicMock(name="runnable"),
            }
        ]

        tool = self.fake_module._build_task_tool(subagents_spec)

        runtime = MagicMock(spec=ToolRuntime)
        runtime.tool_call_id = "tc-over-depth"
        runtime.state = {"messages": []}
        # 父 depth=3，子 agent 将 depth=4，超过上限
        runtime.config = {
            "callbacks": [],
            "configurable": {
                "thread_id": "task-1",
                "_on_tool_event": AsyncMock(),
                "depth": 3,
                "agent_path": ["main", "a", "b", "c"],
            },
        }

        with self.assertRaises(ValueError) as ctx:
            await tool.coroutine(
                description="too deep",
                subagent_type="web-researcher",
                runtime=runtime,
            )
        self.assertIn("嵌套深度", str(ctx.exception))


# ============================================================================
# 测试 5: 集成场景 - checkpointer 注入 + callbacks 继承联合
# ============================================================================


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    """checkpointer 注入与 callbacks 继承的联合验证。"""

    def setUp(self):
        self.fake_module = _make_fake_subagents_module()
        _apply_fake_module_to_sys_modules(self.fake_module)
        import Django_xm.apps.agent_hub.builders.subagent_patch as patch_module

        self.patch_module = patch_module
        self.original_patched = patch_module._patched
        patch_module._patched = False
        patch_module.patch_subagent_middleware()

    def tearDown(self):
        self.patch_module._patched = self.original_patched

    async def test_checkpointer_persists_across_subagent_creation(self):
        """checkpointer 通过 contextvar 传递，确保子智能体 interrupt 状态持久化。"""
        from Django_xm.apps.agent_hub.builders.subagent_patch import (
            reset_current_checkpointer,
            set_current_checkpointer,
        )

        checkpointer = MagicMock(name="persistent_checkpointer")
        fake_create_agent = MagicMock(return_value=MagicMock(name="runnable"))
        fake_resolve_model = MagicMock(return_value=MagicMock(name="model"))

        # 模拟 SubAgent 配置（含 interrupt_on）
        middleware = self.fake_module.SubAgentMiddleware(
            subagents=[
                {
                    "name": "approval-required-agent",
                    "description": "needs human approval",
                    "model": "gpt-4o",
                    "tools": [MagicMock()],
                    "system_prompt": "x",
                    "middleware": [],
                    "interrupt_on": {"shell_exec": True},
                }
            ]
        )

        with (
            patch(
                "langchain.agents.create_agent",
                fake_create_agent,
            ),
            patch(
                "deepagents._models.resolve_model",
                fake_resolve_model,
            ),
            patch(
                "langchain.agents.middleware.HumanInTheLoopMiddleware",
            ) as mock_hitl,
        ):
            token = set_current_checkpointer(checkpointer)
            try:
                specs = middleware._get_subagents()
            finally:
                reset_current_checkpointer(token)

        # 验证 create_agent 收到 checkpointer
        self.assertEqual(fake_create_agent.call_count, 1)
        self.assertIs(
            fake_create_agent.call_args.kwargs["checkpointer"],
            checkpointer,
        )
        # 验证 HumanInTheLoopMiddleware 被创建（interrupt_on 触发）
        mock_hitl.assert_called_once()
        # 返回的 spec 数量正确
        self.assertEqual(len(specs), 1)


if __name__ == "__main__":
    unittest.main()
