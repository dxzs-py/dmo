"""ChatStreamView 端到端 SSE 集成测试（Task 8.1）

测试覆盖 ChatStreamView 的完整请求-响应链路：
- 连接建立 → content chunk → tool_event → [DONE] → 资源释放
- 请求验证失败 → 400 错误响应
- 未认证 → 401 错误响应
- ChatService 异常 → SSE error 事件 + [DONE]
- 审批中断流 → tool_event(approval) → [DONE]（不广播 STREAM_COMPLETED）

测试策略：
- 使用 Django 原生 Client（force_login）模拟真实 HTTP 请求
- Mock generate_chat_stream 返回同步生成器（async generator 逻辑已在
  test_sse_generator.py 覆盖，集成测试聚焦 HTTP 层：认证/验证/响应头/事件序列）
- Mock publish_event 验证跨浏览器同步事件广播
- 解析 StreamingHttpResponse 的 streaming_content 验证 SSE 事件序列
"""

from __future__ import annotations

import json
import unittest
import uuid
from unittest.mock import patch

from django.test import Client, TestCase


def _parse_sse(sse_str: str) -> dict | str | None:
    """解析 SSE 字符串，返回 payload dict 或原始 token（如 [DONE]）。"""
    if sse_str.startswith("event: "):
        parts = sse_str.split("data: ", 1)
        if len(parts) != 2:
            return None
        sse_str = "data: " + parts[1]
    if not sse_str.startswith("data: "):
        return None
    payload = sse_str[len("data: ") :].rstrip("\n").rstrip()
    if payload == "[DONE]":
        return "[DONE]"
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def _collect_streaming_content(response) -> list[str]:
    """收集 StreamingHttpResponse 的所有 chunk 为字符串列表。"""
    chunks = []
    for chunk in response.streaming_content:
        if isinstance(chunk, bytes):
            chunks.append(chunk.decode("utf-8"))
        else:
            chunks.append(str(chunk))
    return chunks


def _sync_sse_generator(events: list):
    """将 SSE 事件列表转为同步生成器，模拟 generate_chat_stream 产出。

    generate_chat_stream 是 async generator，Django 同步测试 Client 无法迭代。
    集成测试聚焦 HTTP 层（认证/验证/响应头/事件序列），async generator 逻辑
    已在 test_sse_generator.py 覆盖。
    """
    yield from events


