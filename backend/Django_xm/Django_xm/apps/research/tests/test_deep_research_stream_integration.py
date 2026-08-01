"""DeepResearchStreamView 端到端 SSE 集成测试（Task 8.2）

测试覆盖 ``deep_research_stream`` 函数视图（注册于 ``research/urls.py`` 的
``stream/<str:task_id>/``，GET 方法）的完整请求-响应链路：

- 认证失败 → 401 SSE 错误事件
- 任务不存在 / 无权访问 → 404 SSE 错误事件
- 正常流：approval_history → connected → status_change(pending/running/completed)
  + final_report → done
- failed 状态流：status_change(failed) → done（无 final_report）
- step_update 事件（current_step 与 status 不一致时推送）
- get_task_status 返回 None → error 事件
- 响应头验证（Content-Type / Cache-Control / X-Accel-Buffering）

测试策略：
- 使用 Django 原生 Client（force_login）模拟真实 HTTP GET 请求
- 真实创建 ResearchTask 记录（覆盖 ownership 校验路径），Mock get_task_status
  控制状态流转序列（避免真实 2s 轮询与 DB 状态不变导致的死循环）
- Mock time.sleep 避免 4×0.5s 轮询等待拖慢测试
- Mock _load_approval_history_from_db 隔离 DB 审批历史，验证 approval_history 事件
- 解析 StreamingHttpResponse.streaming_content 验证 SSE 事件序列

关键实现细节：
- ``event_stream()`` 是惰性生成器，``get_task_status``/``time.sleep``/
  ``_load_approval_history_from_db`` 在迭代时调用。因此流内容消费
  （``_collect_streaming_content``）必须在 mock 上下文内完成，否则 mock 失效
  会导致真实 DB 查询 + 2s 轮询死循环。``_run_stream`` 辅助方法统一处理。

视图关键路径（views_stream.py:deep_research_stream）：
1. authenticate_sse_request → 401 if 未认证
2. ResearchTask.objects.get(task_id, created_by=user, is_deleted=False) → 404 if 不存在
3. _load_approval_history_from_db → 逐条 yield approval_history 事件
4. yield connected 事件
5. while 循环：get_task_status → status_change（状态变更时）→ step_update
   （current_step 变更时）→ completed/failed 时 break
6. yield done 事件
"""

from __future__ import annotations

import json
import unittest
import uuid
from unittest.mock import patch

from django.test import Client, TestCase


def _parse_sse(sse_str: str) -> dict | str | None:
    """解析 SSE 字符串，返回 payload dict 或原始 token（如 [DONE]）。

    兼容两种格式：
    - ``data: {...}\\n\\n``（普通事件）
    - ``event: error\\ndata: {...}\\n\\n``（错误事件，先剥离 event 行）
    """
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
    """收集 StreamingHttpResponse 的所有 chunk 为字符串列表。

    支持同步和异步生成器：Django 5.2 ASGI 下 ``event_stream`` 为 async generator，
    同步 Client 测试时需通过 ``asyncio.run`` 消费；WSGI 下为同步生成器，直接迭代。
    """
    import asyncio

    chunks: list[str] = []
    content = response.streaming_content
    if hasattr(content, "__aiter__"):
        # 异步生成器：在临时事件循环中消费
        async def _collect():
            async for chunk in content:
                if isinstance(chunk, bytes):
                    chunks.append(chunk.decode("utf-8"))
                else:
                    chunks.append(str(chunk))

        asyncio.run(_collect())
    else:
        for chunk in content:
            if isinstance(chunk, bytes):
                chunks.append(chunk.decode("utf-8"))
            else:
                chunks.append(str(chunk))
    return chunks


def _make_status_sequence(sequence: list):
    """构造 get_task_status 的 mock side_effect。

    按顺序返回 sequence 中的状态字典；超出长度后重复最后一项
    （最后一项应为 completed/failed 终态，确保 while 循环正常 break）。

    注意：None 元素表示"任务不存在"场景，需原样返回（不能被包装）。
    """

    calls = {"n": 0}

    def _side_effect(task_id, user_id=None):
        idx = min(calls["n"], len(sequence) - 1)
        calls["n"] += 1
        return sequence[idx]

    return _side_effect


