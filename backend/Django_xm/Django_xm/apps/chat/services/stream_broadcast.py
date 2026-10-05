"""chat 流式执行事件 → WebSocket 广播桥。

chat agent 由 FastAPI 执行服务单协程运行，执行事件经 ``publish_stream_event``
统一广播到 WebSocket 会话频道，供触发/非触发浏览器消费；另承载正文尾部
重叠去重（``find_content_overlap``，全项目唯一权威实现，恢复轮拼接防重复）。

工具调用生命周期事件不再经本模块广播：已收敛为单一管线
（``apps.tools.tool_event_extractor`` 归一化 + ``common.tool_event_publisher``
发布编排），由 ``stream.loop._process_chunk``（主代理）与
``chat_service._handle_subagent_tool_event``（子代理）直接发布。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

# error 事件兜底文案/错误码（A1.2）：产出点未携带时回退，保证用户始终可见可行动提示
_DEFAULT_ERROR_CODE = "AGENT_EXECUTION_ERROR"
_DEFAULT_ERROR_MESSAGE = "执行失败，请稍后重试"

# ── reasoning 时间窗合流节流（D2 / audit P2-8 PF-01）──
# 取值依据：与正文快照节流间隔（chunk 分支 >= 0.5s）对齐——同一"流式 UI 感知
# 阈值"两通道统一；推理文本为辅助信息，感知敏感度低于正文，500ms 无可感知滞后；
# 收益：thinking token 级 chunk（10-50 chunk/s）→ 2 次/s 广播，Redis Lua 写入
# 与 group_send 降 ~95%，且合流后 payload 仍为全量快照（前端替换语义零改动）。
REASONING_BROADCAST_INTERVAL = 0.5

# 语义边界事件：到达即冲刷 reasoning 窗口（流形态切换，推理阶段结束）。
# chunk 刻意不在列：正文 chunk 高频到达，会令推理节流失效；推理尾部由
# 完成态事件（duration>0 / finished）或流收尾显式 flush 兜底送达。
_REASONING_FLUSH_SUBTYPES = frozenset({
    "approval", "interrupted", "model_fallback", "research_task_id",
    "deep_research", "error",
})


def _is_final_reasoning(data: Any) -> bool:
    """判定 reasoning 事件是否为完成态（必须绕过节流窗口精确送达）。

    完成态形态（strategy.on_loop_success 产出）：
    - NormalStreamStrategy：data.finished=True（全量终值）；
    - DeepThinkingStreamStrategy：data.duration>0（携带实际思考秒数）。
    进行中事件恒 duration=0 且无 finished（stream_chunk_processors /
    DeepThinkingStreamStrategy.on_loop_start）。
    """
    if not isinstance(data, dict):
        return False
    if data.get("finished"):
        return True
    try:
        return float(data.get("duration") or 0) > 0
    except (TypeError, ValueError):
        return False


class TimeWindowThrottle:
    """时间窗合流节流器（可复用单点实现，D2）。

    合流语义：窗口内的多次 ``submit`` 仅保留最新待发值——调用方保证 payload
    为全量快照（前端消费为替换语义），丢弃中间帧不影响最终状态，最终一致性
    由 ``flush``（边界强制冲刷）保证，节流不丢最终态。

    节奏（leading edge 模式，与既有 content_state chunk 节流语义一致）：
    - 窗口空闲（距上次广播 >= min_interval）时首个事件立即广播；
    - 窗口内的后续事件只更新 pending（合流），不广播；
    - ``flush`` 显式冲刷 pending（流收尾 / 语义边界事件 / 完成态事件前）。

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


def _normalize_error_event_data(event: dict[str, Any]) -> dict[str, Any]:
    """error 事件 data 规范化为 {code, message}（A1.2）。

    流内 error 事件产出形态为 ``{"type": "error", "content": "..."}``
    （agent_executor / slash_commands），与 WS error 帧规范（consumers.py /
    sse_utils.py 的 {code, message}）不一致。此处统一归一，前端按 data.message
    展示、data.code 预留差异化处理；message 兜底链：data.message → message →
    content → 默认文案，确保不透出裸异常结构。
    """
    raw = event.get("data")
    raw = raw if isinstance(raw, dict) else {}
    return {
        "code": str(raw.get("code") or event.get("code") or _DEFAULT_ERROR_CODE),
        "message": str(
            raw.get("message")
            or event.get("message")
            or event.get("content")
            or _DEFAULT_ERROR_MESSAGE
        ),
    }


