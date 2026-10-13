"""时间窗合流节流器（可复用单点实现，全项目唯一）。

下沉自 ``apps/chat/services/stream_broadcast.py``（eliminate-priority-debts 阶段 2）：
research 链 STREAM_SUBAGENT_CONTENT 全量快照节流需复用同一实现，而 research 仅经
common 门面消费共享设施（禁止 research → chat.services 直连边），故将本纯无依赖
工具类下沉 common；chat 链 reasoning 节流（D2）与子代理正文节流（阶段 2）均消费此类。

合流语义：窗口内的多次 ``submit`` 仅保留最新待发值——调用方保证 payload
为全量快照（前端消费为替换语义），丢弃中间帧不影响最终状态，最终一致性
由 ``flush``（边界强制冲刷）保证，节流不丢最终态。

节奏（leading edge 模式，与既有 content_state chunk 节流语义一致）：
- 窗口空闲（距上次广播 >= min_interval）时首个事件立即广播；
- 窗口内的后续事件只更新 pending（合流），不广播；
- ``flush`` 显式冲刷 pending（流收尾 / 语义边界事件 / 完成态事件前）。
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any


class TimeWindowThrottle:
    """时间窗合流节流器（可复用单点实现，D2 / eliminate-priority-debts 阶段 2）。

    Attributes:
        min_interval: 最小广播间隔（秒）。
        broadcaster: 广播回调 ``async fn(payload) -> None``，由接线方构造
            （捕获 session_id / message_id 等 payload 上下文）。
        clock: 单调时钟（默认运行时解析 time.monotonic，可注入替身便于测试）。
    """

    def __init__(
        self,
        min_interval: float,
        broadcaster: Callable[[Any], Awaitable[None]],
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._min_interval = min_interval
        self._broadcaster = broadcaster
        self._clock = clock if clock is not None else time.monotonic
        # -inf：首个事件必满足窗口条件（leading edge 立即广播），不受
        # monotonic 起点影响（Windows 上起点不定，0.0 基线有首包吞没风险）
        self._last_sent: float = float("-inf")
        self._pending: Any = None

    async def submit(self, payload: Any) -> None:
        """提交一个全量快照：合流为最新 pending，窗口空闲时立即广播。"""
        self._pending = payload
        if self._clock() - self._last_sent >= self._min_interval:
            await self._flush()

    async def flush(self) -> None:
        """强制冲刷窗口内未广播的 pending（边界红线：最终态必须送达）。幂等。"""
        await self._flush()

    async def _flush(self) -> None:
        payload = self._pending
        if payload is None:
            return
        self._pending = None
        self._last_sent = self._clock()
        await self._broadcaster(payload)
