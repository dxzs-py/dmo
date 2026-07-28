"""DeepAgentBuilder 子 agent 安全机制测试

验证两个关键缺陷的修复：

缺陷 1：checkpointer contextvar 设置时机错误
    - 原问题：set_current_checkpointer 仅在 astream_research_with_interrupts
      （执行阶段）调用，而 SubAgentMiddleware.__init__ 在 create_deep_agent
      （构建阶段）内执行 _get_subagents，此时 contextvar 为 None，子 agent
      无 checkpointer，interrupt 被 Pregel 抑制，ApprovalMiddleware 完全失效。
    - 修复：DeepAgentBuilder._build_internal 在调用 create_deep_agent 前设置
      contextvar，try/finally 确保清理。

缺陷 2：general-purpose 子 agent 缺失 ApprovalMiddleware
    - 原问题：create_deep_agent 自动添加的 general-purpose 子 agent 使用内部
      硬编码的 middleware 栈，不包含用户传入的 ApprovalMiddleware。
    - 修复：_resolve_subagents 无条件追加含 ApprovalMiddleware 的 general-purpose
      SubAgent，覆盖 create_deep_agent 的自动添加。

运行方式:
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    conda activate langchain_xm
    python -m pytest Django_xm/apps/agent_hub/builders/tests/test_deep_builder_subagent_safety.py -v
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Django 环境初始化
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django
import django.apps

if not django.apps.apps.ready:
    django.setup()

from Django_xm.apps.agent_hub.builders import subagent_patch
from Django_xm.apps.agent_hub.builders.deep_builder import DeepAgentBuilder


class TestCheckpointerContextvarSetBeforeBuild(unittest.IsolatedAsyncioTestCase):
    """缺陷 1 修复验证：_build_internal 在 create_deep_agent 前设置 contextvar"""

    async def test_set_current_checkpointer_called_before_create_deep_agent(self):
        """_build_internal 调用 create_deep_agent 前必须设置 contextvar

        验证顺序：set_current_checkpointer → create_deep_agent → reset_current_checkpointer
        """
        builder = DeepAgentBuilder()

        # 记录调用顺序
        call_sequence: list = []

        # 用 wraps 让 mock 既记录调用又执行真实逻辑（实际设置 contextvar）
        real_set = subagent_patch.set_current_checkpointer
        real_reset = subagent_patch.reset_current_checkpointer
        real_patch_fn = subagent_patch.patch_subagent_middleware

        def _spy_set(cp):
            call_sequence.append(("set_current_checkpointer", cp))
            return real_set(cp)

        def _spy_reset(token):
            call_sequence.append(("reset_current_checkpointer", token))
            return real_reset(token)

        def _spy_patch():
            call_sequence.append(("patch_subagent_middleware",))
            return real_patch_fn()

        # mock create_deep_agent 捕获 contextvar 当时的值
        captured_contextvar_during_build = {}

        def _fake_create_deep_agent(**kwargs):
            call_sequence.append(("create_deep_agent",))
            # 在 create_deep_agent 执行期间（模拟 SubAgentMiddleware.__init__）
            # 读取 contextvar，此时应有值
            captured_contextvar_during_build["value"] = subagent_patch._CURRENT_CHECKPOINTER.get()
            graph = MagicMock()
            graph.checkpointer = kwargs.get("checkpointer")
            return graph

        fake_checkpointer = MagicMock(name="fake_checkpointer")

        # 构造最小 config，绕过实际 resolve_tools / resolve_model
        from Django_xm.apps.agent_hub.config import AgentConfig, AgentType
        config = AgentConfig(
            agent_type=AgentType.DEEP_RESEARCH,
            checkpointer=fake_checkpointer,
            session_id="test-session-1",
            work_dir="/tmp/test_work_dir",
        )

        with patch(
            "Django_xm.apps.agent_hub.builders.subagent_patch.set_current_checkpointer",
            side_effect=_spy_set,
        ), patch(
            "Django_xm.apps.agent_hub.builders.subagent_patch.reset_current_checkpointer",
            side_effect=_spy_reset,
        ), patch(
            "Django_xm.apps.agent_hub.builders.subagent_patch.patch_subagent_middleware",
            side_effect=_spy_patch,
        ), patch(
            "deepagents.create_deep_agent",
            side_effect=_fake_create_deep_agent,
        ), patch.object(
            builder, "_resolve_subagents", return_value=([], None),
        ), patch.object(
            builder, "_resolve_backend", return_value=None,
        ), patch.object(
            builder, "_resolve_skills", return_value=None,
        ), patch.object(
            builder, "_ensure_work_dir", return_value=("/tmp/test", "/tmp/test/sandbox"),
        ), patch.object(
            builder, "_build_system_prompt", return_value="test prompt",
        ), patch(
            "Django_xm.apps.agent_hub.model_resolver.resolve_model",
            return_value=MagicMock(),
        ), patch(
            "Django_xm.apps.agent_hub.tool_resolver.resolve_tools",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "Django_xm.apps.agent_hub.middleware.build_middleware",
            return_value=[],
        ), patch(
            "Django_xm.apps.agent_hub.builders.deep_builder.OfficialDeepAgentAdapter",
            return_value=MagicMock(),
        ):
            await builder.build(config)

        # 断言 1：set_current_checkpointer 在 create_deep_agent 之前调用
        set_index = next(
            (i for i, c in enumerate(call_sequence) if c[0] == "set_current_checkpointer"),
            -1,
        )
        create_index = next(
            (i for i, c in enumerate(call_sequence) if c[0] == "create_deep_agent"),
            -1,
        )
        self.assertGreater(set_index, -1, "set_current_checkpointer 未被调用")
        self.assertGreater(create_index, -1, "create_deep_agent 未被调用")
        self.assertLess(
            set_index, create_index,
            f"set_current_checkpointer 必须在 create_deep_agent 之前调用，"
            f"实际顺序: {call_sequence}",
        )

        # 断言 2：create_deep_agent 执行期间，contextvar 必须有值
        self.assertEqual(
            captured_contextvar_during_build.get("value"), fake_checkpointer,
            "create_deep_agent 期间 contextvar 必须为 config.checkpointer，"
            "否则 patched _get_subagents 走 fallback 分支，子 agent 无 checkpointer",
        )

        # 断言 3：reset_current_checkpointer 在 create_deep_agent 之后调用
        reset_index = next(
            (i for i, c in enumerate(call_sequence) if c[0] == "reset_current_checkpointer"),
            -1,
        )
        self.assertGreater(reset_index, -1, "reset_current_checkpointer 未被调用")
        self.assertGreater(
            reset_index, create_index,
            "reset_current_checkpointer 必须在 create_deep_agent 之后调用",
        )

    async def test_contextvar_reset_after_build_even_on_exception(self):
        """create_deep_agent 抛异常时，contextvar 也必须被 reset（try/finally）"""
        builder = DeepAgentBuilder()

        reset_called = []

        def _fake_reset(token):
            reset_called.append(token)

        from Django_xm.apps.agent_hub.config import AgentConfig, AgentType
        config = AgentConfig(
            agent_type=AgentType.DEEP_RESEARCH,
            checkpointer=MagicMock(),
            session_id="test-session-2",
            work_dir="/tmp/test_work_dir",
        )

        with patch(
            "Django_xm.apps.agent_hub.builders.subagent_patch.set_current_checkpointer",
            return_value=MagicMock(),
        ), patch(
            "Django_xm.apps.agent_hub.builders.subagent_patch.reset_current_checkpointer",
            side_effect=_fake_reset,
        ), patch(
            "Django_xm.apps.agent_hub.builders.subagent_patch.patch_subagent_middleware",
        ), patch(
            "deepagents.create_deep_agent",
            side_effect=RuntimeError("simulated build failure"),
        ), patch.object(
            builder, "_resolve_subagents", return_value=([], None),
        ), patch.object(
            builder, "_resolve_backend", return_value=None,
        ), patch.object(
            builder, "_resolve_skills", return_value=None,
        ), patch.object(
            builder, "_ensure_work_dir", return_value=("/tmp/test", "/tmp/test/sandbox"),
        ), patch.object(
            builder, "_build_system_prompt", return_value="test prompt",
        ), patch(
            "Django_xm.apps.agent_hub.model_resolver.resolve_model",
            return_value=MagicMock(),
        ), patch(
            "Django_xm.apps.agent_hub.tool_resolver.resolve_tools",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "Django_xm.apps.agent_hub.middleware.build_middleware",
            return_value=[],
        ), self.assertRaises(RuntimeError):
            await builder.build(config)

        self.assertEqual(len(reset_called), 1, "异常路径下 reset_current_checkpointer 必须被调用一次")


class TestGeneralPurposeSubagentApproval(unittest.TestCase):
    """缺陷 2 修复验证：_resolve_subagents 无条件添加含 ApprovalMiddleware 的 general-purpose"""

    def test_general_purpose_added_when_no_search_no_doc(self):
        """enable_web_search=False, enable_doc_analysis=False 时仍添加 general-purpose"""
        builder = DeepAgentBuilder()

        from Django_xm.apps.agent_hub.approval.middleware import ApprovalMiddleware
        from Django_xm.apps.agent_hub.config import AgentConfig, AgentType
        config = AgentConfig(
            agent_type=AgentType.DEEP_RESEARCH,
            tool_config={"enable_web_search": False, "enable_doc_analysis": False},
        )
        approval_mw = ApprovalMiddleware()

        subagents, retriever_name = builder._resolve_subagents(
            config, tools=[], approval_middleware=approval_mw,
        )

        # 必须有且仅有 general-purpose 子 agent
        self.assertEqual(len(subagents), 1)
        self.assertEqual(subagents[0]["name"], "general-purpose")

        # general-purpose 的 middleware 必须包含 ApprovalMiddleware
        gp_middleware = subagents[0].get("middleware", [])
        has_approval = any(
            isinstance(m, ApprovalMiddleware) for m in gp_middleware
        )
        self.assertTrue(
            has_approval,
            "general-purpose 子 agent 的 middleware 必须包含 ApprovalMiddleware",
        )

    def test_general_purpose_added_with_search_enabled(self):
        """enable_web_search=True 时同时添加 web-researcher 和 general-purpose"""
        builder = DeepAgentBuilder()

        from Django_xm.apps.agent_hub.approval.middleware import ApprovalMiddleware
        from Django_xm.apps.agent_hub.config import AgentConfig, AgentType
        config = AgentConfig(
            agent_type=AgentType.DEEP_RESEARCH,
            tool_config={"enable_web_search": True, "enable_doc_analysis": False},
        )
        approval_mw = ApprovalMiddleware()

        with patch(
            "Django_xm.apps.tools.langchain.web_search.create_tavily_search_tool",
            side_effect=ValueError("no tavily key"),
        ), patch(
            "Django_xm.apps.agent_hub.builders.deep_builder._has_search_tools",
            return_value=True,
        ):
            subagents, _ = builder._resolve_subagents(
                config, tools=[], approval_middleware=approval_mw,
            )

        names = [s["name"] for s in subagents]
        self.assertIn("web-researcher", names)
        self.assertIn("general-purpose", names)

        # 所有子 agent 都应有 ApprovalMiddleware
        for s in subagents:
            has_approval = any(
                isinstance(m, ApprovalMiddleware) for m in s.get("middleware", [])
            )
            self.assertTrue(
                has_approval,
                f"子 agent {s['name']} 的 middleware 必须包含 ApprovalMiddleware",
            )

    def test_general_purpose_inherits_main_tools(self):
        """general-purpose 子 agent 必须继承主 agent 的完整工具集"""
        builder = DeepAgentBuilder()

        # 用 MagicMock 模拟工具（_merge_tool_lists 用 getattr(t, 'name', '') 去重）
        tool_a = MagicMock()
        tool_a.name = "tool_a"
        tool_b = MagicMock()
        tool_b.name = "tool_b"
        main_tools = [tool_a, tool_b]

        from Django_xm.apps.agent_hub.config import AgentConfig, AgentType
        config = AgentConfig(
            agent_type=AgentType.DEEP_RESEARCH,
            tool_config={"enable_web_search": False, "enable_doc_analysis": False},
            tools=main_tools,
        )

        # _resolve_subagents 的 tools 参数由调用方传入（_build_internal 中是 resolve_tools 返回值）
        subagents, _ = builder._resolve_subagents(
            config, tools=main_tools, approval_middleware=None,
        )

        gp = next(s for s in subagents if s["name"] == "general-purpose")
        gp_tool_names = {getattr(t, "name", "") for t in gp.get("tools", [])}
        self.assertIn("tool_a", gp_tool_names)
        self.assertIn("tool_b", gp_tool_names)


if __name__ == "__main__":
    unittest.main()
