"""chat 代理模式 stream 审批记录创建单测（Task 8 / 根因 6）。

验证 ``loop._handle_updates_chunk`` 在 yield approval 事件前，
对每个 approval_data 调用 ``approval_service.request_approval_async`` 创建 Approval 记录。

关键回归点：
1. 批量 interrupt（3 个 requests）→ request_approval_async 调用 3 次
2. 每次调用的 interrupt_id = 该工具的 tool_call_id
3. source = Approval.SOURCE_CHAT
4. approval_data.extra 含 tool_config / model_config / graph_interrupt_id
5. request_approval_async 抛异常时不中断 stream（仍 yield approval 事件）

mock 策略:
- patch ``loop.approval_service.request_approval_async``，断言调用次数与参数
- 不依赖真实 DB / Redis / Channels

运行方式:
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    conda activate langchain_xm
    python manage.py test Django_xm.apps.chat.tests.test_chat_proxy_approval_creation --verbosity=2
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

# Django 环境初始化
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django
import django.apps

if not django.apps.apps.ready:
    django.setup()

from langgraph.types import Interrupt

from Django_xm.apps.approvals.models import Approval
from Django_xm.apps.chat.services.stream.context import StreamContext
from Django_xm.apps.chat.services.stream.loop import _handle_updates_chunk

# ApprovalMiddleware 生成的批次 UUID（嵌入 _meta.graph_interrupt_id）
BATCH_ID = "batch-graph-interrupt-456"
# LangGraph 内部为 interrupt 生成的命名空间哈希（intr.id，用于 Command(resume=...)）
# 真实值为 xxh3_128_hexdigest("|".join(ns).encode())，此处用固定哈希模拟
LANGGRAPH_INTR_ID = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"


def _make_request(
    tool_name: str,
    tool_call_id: str,
    *,
    operation: str = "",
    danger_level: str = "medium",
    title: str = "确认操作",
    description: str = "",
    args: dict | None = None,
) -> dict:
    """构造与 ApprovalMiddleware after_model 产生的单条审批请求一致的 dict。"""
    return {
        "_approval": True,
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "graph_interrupt_id": BATCH_ID,
        "session_id": "session-chat-1",
        "title": title,
        "description": description,
        "operation": operation,
        "danger_level": danger_level,
        "risk_level": "controlled",
        "args": args or {},
    }


def _make_batch_interrupt_value(requests: list[dict]) -> dict:
    """构造批量 interrupt_value（ApprovalMiddleware 产生的格式）。"""
    return {
        "_approval": True,
        "requests": requests,
        "_meta": {"graph_interrupt_id": BATCH_ID},
    }


def _make_mode_data(interrupt_value: dict, *, langgraph_intr_id: str = LANGGRAPH_INTR_ID) -> dict:
    """构造 updates stream mode 的 chunk 数据。

    langgraph ``astream(stream_mode=["updates"])`` 中 ``__interrupt__`` 列表元素
    是 ``Interrupt`` 对象（非 dict），其 ``id`` 为 LangGraph 内部根据节点命名空间
    生成的哈希（``xxh3_128_hexdigest``），与 ApprovalMiddleware 的 ``graph_interrupt_id``
    是两个不同的值：
    - ``intr.id``（= langgraph_intr_id）：Command(resume=...) 的 KEY，用于恢复图
    - ``_meta.graph_interrupt_id``（= BATCH_ID）：前端分组 + DB 查询用

    使用真实 ``Interrupt`` 对象确保 ``extract_interrupt_ids`` 走 ``isinstance(intr, Interrupt)``
    分支，与生产行为一致。
    """
    return {"__interrupt__": [Interrupt(value=interrupt_value, id=langgraph_intr_id)]}


def _make_data() -> dict:
    """构造 chat 代理模式请求 data（含 session/model/tool 配置字段）。"""
    return {
        "session_id": "session-chat-1",
        "_assistant_message_id": 12345,
        "mode": "agent",
        "use_tools": True,
        "use_web_search": True,
        "use_mcp": False,
        "selected_mcp_servers": [],
        "selected_tools": ["shell_exec", "fs_write_file"],
        "use_knowledge_base": False,
        "selected_knowledge_bases": [],
        "tool_tier": "standard",
        "provider_id": "openai",
        "model_name": "gpt-4o",
        "use_deep_thinking": False,
        "special_params": None,
        "temperature": 0.7,
        "max_tokens": 4096,
    }


class ChatProxyApprovalCreationTests(unittest.IsolatedAsyncioTestCase):
    """验证 _handle_updates_chunk 在 yield 前创建 Approval 记录。"""

    async def _drain(self, gen):
        """耗尽 async generator 并收集 yield 的事件。"""
        events = []
        async for ev in gen:
            events.append(ev)
        return events

    async def test_batch_three_requests_calls_request_approval_three_times(self):
        """用例 1：3 个 requests → request_approval_async 调用 3 次。"""
        requests = [
            _make_request("shell_exec", "tc-shell-1", operation="ls -la", danger_level="high"),
            _make_request("shell_exec", "tc-shell-2", operation="rm -rf /tmp/x", danger_level="high"),
            _make_request("fs_write_file", "tc-fs-1", operation="/tmp/out.txt", danger_level="medium"),  # noqa: S108
        ]
        interrupt_value = _make_batch_interrupt_value(requests)
        mode_data = _make_mode_data(interrupt_value)
        ctx = StreamContext()
        data = _make_data()

        with patch(
            "Django_xm.apps.chat.services.stream.loop.approval_service.request_approval_async",
            new=AsyncMock(return_value=None),
        ) as mock_req:
            events = await self._drain(_handle_updates_chunk(mode_data, ctx, data))

        # 3 个 approval 事件被 yield
        approval_events = [e for e in events if e.get("type") == "approval"]
        self.assertEqual(len(approval_events), 3)
        # request_approval_async 被调用 3 次
        self.assertEqual(mock_req.call_count, 3)

    async def test_each_call_interrupt_id_matches_tool_call_id(self):
        """用例 2：每次调用的 interrupt_id = 该工具的 tool_call_id。"""
        requests = [
            _make_request("shell_exec", "tc-shell-1", operation="ls -la"),
            _make_request("shell_exec", "tc-shell-2", operation="rm -rf /tmp/x"),
            _make_request("fs_write_file", "tc-fs-1", operation="/tmp/out.txt"),  # noqa: S108
        ]
        interrupt_value = _make_batch_interrupt_value(requests)
        mode_data = _make_mode_data(interrupt_value)
        ctx = StreamContext()
        data = _make_data()

        with patch(
            "Django_xm.apps.chat.services.stream.loop.approval_service.request_approval_async",
            new=AsyncMock(return_value=None),
        ) as mock_req:
            await self._drain(_handle_updates_chunk(mode_data, ctx, data))

        expected_ids = ["tc-shell-1", "tc-shell-2", "tc-fs-1"]
        actual_ids = [call.kwargs.get("interrupt_id") for call in mock_req.call_args_list]
        self.assertEqual(actual_ids, expected_ids)

    async def test_source_is_chat(self):
        """用例 3：source = Approval.SOURCE_CHAT。"""
        requests = [_make_request("shell_exec", "tc-shell-1")]
        interrupt_value = _make_batch_interrupt_value(requests)
        mode_data = _make_mode_data(interrupt_value)
        ctx = StreamContext()
        data = _make_data()

        with patch(
            "Django_xm.apps.chat.services.stream.loop.approval_service.request_approval_async",
            new=AsyncMock(return_value=None),
        ) as mock_req:
            await self._drain(_handle_updates_chunk(mode_data, ctx, data))

        self.assertEqual(mock_req.call_count, 1)
        call = mock_req.call_args
        self.assertEqual(call.kwargs.get("source"), Approval.SOURCE_CHAT)
        # source_id 应为 session_id
        self.assertEqual(call.kwargs.get("source_id"), "session-chat-1")

    async def test_approval_data_contains_tool_config_model_config_graph_interrupt_id(self):
        """用例 4：approval_data.extra 含 tool_config / model_config / graph_interrupt_id。"""
        requests = [_make_request("shell_exec", "tc-shell-1", operation="ls -la")]
        interrupt_value = _make_batch_interrupt_value(requests)
        mode_data = _make_mode_data(interrupt_value)
        ctx = StreamContext()
        data = _make_data()

        with patch(
            "Django_xm.apps.chat.services.stream.loop.approval_service.request_approval_async",
            new=AsyncMock(return_value=None),
        ) as mock_req:
            await self._drain(_handle_updates_chunk(mode_data, ctx, data))

        self.assertEqual(mock_req.call_count, 1)
        approval_data = mock_req.call_args.kwargs.get("approval_data", {})

        # 顶层关键字段
        self.assertEqual(approval_data.get("interrupt_id"), "tc-shell-1")
        self.assertEqual(approval_data.get("session_id"), "session-chat-1")
        self.assertEqual(approval_data.get("source"), Approval.SOURCE_CHAT)
        self.assertEqual(approval_data.get("state"), "pending")
        self.assertEqual(approval_data.get("message_id"), 12345)
        self.assertEqual(approval_data.get("tool_call_id"), "tc-shell-1")

        # extra 字段
        extra = approval_data.get("extra", {})
        self.assertIn("tool_config", extra)
        self.assertIn("model_config", extra)
        self.assertEqual(extra.get("graph_interrupt_id"), BATCH_ID)
        # P0 核心断言：langgraph_resume_id 必须等于 intr.id（LangGraph 命名空间哈希），
        # 而非 BATCH_ID（middleware UUID）。若为空或等于 BATCH_ID，
        # chat_resume_generator.py 的 effective_resume_key 会不匹配 checkpoint 的 intr.id，
        # 导致问题22/24 "该审批请求不属于当前会话"错误。
        self.assertEqual(extra.get("langgraph_resume_id"), LANGGRAPH_INTR_ID)
        self.assertNotEqual(extra.get("langgraph_resume_id"), BATCH_ID)

        # tool_config 字段从 data 透传
        tool_config = extra["tool_config"]
        self.assertEqual(tool_config.get("use_tools"), True)
        self.assertEqual(tool_config.get("use_web_search"), True)
        self.assertEqual(tool_config.get("use_mcp"), False)
        self.assertEqual(tool_config.get("selected_tools"), ["shell_exec", "fs_write_file"])
        self.assertEqual(tool_config.get("tool_tier"), "standard")

        # model_config 字段从 data 透传
        model_config = extra["model_config"]
        self.assertEqual(model_config.get("provider_id"), "openai")
        self.assertEqual(model_config.get("model_name"), "gpt-4o")
        self.assertEqual(model_config.get("use_deep_thinking"), False)
        self.assertEqual(model_config.get("temperature"), 0.7)
        self.assertEqual(model_config.get("max_tokens"), 4096)

    async def test_request_approval_failure_does_not_break_stream(self):
        """用例 5：request_approval_async 抛异常时不中断 stream（仍 yield approval 事件）。"""
        requests = [
            _make_request("shell_exec", "tc-shell-1", operation="ls -la"),
            _make_request("shell_exec", "tc-shell-2", operation="rm -rf /tmp/x"),
        ]
        interrupt_value = _make_batch_interrupt_value(requests)
        mode_data = _make_mode_data(interrupt_value)
        ctx = StreamContext()
        data = _make_data()

        mock_req = AsyncMock(side_effect=RuntimeError("redis down"))
        with patch(
            "Django_xm.apps.chat.services.stream.loop.approval_service.request_approval_async",
            new=mock_req,
        ):
            events = await self._drain(_handle_updates_chunk(mode_data, ctx, data))

        # 即使 request_approval_async 抛异常，approval 事件仍正常 yield
        approval_events = [e for e in events if e.get("type") == "approval"]
        self.assertEqual(len(approval_events), 2)
        # 两次调用都执行（异常被捕获，不中断循环）
        self.assertEqual(mock_req.call_count, 2)

    async def test_ctx_interrupt_info_set_to_first_request(self):
        """用例 6：ctx.interrupt_info 用第一个请求的信息。"""
        requests = [
            _make_request("shell_exec", "tc-shell-1", operation="ls -la"),
            _make_request("shell_exec", "tc-shell-2", operation="rm -rf /tmp/x"),
        ]
        interrupt_value = _make_batch_interrupt_value(requests)
        mode_data = _make_mode_data(interrupt_value)
        ctx = StreamContext()
        data = _make_data()

        with patch(
            "Django_xm.apps.chat.services.stream.loop.approval_service.request_approval_async",
            new=AsyncMock(return_value=None),
        ):
            await self._drain(_handle_updates_chunk(mode_data, ctx, data))

        self.assertIsNotNone(ctx.interrupt_info)
        self.assertEqual(ctx.interrupt_info.get("tool_name"), "shell_exec")
        self.assertEqual(ctx.interrupt_info.get("interrupt_id"), "tc-shell-1")
        self.assertEqual(ctx.interrupt_info.get("graph_interrupt_id"), BATCH_ID)

    async def test_non_approval_interrupt_skips_request_approval(self):
        """用例 7：非审批类型 interrupt 不触发 request_approval_async。"""
        # 非 _approval 的 interrupt（如用户输入中断）应被 is_approval_interrupt 过滤
        interrupt_value = {"some_other_type": True, "message": "user input required"}
        mode_data = _make_mode_data(interrupt_value)
        ctx = StreamContext()
        data = _make_data()

        with patch(
            "Django_xm.apps.chat.services.stream.loop.approval_service.request_approval_async",
            new=AsyncMock(return_value=None),
        ) as mock_req:
            events = await self._drain(_handle_updates_chunk(mode_data, ctx, data))

        self.assertEqual(mock_req.call_count, 0)
        self.assertEqual(events, [])
        self.assertIsNone(ctx.interrupt_info)

    async def test_langgraph_resume_id_persisted_for_all_batch_requests(self):
        """用例 8（P0 回归）：批量审批中每个 approval 的 extra 都持久化 langgraph_resume_id。

        问题22/24 根因：若 langgraph_resume_id 未持久化，chat_resume_generator.py 的
        effective_resume_key 回退到 tool_call_id，与 checkpoint 的 intr.id 不匹配，
        导致"该审批请求不属于当前会话"错误，图卡死工具永不执行。
        """
        requests = [
            _make_request("shell_exec", "tc-shell-1", operation="ollama list"),
            _make_request("shell_exec", "tc-shell-2", operation="ollama ps"),
        ]
        interrupt_value = _make_batch_interrupt_value(requests)
        mode_data = _make_mode_data(interrupt_value)
        ctx = StreamContext()
        data = _make_data()

        with patch(
            "Django_xm.apps.chat.services.stream.loop.approval_service.request_approval_async",
            new=AsyncMock(return_value=None),
        ) as mock_req:
            await self._drain(_handle_updates_chunk(mode_data, ctx, data))

        self.assertEqual(mock_req.call_count, 2)
        for call in mock_req.call_args_list:
            approval_data = call.kwargs.get("approval_data", {})
            extra = approval_data.get("extra", {})
            # 每个审批的 langgraph_resume_id 都应等于 intr.id（同一 interrupt 共享）
            self.assertEqual(
                extra.get("langgraph_resume_id"),
                LANGGRAPH_INTR_ID,
                "批量审批中每个 approval 的 extra.langgraph_resume_id 必须等于 intr.id",
            )
            # graph_interrupt_id 是批次 UUID（与 langgraph_resume_id 不同）
            self.assertEqual(extra.get("graph_interrupt_id"), BATCH_ID)
            self.assertNotEqual(extra.get("langgraph_resume_id"), extra.get("graph_interrupt_id"))


if __name__ == "__main__":
    unittest.main()
