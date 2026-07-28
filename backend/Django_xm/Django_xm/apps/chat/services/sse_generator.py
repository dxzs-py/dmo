"""SSE 流式聊天生成器

将原 ``views_chat.py`` 中 ``ChatStreamView.post().generate()`` 闭包拆分为
三个职责单一的子函数（Task 23.1）：

- ``_init_stream``：初始化事件循环 + 发送附件预处理事件
- ``_process_chunks``：迭代 ``ChatService`` 异步生成器，处理心跳与事件分发
- ``_cleanup_stream``：释放 checkpointer、刷新待处理内容、清理残留 Task、关闭 loop

共享状态通过 ``ChatStreamContext`` dataclass 显式传递，取代原闭包变量捕获。
``_cleanup_stream`` 中的 checkpointer 释放失败现在会记录 WARNING 日志（Task 23.4），
不再静默吞掉异常，便于运维定位连接泄漏。

设计要点：
- ``stream_state`` 共享字典仍保留，因为 async generator 被强制关闭时
  ``finally`` 块中需要兜底刷新 ``pending_content``，与原实现行为一致
- ``[DONE]`` 事件由 ``generate_chat_stream`` 的 ``finally`` 块统一发送，
  确保客户端在任何路径下都能收到流结束信号
- 所有 yield 都是 SSE 格式字符串（``"data: ...\\n\\n"``）
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any

from django.http import HttpRequest

from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.sse_utils import sse_error_event

logger = logging.getLogger(__name__)


# ============== 流式上下文 ==============

@dataclass
class ChatStreamContext:
    """SSE 流式聊天上下文，封装原 generate() 闭包的共享状态。

    通过 dataclass 显式传递共享状态，取代原闭包变量捕获，使
    ``_init_stream`` / ``_process_chunks`` / ``_cleanup_stream``
    可以作为独立函数测试与复用。
    """
    request: HttpRequest
    data: dict[str, Any]
    original_attachment_ids: list[str]
    pending_progress: list[dict[str, Any]]
    # 共享状态：async generator 被强制关闭时（aclose/GeneratorExit），
    # try/except 之后的代码不会执行，导致 _pending_content 无法刷新。
    # 通过共享状态字典，在 finally 块中兜底刷新。
    stream_state: dict[str, str] = field(default_factory=lambda: {"pending_content": ""})
    loop: asyncio.AbstractEventLoop | None = None
    gen: AsyncIterator | None = None
    pending_task: asyncio.Task | None = None


# ============== 子函数 ==============

def _init_stream(ctx: ChatStreamContext) -> Iterator[str]:
    """初始化 SSE 流：创建事件循环，发送附件 ID 与预处理进度事件。"""
    ctx.loop = asyncio.new_event_loop()
    # 共享状态字典注入到 data，供 ChatService 内部写入 pending_content
    ctx.data['_stream_state'] = ctx.stream_state

    if ctx.original_attachment_ids:
        yield f"data: {json.dumps({'type': 'attachment_ids', 'data': ctx.original_attachment_ids}, ensure_ascii=False)}\n\n"

    for evt in ctx.pending_progress:
        yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
    ctx.pending_progress.clear()


def _process_chunks(ctx: ChatStreamContext) -> Iterator[str]:
    """处理流式 chunks：迭代 ChatService async generator，30s 心跳保活。

    使用 ``asyncio.wait`` 而非 ``wait_for``，超时不取消底层任务，
    确保长耗时操作（LLM 推理、工具执行）能继续执行，下一次循环复用同一 pending task。
    """
    from .chat_service import ChatService

    chat_service = ChatService(
        user_id=ctx.request.user.id if ctx.request.user.is_authenticated else None,
        thread_id=ctx.data.get('session_id'),
    )
    ctx.gen = chat_service.process_stream_chat_request(ctx.data).__aiter__()

    while True:
        try:
            if ctx.pending_task is None:
                ctx.pending_task = ctx.loop.create_task(ctx.gen.__anext__())

            done, _ = ctx.loop.run_until_complete(
                asyncio.wait({ctx.pending_task}, timeout=30.0)
            )

            if not done:
                # 超时但任务仍在运行，发送心跳保活，不取消任务
                yield f"data: {json.dumps({'type': 'heartbeat'}, ensure_ascii=False)}\n\n"
                continue

            # 任务完成
            event = ctx.pending_task.result()
            ctx.pending_task = None

            if isinstance(event, dict) and event.get('type') == 'error':
                yield sse_error_event(
                    code=str(int(ErrorCode.SERVER_ERROR)),
                    message=event.get('message', '处理出错'),
                )
            else:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except StopAsyncIteration:
            ctx.pending_task = None
            logger.debug("generate(): StopAsyncIteration, 流式处理完成")
            break


def _cleanup_stream(ctx: ChatStreamContext) -> Iterator[str]:
    """清理 SSE 流资源：释放 checkpointer、刷新待处理内容、清理残留 Task。

    Task 23.4：checkpointer 释放失败现在记录 WARNING 日志，不再静默吞掉，
    便于运维定位 PostgreSQL 连接泄漏问题。
    """
    # 1. 取消未完成的 pending task
    if ctx.pending_task is not None:
        ctx.pending_task.cancel()

    # 2. 关闭 async generator（触发其内部 finally）
    if ctx.gen is not None:
        try:
            ctx.loop.run_until_complete(ctx.gen.aclose())
        except Exception as e:
            logger.debug(f"async generator aclose 异常（通常无害）: {e}")

    # 3. 释放当前事件循环的异步 Checkpointer 连接池，防止 PostgreSQL 连接泄漏
    # Task 23.4：记录释放失败，便于监控连接泄漏
    try:
        from Django_xm.apps.ai_engine.services.checkpointer_factory import release_async_checkpointer
        ctx.loop.run_until_complete(release_async_checkpointer())
    except Exception as e:
        logger.warning(
            f"[SSE Cleanup] Checkpointer 连接释放失败，可能存在连接泄漏: {e}",
            exc_info=True,
        )

    # 4. 兜底刷新：async generator 被强制关闭时，_pending_content 可能未被刷新
    pending_content = ctx.stream_state.get("pending_content", "")
    if pending_content:
        logger.debug(f"generate() finally: 兜底刷新 _pending_content ({len(pending_content)} 字符)")
        yield f"data: {json.dumps({'type': 'chunk', 'content': pending_content}, ensure_ascii=False)}\n\n"

    # 5. 取消所有残留 Task，避免 "Task was destroyed but it is pending!" 警告
    try:
        # 先让事件循环运行一小段时间，让 aclose 产生的清理任务有机会完成
        ctx.loop.run_until_complete(asyncio.sleep(0.05))
        pending = asyncio.all_tasks(ctx.loop)
        for task in pending:
            task.cancel()
        if pending:
            ctx.loop.run_until_complete(
                asyncio.gather(*pending, return_exceptions=True)
            )
    except Exception as e:
        logger.debug(f"残留 Task 清理异常（通常无害）: {e}")

    ctx.loop.close()


# ============== 主生成器 ==============

def generate_chat_stream(ctx: ChatStreamContext) -> Iterator[str]:
    """SSE 流式聊天生成器（组合 ``_init_stream`` → ``_process_chunks`` → ``_cleanup_stream``）。

    取代原 ``ChatStreamView.post().generate()`` 闭包，职责清晰：
    - 初始化 → 处理 → 清理 三阶段显式分离
    - 异常处理统一在主生成器，子函数只关注本职
    - ``[DONE]`` 事件由 finally 块统一发送

    Args:
        ctx: 流式上下文，封装 request/data/共享状态

    Yields:
        SSE 格式字符串（``"data: ...\\n\\n"``）
    """
    yield from _init_stream(ctx)
    try:
        yield from _process_chunks(ctx)
    except Exception as e:
        logger.error(f"流式处理出错: {e!s}", exc_info=True)
        from Django_xm.apps.ai_engine.services.exceptions import classify_exception
        classified = classify_exception(e)
        yield sse_error_event(
            code=str(int(ErrorCode.SERVER_ERROR)),
            message=classified.user_message,
        )
    finally:
        yield from _cleanup_stream(ctx)
        yield "data: [DONE]\n\n"
