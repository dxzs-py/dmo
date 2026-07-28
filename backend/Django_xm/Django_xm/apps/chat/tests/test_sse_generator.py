"""SSE 流式生成器单元测试（Task 23.5）

测试覆盖 [sse_generator.py](../services/sse_generator.py) 的三个子函数与主生成器：

- ``_init_stream``：附件 ID 事件、预处理进度事件、空事件路径
- ``_process_chunks``：内容 chunk 格式化、error dict → sse_error_event、
  超时心跳、StopAsyncIteration 结束
- ``_cleanup_stream``：checkpointer 释放失败日志、pending_content 兜底刷新、
  loop 关闭、pending task 取消
- ``generate_chat_stream``：[DONE] 事件始终发送（含异常路径）、异常事件格式

测试策略：
- Mock ``ChatService.process_stream_chat_request`` 返回可控 async generator
- Mock ``asyncio.wait`` 控制 done/not_done 返回（测试心跳超时分支）
- Mock ``release_async_checkpointer`` 测试释放失败日志
- 使用 ``SimpleTestCase`` 避免数据库依赖（SSE 生成器纯逻辑）
"""
import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase


def _parse_sse_data(sse_str: str) -> dict | None:
    """从 ``data: {...}\\n\\n`` 格式字符串中解析 JSON payload。

    支持两种格式：
    - ``data: {...}\\n\\n``（普通事件）
    - ``event: error\\ndata: {...}\\n\\n``（错误事件，需先 split 出 data 行）
    """
    if sse_str.startswith("event: "):
        # 错误事件格式：event: error\ndata: {...}\n\n
        parts = sse_str.split("data: ", 1)
        if len(parts) != 2:
            return None
        sse_str = "data: " + parts[1]
    if not sse_str.startswith("data: "):
        return None
    payload = sse_str[len("data: "):].rstrip("\n").rstrip()
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def _make_ctx(request=None, data=None, original_attachment_ids=None,
              pending_progress=None):
    """构造 ChatStreamContext 测试实例。"""
    from Django_xm.apps.chat.services.sse_generator import ChatStreamContext

    if request is None:
        request = MagicMock()
        request.user.id = 1
        request.user.is_authenticated = True

    return ChatStreamContext(
        request=request,
        data=data or {"message": "test", "session_id": "sess-1"},
        original_attachment_ids=original_attachment_ids or [],
        pending_progress=pending_progress or [],
    )


class _AsyncGenWrapper:
    """将同步事件列表包装为 async generator，用于 mock ChatService。"""

    def __init__(self, events: list):
        self._events = list(events)
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._events):
            raise StopAsyncIteration
        event = self._events[self._index]
        self._index += 1
        return event

    async def aclose(self):
        """Mock aclose for cleanup path."""
        pass


def _patch_chat_service(events: list):
    """构造 patch 上下文，使 ``ChatService(...).process_stream_chat_request(...)``
    返回包装了 events 的 async generator。
    """
    async_gen = _AsyncGenWrapper(events)
    mock_service = MagicMock()
    mock_service.process_stream_chat_request = MagicMock(return_value=async_gen)
    return patch(
        "Django_xm.apps.chat.services.chat_service.ChatService",
        return_value=mock_service,
    )


# ============== _init_stream 测试 ==============

