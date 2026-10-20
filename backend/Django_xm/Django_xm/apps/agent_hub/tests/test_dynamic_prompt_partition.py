"""builder 链 system_prompt 静态性 / 易变分区注入通道测试（P0-1 前缀缓存裁决）。

背景：system_prompt 位于消息序列位置 0，agent 每次构建都会重建（chat 主链每
请求重建、深度研究 Celery 恢复路径重建）。任何易变内容（时间戳、文档上下文）
进入 system_prompt 都使整条 prompt 前缀缓存失效。修复后：

- system_prompt = 静态段（base prompt + 技能 L1 元数据 + 工具使用说明），
  同一 config 两次构建逐字节相同（本文件守护）；
- 易变分区（当前时间行 + 文档上下文）经 ``build_dynamic_context_prompt`` 的
  volatile_text 返回，由 ``attach_volatile_injection`` 装配进尾部注入通道
  （MemoryInjectionMiddleware；栈中已有注入中间件时按段落去重合并，避免双注入）。

覆盖：
1. 分区拆分：static_prompt 稳定且不含时间戳；volatile_text 含时间戳行与
   文档上下文（tools 非空时）；tools 为空时 volatile 仅时间戳（非工具链路
   不引入文档注入）。
2. attach_volatile_injection：空栈新建 / 已有实例前插去重合并 / 空文本 no-op。
3. base_builder 接线：dynamic 成功 → system_prompt=静态段 + volatile 进注入
   中间件；调用方已有注入中间件 → 合并（单实例、无双注入）。
4. deep_builder 接线：deep_dynamic_context_enabled 开启 → volatile 装配；
   关闭（默认）→ 静态回退、不装配（开关语义保持）。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.agent_hub.tests.test_dynamic_prompt_partition --noinput
"""

import asyncio
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from Django_xm.apps.agent_hub.builders._common import (
    attach_volatile_injection,
    build_dynamic_context_prompt,
)
from Django_xm.apps.agent_hub.builders.base_builder import BaseAgentBuilder
from Django_xm.apps.agent_hub.builders.deep_builder import DeepAgentBuilder
from Django_xm.apps.agent_hub.config import AgentConfig, AgentType
from Django_xm.apps.context_manager.middleware import MemoryInjectionMiddleware

# manager.py 内 from-import 绑定，patch 打在 manager 命名空间
_DOC_PROVIDER = "Django_xm.apps.context_manager.services.manager.get_document_context_provider"


def _make_doc_tool() -> SimpleNamespace:
    """确定性模拟 MCP 工具（dict metadata，两次构建产出稳定文本）。"""
    return SimpleNamespace(name="doc_tool", description="文档检索工具", metadata={"is_mcp_tool": True})


def _build_result(config, *, prompt_mode, tools):
    return asyncio.run(build_dynamic_context_prompt(config, prompt_mode=prompt_mode, tools=tools))


class BuildDynamicContextPromptPartitionTests(SimpleTestCase):
    """静态/易变分区拆分与静态性守护。"""

    def test_static_prompt_byte_stable_across_builds(self):
        """同 config 两次构建：static_prompt 逐字节相同且不含时间戳。"""
        config = AgentConfig(agent_type=AgentType.BASE, model_name="gpt-4o", user_id=1, session_id="t-1")
        tool = _make_doc_tool()
        with mock.patch(_DOC_PROVIDER, return_value=None):
            result_first = _build_result(config, prompt_mode="agent", tools=[tool])
            result_second = _build_result(config, prompt_mode="agent", tools=[tool])

        self.assertIsNotNone(result_first)
        static_first, _volatile_first = result_first
        static_second, _volatile_second = result_second
        self.assertEqual(static_first, static_second, "static_prompt 必须逐字节稳定（前缀缓存前提）")
        self.assertNotIn("当前时间：", static_first, "system_prompt 不得含时间戳（易变内容）")

    def test_volatile_contains_time_and_document_context(self):
        """tools 非空：volatile_text 含当前时间行 + 文档上下文（含参考尾注）。"""
        config = AgentConfig(agent_type=AgentType.BASE, model_name="gpt-4o", user_id=1, session_id="t-1")
        tool = _make_doc_tool()
        provider = mock.patch(
            _DOC_PROVIDER,
            return_value=lambda user_id, store: "用户最近上传了 report.pdf 与 data.xlsx",
        )
        with provider:
            result = _build_result(config, prompt_mode="agent", tools=[tool])

        static_prompt, volatile_text = result
        self.assertTrue(volatile_text.startswith("当前时间："), "易变分区必须以当前时间行开头")
        self.assertIn("用户最近上传了 report.pdf 与 data.xlsx", volatile_text)
        self.assertIn("请在回答时优先参考用户已上传的文档内容。", volatile_text)
        self.assertNotIn("report.pdf", static_prompt, "文档上下文不得进静态段")

    def test_no_tools_volatile_time_only(self):
        """无 tools（纯聊天链路语义）：volatile 仅时间戳，不引入文档注入。"""
        config = AgentConfig(agent_type=AgentType.BASE, model_name="gpt-4o", user_id=1, session_id="t-1")
        with mock.patch(_DOC_PROVIDER) as mock_provider:
            result = _build_result(config, prompt_mode="default", tools=None)

        _static_prompt, volatile_text = result
        self.assertEqual(volatile_text, volatile_text.strip())
        self.assertTrue(volatile_text.startswith("当前时间："))
        mock_provider.assert_not_called()

    def test_static_contains_tools_section_when_tools_present(self):
        """tools 非空：静态段含 <tools> 工具使用说明分区（静态内容）。"""
        config = AgentConfig(agent_type=AgentType.BASE, model_name="gpt-4o", user_id=1, session_id="t-1")
        tool = _make_doc_tool()
        with mock.patch(_DOC_PROVIDER, return_value=None):
            static_prompt, _volatile_text = _build_result(config, prompt_mode="agent", tools=[tool])

        self.assertIn("<tools>", static_prompt)
        self.assertIn("doc_tool", static_prompt)


