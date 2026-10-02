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
from unittest import mock

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
