"""FastAPI Agent 执行服务入口（独立进程，端口 8001）。

与 Django Web 进程完全隔离：
- 承载聊天 + 深度研究长任务（SessionManager + SessionExecutor，单协程 + 事件驱动）
- 事件复用 Django 的 realtime_sync 通道（Redis + Channels 网关），前端零改动
- 共享 PostgreSQL + Redis

启动：
    cd backend/Django_xm && uvicorn Django_xm.services.fastapi_service.main:app --host 0.0.0.0 --port 8001
"""

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager

# psycopg 异步驱动要求事件循环为 SelectorEventLoop（Windows 默认 ProactorEventLoop 不兼容）。
# 注意：仅设置 policy 不够——uvicorn 0.36+ 默认 loop="auto" 走
# ``uvicorn.loops.asyncio.asyncio_loop_factory``，在 Windows 强制返回 ProactorEventLoop，
# 完全绕过 policy。必须用 `loop="none"`（见 run_fastapi.py）让 asyncio.new_event_loop()
# 遵循 policy。此处保留 policy 设置，供 run_fastapi.py 及 programmatic 加载方式使用。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
# 日志隔离：settings.LOGGING 据此选择日志文件（8001 → fastapi.log，Web → django.log），
# 必须在 django.setup() 前设置（LOGGING 在 settings 加载时按 SERVICE_ROLE 构建）
os.environ["SERVICE_ROLE"] = "fastapi"
import django

django.setup()

from fastapi import FastAPI

from Django_xm.services.fastapi_service.session_manager import SessionManager

logger = logging.getLogger(__name__)

_manager: SessionManager | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 缓存预热：uvicorn 在事件循环内 import app（config.load 在 asyncio.run 中），
    # AppConfig.ready() 的同步预热在 8001 进程实际处于异步上下文而跳过（SynchronousOnlyOperation），
    # 故在此用 sync_to_async 在线程中同步预热，保证后续 async agent 构建直接命中缓存。
    from asgiref.sync import sync_to_async

    from Django_xm.apps.ai_engine.models import warmup_system_config_cache
    from Django_xm.apps.ai_engine.services.registry_service import warmup_cache

    await sync_to_async(warmup_cache)()
    await sync_to_async(warmup_system_config_cache)()

    # 模块级单例（health 路由读取），lifespan 中初始化是 FastAPI 标准模式
    global _manager  # noqa: PLW0603
    _manager = SessionManager()
    await _manager.start()
    logger.info("[FastAPI] Agent 执行服务已启动")
    yield
    if _manager:
        await _manager.shutdown()
    logger.info("[FastAPI] Agent 执行服务已停止")


app = FastAPI(title="Agent 执行服务", lifespan=lifespan)


@app.get("/health")
async def health():
    active = len(_manager._sessions) if _manager else 0
    return {"status": "ok", "active_sessions": active}
