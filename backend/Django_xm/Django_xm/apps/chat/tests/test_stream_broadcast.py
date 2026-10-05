"""stream_broadcast STREAM_EVENT 子类型注入测试（审计项 ARCH-19）。

后端将 deep_research / research_task_id / model_fallback 等执行事件统一包装为
STREAM_EVENT（event_type 恒为 "stream_event"），原始子类型信息此前丢失，
前端按内层子类型分流永不命中。修复后包装时在 payload 顶层注入
``event_subtype`` 原始子类型；有独立 EventType 的子事件（reasoning 等）
不注入，避免污染前端 fieldMap 分流。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.chat.tests.test_stream_broadcast --noinput
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase

from Django_xm.async_utils import run_async
from Django_xm.apps.chat.services.stream_broadcast import publish_stream_event

PUBLISH_EVENT = "Django_xm.common.realtime_events.publish_event"


class TestStreamBroadcastEventSubtype(SimpleTestCase):
    """STREAM_EVENT 包装时注入原始子类型（ARCH-19）。"""

    def _assert_published(self, mock_publish, *, event_type: str, subtype: str | None):
        """断言 publish_event 单次调用且 payload 子类型注入符合预期。"""
        mock_publish.assert_awaited_once()
        args, kwargs = mock_publish.call_args
        evt_type, payload = args[0], args[1]
        self.assertEqual(evt_type.value, event_type)
        if subtype is None:
            self.assertNotIn("event_subtype", payload)
        else:
            self.assertEqual(payload.get("event_subtype"), subtype)
        return payload

    def test_deep_research_wraps_stream_event_with_subtype(self):
        """deep_research → STREAM_EVENT + payload.event_subtype='deep_research'。"""
        event = {"type": "deep_research", "data": {"task_id": "t1", "mode": "standard"}}
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish:
            run_async(publish_stream_event(event, "s1", message_id=42))
        payload = self._assert_published(
            mock_publish, event_type="stream_event", subtype="deep_research"
        )
        self.assertEqual(payload["data"]["task_id"], "t1")
        self.assertEqual(payload["message_id"], "42")

    def test_research_task_id_wraps_stream_event_with_subtype(self):
        """research_task_id → STREAM_EVENT + payload.event_subtype='research_task_id'。"""
        event = {"type": "research_task_id", "data": {"research_task_id": "t2"}}
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish:
            run_async(publish_stream_event(event, "s1", message_id=42))
        self._assert_published(
            mock_publish, event_type="stream_event", subtype="research_task_id"
        )

    def test_model_fallback_wraps_stream_event_with_subtype(self):
        """model_fallback → STREAM_EVENT + payload.event_subtype='model_fallback'。"""
        event = {"type": "model_fallback", "data": {"message": "主模型不可用，已降级"}}
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish:
            run_async(publish_stream_event(event, "s1", message_id=42))
        self._assert_published(
            mock_publish, event_type="stream_event", subtype="model_fallback"
        )

    def test_reasoning_keeps_dedicated_event_type_without_subtype(self):
        """reasoning → 独立 STREAM_REASONING 事件，不注入 event_subtype（不污染 fieldMap 分流）。"""
        event = {"type": "reasoning", "data": {"content": "思考中"}}
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish:
            run_async(publish_stream_event(event, "s1", message_id=42))
        self._assert_published(
            mock_publish, event_type="stream_reasoning", subtype=None
        )

    def test_chunk_throttled_content_update_without_subtype(self):
        """chunk → STREAM_CONTENT_UPDATE（节流后广播），不注入 event_subtype。"""
        event = {"type": "chunk", "content": "正文增量"}
        content_state = {"content": "", "last_broadcast": 0.0}
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish:
            run_async(
                publish_stream_event(event, "s1", message_id=42, content_state=content_state)
            )
        self._assert_published(
            mock_publish, event_type="stream_content_update", subtype=None
        )


class TestErrorEventBroadcast(SimpleTestCase):
    """流内 error 事件广播（整改 A1.1 复现 + A1.2 修复）。

    修复前缺口（审计 F1-1）：error 事件类型不在 publish_stream_event 广播
    白名单内，被静默丢弃 → chat 链失败（模型全败/回退失败/slash 命令失败）
    对用户不可见，且 chat_executor_core finally 恒广播 success=True（假成功）。
    修复后：error → STREAM_EVENT + event_subtype='error'，data 规范化为
    {code, message}（对齐 consumers.py / sse_utils.py 的 WS error 帧规范）。
    """

    def _assert_published(self, mock_publish, *, event_type: str, subtype: str | None):
        """断言 publish_event 单次调用且 payload 子类型注入符合预期。"""
        mock_publish.assert_awaited_once()
        args, kwargs = mock_publish.call_args
        evt_type, payload = args[0], args[1]
        self.assertEqual(evt_type.value, event_type)
        if subtype is None:
            self.assertNotIn("event_subtype", payload)
        else:
            self.assertEqual(payload.get("event_subtype"), subtype)
        return payload

    def test_error_event_broadcasts_as_stream_event_with_subtype(self):
        """error 事件（content 形态）→ STREAM_EVENT + subtype=error + data.message。"""
        event = {"type": "error", "content": "模型服务暂时不可用，请稍后重试"}
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish:
            run_async(publish_stream_event(event, "s1", message_id=42))
        payload = self._assert_published(
            mock_publish, event_type="stream_event", subtype="error"
        )
        self.assertEqual(payload["data"]["message"], "模型服务暂时不可用，请稍后重试")
        self.assertTrue(payload["data"].get("code"))

    def test_error_event_normalizes_to_code_and_message(self):
        """error 事件（code/message 形态）原样透传 code 与 message。"""
        event = {"type": "error", "code": "MODEL_CALL_ERROR", "message": "请求超时，请稍后重试"}
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish:
            run_async(publish_stream_event(event, "s1", message_id=42))
        payload = self._assert_published(
            mock_publish, event_type="stream_event", subtype="error"
        )
        self.assertEqual(payload["data"]["code"], "MODEL_CALL_ERROR")
        self.assertEqual(payload["data"]["message"], "请求超时，请稍后重试")

    def test_error_event_missing_message_falls_back_to_default(self):
        """error 事件缺文案时回退默认可行动文案（不透出裸异常结构）。"""
        event = {"type": "error"}
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish:
            run_async(publish_stream_event(event, "s1", message_id=42))
        payload = self._assert_published(
            mock_publish, event_type="stream_event", subtype="error"
        )
        self.assertTrue(payload["data"]["message"])
        self.assertNotIn("content", payload["data"])


class TestRetryAndTimeoutWarningBroadcast(SimpleTestCase):
    """retry / timeout_warning 事件转发（整改 E1/F5）。

    修复前缺口（审计 F5）：执行服务化后，AgentExecutor 的执行可观测事件
    （agent_executor.py 的 retry :194-202 / timeout_warning :276-282）不在
    publish_stream_event 广播白名单内，被静默跳过 → 旧 SSE 模型下前端可见的
    重试提示与软超时警告在 WS 桥上不可达。修复后：两者经 STREAM_EVENT +
    event_subtype 通道透传，data payload 原样保形（无 error 帧规范化语义）；
    前端暂无消费点（_handleGenericStreamEvent 对未知子类型安全忽略并记 debug
    日志），事件到达 WS 层后可观测性恢复，前端后续接入零后端改动。
    """

    def _assert_published(self, mock_publish, *, event_type: str, subtype: str | None):
        """断言 publish_event 单次调用且 payload 子类型注入符合预期。"""
        mock_publish.assert_awaited_once()
        args, kwargs = mock_publish.call_args
        evt_type, payload = args[0], args[1]
        self.assertEqual(evt_type.value, event_type)
        if subtype is None:
            self.assertNotIn("event_subtype", payload)
        else:
            self.assertEqual(payload.get("event_subtype"), subtype)
        return payload

    def test_retry_event_broadcasts_with_intact_payload(self):
        """retry 事件 → STREAM_EVENT + subtype=retry，data 保形透传（不改写）。"""
        event = {
            "type": "retry",
            "data": {
                "attempt": 1,
                "max": 3,
                "backoff": 2.0,
                "error_code": "TIMEOUT",
            },
        }
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish:
            run_async(publish_stream_event(event, "s1", message_id=42))
        payload = self._assert_published(
            mock_publish, event_type="stream_event", subtype="retry"
        )
        # 保 payload 形状：AgentExecutor 产出字段逐字段原样透传
        self.assertEqual(
            payload["data"],
            {"attempt": 1, "max": 3, "backoff": 2.0, "error_code": "TIMEOUT"},
        )

    def test_timeout_warning_event_broadcasts_with_intact_payload(self):
        """timeout_warning 事件 → STREAM_EVENT + subtype=timeout_warning，data 保形。"""
        event = {"type": "timeout_warning", "data": {"elapsed": 1801, "limit": 1800}}
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish:
            run_async(publish_stream_event(event, "s1", message_id=42))
        payload = self._assert_published(
            mock_publish, event_type="stream_event", subtype="timeout_warning"
        )
        self.assertEqual(payload["data"], {"elapsed": 1801, "limit": 1800})

    def test_stream_continue_events_do_not_flush_reasoning_window(self):
        """保序与节流边界：retry/timeout_warning 属"流继续"类事件，不冲刷
        reasoning 节流窗口（与 chunk 同性质，D2 边界红线）；到达即按序广播，
        不插队不等待窗口内 pending。"""
        from Django_xm.apps.chat.services.stream_broadcast import TimeWindowThrottle

        event_retry = {"type": "retry", "data": {"attempt": 1, "max": 3}}
        event_warning = {"type": "timeout_warning", "data": {"elapsed": 1801, "limit": 1800}}
        with patch(PUBLISH_EVENT, new_callable=AsyncMock) as mock_publish:
            reasoning_state: dict = {}
            # 首个 reasoning：窗口 leading edge 立即广播（call#0）
            run_async(publish_stream_event(
                {"type": "reasoning", "data": {"content": "第一段"}},
                "s1", message_id=42, reasoning_state=reasoning_state,
            ))
            # 第二个 reasoning：进窗口合流为 pending，不广播
            run_async(publish_stream_event(
                {"type": "reasoning", "data": {"content": "第二段"}},
                "s1", message_id=42, reasoning_state=reasoning_state,
            ))
            # retry / timeout_warning 到达：立即广播，窗口 pending 不被冲刷
            run_async(publish_stream_event(event_retry, "s1", message_id=42))
            run_async(publish_stream_event(event_warning, "s1", message_id=42))
            # call#1=retry、call#2=timeout_warning（到达序，无 pending reasoning 插入）
            self.assertEqual(mock_publish.call_count, 3)
            self.assertEqual(mock_publish.call_args_list[1].args[1].get("event_subtype"), "retry")
            self.assertEqual(
                mock_publish.call_args_list[2].args[1].get("event_subtype"),
                "timeout_warning",
            )
            # 流收尾显式 flush：pending reasoning 送达（最终态不丢，D2 flush 契约）
            run_async(publish_stream_event(
                {"type": "reasoning", "data": {"finished": True, "content": "终值"}},
                "s1", message_id=42, reasoning_state=reasoning_state,
            ))
            self.assertEqual(mock_publish.call_count, 5)
            self.assertEqual(mock_publish.call_args_list[4].args[0].value, "stream_reasoning")
            self.assertEqual(
                mock_publish.call_args_list[4].args[1]["data"],
                {"finished": True, "content": "终值"},
            )
