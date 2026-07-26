"""stream 子包 — 统一流式循环实现

将 chat_service 中重复的流式循环逻辑提取为单一底层模块，
两种模式（普通 agent / 深度思考）通过策略对象注入差异点，共用同一套实现。

模块组成：
- context.StreamContext        : 流式循环可变状态封装
- strategy.*StreamStrategy     : 模式策略（差异点注入）
- loop.run_stream_loop         : 核心流式循环（单一底层）
- interrupt.finalize_interrupt : 审批中断统一收尾
- finalizer.finalize_stream    : 循环后统一处理（元数据驱动）
- resilience.ResilienceRunner  : 韧性包装器（重试/降级/超时/回退）
- fallback.FallbackStreamService : 无工具纯对话回退

公开 API：
    from Django_xm.apps.chat.services.stream import (
        StreamContext,
        NormalStreamStrategy,
        DeepThinkingStreamStrategy,
        run_stream_loop,
        finalize_interrupt,
        finalize_stream,
        ResilienceRunner,
        FallbackStreamService,
    )
"""

from .context import StreamContext
from .fallback import FallbackStreamService
from .finalizer import finalize_stream
from .interrupt import finalize_interrupt
from .loop import run_stream_loop
from .resilience import ResilienceRunner
from .strategy import (
    BaseStreamStrategy,
    DeepThinkingStreamStrategy,
    NormalStreamStrategy,
)

__all__ = [
    "StreamContext",
    "BaseStreamStrategy",
    "NormalStreamStrategy",
    "DeepThinkingStreamStrategy",
    "run_stream_loop",
    "finalize_interrupt",
    "finalize_stream",
    "ResilienceRunner",
    "FallbackStreamService",
]