class DeepResearchStreamIntegrationTests(TestCase):
    """``deep_research_stream`` 函数视图端到端 SSE 集成测试。

    使用 Django 原生 Client（force_login）而非 DRF APIClient，
    确保 StreamingHttpResponse 保留 streaming_content 属性。
    """

    def setUp(self):
        from django.contrib.auth import get_user_model

        from Django_xm.apps.research.models import ResearchTask

        User = get_user_model()
        self.user = User.objects.create_user(username="research_sse_user", password="testpass123")
        self.other_user = User.objects.create_user(username="research_other_user", password="testpass123")
        self.client = Client()
        self.client.force_login(self.user)
        self.task_id = str(uuid.uuid4())

        # 创建一条属于 self.user 的真实任务，使 ownership 校验通过。
        # while 循环内的状态流转由 mock get_task_status 控制，不依赖 DB status。
        self.task = ResearchTask.objects.create(
            task_id=self.task_id,
            query="深度研究测试主题",
            status="pending",
            created_by=self.user,
        )

    # ============== mock 辅助 ==============

    def _patch_time_sleep(self):
        """Mock asyncio.sleep 避免 4×0.5s 轮询等待。

        views_stream.py 的 event_stream 已迁移为 async generator，
        轮询等待使用 ``await asyncio.sleep(0.5)`` 而非 ``time.sleep``。
        """
        return patch("Django_xm.apps.research.views_stream.asyncio.sleep", return_value=None)

    def _patch_status(self, sequence):
        """Mock views_stream.get_task_status 返回状态序列。

        get_task_status 在 views_stream.py 顶层通过
        ``from .services.task_manager import get_task_status`` 导入，
        故在 ``Django_xm.apps.research.views_stream.get_task_status`` 处 patch。
        """
        return patch(
            "Django_xm.apps.research.views_stream.get_task_status",
            side_effect=_make_status_sequence(sequence),
        )

    def _patch_approval_history(self, history=None):
        """Mock _load_approval_history_from_db 隔离 DB 审批历史读取。"""
        return patch(
            "Django_xm.apps.research.views_stream._load_approval_history_from_db",
            return_value=history or [],
        )

    def _get_stream(self, task_id=None, client=None):
        """发送 GET 请求到 deep research stream 端点。"""
        tid = task_id or self.task_id
        used_client = client or self.client
        return used_client.get(f"/api/v1/research/stream/{tid}/")

    def _run_stream(self, sequence, history=None):
        """获取 SSE 响应并在 mock 上下文内消费流内容。

        必须在 mock 上下文内消费：``event_stream()`` 生成器在迭代时调用
        ``get_task_status`` / ``time.sleep`` / ``_load_approval_history_from_db``，
        若在 ``with`` 块外消费则 mock 已失效，会导致真实 DB 查询与 2s 轮询死循环。

        Returns:
            (response, chunks) —— response 为 StreamingHttpResponse，
            chunks 为 SSE 字符串列表
        """
        with self._patch_status(sequence), self._patch_time_sleep(), self._patch_approval_history(history=history):
            response = self._get_stream()
            chunks = _collect_streaming_content(response)
        return response, chunks

    # ============== 认证与权限 ==============

    def test_unauthenticated_returns_401(self):
        """未认证请求返回 401 SSE 错误事件。"""
        anon_client = Client()
        response = anon_client.get(f"/api/v1/research/stream/{self.task_id}/")
        self.assertEqual(response.status_code, 401)
        self.assertIn("text/event-stream", response["Content-Type"])

        chunks = _collect_streaming_content(response)
        payloads = [_parse_sse(c) for c in chunks]
        error_events = [p for p in payloads if isinstance(p, dict) and p.get("type") == "error"]
        self.assertEqual(len(error_events), 1)
        self.assertEqual(error_events[0]["code"], "40101")

    def test_nonexistent_task_returns_404(self):
        """任务不存在返回 404 SSE 错误事件。"""
        fake_task_id = str(uuid.uuid4())
        response = self._get_stream(task_id=fake_task_id)
        self.assertEqual(response.status_code, 404)

        chunks = _collect_streaming_content(response)
        payloads = [_parse_sse(c) for c in chunks]
        error_events = [p for p in payloads if isinstance(p, dict) and p.get("type") == "error"]
        self.assertEqual(len(error_events), 1)
        self.assertEqual(error_events[0]["code"], "40401")

    def test_task_not_owned_by_user_returns_404(self):
        """任务属于其他用户时返回 404（created_by 过滤隔离）。"""
        # 用其他用户的 client 访问 self.user 的任务
        other_client = Client()
        other_client.force_login(self.other_user)
        response = self._get_stream(client=other_client)
        self.assertEqual(response.status_code, 404)

        chunks = _collect_streaming_content(response)
        payloads = [_parse_sse(c) for c in chunks]
        error_events = [p for p in payloads if isinstance(p, dict) and p.get("type") == "error"]
        self.assertEqual(len(error_events), 1)
        self.assertEqual(error_events[0]["code"], "40401")

    # ============== 正常事件序列 ==============

    def test_connected_and_completed_sequence(self):
        """正常流：connected → status_change(pending) → status_change(running)
        → status_change(completed, final_report) → done。

        状态序列设计（每轮 while 调用 get_task_status 2 次：line 112 + line 143，
        completed 终态在 line 112 即 break，不调用 line 143）：
        - call 1 (line 112): pending → status_change(pending)
        - call 2 (line 143): pending → step==status，无 step_update
        - call 3 (line 112): running → status_change(running)
        - call 4 (line 143): running → 无 step_update
        - call 5 (line 112): completed+final_report → status_change(completed) → break
        """
        sequence = [
            {"status": "pending", "current_step": "pending"},
            {"status": "pending", "current_step": "pending"},
            {"status": "running", "current_step": "running"},
            {"status": "running", "current_step": "running"},
            {
                "status": "completed",
                "current_step": "completed",
                "final_report": "# 研究报告\n深度研究最终结果。",
            },
        ]
        response, chunks = self._run_stream(sequence)

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response["Content-Type"])

        payloads = [_parse_sse(c) for c in chunks]
        types = [p.get("type") if isinstance(p, dict) else p for p in payloads]

        # 事件序列：connected, status_change(pending), status_change(running),
        # status_change(completed), done
        self.assertIn("connected", types)
        self.assertEqual(types.count("status_change"), 3)
        self.assertIn("done", types)

        # connected 事件携带 task_id
        connected = next(p for p in payloads if isinstance(p, dict) and p.get("type") == "connected")
        self.assertEqual(connected["task_id"], self.task_id)

        # status_change 序列：pending → running → completed
        status_changes = [p for p in payloads if isinstance(p, dict) and p.get("type") == "status_change"]
        statuses = [sc["status"] for sc in status_changes]
        self.assertEqual(statuses, ["pending", "running", "completed"])

        # completed 事件携带 final_report
        completed_event = status_changes[-1]
        self.assertEqual(completed_event["status"], "completed")
        self.assertIn("研究最终结果", completed_event["final_report"])

        # done 事件携带 task_id
        done_event = next(p for p in payloads if isinstance(p, dict) and p.get("type") == "done")
        self.assertEqual(done_event["task_id"], self.task_id)

    def test_failed_status_sequence(self):
        """failed 状态流：status_change(pending) → status_change(failed) → done。

        failed 事件不携带 final_report，且 while 循环在 failed 时 break。
        """
        sequence = [
            {"status": "pending", "current_step": "pending"},
            {"status": "pending", "current_step": "pending"},
            {"status": "failed", "current_step": "failed"},
        ]
        response, chunks = self._run_stream(sequence)

        self.assertEqual(response.status_code, 200)
        payloads = [_parse_sse(c) for c in chunks]
        status_changes = [p for p in payloads if isinstance(p, dict) and p.get("type") == "status_change"]

        statuses = [sc["status"] for sc in status_changes]
        self.assertEqual(statuses, ["pending", "failed"])

        # failed 事件不含 final_report 字段
        failed_event = status_changes[-1]
        self.assertEqual(failed_event["status"], "failed")
        self.assertNotIn("final_report", failed_event)

        # done 事件存在
        types = [p.get("type") if isinstance(p, dict) else p for p in payloads]
        self.assertIn("done", types)

    # ============== step_update 与 approval_history ==============

    def test_step_update_event(self):
        """current_step 与 status 不一致时推送 step_update 事件。

        序列设计：
        - call 1 (line 112): running → status_change(running)
        - call 2 (line 143): running + current_step="searching" → step_update
        - call 3 (line 112): completed → status_change(completed) → break
        """
        sequence = [
            {"status": "running", "current_step": "running"},
            {"status": "running", "current_step": "searching"},
            {"status": "completed", "current_step": "completed", "final_report": "done"},
        ]
        response, chunks = self._run_stream(sequence)

        self.assertEqual(response.status_code, 200)
        payloads = [_parse_sse(c) for c in chunks]

        step_updates = [p for p in payloads if isinstance(p, dict) and p.get("type") == "step_update"]
        self.assertEqual(len(step_updates), 1)
        self.assertEqual(step_updates[0]["step"], "searching")
        self.assertEqual(step_updates[0]["task_id"], self.task_id)

    def test_approval_history_emitted_before_connected(self):
        """approval_history 事件在 connected 之前推送（来自 DB 历史）。"""
        approval_history = [
            {
                "interrupt_id": "intr-1",
                "tool_name": "shell_exec",
                "title": "执行命令",
                "state": "approved",
                "tool_call_id": "tc-1",
            },
            {
                "interrupt_id": "intr-2",
                "tool_name": "deep_search",
                "title": "深度搜索",
                "state": "pending",
                "tool_call_id": "tc-2",
            },
        ]
        sequence = [
            {"status": "completed", "current_step": "completed", "final_report": "报告"},
        ]
        response, chunks = self._run_stream(sequence, history=approval_history)

        self.assertEqual(response.status_code, 200)
        payloads = [_parse_sse(c) for c in chunks]
        types = [p.get("type") if isinstance(p, dict) else p for p in payloads]

        # approval_history 事件数量等于 DB 历史数量
        approval_events = [p for p in payloads if isinstance(p, dict) and p.get("type") == "approval_history"]
        self.assertEqual(len(approval_events), 2)
        self.assertEqual(approval_events[0]["data"]["interrupt_id"], "intr-1")
        self.assertEqual(approval_events[0]["task_id"], self.task_id)
        self.assertEqual(approval_events[1]["data"]["interrupt_id"], "intr-2")

        # approval_history 全部在 connected 之前
        first_connected_idx = next(i for i, t in enumerate(types) if t == "connected")
        last_approval_idx = max(i for i, t in enumerate(types) if t == "approval_history")
        self.assertLess(last_approval_idx, first_connected_idx)

    # ============== 异常路径 ==============

    def test_get_task_status_none_emits_error(self):
        """get_task_status 返回 None（任务不存在或无权访问）→ error 事件 + break。

        视图 line 113-115：status_data 为 None 时 yield sse_error_event(40401) 并 break。
        break 后仍在 try 块内执行 ``yield done``（line 154），
        故事件序列：connected → error → done。
        """
        sequence = [None]
        response, chunks = self._run_stream(sequence)

        self.assertEqual(response.status_code, 200)
        payloads = [_parse_sse(c) for c in chunks]

        error_events = [p for p in payloads if isinstance(p, dict) and p.get("type") == "error"]
        self.assertEqual(len(error_events), 1)
        self.assertEqual(error_events[0]["code"], "40401")

    # ============== 响应头 ==============

    def test_response_headers_correct(self):
        """SSE 响应头正确设置（Content-Type / Cache-Control / X-Accel-Buffering）。"""
        sequence = [{"status": "completed", "current_step": "completed", "final_report": "x"}]
        response, _ = self._run_stream(sequence)

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response["Content-Type"])
        self.assertEqual(response["Cache-Control"], "no-cache")
        self.assertEqual(response["X-Accel-Buffering"], "no")


if __name__ == "__main__":
    unittest.main()