def find_content_overlap(existing: str, new_chunk: str) -> int:
    """计算 new_chunk 前缀与 existing 后缀的最长重叠长度（尾部重叠去重核心判定）。

    业务等待挂起（wait_for_subagent）时挂起前 content 已落库（含流式中途的
    尾部字符），恢复轮 LLM 从 checkpoint 继续生成时会重新输出与该尾部重叠的
    内容。若直接 ``existing += new_chunk``，会出现「三个三个」类重复。

    规则：取 new_chunk 前缀与 existing 后缀的最大重叠（最长重叠后缀/前缀），
    无重叠返回 0。此函数为全项目唯一权威实现，stream_persistence 等
    持久化链路通过 import 复用，禁止复制实现。

    Args:
        existing: 已有累计正文
        new_chunk: 新到达的正文增量

    Returns:
        int: 最长重叠长度（0 表示无重叠）
    """
    if not new_chunk or not existing:
        return 0
    max_overlap = min(len(existing), len(new_chunk))
    for i in range(max_overlap, 0, -1):
        if existing[-i:] == new_chunk[:i]:
            return i
    return 0


def _merge_content_with_overlap(existing: str, new_chunk: str) -> str:
    """累积正文并做尾部重叠去重（恢复轮拼接防重复）。

    重叠判定（find_content_overlap）与拼接逻辑为全项目唯一实现：
    - 取 new_chunk 前缀与 existing 后缀的最大重叠，拼接时去掉重叠部分；
    - 无重叠时退化为普通追加；空 chunk 幂等返回 existing。
    """
    if not new_chunk:
        return existing
    if not existing:
        return new_chunk
    overlap = find_content_overlap(existing, new_chunk)
    return existing + new_chunk[overlap:]


async def _publish_reasoning_event(
    payload_data: dict[str, Any],
    session_id: str,
    message_id: int | None,
    reasoning_state: dict[str, Any] | None,
) -> None:
    """reasoning 事件发布（D2 节流接线 + 快照不入回放窗口）。

    - ``reasoning_state`` 为 None：保持旧行为直接广播（兼容既有调用方/测试）；
    - 否则经惰性创建的 ``TimeWindowThrottle``（挂在 reasoning_state["throttle"]）
      时间窗合流：窗口内多次广播合并为最新全量快照单次广播；
    - 完成态推理（finished=True / duration>0）不进窗口：先 flush 保序冲刷
      窗口内中间态，完成态本身立即广播（前端 AiReasoning isStreaming 判定
      依赖完成态事件精确送达）；
    - 中间态推理快照 persist_history=False（仅实时广播，不占用回放历史
      席位，D2/PF-02）；完成态推理正常入历史（重连回放恢复最终推理文本）。
    """
    from Django_xm.common.event_schema import EventSource, EventType
    from Django_xm.common.realtime_events import publish_event

    is_final = _is_final_reasoning(payload_data)

    async def _broadcast_one(data: dict[str, Any], *, persist_history: bool) -> None:
        await publish_event(
            EventType.STREAM_REASONING,
            {
                "source": EventSource.CHAT,
                "source_id": session_id,
                "message_id": str(message_id) if message_id else None,
                "data": data,
            },
            session_id=session_id,
            persist_history=persist_history,
        )

    if reasoning_state is None:
        try:
            await _broadcast_one(payload_data, persist_history=is_final)
        except Exception as e:
            logger.warning(
                f"广播 reasoning 失败: session_id={session_id}, "
                f"message_id={message_id}, error={e}"
            )
        return

    throttle = reasoning_state.get("throttle")
    if throttle is None:
        # 中间态经节流器广播：恒不入历史（快照治理）
        throttle = TimeWindowThrottle(
            min_interval=REASONING_BROADCAST_INTERVAL,
            broadcaster=lambda data: _broadcast_one(data, persist_history=False),
        )
        reasoning_state["throttle"] = throttle

    if is_final:
        try:
            await throttle.flush()  # 先冲刷窗口内中间态（保序，persist=False）
            await _broadcast_one(payload_data, persist_history=True)  # 完成态入历史
        except Exception as e:
            logger.warning(
                f"广播 reasoning 完成态失败: session_id={session_id}, "
                f"message_id={message_id}, error={e}"
            )
        return

    try:
        await throttle.submit(payload_data)
    except Exception as e:
        logger.warning(
            f"广播 reasoning 失败: session_id={session_id}, "
            f"message_id={message_id}, error={e}"
        )