class AttachVolatileInjectionTests(SimpleTestCase):
    """attach_volatile_injection 装配语义（避免双注入）。"""

    def test_attaches_new_middleware_when_stack_empty(self):
        stack: list = []
        attach_volatile_injection(stack, "当前时间：T\n\n文档上下文")
        injection = [m for m in stack if isinstance(m, MemoryInjectionMiddleware)]
        self.assertEqual(len(injection), 1)
        self.assertEqual(injection[0]._injection_text, "当前时间：T\n\n文档上下文")

    def test_merges_into_existing_middleware_with_dedup(self):
        existing = MemoryInjectionMiddleware(injection_text="【跨会话上下文】历史记忆")
        stack: list = [existing]
        # 前插 + 段落去重：重叠段落（【跨会话上下文】历史记忆）只保留一份
        attach_volatile_injection(stack, "当前时间：T\n\n【跨会话上下文】历史记忆")
        injection = [m for m in stack if isinstance(m, MemoryInjectionMiddleware)]
        self.assertEqual(len(injection), 1, "已有注入中间件时必须合并而非新建（避免双注入）")
        self.assertEqual(
            injection[0]._injection_text,
            "当前时间：T\n\n【跨会话上下文】历史记忆",
        )

    def test_empty_text_is_noop(self):
        stack: list = []
        attach_volatile_injection(stack, "")
        attach_volatile_injection(stack, "   ")
        attach_volatile_injection(stack, None)
        attach_volatile_injection(None, "非空文本")
        self.assertEqual(stack, [])

    def test_merge_empty_extra_keeps_existing(self):
        existing = MemoryInjectionMiddleware(injection_text="原有注入")
        existing.merge_injection_text("")
        existing.merge_injection_text(None)
        self.assertEqual(existing._injection_text, "原有注入")


