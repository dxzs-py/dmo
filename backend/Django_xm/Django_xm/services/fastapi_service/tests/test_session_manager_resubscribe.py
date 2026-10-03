"""SessionManager 信令订阅循环断线重连测试（REL-03）。

覆盖：
- Redis 连接异常一次后恢复：循环重建 PubSub 并重新 psubscribe 原有频道
  + 补跑 recover_unfinished（恢复未完成任务）；
- 连接中断前收到的 pmessage 正常路由分发；
- 停机标记（_shutting_down）置位后不再重连，循环退出；
- shutdown() 优雅停机取消订阅任务（不产生新的重连）。

隔离策略：不触碰真实 Redis——pubsub.listen 为 async generator fake，
异常注入一次后由新 fake pubsub 模拟恢复；recover_unfinished 打桩；
信令使用未知 kind（仅告警，不触发 SessionExecutor 副作用）。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.services.fastapi_service.tests.test_session_manager_resubscribe
"""

import asyncio
import contextlib
import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from Django_xm.services.fastapi_service.session_manager import (
    SIGNAL_PATTERNS,
    SessionManager,
)

_SM_LOGGER = "Django_xm.services.fastapi_service.session_manager"


class _FakePubSub:
    """fake pubsub：按序 yield 预置消息后可注入异常；无异常则永久挂起（健康连接）。"""

    def __init__(self, messages=None, *, raise_after=False):
        self._messages = list(messages or [])
        self._raise_after = raise_after
        self.psubscribed_patterns = None
        self.closed = False

    async def psubscribe(self, *patterns):
        self.psubscribed_patterns = list(patterns)

    def listen(self):
        return self._listen_gen()

    async def _listen_gen(self):
        for message in self._messages:
            yield message
        if self._raise_after:
            raise ConnectionError("redis connection lost")
        # 健康 fake：永久挂起（等价真实连接静默等待消息）
        await asyncio.Event().wait()

    async def close(self):
        self.closed = True


def _default_pubsub() -> _FakePubSub:
    return _FakePubSub()


class _FakeSignalRedis:
    """fake redis：pubsub() 每次发一个新的 fake（记录创建次数，供重连断言）。"""

    def __init__(self, pubsub_factory=None):
        self._factory = pubsub_factory or _default_pubsub
        self.created_pubsubs = []

    def pubsub(self):
        ps = self._factory()
        self.created_pubsubs.append(ps)
        return ps

    async def aclose(self):
        pass


def _pmessage(kind="bogus_kind", thread_id="t1"):
    return {
        "type": "pmessage",
        "channel": f"agent:{kind}:{thread_id}".encode(),
        "data": b"{}",
    }


class SubscriptionReconnectTests(unittest.TestCase):
    """订阅循环断线重连（REL-03）。"""

    def _make_manager(self, redis):
        manager = SessionManager()
        manager._reconnect_initial_delay = 0.01
        manager._reconnect_max_delay = 0.03
        manager._redis = redis
        return manager

    def test_reconnect_after_connection_error(self):
        """连接异常一次后恢复：重建 pubsub + 重新订阅原频道 + 补跑恢复。"""

        async def _case():
            manager = self._make_manager(_FakeSignalRedis())
            # 首个 pubsub：先投递一条 pmessage，再抛 ConnectionError 模拟 Redis 抖动
            first = _FakePubSub(messages=[_pmessage()], raise_after=True)
            manager._pubsub = first
            recover_mock = mock.AsyncMock()
            with (
                mock.patch.object(manager, "recover_unfinished", recover_mock),
                self.assertLogs(_SM_LOGGER, level="WARNING") as logs,
            ):
                task = asyncio.create_task(manager._subscription_loop())
                await asyncio.sleep(0.08)

            # 循环仍存活（未因异常退出，阻塞在新连接的 listen 上）
            self.assertFalse(task.done())
            # 断线前的 pmessage 已路由分发（未知 kind 告警）
            self.assertTrue(any("未知信令类型" in message for message in logs.output))
            # 重建了新 pubsub 并重新 psubscribe 原有频道
            self.assertEqual(len(manager._redis.created_pubsubs), 1)
            reconnected = manager._redis.created_pubsubs[0]
            self.assertEqual(reconnected.psubscribed_patterns, list(SIGNAL_PATTERNS))
            self.assertIs(manager._pubsub, reconnected)
            # 旧 pubsub 已关闭
            self.assertTrue(first.closed)
            # 重连后补跑未完成任务恢复
            recover_mock.assert_awaited_once()

            # 清理
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        asyncio.run(_case())

    def test_no_reconnect_after_shutdown_flag(self):
        """停机标记置位后不再重连：持续故障下循环自行退出且不再创建连接。"""

        async def _case():
            factory = lambda: _FakePubSub(raise_after=True)  # noqa: E731 每次监听即抛错（持续故障）
            manager = self._make_manager(_FakeSignalRedis(pubsub_factory=factory))
            manager._pubsub = _FakePubSub(raise_after=True)
            # 重连路径的恢复扫描为 DB 副作用，非本组被测对象，统一打桩
            with mock.patch.object(manager, "recover_unfinished", mock.AsyncMock()):
                task = asyncio.create_task(manager._subscription_loop())
                await asyncio.sleep(0.06)
                self.assertGreaterEqual(len(manager._redis.created_pubsubs), 1)

                # 模拟 shutdown 置位（不 cancel，单独验证 flag 生效路径）
                manager._shutting_down = True
                await asyncio.wait_for(task, timeout=0.3)
                count_at_exit = len(manager._redis.created_pubsubs)
                await asyncio.sleep(0.05)
                self.assertEqual(len(manager._redis.created_pubsubs), count_at_exit)

        asyncio.run(_case())

    def test_shutdown_cancels_subscription_cleanly(self):
        """shutdown()：置位停机标记 + 取消订阅任务，不产生新的重连。"""

        async def _case():
            manager = self._make_manager(_FakeSignalRedis())
            manager._pubsub = _FakePubSub()  # 健康连接（永久挂起）
            with mock.patch.object(manager, "recover_unfinished", mock.AsyncMock()):
                task = asyncio.create_task(manager._subscription_loop())
                manager._subscription_task = task
                await asyncio.sleep(0.02)
                self.assertFalse(task.done())

                await manager.shutdown()

            self.assertTrue(manager._shutting_down)
            for _ in range(50):
                if task.done():
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(task.done())
            self.assertTrue(manager._pubsub.closed)
            await asyncio.sleep(0.03)
            self.assertEqual(len(manager._redis.created_pubsubs), 0)

        asyncio.run(_case())


if __name__ == "__main__":
    unittest.main()