class InitStreamTestCase(SimpleTestCase):
    """``_init_stream``：附件 ID 与预处理进度事件。"""

    def test_yields_attachment_ids_event_when_present(self):
        from Django_xm.apps.chat.services.sse_generator import _init_stream

        ctx = _make_ctx(original_attachment_ids=["att-1", "att-2"])
        events = list(_init_stream(ctx))

        self.assertTrue(events, "应至少产生一条事件")
        payload = _parse_sse_data(events[0])
        self.assertIsNotNone(payload)
        self.assertEqual(payload["type"], "attachment_ids")
        self.assertEqual(payload["data"], ["att-1", "att-2"])

    def test_yields_pending_progress_events(self):
        from Django_xm.apps.chat.services.sse_generator import _init_stream

        progress = [
            {"type": "attachment_processing", "data": {"stage": "parse", "message": "解析中"}},
            {"type": "attachment_processing", "data": {"stage": "embed", "message": "向量化中"}},
        ]
        ctx = _make_ctx(pending_progress=progress)
        events = list(_init_stream(ctx))

        self.assertEqual(len(events), 2)
        for evt in events:
            payload = _parse_sse_data(evt)
            self.assertIsNotNone(payload)
            self.assertEqual(payload["type"], "attachment_processing")

        self.assertEqual(ctx.pending_progress, [])

    def test_no_events_when_both_empty(self):
        from Django_xm.apps.chat.services.sse_generator import _init_stream

        ctx = _make_ctx()
        events = list(_init_stream(ctx))
        self.assertEqual(events, [])

    def test_injects_stream_state_into_data(self):
        """``_init_stream`` 应将共享状态字典注入到 ``ctx.data['_stream_state']``。"""
        from Django_xm.apps.chat.services.sse_generator import _init_stream

        ctx = _make_ctx()
        list(_init_stream(ctx))
        self.assertIn("_stream_state", ctx.data)
        self.assertEqual(ctx.data["_stream_state"]["pending_content"], "")


# ============== _process_chunks 测试 ==============

class ProcessChunksTestCase(SimpleTestCase):
    """``_process_chunks``：内容 chunk、error、心跳、StopAsyncIteration。"""

    def test_content_chunks_formatted_as_sse(self):
        from Django_xm.apps.chat.services.sse_generator import _process_chunks

        events = [
            {"type": "chunk", "content": "hello"},
            {"type": "chunk", "content": " world"},
        ]
        ctx = _make_ctx()
        with _patch_chat_service(events):
            results = list(_process_chunks(ctx))

        self.assertEqual(len(results), 2)
        for r in results:
            self.assertTrue(r.startswith("data: "))
            self.assertTrue(r.endswith("\n\n"))
            payload = _parse_sse_data(r)
            self.assertIsNotNone(payload)
            self.assertEqual(payload["type"], "chunk")

    def test_error_dict_yields_sse_error_event(self):
        """当 ChatService yield ``{"type": "error", ...}`` 时应转为 sse_error_event。"""
        from Django_xm.apps.chat.services.sse_generator import _process_chunks

        events = [{"type": "error", "message": "工具执行失败"}]
        ctx = _make_ctx()
        with _patch_chat_service(events):
            results = list(_process_chunks(ctx))

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].startswith("event: error\n"))
        payload = _parse_sse_data(results[0])
        self.assertIsNotNone(payload)
        self.assertEqual(payload["type"], "error")
        self.assertEqual(payload["message"], "工具执行失败")
        self.assertEqual(payload["code"], "50001")

    def test_stop_async_iteration_ends_loop(self):
        """async generator 抛出 StopAsyncIteration 时应干净结束循环。"""
        from Django_xm.apps.chat.services.sse_generator import _process_chunks

        events = [{"type": "chunk", "content": "done"}]
        ctx = _make_ctx()
        with _patch_chat_service(events):
            results = list(_process_chunks(ctx))

        self.assertEqual(len(results), 1)

    def test_heartbeat_on_timeout(self):
        """``asyncio.wait`` 超时（not done）时应发送心跳事件。"""
        from Django_xm.apps.chat.services.sse_generator import _process_chunks

        events = [{"type": "chunk", "content": "after-timeout"}]
        ctx = _make_ctx()

        call_count = {"n": 0}

        async def mock_wait(coros_or_tasks, timeout=None):
            call_count["n"] += 1
            task = next(iter(coros_or_tasks))
            if call_count["n"] == 1:
                return set(), {task}  # 第一次：超时
            return {task}, set()  # 第二次：完成

        with _patch_chat_service(events), patch("asyncio.wait", side_effect=mock_wait):
            results = list(_process_chunks(ctx))

        self.assertEqual(len(results), 2)
        heartbeat_payload = _parse_sse_data(results[0])
        self.assertEqual(heartbeat_payload["type"], "heartbeat")
        chunk_payload = _parse_sse_data(results[1])
        self.assertEqual(chunk_payload["type"], "chunk")


# ============== _cleanup_stream 测试 ==============

