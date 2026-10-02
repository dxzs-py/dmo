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
from typing import Any

logger = logging.getLogger(__name__)


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


async def publish_stream_event(
    event: dict[str, Any],
    session_id: str,
    message_id: int | None = None,
    content_state: dict[str, Any] | None = None,
    subagent_contents: dict[str, dict[str, str]] | None = None,
) -> None:
    """将执行事件同步到 WebSocket 会话频道。

    - reasoning/sources/suggestions/context/deep_research/approval/interrupted：
      直接广播 STREAM_* 事件
    - chunk：累积 content，500ms 节流广播 STREAM_CONTENT_UPDATE
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
    """
    if not isinstance(event, dict) or not session_id:
        return

    from Django_xm.common.event_schema import EventSource, EventType
    from Django_xm.common.realtime_events import publish_event

    event_type_str = event.get("type", "")

    # ── chunk 事件：累积 + 节流广播 ──
    if event_type_str == "chunk":
        if content_state is not None:
            content_state["content"] = _merge_content_with_overlap(
                content_state.get("content", ""), event.get("content", "")
            )
            now = time.monotonic()
            if now - content_state.get("last_broadcast", 0) >= 0.5:
                content_state["last_broadcast"] = now
                try:
                    await publish_event(
                        EventType.STREAM_CONTENT_UPDATE,
                        {
                            "source": EventSource.CHAT,
                            "source_id": session_id,
                            "message_id": str(message_id) if message_id else None,
                            "data": {
                                "content": content_state["content"],
                            },
                        },
                        session_id=session_id,
                    )
                except Exception as e:
                    logger.warning(
                        f"广播 STREAM_CONTENT_UPDATE 失败: event_type=chunk, "
                        f"session_id={session_id}, message_id={message_id}, error={e}"
                    )
        return

    # ── reasoning / sources / suggestions / context / deep_research / approval ──
    if event_type_str in (
        "reasoning", "sources", "suggestions", "context",
        "deep_research", "approval", "interrupted",
        "model_fallback", "research_task_id",
    ):
        try:
            stream_event_type_map = {
                "reasoning": EventType.STREAM_REASONING,
                "sources": EventType.STREAM_SOURCES,
                "suggestions": EventType.STREAM_SUGGESTIONS,
                "context": EventType.STREAM_CONTEXT,
                # 深度研究模式：聊天流式连接因审批中断结束，广播 stream_interrupted，
                # 前端 handleStreamInterrupted 据此设置 researchTaskId + INTERRUPTED 状态，
                # 使非触发浏览器也能显示"研究进行中 + 查看详情"卡片。
                "interrupted": EventType.STREAM_INTERRUPTED,
            }
            evt_type = stream_event_type_map.get(event_type_str, EventType.STREAM_EVENT)
            payload = {
                "source": EventSource.CHAT,
                "source_id": session_id,
                "message_id": str(message_id) if message_id else None,
                "data": event.get("data", event),
            }
            # ARCH-19：包装为 STREAM_EVENT 的原始执行事件子类型（deep_research /
            # research_task_id / model_fallback / approval）注入 payload 顶层，
            # 前端 _handleGenericStreamEvent 据此分流；未包装进 STREAM_EVENT 的
            # 子事件（reasoning/sources 等）有独立 EventType，不注入该字段，
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