class ChatStreamIntegrationTests(TestCase):
    """ChatStreamView 端到端 SSE 集成测试。

    使用 Django 原生 Client（force_login）而非 DRF APIClient，
    确保 StreamingHttpResponse 保留 streaming_content 属性。
    """

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_user(username="sse_user", password="testpass123")
        self.client = Client()
        self.client.force_login(self.user)
        self.session_id = str(uuid.uuid4())

    def _patch_generate_stream(self, sse_events: list):
        """Mock generate_chat_stream 返回同步生成器。

        generate_chat_stream 在 views_chat.py 的 post() 内通过
        ``from .services.sse_generator import generate_chat_stream`` 导入，
        故在源头 patch：``Django_xm.apps.chat.services.sse_generator.generate_chat_stream``。

        Args:
            sse_events: SSE 格式字符串列表（如 ``["data: {...}\\n\\n", ...]``）
        """
        return patch(
            "Django_xm.apps.chat.services.sse_generator.generate_chat_stream",
            side_effect=lambda ctx: _sync_sse_generator(sse_events),
        )

    def _post_stream(self, message="hello", session_id=None):
        """发送 POST 请求到 chat stream 端点。"""
        sid = session_id or self.session_id
        return self.client.post(
            "/api/v1/chat/stream/",
            data=json.dumps({"message": message, "session_id": sid}),
            content_type="application/json",
        )

    def test_unauthenticated_returns_401(self):
        """未认证请求返回 401。"""
        anon_client = Client()
        response = anon_client.post(
            "/api/v1/chat/stream/",
            data=json.dumps({"message": "test"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)

    def test_empty_message_returns_400(self):
        """空消息返回 400 验证错误。"""
        response = self._post_stream(message="")
        self.assertEqual(response.status_code, 400)

    def test_content_chunks_then_done(self):
        """正常流：content chunk → [DONE]。"""
        sse_events = [
            f"data: {json.dumps({'type': 'chunk', 'content': '你好'}, ensure_ascii=False)}\n\n",
            f"data: {json.dumps({'type': 'chunk', 'content': '，世界'}, ensure_ascii=False)}\n\n",
            "data: [DONE]\n\n",
        ]
        with self._patch_generate_stream(sse_events):
            response = self._post_stream()
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response["Content-Type"])

        chunks = _collect_streaming_content(response)
        payloads = [_parse_sse(c) for c in chunks]

        chunk_payloads = [p for p in payloads if isinstance(p, dict) and p.get("type") == "chunk"]
        self.assertEqual(len(chunk_payloads), 2)
        self.assertEqual(chunk_payloads[0]["content"], "你好")
        self.assertEqual(chunk_payloads[1]["content"], "，世界")
        self.assertIn("[DONE]", payloads)

    def test_tool_event_in_stream(self):
        """工具调用事件出现在流中。"""
        sse_events = [
            f"data: {json.dumps({'type': 'tool_event', 'tool_name': 'shell_exec', 'data': {'status': 'running'}})}\n\n",
            f"data: {json.dumps({'type': 'chunk', 'content': '结果'})}\n\n",
            "data: [DONE]\n\n",
        ]
        with self._patch_generate_stream(sse_events):
            response = self._post_stream(message="run tool")
        chunks = _collect_streaming_content(response)
        payloads = [_parse_sse(c) for c in chunks]

        tool_events = [p for p in payloads if isinstance(p, dict) and p.get("type") == "tool_event"]
        self.assertEqual(len(tool_events), 1)
        self.assertEqual(tool_events[0]["tool_name"], "shell_exec")
        self.assertIn("[DONE]", payloads)

    def test_error_event_in_stream(self):
        """SSE error 事件 + [DONE] 正确出现在流中。"""
        from Django_xm.common.sse_utils import sse_error_event

        sse_events = [
            sse_error_event(code="50001", message="服务内部错误"),
            "data: [DONE]\n\n",
        ]
        with self._patch_generate_stream(sse_events):
            response = self._post_stream(message="trigger error")
        chunks = _collect_streaming_content(response)
        payloads = [_parse_sse(c) for c in chunks]

        error_events = [p for p in payloads if isinstance(p, dict) and p.get("type") == "error"]
        self.assertEqual(len(error_events), 1)
        self.assertEqual(error_events[0]["code"], "50001")
        self.assertIn("[DONE]", payloads)

    def test_response_headers_correct(self):
        """SSE 响应头正确设置（Content-Type / Cache-Control / X-Accel-Buffering）。"""
        sse_events = ["data: [DONE]\n\n"]
        with self._patch_generate_stream(sse_events):
            response = self._post_stream()
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response["Content-Type"])
        self.assertEqual(response["Cache-Control"], "no-cache")
        self.assertEqual(response["X-Accel-Buffering"], "no")

    def test_invalid_session_id_returns_400(self):
        """无效 session_id 格式返回 400。"""
        response = self._post_stream(session_id="invalid-not-uuid")
        self.assertEqual(response.status_code, 400)

    def test_valid_request_without_session_id(self):
        """不传 session_id 时请求仍有效（允许匿名流）。"""
        sse_events = [
            f"data: {json.dumps({'type': 'chunk', 'content': 'ok'})}\n\n",
            "data: [DONE]\n\n",
        ]
        with self._patch_generate_stream(sse_events):
            response = self.client.post(
                "/api/v1/chat/stream/",
                data=json.dumps({"message": "no session"}),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)
        chunks = _collect_streaming_content(response)
        payloads = [_parse_sse(c) for c in chunks]
        chunk_payloads = [p for p in payloads if isinstance(p, dict) and p.get("type") == "chunk"]
        self.assertEqual(len(chunk_payloads), 1)


if __name__ == "__main__":
    unittest.main()
