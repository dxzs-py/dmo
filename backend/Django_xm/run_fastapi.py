"""FastAPI Agent 执行服务启动入口（8001）。

为什么必须用本入口 + ``loop="none"``，而非 ``python -m uvicorn ...``：
psycopg 异步驱动在 Windows 默认 ``ProactorEventLoop`` 下报
"Psycopg cannot use the 'ProactorEventLoop'"，必须使用 ``SelectorEventLoop``。

uvicorn 0.36+ 用 ``loop_factory`` 机制创建事件循环：默认 ``loop="auto"`` 会走
``uvicorn.loops.asyncio.asyncio_loop_factory``，该工厂在 Windows（非 subprocess）下
**强制返回 ``asyncio.ProactorEventLoop``**（见 uvicorn/loops/asyncio.py），
完全绕过 ``asyncio.set_event_loop_policy``——因此在顶层设置 policy 也无法生效。

``loop="none"`` 使 ``get_loop_factory()`` 返回 None，uvicorn 转由
``asyncio.new_event_loop()`` 创建循环（遵循已设置的 policy），
配合 ``WindowsSelectorEventLoopPolicy`` 得到 ``SelectorEventLoop``。

启动（工作目录 backend/Django_xm）：
    python run_fastapi.py
"""

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "Django_xm.apps.fastapi_service.main:app",
        host="0.0.0.0",  # noqa: S104  # 部署需求：多浏览器/多设备访问，与原 uvicorn 命令行 --host 0.0.0.0 一致
        port=8001,
        loop="none",  # 关键：绕过 uvicorn 默认 loop_factory（Windows 强制 ProactorEventLoop），
        # 让 asyncio.new_event_loop() 遵循上方设置的 SelectorEventLoopPolicy
    )