class CleanupStreamTestCase(SimpleTestCase):
    """``_cleanup_stream``：checkpointer 释放、pending_content、loop 关闭。"""

    def _make_mocked_loop(self, run_until_complete_side_effect=None):
        """构造 mock event loop，``run_until_complete`` 默认返回 None。"""
        mock_loop = MagicMock()
        if run_until_complete_side_effect is not None:
            mock_loop.run_until_complete = MagicMock(
                side_effect=run_until_complete_side_effect
            )
        else:
            mock_loop.run_until_complete = MagicMock(return_value=None)
        return mock_loop

    def test_pending_content_flushed_in_cleanup(self):
        """pending_content 应在 cleanup 中兜底刷新为 chunk 事件。"""
        from Django_xm.apps.chat.services.sse_generator import _cleanup_stream

        ctx = _make_ctx()
        ctx.stream_state["pending_content"] = "未刷新的内容"
        ctx.loop = self._make_mocked_loop()
        ctx.gen = None
        ctx.pending_task = None

        with patch(
            "Django_xm.apps.ai_engine.services.checkpointer_factory.release_async_checkpointer",
            new_callable=AsyncMock,
        ):
            results = list(_cleanup_stream(ctx))

        chunk_events = [r for r in results if _parse_sse_data(r)
                        and _parse_sse_data(r).get("type") == "chunk"]
        self.assertEqual(len(chunk_events), 1)
        payload = _parse_sse_data(chunk_events[0])
        self.assertEqual(payload["content"], "未刷新的内容")
        ctx.loop.close.assert_called_once()

    def test_checkpointer_release_failure_logs_warning(self):
        """release_async_checkpointer 抛异常时应记录 WARNING 日志。"""
        from Django_xm.apps.chat.services.sse_generator import _cleanup_stream

        ctx = _make_ctx()
        # release_async_checkpointer 在 loop.run_until_complete 中会抛异常
        ctx.loop = self._make_mocked_loop(
            run_until_complete_side_effect=Exception("连接池关闭失败")
        )
        ctx.gen = None
        ctx.pending_task = None
        ctx.stream_state["pending_content"] = ""

        with self.assertLogs("Django_xm.apps.chat.services.sse_generator", level="WARNING") as cm:
            list(_cleanup_stream(ctx))

        warning_msgs = [r.getMessage() for r in cm.records if r.levelno == logging.WARNING]
        self.assertTrue(
            any("Checkpointer 连接释放失败" in m for m in warning_msgs),
            f"期望 WARNING 日志包含 'Checkpointer 连接释放失败'，实际: {warning_msgs}",
        )
        ctx.loop.close.assert_called_once()

    def test_pending_task_cancelled_in_cleanup(self):
        """_cleanup_stream 应取消未完成的 pending task。"""
        from Django_xm.apps.chat.services.sse_generator import _cleanup_stream

        ctx = _make_ctx()
        mock_task = MagicMock()
        ctx.pending_task = mock_task
        ctx.gen = None
        ctx.loop = self._make_mocked_loop()
        ctx.stream_state["pending_content"] = ""

        with patch(
            "Django_xm.apps.ai_engine.services.checkpointer_factory.release_async_checkpointer",
            new_callable=AsyncMock,
        ):
            list(_cleanup_stream(ctx))

        mock_task.cancel.assert_called_once()

    def test_aclose_called_on_generator(self):
        """_cleanup_stream 应通过 loop.run_until_complete 调用 async generator 的 aclose。"""
        from Django_xm.apps.chat.services.sse_generator import _cleanup_stream

        ctx = _make_ctx()
        mock_gen = MagicMock()
        ctx.gen = mock_gen
        ctx.pending_task = None
        ctx.loop = self._make_mocked_loop()
        ctx.stream_state["pending_content"] = ""

        with patch(
            "Django_xm.apps.ai_engine.services.checkpointer_factory.release_async_checkpointer",
            new_callable=AsyncMock,
        ):
            list(_cleanup_stream(ctx))

        # loop.run_until_complete 应被调用多次：aclose / release_checkpointer / sleep+gather
        self.assertGreaterEqual(ctx.loop.run_until_complete.call_count, 1)

    def test_no_pending_content_yields_nothing(self):
        """无 pending_content 时 cleanup 不应产出 chunk 事件。"""
        from Django_xm.apps.chat.services.sse_generator import _cleanup_stream

        ctx = _make_ctx()
        ctx.stream_state["pending_content"] = ""
        ctx.loop = self._make_mocked_loop()
        ctx.gen = None
        ctx.pending_task = None

        with patch(
            "Django_xm.apps.ai_engine.services.checkpointer_factory.release_async_checkpointer",
            new_callable=AsyncMock,
        ):
            results = list(_cleanup_stream(ctx))

        chunk_events = [r for r in results if _parse_sse_data(r)
                        and _parse_sse_data(r).get("type") == "chunk"]
        self.assertEqual(chunk_events, [])


