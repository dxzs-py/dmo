"""异步工具函数

提供在同步上下文中安全运行异步协程的公共函数，
消除各模块中重复定义的 _run_async 桥接代码。
"""
import asyncio
import concurrent.futures
from typing import TypeVar

T = TypeVar("T")


def run_async(coro) -> T:
    """在同步上下文中安全运行异步协程

    策略：
    1. 检测当前是否已有运行中的事件循环
    2. 若有，在新线程中通过 asyncio.run() 执行，避免事件循环冲突
    3. 若无，直接 asyncio.run()

    适用场景：
    - Django 同步视图调用异步服务
    - Celery Worker 中调用异步函数
    - __init__ 等同步方法中需要获取异步结果
    """
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result(timeout=120)
    except RuntimeError:
        pass
    return asyncio.run(coro)
