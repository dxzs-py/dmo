"""realtime_events persist_history（快照不入回放窗口）单元测试（D2 / audit P2-8 PF-02）。

背景：每频道历史 List 上限 500 条（LTRIM），全量正文快照（STREAM_CONTENT_UPDATE）
与中间态推理快照逐条占席，长回答会把 tool_call/approval/message_added 等关键
历史事件挤出窗口，重连回放语义受损。

整改：publish_event 链路透传 ``persist_history`` 标志——False 时 seq 照常 INCR
（同频道 seq 严格单调、允许跳号的既有语义不变），但事件不 RPUSH 进历史 List
（仅实时 group_send 广播）。默认 True，全部既有调用方行为不变。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.common.tests.test_realtime_events_persist_history --noinput
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from unittest import mock
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase

from Django_xm.async_utils import run_async
from Django_xm.common import realtime_events
from Django_xm.common.realtime_events import (
    _atomic_publish_event,
    _publish_to_session_async,
)

REDIS_CLIENT = "Django_xm.common.realtime_events.get_redis_client"
LOOP_LAYER = "Django_xm.common.realtime_events._get_loop_channel_layer"
ATOMIC_PUBLISH = "Django_xm.common.realtime_events._atomic_publish_event"


class _ScriptCapture:
    """捕获 register_script 调用参数的假脚本对象（eval 之外的 script 路径）。"""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, keys=None, args=None, client=None):
        self.calls.append({"keys": keys, "args": list(args or [])})
        return len(self.calls)  # seq


class TestAtomicPublishEventPersistFlag(SimpleTestCase):
    """_atomic_publish_event：persist_history 透传到 Lua/eval 参数。"""

    def setUp(self):
        super().setUp()
        # 模块级脚本缓存（_PUBLISH_EVENT_LUA_SCRIPT）为跨用例全局态：
        # eval 回退用例会把它污染为 False，逐用例重置为 None 隔离
        patcher = mock.patch.object(
            realtime_events, "_PUBLISH_EVENT_LUA_SCRIPT", None
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _make_redis(self, script_capture: _ScriptCapture) -> MagicMock:
        redis_client = MagicMock()
        redis_client.register_script.return_value = script_capture
        return redis_client

    def test_persist_true_writes_history(self):
        """默认（persist_history=True）→ Lua ARGV[5]='1'（RPUSH 历史分支）。"""
        capture = _ScriptCapture()
        seq = _atomic_publish_event(
            self._make_redis(capture), "session", "s1", {"type": "t", "payload": {}}
        )
        self.assertEqual(seq, 1)
        self.assertEqual(capture.calls[0]["args"][4], "1")

    def test_persist_false_skips_history_flag(self):
        """persist_history=False → Lua ARGV[5]='0'（跳过 RPUSH/LTRIM/EXPIRE 历史分支）。"""
        capture = _ScriptCapture()
        seq = _atomic_publish_event(
            self._make_redis(capture), "session", "s1",
            {"type": "t", "payload": {}}, persist_history=False,
        )
        self.assertEqual(seq, 1)  # seq 照常生成（同频道单调、允许跳号语义不变）
        self.assertEqual(capture.calls[0]["args"][4], "0")

    def test_persist_false_eval_path_keeps_seq(self):
        """脚本注册失败回退 eval 路径时，persist 标志同样作为第 5 个 ARGV 透传。"""
        redis_client = MagicMock()
        redis_client.register_script.side_effect = RuntimeError("no script")
        redis_client.eval.return_value = 7

        seq = _atomic_publish_event(
            redis_client, "session", "s1", {"type": "t", "payload": {}},
            persist_history=False,
        )
        self.assertEqual(seq, 7)
        eval_args = redis_client.eval.call_args.args
        self.assertEqual(eval_args[-1], "0")  # ARGV[5] = persist flag


class TestPublishToSessionPersistFlag(SimpleTestCase):
    """_publish_to_session_async：persist_history 参数透传到 _atomic_publish_event。"""

    def test_flag_forwarded_to_atomic_publish(self):
        for persist, expected in ((True, True), (False, False)):
            with self.subTest(persist=persist):
                with patch(REDIS_CLIENT, return_value=MagicMock()), patch(
                    ATOMIC_PUBLISH, return_value=1
                ) as mock_atomic, patch(LOOP_LAYER) as mock_layer:
                    mock_layer.return_value.group_send = AsyncMock()
                    run_async(
                        _publish_to_session_async(
                            "s1", "stream_event", {"k": "v"},
                            persist_history=persist,
                        )
                    )
                mock_atomic.assert_called_once()
                self.assertEqual(
                    mock_atomic.call_args.kwargs.get("persist_history"), expected
                )

    def test_default_keeps_history(self):
        """不传参默认 True：全部既有调用方行为不变。"""
        with patch(REDIS_CLIENT, return_value=MagicMock()), patch(
            ATOMIC_PUBLISH, return_value=1
        ) as mock_atomic, patch(LOOP_LAYER) as mock_layer:
            mock_layer.return_value.group_send = AsyncMock()
            run_async(_publish_to_session_async("s1", "session_created", {}))
        self.assertEqual(mock_atomic.call_args.kwargs.get("persist_history"), True)

    def test_atomic_failure_does_not_block_group_send(self):
        """persist=False 时 _atomic_publish_event 正常返回 seq；广播链路不受影响。"""
        with patch(REDIS_CLIENT, return_value=MagicMock()), patch(
            ATOMIC_PUBLISH, return_value=3
        ) as mock_atomic, patch(LOOP_LAYER) as mock_layer:
            mock_layer.return_value.group_send = AsyncMock()
            run_async(
                _publish_to_session_async(
                    "s1", "stream_event", {"k": "v"}, persist_history=False
                )
            )
        mock_atomic.assert_called_once()
        mock_layer.return_value.group_send.assert_awaited_once()