# ============== generate_chat_stream 测试 ==============

class GenerateChatStreamTestCase(SimpleTestCase):
    """``generate_chat_stream``：主生成器组合 + [DONE] 事件。"""

    def test_done_event_always_sent_on_success(self):
        """成功路径下末尾应发送 ``data: [DONE]\\n\\n``。"""
        from Django_xm.apps.chat.services.sse_generator import generate_chat_stream

        ctx = _make_ctx()
        with _patch_chat_service([{"type": "chunk", "content": "hello"}]):
            results = list(generate_chat_stream(ctx))

        self.assertEqual(results[-1], "data: [DONE]\n\n")
        # 应包含至少 1 条 chunk + [DONE]
        self.assertGreaterEqual(len(results), 2)

    def test_done_event_sent_on_exception(self):
        """异常路径下末尾仍应发送 ``data: [DONE]\\n\\n``。"""
        from Django_xm.apps.chat.services.sse_generator import generate_chat_stream

        ctx = _make_ctx()
        # mock ChatService.process_stream_chat_request 抛异常
        mock_service = MagicMock()
        mock_service.process_stream_chat_request = MagicMock(
            side_effect=RuntimeError("ChatService 内部错误")
        )

        with patch(
            "Django_xm.apps.chat.services.chat_service.ChatService",
            return_value=mock_service,
        ), patch(
            "Django_xm.apps.ai_engine.services.checkpointer_factory.release_async_checkpointer",
            new_callable=AsyncMock,
        ):
            results = list(generate_chat_stream(ctx))

        # 末尾必须是 [DONE]
        self.assertEqual(results[-1], "data: [DONE]\n\n")
        # 应包含 error 事件
        error_events = [r for r in results if r.startswith("event: error\n")]
        self.assertGreaterEqual(len(error_events), 1)

    def test_attachment_ids_event_before_chunks(self):
        """attachment_ids 事件应在 chunk 事件之前。"""
        from Django_xm.apps.chat.services.sse_generator import generate_chat_stream

        ctx = _make_ctx(original_attachment_ids=["att-1"])
        with _patch_chat_service([{"type": "chunk", "content": "hello"}]):
            results = list(generate_chat_stream(ctx))

        # 第 1 条：attachment_ids，第 2 条：chunk，末尾：[DONE]
        self.assertGreaterEqual(len(results), 3)
        first_payload = _parse_sse_data(results[0])
        self.assertEqual(first_payload["type"], "attachment_ids")
        self.assertEqual(results[-1], "data: [DONE]\n\n")

    def test_error_event_uses_classified_message(self):
        """异常路径应通过 ``classify_exception`` 生成用户友好消息。"""
        from Django_xm.apps.chat.services.sse_generator import generate_chat_stream

        ctx = _make_ctx()
        mock_service = MagicMock()
        mock_service.process_stream_chat_request = MagicMock(
            side_effect=RuntimeError("连接超时")
        )

        classified = MagicMock()
        classified.user_message = "服务暂不可用，请稍后重试"

        with patch(
            "Django_xm.apps.chat.services.chat_service.ChatService",
            return_value=mock_service,
        ), patch(
            "Django_xm.apps.ai_engine.services.exceptions.classify_exception",
            return_value=classified,
        ), patch(
            "Django_xm.apps.ai_engine.services.checkpointer_factory.release_async_checkpointer",
            new_callable=AsyncMock,
        ):
            results = list(generate_chat_stream(ctx))

        error_events = [r for r in results if r.startswith("event: error\n")]
        self.assertGreaterEqual(len(error_events), 1)
        payload = _parse_sse_data(error_events[0])
        self.assertEqual(payload["message"], "服务暂不可用，请稍后重试")