class BaseBuilderVolatileWiringTests(SimpleTestCase):
    """BaseAgentBuilder 接线：静态段进 system_prompt、易变段进注入通道。"""

    def _build_and_capture(self, config: AgentConfig) -> dict:
        captured: dict = {}

        def fake_create_agent(**kwargs):
            captured.update(kwargs)
            return mock.MagicMock(name="agent-graph")

        with (
            mock.patch(
                "Django_xm.apps.agent_hub.tool_resolver.resolve_tools",
                new=mock.AsyncMock(return_value=[_make_doc_tool()]),
            ),
            mock.patch(
                "Django_xm.apps.agent_hub.builders._common.build_dynamic_context_prompt",
                new=mock.AsyncMock(return_value=("STATIC_PROMPT", "VOLATILE_TEXT")),
            ),
            mock.patch("langchain.agents.create_agent", side_effect=fake_create_agent),
        ):
            builder = BaseAgentBuilder()
            asyncio.run(builder._build_internal(config))
        return captured

    def test_static_prompt_and_volatile_injection_wiring(self):
        """system_prompt=静态段；volatile 新建注入中间件（调用方未装配时）。"""
        config = AgentConfig(agent_type=AgentType.BASE, model_name="gpt-4o", user_id=1, session_id="t-1")
        captured = self._build_and_capture(config)

        self.assertEqual(captured["system_prompt"], "STATIC_PROMPT")
        injection = [m for m in captured["middleware"] if isinstance(m, MemoryInjectionMiddleware)]
        self.assertEqual(len(injection), 1)
        self.assertEqual(injection[0]._injection_text, "VOLATILE_TEXT")

    def test_merges_into_caller_provided_injection_middleware(self):
        """调用方已装配注入中间件（chat 主链通道）→ 合并去重，无双实例。"""
        config = AgentConfig(
            agent_type=AgentType.BASE,
            model_name="gpt-4o",
            user_id=1,
            session_id="t-1",
            middleware=[MemoryInjectionMiddleware(injection_text="【跨会话上下文】历史记忆")],
        )
        captured = self._build_and_capture(config)

        injection = [m for m in captured["middleware"] if isinstance(m, MemoryInjectionMiddleware)]
        self.assertEqual(len(injection), 1, "必须合并进调用方中间件（唯一易变载体）")
        self.assertEqual(
            injection[0]._injection_text,
            "VOLATILE_TEXT\n\n【跨会话上下文】历史记忆",
        )


class DeepBuilderVolatileWiringTests(SimpleTestCase):
    """DeepAgentBuilder 接线：开关开启装配 volatile，关闭保持静态（语义保持）。"""

    def _build_and_capture(self, config: AgentConfig) -> dict:
        captured: dict = {}
        tools = [_make_doc_tool()]

        def fake_create_wrapper(config_arg, model, resolved_tools, middleware_stack, agent_kwargs, backend, work_dir):
            captured["middleware_stack"] = middleware_stack
            captured["agent_kwargs"] = agent_kwargs
            return mock.MagicMock(name="agent-wrapper")

        with (
            mock.patch.object(DeepAgentBuilder, "_resolve_deep_tools", new=mock.AsyncMock(return_value=(None, tools))),
            mock.patch.object(
                DeepAgentBuilder, "_build_deep_middleware", return_value=([], tools)
            ),
            mock.patch.object(
                DeepAgentBuilder,
                "_resolve_deep_runtime",
                return_value=("state", None, None, None),
            ),
            mock.patch.object(DeepAgentBuilder, "_create_deep_agent_wrapper", side_effect=fake_create_wrapper),
        ):
            builder = DeepAgentBuilder()
            asyncio.run(builder._build_internal(config))
        return captured

    def test_dynamic_enabled_attaches_volatile(self):
        """开关开启：static 进 system_prompt、volatile 进注入中间件。"""
        config = AgentConfig(
            agent_type=AgentType.DEEP_RESEARCH,
            model_name="gpt-4o",
            user_id=1,
            session_id="task-1",
        )
        with (
            mock.patch(
                "Django_xm.apps.context_manager.config.context_settings.deep_dynamic_context_enabled",
                True,
            ),
            mock.patch(
                "Django_xm.apps.agent_hub.builders._common.build_dynamic_context_prompt",
                new=mock.AsyncMock(return_value=("DEEP_STATIC", "DEEP_VOLATILE")),
            ),
        ):
            captured = self._build_and_capture(config)

        self.assertEqual(captured["agent_kwargs"]["system_prompt"], "DEEP_STATIC")
        injection = [
            m for m in captured["middleware_stack"] if isinstance(m, MemoryInjectionMiddleware)
        ]
        self.assertEqual(len(injection), 1)
        self.assertEqual(injection[0]._injection_text, "DEEP_VOLATILE")

    def test_dynamic_disabled_keeps_static_factory_prompt(self):
        """开关关闭（默认）：prompt_factory 静态回退，不装配注入（开关语义保持）。"""
        config = AgentConfig(
            agent_type=AgentType.DEEP_RESEARCH,
            model_name="gpt-4o",
            user_id=1,
            session_id="task-1",
            prompt_factory=lambda: "FACTORY_STATIC_PROMPT",
        )
        with mock.patch(
            "Django_xm.apps.context_manager.config.context_settings.deep_dynamic_context_enabled",
            False,
        ):
            captured = self._build_and_capture(config)

        self.assertEqual(captured["agent_kwargs"]["system_prompt"], "FACTORY_STATIC_PROMPT")
        self.assertFalse(
            any(isinstance(m, MemoryInjectionMiddleware) for m in captured["middleware_stack"]),
            "静态回退无易变分区，不得装配注入中间件",
        )