async def publish_stream_event_content_update(
    content: str,
    session_id: str,
    message_id: int | None,
) -> None:
    """广播正文快照 STREAM_CONTENT_UPDATE（D2/PF-02：persist_history=False）。

    快照治理：正文快照仅实时广播（在线浏览器替换语义渲染），不写入回放
    历史 List——每频道历史席位（LTRIM 500 条）留给 tool_call/approval/
    message_added 等关键事件。重连回放的正文恢复路径不受影响：落库终态经
    persist_stream_result._broadcast_message_updated 广播 message_updated
    （正常入历史，含最终全量 content）。
    """
    from Django_xm.common.event_schema import EventSource, EventType
    from Django_xm.common.realtime_events import publish_event

    await publish_event(
        EventType.STREAM_CONTENT_UPDATE,
        {
            "source": EventSource.CHAT,
            "source_id": session_id,
            "message_id": str(message_id) if message_id else None,
            "data": {
                "content": content,
            },
        },
        session_id=session_id,
        persist_history=False,
    )


async def publish_stream_event(
    event: dict[str, Any],
    session_id: str,
    message_id: int | None = None,
    content_state: dict[str, Any] | None = None,
    subagent_contents: dict[str, dict[str, str]] | None = None,
    reasoning_state: dict[str, Any] | None = None,
) -> None:
    """将执行事件同步到 WebSocket 会话频道。

    - reasoning：经 ``TimeWindowThrottle`` 时间窗合流节流广播 STREAM_REASONING
      （D2/PF-01；未接 reasoning_state 时保持直接广播旧行为），中间态快照
      不入回放历史；完成态推理绕过窗口精确送达并入历史
    - sources/suggestions/context/deep_research/approval/interrupted：
      直接广播 STREAM_* 事件
    - error（A1.2）：经 STREAM_EVENT + event_subtype='error' 广播，data 规范化
      为 {code, message}（对齐 WS error 帧规范），保证 chat 链失败对用户可见
    - retry / timeout_warning（E1/F5）：经 STREAM_EVENT + event_subtype 透传
      （data 保形，不冲刷 reasoning 窗口），执行可观测事件不再被桥静默丢弃
    - chunk：累积 content，500ms 节流广播 STREAM_CONTENT_UPDATE（快照不入
      回放历史，D2/PF-02）
    - 其他事件类型：静默跳过

    工具调用事件（tool/tool_result）不再经本函数；其发布由
    ``common.tool_event_publisher.publish_tool_lifecycle_event`` 编排。

    Args:
        event: 执行事件 dict
        session_id: 会话 ID
        message_id: 消息 ID
        content_state: chunk 节流累积状态
        subagent_contents: 保留参数（历史调用兼容；工具事件 position 采集已由
            编排函数在各调用点完成，本函数不再使用）。
        reasoning_state: reasoning 节流状态容器（D2）。由调用方按会话执行
            生命周期创建（chat_executor_core，与 content_state 同模式）；
            节流器惰性创建并挂在该 dict 的 "throttle" 键下，流收尾由调用方
            显式 flush。None 时不节流（旧行为）。
    """
    if not isinstance(event, dict) or not session_id:
        return

    from Django_xm.common.event_schema import EventSource, EventType
    from Django_xm.common.realtime_events import publish_event

    event_type_str = event.get("type", "")

    # ── chunk 事件：累积 + 节流广播（快照不入回放历史，D2/PF-02）──
    if event_type_str == "chunk":
        if content_state is not None:
            content_state["content"] = _merge_content_with_overlap(
                content_state.get("content", ""), event.get("content", "")
            )
            now = time.monotonic()
            if now - content_state.get("last_broadcast", 0) >= 0.5:
                content_state["last_broadcast"] = now
                try:
                    await publish_stream_event_content_update(
                        content_state["content"], session_id, message_id
                    )
                except Exception as e:
                    logger.warning(
                        f"广播 STREAM_CONTENT_UPDATE 失败: event_type=chunk, "
                        f"session_id={session_id}, message_id={message_id}, error={e}"
                    )
        return

    # ── reasoning（节流合流）──────────────────────────────
    if event_type_str == "reasoning":
        await _publish_reasoning_event(
            event.get("data", event), session_id, message_id, reasoning_state
        )
        return

    # ── 语义边界事件：冲刷 reasoning 窗口（D2 边界红线）────
    # approval/error/interrupted 等意味着流形态切换，推理阶段结束；
    # chunk 不冲刷（高频到达会令节流失效，见 _REASONING_FLUSH_SUBTYPES 注释）。
    if reasoning_state is not None and event_type_str in _REASONING_FLUSH_SUBTYPES:
        throttle = reasoning_state.get("throttle")
        if throttle is not None:
            try:
                await throttle.flush()
            except Exception as e:
                logger.warning(f"冲刷 reasoning 节流窗口失败: session_id={session_id}, error={e}")

    # ── sources / suggestions / context / deep_research / approval ──
    # A1.2：error 纳入广播白名单（此前流内 error 事件被静默丢弃 → chat 链失败
    # 对用户不可见，审计 F1-1）。经 STREAM_EVENT + event_subtype='error' 通道
    # 广播，data 规范化为 {code, message}（对齐 WS error 帧规范）。
    # E1/F5：retry / timeout_warning 纳入广播白名单（此前执行服务化后被静默
    # 丢弃 → AgentExecutor 的重试提示 agent_executor.py:194-202 与软超时警告
    # :276-282 在 WS 桥上不可达）。两者属"流继续"类事件（不同于 error/approval
    # 的流形态切换），不冲刷 reasoning 节流窗口（不加入 _REASONING_FLUSH_SUBTYPES）；
    # data 原样保形透传（无 error 帧规范化语义），前端暂无消费点——经
    # _handleGenericStreamEvent 未知子类型路径安全忽略，可观测性在 WS 层恢复。
    # reasoning 已在上分支节流处理，不再进入白名单。
    if event_type_str in (
        "sources", "suggestions", "context",
        "deep_research", "approval", "interrupted",
        "model_fallback", "research_task_id", "error",
        "retry", "timeout_warning",
    ):
        try:
            stream_event_type_map = {
                "sources": EventType.STREAM_SOURCES,
                "suggestions": EventType.STREAM_SUGGESTIONS,
                "context": EventType.STREAM_CONTEXT,
                # 深度研究模式：聊天流式连接因审批中断结束，广播 stream_interrupted，
                # 前端 handleStreamInterrupted 据此设置 researchTaskId + INTERRUPTED 状态，
                # 使非触发浏览器也能显示"研究进行中 + 查看详情"卡片。
                "interrupted": EventType.STREAM_INTERRUPTED,
            }
            evt_type = stream_event_type_map.get(event_type_str, EventType.STREAM_EVENT)
            payload_data = event.get("data", event)
            if event_type_str == "error":
                payload_data = _normalize_error_event_data(event)
            payload = {
                "source": EventSource.CHAT,
                "source_id": session_id,
                "message_id": str(message_id) if message_id else None,
                "data": payload_data,
            }
            # ARCH-19：包装为 STREAM_EVENT 的原始执行事件子类型（deep_research /
            # research_task_id / model_fallback / approval）注入 payload 顶层，
            # 前端 _handleGenericStreamEvent 据此分流；有独立 EventType 的
            # 子事件（sources/suggestions/context 等）不注入该字段，
            # 避免污染前端 fieldMap 分流。
            if evt_type is EventType.STREAM_EVENT:
                payload["event_subtype"] = event_type_str
            await publish_event(
                evt_type,
                payload,
                session_id=session_id,
            )
        except Exception as e:
            logger.warning(
                f"广播事件失败: event_type={event_type_str}, "
                f"session_id={session_id}, message_id={message_id}, error={e}"
            )
