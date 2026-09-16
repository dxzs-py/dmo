"""realtime_events channel layer 按事件循环隔离（Task 3）单元测试。

根因背景：channels 官方 get_channel_layer() 返回进程级单例，channels_redis
连接池绑定首次使用的事件循环；子代理线程各自 new_event_loop 运行导致跨
事件循环复用连接池 → Redis ConnectionError: Connection closed by server。
本测试验证 per-loop 隔离修复（_get_loop_channel_layer）。

覆盖：
- 两个线程各自独立 loop → 获取不同实例（连接池按 loop 隔离）
- 同一 loop 内两次调用 → 同一实例（缓存命中，make_backend 仅调用一次）
- loop 关闭后在新 loop 调用 → 新实例（per-loop key 不复用旧条目）
- 已关闭 loop 被 GC 后 WeakKeyDictionary 条目自动清除（实例可释放）

隔离策略：mock channels.layers.channel_layers.make_backend 返回可识别
mock 对象，不触碰真实 Redis / channel layer 后端。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python -m unittest Django_xm.common.tests.test_realtime_events_channel_layer
"""

import asyncio
import gc
import os
import threading
import unittest
from unittest import mock

# python -m unittest 直跑时：Django_xm/__init__.py → celery.py 已将
# DJANGO_SETTINGS_MODULE setdefault 为 dev settings，此处必须强制覆盖为 test
# （多测试模块合并运行时，先加载者决定 settings——强制赋值保证任意加载顺序一致）
os.environ["DJANGO_SETTINGS_MODULE"] = "Django_xm.settings.test"
import django

django.setup()

from channels.layers import channel_layers

from Django_xm.common.realtime_events import _get_loop_channel_layer, _loop_channel_layers


async def _get_layer_once():
    """协程内调用 _get_loop_channel_layer（get_running_loop 需在协程内）。"""
    return _get_loop_channel_layer()


async def _get_layer_twice():
    """同一协程内两次调用，返回二元组用于验证缓存命中。"""
    return _get_loop_channel_layer(), _get_loop_channel_layer()


def _run_in_new_loop(coro_factory):
    """创建新事件循环并 run_until_complete，结束后关闭 loop。

    Args:
        coro_factory: 返回协程对象的无参工厂函数

    Returns:
        tuple: (loop, 协程返回值)
    """
    loop = asyncio.new_event_loop()
    try:
        return loop, loop.run_until_complete(coro_factory())
    finally:
        loop.close()


class TestLoopChannelLayerIsolation(unittest.TestCase):
    """_get_loop_channel_layer 按事件循环隔离行为。"""

    def setUp(self):
        # 每个用例前清空 per-loop 缓存，避免用例间实例残留干扰断言
        _loop_channel_layers.clear()

    def test_different_loops_get_different_instances(self):
        """两个线程各自独立 loop → 不同实例（连接池按 loop 隔离）。"""
        lock = threading.Lock()
        results = []

        def _worker():
            _, layer = _run_in_new_loop(_get_layer_once)
            with lock:
                results.append(layer)

        # 主线程统一 patch，避免多线程并发 patch 同一属性产生恢复竞态
        with mock.patch.object(
            channel_layers, "make_backend", side_effect=lambda name: mock.Mock(name=f"layer-{name}")
        ):
            threads = [threading.Thread(target=_worker) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        self.assertEqual(len(results), 2)
        self.assertIsNot(results[0], results[1])

    def test_same_loop_cache_hit(self):
        """同一 loop 内两次调用 → 同一实例，make_backend 仅调用一次。"""
        calls = []

        def _factory(name):
            calls.append(name)
            return mock.Mock(name=f"layer-{len(calls)}")

        with mock.patch.object(channel_layers, "make_backend", side_effect=_factory):
            loop = asyncio.new_event_loop()
            try:
                first, second = loop.run_until_complete(_get_layer_twice())
            finally:
                loop.close()

        self.assertIs(first, second)
        self.assertEqual(calls, ["default"])

    def test_new_loop_after_close_gets_new_instance(self):
        """loop 关闭后在新 loop 调用 → 新实例（不复用已关闭 loop 的条目）。"""
        calls = []

        def _factory(name):
            calls.append(name)
            return mock.Mock(name=f"layer-{len(calls)}")

        with mock.patch.object(channel_layers, "make_backend", side_effect=_factory):
            _, layer_a = _run_in_new_loop(_get_layer_once)
            _, layer_b = _run_in_new_loop(_get_layer_once)

        self.assertIsNot(layer_a, layer_b)
        self.assertEqual(len(calls), 2)

    def test_closed_loop_entry_released_after_gc(self):
        """已关闭 loop 被 GC 后，per-loop 缓存条目自动清除（实例可释放）。"""
        with mock.patch.object(channel_layers, "make_backend", return_value=mock.Mock()):
            # loop 在 _run_in_new_loop 内创建并关闭，返回值不接收 → 无强引用残留
            _run_in_new_loop(_get_layer_once)

        gc.collect()
        self.assertEqual(len(_loop_channel_layers), 0)


if __name__ == "__main__":
    unittest.main()
