"""深度研究 runner 相关测试。

测试 astream_research_with_interrupts 的中断检测与审批发起，
以及 resume_research_task 的恢复逻辑（thread_id 与 Command 构造）。
"""

import unittest
from unittest.mock import patch, MagicMock, AsyncMock

from django.test import TestCase


class AstreamResearchWithInterruptsTests(TestCase):
    """测试 OfficialDeepAgentAdapter.astream_research_with_interrupts。"""

    @staticmethod
    def _make_adapter_with_interrupt_stream(interrupt_value=None):
        """构建一个 graph.astream 产出 __interrupt__ 的 adapter。

        mock_astream 第一次调用 yield __interrupt__ 事件，
        第二次调用（resume 后）不 yield 任何事件，直接结束流。
        mock_aget_state 返回空 messages 状态，供流结束后获取最终状态。
        """
        if interrupt_value is None:
            interrupt_value = {
                "_approval": True,
                "tool_name": "shell_exec",
                "title": "确认执行",
                "description": "执行 shell 命令",
                "danger_level": "high",
                "operation": "rm -rf /tmp/test",
                "parameters": {"command": "rm -rf /tmp/test"},
                "extra": {},
            }
        interrupt_dict = {"value": interrupt_value, "id": "test-interrupt-id"}

        call_count = [0]

        async def mock_astream(graph_input, config=None, stream_mode=None, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                yield ("updates", {"__interrupt__": [interrupt_dict]})
            # 第二次（resume 后）不 yield，流直接结束

        async def mock_aget_state(config):
            mock_state = MagicMock()
            mock_state.values = {"messages": []}
            return mock_state

        mock_graph = MagicMock()
        mock_graph.astream = mock_astream
        mock_graph.aget_state = mock_aget_state
        from Django_xm.apps.research.services.official_deep_agent import (
            OfficialDeepAgentAdapter,
        )
        return OfficialDeepAgentAdapter(
            graph=mock_graph, thread_id="test-task-id", work_dir=None,
        )

    async def test_astream_interrupt_detection(self):
        """检测到 __interrupt__ 后调用 on_interrupt 回调，并使用 Command(resume=...) 恢复 agent。"""
        adapter = self._make_adapter_with_interrupt_stream()

        captured_interrupts = []

        async def on_interrupt(interrupts_data):
            captured_interrupts.extend(interrupts_data)
            return {item["interrupt_id"]: True for item in interrupts_data}

        with patch(
            "Django_xm.apps.tools.base.is_approval_interrupt", return_value=True
        ):
            result = await adapter.astream_research_with_interrupts(
                query="测试查询",
                on_interrupt=on_interrupt,
            )

        # 验证 on_interrupt 被调用，且收到 interrupt 数据
        self.assertTrue(len(captured_interrupts) > 0)
        # 验证 resume 后流正常结束（adapter 第二次调用 astream 后退出循环）
        self.assertTrue(result.get("success"))

    async def test_request_approval_async_called(self):
        """on_interrupt 回调接收到的 interrupt_data 字段完整，供调用方构造 request_approval_async 参数。

        新接口下 adapter 不再直接调用 request_approval_async，而是通过 on_interrupt 回调
        将 interrupt_data 传给调用方（research_runner._handle_interrupt），由调用方构造
        approval_data 并调用 request_approval_async。本测试验证 interrupt_data 字段完整。
        """
        adapter = self._make_adapter_with_interrupt_stream()

        captured_interrupts = []

        async def on_interrupt(interrupts_data):
            captured_interrupts.extend(interrupts_data)
            return {item["interrupt_id"]: True for item in interrupts_data}

        with patch(
            "Django_xm.apps.tools.base.is_approval_interrupt", return_value=True
        ):
            await adapter.astream_research_with_interrupts(
                query="测试查询",
                on_interrupt=on_interrupt,
            )

        self.assertTrue(len(captured_interrupts) > 0)
        interrupt_data = captured_interrupts[0]

        # interrupt_data 字段供 _handle_interrupt 构造 request_approval_async 参数：
        #   source=Approval.SOURCE_DEEP_RESEARCH（常量，硬编码）
        #   source_id=thread_id（即 adapter.thread_id，闭包变量）
        #   interrupt_id=interrupt_data["interrupt_id"]
        #   approval_data={tool_name, title, description, action, operation,
        #                  danger_level, parameters, tool_call_id, session_id, ...}
        self.assertEqual(interrupt_data["tool_name"], "shell_exec")
        self.assertEqual(interrupt_data["interrupt_id"], "test-interrupt-id")
        self.assertEqual(interrupt_data["graph_interrupt_id"], "test-interrupt-id")
        self.assertEqual(interrupt_data["danger_level"], "high")
        self.assertEqual(interrupt_data["operation"], "rm -rf /tmp/test")
        self.assertEqual(interrupt_data["parameters"], {"command": "rm -rf /tmp/test"})
        self.assertEqual(interrupt_data["title"], "确认执行")
        self.assertEqual(interrupt_data["description"], "执行 shell 命令")


@unittest.skip('research_resume_task 已在 Phase 1 移除，相关测试待后续 Phase 重建')
class ResumeResearchTaskTests(TestCase):
    """测试 resume_research_task 使用正确的 thread_id 恢复 agent。"""

    @patch("Django_xm.tasks.research_resume_task._broadcast_stream_completed")
    @patch("Django_xm.tasks.research_resume_task.finalize_research")
    @patch("Django_xm.tasks.research_resume_task.TrackedTask")
    @patch("Django_xm.tasks.research_resume_task.approval_service")
    @patch("Django_xm.apps.research.models.ResearchTask.objects")
    @patch("Django_xm.apps.approvals.models.Approval.objects")
    def test_resume_research_task_thread_id(
        self,
        mock_approval_objects,
        mock_research_task_objects,
        mock_approval_service,
        mock_tracked_task,
        mock_finalize,
        mock_broadcast,
    ):
        """使用 thread_id=task_id 恢复 agent，并构造 Command(resume=...)。"""
        from Django_xm.tasks.research_resume_task import resume_research_task  # noqa: F401,E401
        from Django_xm.apps.research.services.official_deep_agent import (  # noqa: F401,E401
            OfficialDeepAgentAdapter,
        )
        from langgraph.types import Command  # noqa: F401,E401

        task_id = "test-task-id"
        interrupt_id = "test-interrupt-id"
        resume_value = True
        chat_session_id = "test-chat-session-id"

        # Mock Approval.objects.get
        mock_approval = MagicMock()
        mock_approval.chat_session_id = chat_session_id
        mock_approval_objects.get.return_value = mock_approval

        # Mock ResearchTask.objects.get
        mock_research_task = MagicMock()
        mock_research_task.enable_doc_analysis = False
        mock_research_task.use_mcp = False
        mock_research_task.selected_tools = []
        mock_research_task.selected_mcp_servers = []
        mock_research_task.knowledge_base_ids = []
        mock_research_task.created_by_id = 1
        mock_research_task.enable_web_search = False
        mock_research_task_objects.get.return_value = mock_research_task

        # 捕获 astream 调用参数
        captured = {}

        async def mock_astream(graph_input, config=None, stream_mode=None, **kwargs):
            captured["graph_input"] = graph_input
            captured["config"] = config
            captured["stream_mode"] = stream_mode
            return
            yield  # 使函数成为 async generator

        async def mock_aget_state(config):
            mock_state = MagicMock()
            mock_state.values = {"messages": []}
            return mock_state

        mock_graph = MagicMock()
        mock_graph.astream = mock_astream
        mock_graph.aget_state = mock_aget_state

        mock_adapter = OfficialDeepAgentAdapter(
            graph=mock_graph, thread_id=task_id, work_dir=None,
        )

        with patch(
            "Django_xm.apps.agent_hub.create",
            new=AsyncMock(return_value=mock_adapter),
        ), patch(
            "Django_xm.apps.ai_engine.services.checkpointer_factory.get_async_checkpointer",
            new=AsyncMock(return_value=None),
        ), patch(
            "Django_xm.apps.ai_engine.services.checkpointer_factory.release_async_checkpointer",
            new=AsyncMock(),
        ):
            resume_research_task.apply(args=(task_id, interrupt_id, resume_value))

        # 验证 astream 被调用
        self.assertIn("graph_input", captured)
        self.assertIn("config", captured)

        # 验证 thread_id
        self.assertEqual(
            captured["config"]["configurable"]["thread_id"], task_id
        )

        # 验证 Command(resume={interrupt_id: resume_value})
        self.assertIsInstance(captured["graph_input"], Command)
        self.assertEqual(
            captured["graph_input"].resume, {interrupt_id: resume_value}
        )
