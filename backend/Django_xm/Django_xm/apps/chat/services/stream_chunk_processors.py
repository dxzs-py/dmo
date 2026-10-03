"""流式消息块处理（仅正文流式与思考内容提取）。

工具调用生命周期事件已收敛为单一管线，本模块不再参与：
- 归一化：``apps.tools.tool_event_extractor.ToolEventExtractor``（有状态唯一提取器）
- 发布编排：``common.tool_event_publisher.publish_tool_lifecycle_event``
由 ``stream.loop._process_chunk`` 调用（含重复调用检测 / 计数 / ToolUsageGuard）。

本模块仅保留：
- ``_sync_pending_to_stream_state``：将 _pending_content 同步到共享 stream_state
- ``_handle_ai_message_chunk``：处理 AIMessage/AIMessageChunk（正文流式 + 思考 + 中间内容缓冲）
- ``process_stream_chunk``：流式 chunk 分发入口（消息类型分派）

思考内容提取统一使用 ai_engine 单源（G1 合并）：
``extract_thinking_content`` 导入自 ``apps.ai_engine.services.thinking``，
本模块不再持有副本。
"""

import logging
from typing import Any

from langchain_core.messages import AIMessage

from Django_xm.apps.ai_engine.services.thinking import extract_thinking_content
from Django_xm.common.messages import content_to_str

logger = logging.getLogger(__name__)


def _sync_pending_to_stream_state(accumulated_reasoning: dict[str, Any]):
    """将 _pending_content 同步到 data['_stream_state']，确保 generate() finally 能兜底刷新。

    当 async generator 被强制关闭（aclose/GeneratorExit）时，
    try/except 之后的代码不会执行，导致 _pending_content 无法通过正常路径刷新。
    通过共享状态字典，views_chat.py 的 generate() finally 块可以兜底刷新。
    """
    stream_state = accumulated_reasoning.get("_stream_state")
    if stream_state is not None:
        stream_state["pending_content"] = accumulated_reasoning.get("_pending_content", "")


def _handle_ai_message_chunk(
    message: AIMessage,
    lcp_func,
    current_message_content: str,
    accumulated_reasoning: dict[str, str],
    mode: str,
    enable_deep_thinking: bool = False,
):
    """处理 AIMessage/AIMessageChunk：思考内容提取 + 正文流式发送。

    工具调用事件的提取与发布已移出本模块（统一由 loop._process_chunk 经
    ToolEventExtractor + publish_tool_lifecycle_event 处理）。
    """
    # 统一思考内容提取（兼容 DeepSeek/Ollama/Anthropic）
    # 仅当深度思考启用时才发送 reasoning 事件，避免关闭思考后仍显示"推理中"
    thinking_text = extract_thinking_content(message)
    if thinking_text:
        if accumulated_reasoning is not None:
            prev = accumulated_reasoning.get("content", "") or ""
            accumulated_reasoning["content"] = prev + thinking_text
        if mode in ("agent", "chat") and enable_deep_thinking:
            reasoning_content = accumulated_reasoning.get("content", "") or ""
            yield {
                "type": "reasoning",
                "data": {
                    "content": reasoning_content,
                    "duration": 0,
                    "source": "deep_thinking" if enable_deep_thinking else "model_intrinsic",
                },
            }

    # 内容发送策略：
    # - agent 模式 + 有 tool_calls/tool_call_chunks：缓冲 content（中间文本，不应发送）
    # - agent 模式 + 无 tool_calls：直接流式发送 content（最终回答）
    # - 非 agent 模式：直接发送
    if message.content:
        has_tool_calls = bool(getattr(message, "tool_calls", None)) or bool(
            getattr(message, "tool_call_chunks", None)
        )
        if mode == "agent" and has_tool_calls:
            # agent 模式 + 有 tool_calls：缓冲 content，不立即发送
            # 这是模型在决定调用工具前的"自言自语"，不应发送给用户
            if accumulated_reasoning is not None:
                prev_pending = accumulated_reasoning.get("_pending_content", "")
                # message.content 可能是 str 或 list[ContentBlock]，统一转为 str 拼接
                # 流式 token 连续拼接，sep="" 避免在片段间插入空格
                chunk_text = content_to_str(message.content, sep="")
                accumulated_reasoning["_pending_content"] = prev_pending + chunk_text
                _sync_pending_to_stream_state(accumulated_reasoning)
        else:
            # 非 agent 模式 或 agent 模式无 tool_calls：直接流式发送
            if lcp_func:
                lcp = lcp_func(current_message_content, message.content)
            else:
                lcp = 0
            if lcp < len(message.content):
                new_content = message.content[lcp:]
                yield {"type": "chunk", "content": new_content}


def process_stream_chunk(
    chunk,
    current_message_content: str,
    lcp_func=None,
    accumulated_reasoning: dict[str, str] | None = None,
    mode: str = "agent",
    enable_deep_thinking: bool = False,
):
    """流式 chunk 分派入口（仅 AIMessage 正文/思考；ToolMessage 事件由 extractor 处理）。"""
    assert accumulated_reasoning is not None  # noqa: S101
    if chunk is None:
        return

    if isinstance(chunk, tuple) and len(chunk) == 2:
        message, _ = chunk
    else:
        message = chunk

    if message is None:
        return

    if isinstance(message, AIMessage):
        # 过滤终止信号消息（ContextManagerMiddleware 注入的终止消息，不应展示给用户）
        additional_kwargs = getattr(message, "additional_kwargs", {}) or {}
        if additional_kwargs.get("_termination_signal"):
            logger.debug(f"过滤终止信号消息: {str(message.content)[:50]}")
            return

        # agent 模式下，当检测到 tool_calls 时清除缓冲的中间内容
        # （这些内容是模型在决定调用工具前的"自言自语"，不应发送给用户）
        tool_calls = getattr(message, "tool_calls", [])
        tool_call_chunks = getattr(message, "tool_call_chunks", None)
        if mode == "agent" and (tool_calls or tool_call_chunks) and accumulated_reasoning is not None:
            if accumulated_reasoning.get("_pending_content"):
                logger.debug(
                    f"Agent 模式: 检测到 tool_calls，清除缓冲的中间内容 "
                    f"({len(accumulated_reasoning['_pending_content'])} 字符)"
                )
                accumulated_reasoning["_pending_content"] = ""
                _sync_pending_to_stream_state(accumulated_reasoning)

        yield from _handle_ai_message_chunk(
            message,
            lcp_func,
            current_message_content,
            accumulated_reasoning,
            mode,
            enable_deep_thinking,
        )
