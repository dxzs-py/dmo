"""
流式聊天辅助工具方法

从 chat_service.py 拆分出的通用流式处理逻辑：
- 消息块解析
- 用量/Token 更新
- 上下文信息构建
- 深度思考内容提取（兼容 DeepSeek/Ollama/Anthropic）
"""

import json as _json
import logging
import re as _re
import time
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from Django_xm.common.event_schema import EventSource, EventType
from Django_xm.common.tool_call_lifecycle import ToolCallContext, service

logger = logging.getLogger(__name__)


def _sync_pending_to_stream_state(accumulated_reasoning: dict[str, str]):
    """将 _pending_content 同步到 data['_stream_state']，确保 generate() finally 能兜底刷新。

    当 async generator 被强制关闭（aclose/GeneratorExit）时，
    try/except 之后的代码不会执行，导致 _pending_content 无法通过正常路径刷新。
    通过共享状态字典，views_chat.py 的 generate() finally 块可以兜底刷新。
    """
    stream_state = accumulated_reasoning.get("_stream_state")
    if stream_state is not None:
        stream_state["pending_content"] = accumulated_reasoning.get("_pending_content", "")


def extract_thinking_content(chunk, provider_id: str = "") -> str | None:
    """从流式 chunk 中提取思考内容，兼容多种 Provider 格式。

    - DeepSeek: additional_kwargs["reasoning_content"]
    - Ollama (Qwen3/R1): additional_kwargs["reasoning_content"]（langchain-ollama 自动处理）
    - Anthropic Claude: content blocks 中 type="thinking" 的块
    """
    # 1. DeepSeek / Ollama: additional_kwargs["reasoning_content"]
    additional_kwargs = getattr(chunk, "additional_kwargs", {})
    reasoning_content = additional_kwargs.get("reasoning_content")
    if reasoning_content and isinstance(reasoning_content, str):
        return reasoning_content

    # 2. Anthropic Claude: thinking content blocks
    # ChatAnthropic 将思考内容放在 content blocks 中，type="thinking"
    content = getattr(chunk, "content", None)
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "thinking":
                text = block.get("text", "")
                if text:
                    return text
            # LangChain ContentBlock 对象
            if hasattr(block, "type") and block.type == "thinking":
                text = getattr(block, "text", "")
                if text:
                    return text

    return None


_STATE_TO_STATUS = {
    "input-available": "running",
    "output-available": "completed",
    "output-error": "failed",
}


def _map_state_to_status(state: str) -> str:
    return _STATE_TO_STATUS.get(state, "running")


def _extract_tool_params(tool_call: dict) -> dict:
    args = tool_call.get("args")
    if isinstance(args, dict) and args:
        return args

    function_info = tool_call.get("function", {}) or {}
    if function_info:
        arguments_str = function_info.get("arguments", "")
        if isinstance(arguments_str, str) and arguments_str.strip():
            try:
                return _json.loads(arguments_str)
            except (_json.JSONDecodeError, ValueError):
                pass
        if isinstance(arguments_str, dict) and arguments_str:
            return arguments_str

    arguments_str = tool_call.get("arguments") or ""
    if isinstance(arguments_str, str) and arguments_str.strip():
        try:
            return _json.loads(arguments_str)
        except (_json.JSONDecodeError, ValueError):
            pass

    if isinstance(arguments_str, dict) and arguments_str:
        return arguments_str

    return {}


def _fix_groq_tool_call(tool_call: dict[str, Any]) -> dict[str, Any]:
    raw_name = tool_call.get("name", "")
    if " " not in raw_name and "{" not in raw_name:
        return tool_call

    fixed = dict(tool_call)
    match = _re.match(r"^(\w+)\s*(\{.*\})?\s*$", raw_name, _re.DOTALL)
    if match:
        actual_name = match.group(1)
        inline_args_str = match.group(2)
        fixed["name"] = actual_name
        if inline_args_str:
            try:
                parsed_args = _json.loads(inline_args_str)
                existing_args = fixed.get("args") or {}
                if not existing_args or existing_args == {}:
                    fixed["args"] = parsed_args
            except _json.JSONDecodeError:
                pass
        logger.debug(f"Groq tool call 名称修复: '{raw_name}' -> '{actual_name}'")
    return fixed


def _try_parse_concatenated_json(s: str) -> dict | None:
    if not s or not s.strip():
        return None
    try:
        result = _json.loads(s)
        if isinstance(result, dict):
            return result
        return None
    except (_json.JSONDecodeError, ValueError):
        pass
    decoder = _json.JSONDecoder()
    last_valid = None
    pos = 0
    while pos < len(s):
        next_brace = s.find("{", pos)
        if next_brace == -1:
            break
        try:
            obj, end_pos = decoder.raw_decode(s, next_brace)
            if isinstance(obj, dict) and obj:
                last_valid = obj
            pos = end_pos
        except (_json.JSONDecodeError, ValueError):
            pos = next_brace + 1
    return last_valid


def _find_tool_call_key_by_index(tool_calls_map: dict[str, dict], index: int) -> str | None:
    for key, tc in tool_calls_map.items():
        if tc.get("_index") == index:
            return key
    return None


def _migrate_key_if_needed(tool_calls_map: dict[str, dict], old_key: str, new_key: str) -> str:
    if old_key == new_key or new_key not in tool_calls_map:
        if old_key in tool_calls_map and old_key != new_key:
            tool_calls_map[new_key] = tool_calls_map.pop(old_key)
            return new_key
        return old_key
    if old_key in tool_calls_map and new_key in tool_calls_map:
        existing = tool_calls_map.pop(old_key)
        target = tool_calls_map[new_key]
        if not target.get("id") and existing.get("id"):
            target["id"] = existing["id"]
        if not target.get("name") and existing.get("name"):
            target["name"] = existing["name"]
        if not target.get("parameters") or target["parameters"] == {}:
            if existing.get("parameters") and existing["parameters"] != {}:
                target["parameters"] = existing["parameters"]
        if not target.get("_index") and existing.get("_index") is not None:
            target["_index"] = existing["_index"]
        return new_key
    return old_key


def _handle_ai_message_chunk(
    message: AIMessage,
    tool_calls_map: dict[str, dict],
    tool_call_count: dict[str, int],
    lcp_func,
    current_message_content: str,
    accumulated_reasoning: dict[str, str],
    tool_args_accumulator: dict[str, str],
    mode: str,
    enable_deep_thinking: bool = False,
):
    # ⚠️ 关键修复（与 tool_event_extractor.py / loop.py 一致）：
    # AIMessageChunk 是 AIMessage 的子类，其 ``.tool_calls`` 属性内部使用
    # ``parse_partial_json`` 解析 args，对不完整 JSON 可能返回非空但残缺的 dict
    # （如 ``'{"file_path": "/san'`` 被解析为 ``{"file_path": "/san"}``）。
    # 对 chunk，``.tool_calls`` 路径的参数不完整，不应直接 yield ``tool`` 事件
    # 或更新 ``tool_calls_map`` 的 parameters 字段。
    # 修复：对 AIMessageChunk，``.tool_calls`` 路径仅用于 dedup key 追踪 / guard
    # 检查 / counting（这些不依赖参数完整性），但不 yield ``tool`` 事件和不更新
    # parameters。参数提取与 ``tool`` 事件由 ``tool_call_chunks`` 路径处理（累积 +
    # 严格 ``json.loads``）。
    is_chunk = isinstance(message, AIMessageChunk)
    tool_calls = getattr(message, "tool_calls", [])
    tool_call_chunks = getattr(message, "tool_call_chunks", None)
    if tool_calls:
        for raw_tool_call in tool_calls:
            tool_call = _fix_groq_tool_call(raw_tool_call)
            tool_id = tool_call.get("id") or ""
            tool_name = tool_call.get("name") or ""

            if not tool_name and not tool_id:
                continue

            if tool_name and tool_id:
                name_key = tool_name
                if name_key in tool_calls_map and tool_id not in tool_calls_map:
                    _migrate_key_if_needed(tool_calls_map, name_key, tool_id)
                dedup_key = tool_id
            elif tool_id:
                dedup_key = tool_id
            else:
                dedup_key = tool_name

            if dedup_key in tool_calls_map:
                if tool_id and not tool_calls_map[dedup_key].get("id"):
                    tool_calls_map[dedup_key]["id"] = tool_id
                if tool_name and not tool_calls_map[dedup_key].get("name"):
                    tool_calls_map[dedup_key]["name"] = tool_name
                # 对 AIMessageChunk：不更新 parameters（args 可能不完整），
                # 不 yield tool 事件（由 tool_call_chunks 路径在参数完整时 yield）
                if not is_chunk:
                    updated_params = _extract_tool_params(tool_call)
                    if updated_params:
                        tool_calls_map[dedup_key]["parameters"] = updated_params
                        tool_info = dict(tool_calls_map[dedup_key])
                        tool_info["status"] = _map_state_to_status(tool_info.get("state", ""))
                        yield {"type": "tool", "data": tool_info}
                continue

            if tool_call_count is not None:
                if tool_name not in tool_call_count:
                    tool_call_count[tool_name] = 0
                tool_call_count[tool_name] += 1
                if tool_call_count[tool_name] > 8:
                    logger.info(f"工具 {tool_name} 被调用了 {tool_call_count[tool_name]} 次")

                # ToolUsageGuard 检查：去重 / 软警告 / 渐进式阻断
                # 不再使用 "≥3 次就 force_terminate" 的硬中断策略
                # 改为：去重时返回 DEDUP 响应、警告时 yield warning、阻断时 yield blocked
                # 上层 chat_service 收到这些事件后再决定如何处理 ToolMessage
                # 新版：所有工具都进入通用 dedup + 通用 loop 流程
                try:
                    from Django_xm.apps.ai_engine.services.tool_usage_guard import (
                        ToolUsageDecision,
                        ToolUsageStatus,
                        get_tool_usage_guard,
                    )

                    # 拼装参数用于 guard 检查
                    # 对 AIMessageChunk：参数可能不完整（parse_partial_json 返回部分 dict），
                    # guard 检查基于不完整参数可能误判，因此 chunk 场景下参数为空时跳过
                    current_params = (
                        tool_calls_map[dedup_key].get("parameters", {})
                        if dedup_key in tool_calls_map
                        else (_extract_tool_params(tool_call) if not is_chunk else {})
                    )
                    if not current_params and not is_chunk:
                        current_params = _extract_tool_params(tool_call) or {}
                    # 参数为空时跳过去重检查：流式传输中参数可能尚未解析完成，
                    # 空参数会导致 resource_key='[]'，所有调用被误判为"同一资源"
                    if current_params:
                        decision = get_tool_usage_guard().check(
                            tool_name=tool_name,
                            args=current_params,
                            thread_id="default",  # 流式上下文不直接拿 thread_id，使用 default
                        )
                    else:
                        decision = ToolUsageDecision(status=ToolUsageStatus.ALLOW, reason="params not yet available")
                    # 抽取通用事件字段（所有工具都有 resource_key / payload_preview）
                    sse_data = decision.sse_event.get("data", {}) if decision.sse_event else {}
                    common_data = {
                        "tool_name": tool_name,
                        "tool_id": tool_id,
                        "resource_key": sse_data.get("resource_key", ""),
                        "payload_preview": sse_data.get("payload_preview", ""),
                        "reason": sse_data.get("reason", ""),
                        "message": sse_data.get("message", ""),
                    }
                    if decision.status.value == "dedup" and decision.short_circuit_response:
                        # 短时相同内容：直接以 DEDUP 状态传递，由 chat_service
                        # 注入 ToolMessage 让模型知道本次被跳过
                        common_data["short_circuit_response"] = decision.short_circuit_response
                        yield {
                            "type": "tool_usage_dedup",
                            "data": common_data,
                        }
                        if decision.sse_event:
                            yield decision.sse_event
                        # 跳过本工具的后续累积逻辑
                        continue
                    if decision.status.value == "block" and decision.short_circuit_response:
                        # 渐进式阻断：返回阻断原因，仍由模型决定是否继续
                        common_data["short_circuit_response"] = decision.short_circuit_response
                        yield {
                            "type": "tool_usage_blocked",
                            "data": common_data,
                        }
                        if decision.sse_event:
                            yield decision.sse_event
                        # 跳过本工具的后续累积逻辑
                        continue
                    if decision.sse_event:
                        # 软警告
                        yield decision.sse_event
                except Exception as guard_err:
                    logger.debug(f"ToolUsageGuard 检查失败（不影响工具执行）: {guard_err}")

            # 对 AIMessageChunk：创建 tool_calls_map 条目（空 parameters），
            # 但不 yield tool 事件（由 tool_call_chunks 路径在参数完整时 yield）
            tool_info = {
                "id": tool_id,
                "name": tool_name,
                "type": f"tool-call-{tool_name}",
                "state": "input-available",
                "status": "running",
                "parameters": {} if is_chunk else _extract_tool_params(tool_call),
                "result": None,
                "error": None,
            }
            tool_calls_map[dedup_key] = tool_info
            if not is_chunk:
                yield {"type": "tool", "data": tool_info}

    if tool_call_chunks and tool_args_accumulator is not None:
        for tc_chunk in tool_call_chunks:
            if isinstance(tc_chunk, dict):
                tc_id = tc_chunk.get("id") or ""
                tc_name = tc_chunk.get("name") or ""
                tc_args_str = tc_chunk.get("args") or ""
                tc_index = tc_chunk.get("index")
            else:
                tc_id = getattr(tc_chunk, "id", "") or ""
                tc_name = getattr(tc_chunk, "name", "") or ""
                tc_args_str = getattr(tc_chunk, "args", "") or ""
                tc_index = getattr(tc_chunk, "index", None)

            dedup_key = None
            if tc_id and tc_id in tool_calls_map:
                dedup_key = tc_id
            elif tc_id:
                if tc_name and tc_name in tool_calls_map and tc_id not in tool_calls_map:
                    _migrate_key_if_needed(tool_calls_map, tc_name, tc_id)
                    if tc_name in tool_args_accumulator and tc_id not in tool_args_accumulator:
                        tool_args_accumulator[tc_id] = tool_args_accumulator.pop(tc_name)
                dedup_key = tc_id
            elif tc_index is not None:
                dedup_key = _find_tool_call_key_by_index(tool_calls_map, tc_index)
            elif tc_name:
                same_name_pending = [
                    k
                    for k, tc in tool_calls_map.items()
                    if tc.get("name") == tc_name and tc.get("state") == "input-available"
                ]
                if len(same_name_pending) == 1:
                    dedup_key = same_name_pending[0]
                elif not same_name_pending:
                    dedup_key = tc_name
                elif tc_index is not None:
                    dedup_key = f"{tc_name}_idx_{tc_index}"

            if not dedup_key:
                if tc_index is not None:
                    dedup_key = f"_auto_idx_{tc_index}"
                elif tc_args_str and tool_calls_map:
                    for key in list(tool_calls_map.keys())[::-1]:
                        if tool_calls_map[key].get("state") == "input-available":
                            dedup_key = key
                            break
                if not dedup_key:
                    continue

            if tc_index is not None and dedup_key in tool_calls_map:
                if "_index" not in tool_calls_map[dedup_key]:
                    tool_calls_map[dedup_key]["_index"] = tc_index

            if not tc_args_str:
                if dedup_key in tool_calls_map:
                    if tc_id and not tool_calls_map[dedup_key].get("id"):
                        tool_calls_map[dedup_key]["id"] = tc_id
                    if tc_name and not tool_calls_map[dedup_key].get("name"):
                        tool_calls_map[dedup_key]["name"] = tc_name
                else:
                    new_tool_info: dict[str, Any] = {
                        "id": tc_id,
                        "name": tc_name,
                        "type": f"tool-call-{tc_name}",
                        "state": "input-available",
                        "status": "running",
                        "parameters": {},
                        "result": None,
                        "error": None,
                    }
                    if tc_index is not None:
                        new_tool_info["_index"] = tc_index
                    tool_calls_map[dedup_key] = new_tool_info
                    yield {"type": "tool", "data": dict(new_tool_info)}
                continue

            prev = tool_args_accumulator.get(dedup_key, "")
            tool_args_accumulator[dedup_key] = prev + tc_args_str
            accumulated_str = tool_args_accumulator[dedup_key]

            try:
                parsed_args = _json.loads(accumulated_str)
                if isinstance(parsed_args, dict):
                    if dedup_key in tool_calls_map:
                        tool_calls_map[dedup_key]["parameters"] = parsed_args
                        tool_info = dict(tool_calls_map[dedup_key])
                        tool_info["status"] = _map_state_to_status(tool_info.get("state", ""))
                        yield {"type": "tool", "data": tool_info}
                    else:
                        new_tool_info = {
                            "id": tc_id,
                            "name": tc_name,
                            "type": f"tool-call-{tc_name}",
                            "state": "input-available",
                            "status": "running",
                            "parameters": parsed_args,
                            "result": None,
                            "error": None,
                        }
                        if tc_index is not None:
                            new_tool_info["_index"] = tc_index
                        tool_calls_map[dedup_key] = new_tool_info
                        yield {"type": "tool", "data": dict(new_tool_info)}
            except (_json.JSONDecodeError, ValueError):
                pass

    # 统一思考内容提取（兼容 DeepSeek/Ollama/Anthropic）
    # 仅当深度思考启用时才发送 reasoning 事件，避免关闭思考后仍显示"推理中"
    thinking_text = extract_thinking_content(message, mode if isinstance(mode, str) else "")
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
                    "source": "model_intrinsic",
                },
            }

    # 内容发送策略：
    # - agent 模式 + 有 tool_calls/tool_call_chunks：缓冲 content（中间文本，不应发送）
    # - agent 模式 + 无 tool_calls：直接流式发送 content（最终回答）
    # - 非 agent 模式：直接发送
    if message.content:
        has_tool_calls = bool(tool_calls) or bool(getattr(message, "tool_call_chunks", None))
        if mode == "agent" and has_tool_calls:
            # agent 模式 + 有 tool_calls：缓冲 content，不立即发送
            # 这是模型在决定调用工具前的"自言自语"，不应发送给用户
            if accumulated_reasoning is not None:
                prev_pending = accumulated_reasoning.get("_pending_content", "")
                accumulated_reasoning["_pending_content"] = prev_pending + message.content
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


def _handle_tool_message_chunk(
    message: ToolMessage,
    tool_calls_map: dict[str, dict],
    *,
    session_id: str = "",
    message_id: str = "",
):
    """处理 ToolMessage chunk：更新 tool_info 状态并发布生命周期事件。

    在 yield SSE 事件之前，根据 ToolMessage 的 status 和 content 调用
    ``service.transition`` 发布工具调用生命周期事件。事件优先级：
    超时 > 拒绝 > 失败 > 完成。

    Args:
        message: ToolMessage 实例
        tool_calls_map: 工具调用映射（key=tool_call_id，value=tool_info dict）
        session_id: 会话 ID（保留参数，用于上下文定位）
        message_id: 消息 ID（保留参数，用于上下文定位）
    """
    tool_call_id = getattr(message, "tool_call_id", "")
    is_error = getattr(message, "status", None) == "error"
    tool_info = None
    if tool_call_id and tool_call_id in tool_calls_map:
        tool_info = tool_calls_map[tool_call_id]
    elif tool_call_id:
        for key, tc in tool_calls_map.items():
            if tc.get("id") == tool_call_id or (not tc.get("id") and key == tool_call_id):
                tool_info = tc
                break
    if tool_info:
        # 事件优先级判定：超时 > 拒绝 > 失败 > 完成
        is_timeout = _detect_tool_timeout(message)
        is_rejected = _detect_tool_rejected(message)
        content_is_error = _detect_tool_error(message.content) if not is_error else False
        new_state = "output-error" if (is_error or content_is_error) else "output-available"
        tool_info["state"] = new_state
        tool_info["status"] = _map_state_to_status(new_state)
        if is_error or content_is_error:
            tool_info["result"] = None
            tool_info["error"] = message.content
        else:
            # 知识库检索工具返回大量原始文档，不应原样推给前端
            # 只保留摘要信息，避免前端显示海量原始内容
            tool_name = tool_info.get("name", "")
            if (
                tool_name.startswith("knowledge_base_")
                and isinstance(message.content, str)
                and len(message.content) > 500
            ):
                # 截取前200字符作为预览，标记为已摘要
                preview = message.content[:200].rstrip() + "..."
                tool_info["result"] = preview
                tool_info["_summarized"] = True
            else:
                tool_info["result"] = message.content
            tool_info["error"] = None

        # 发布工具调用生命周期事件（仅在能定位到上下文时调用）
        # 事件优先级：超时 > 拒绝 > 失败 > 完成（与 test_stream_helpers.py 一致）
        # 注意：函数返回 list（而非 generator）以确保 transition 在调用时立即执行，
        # 调用方 ``yield from _handle_tool_message_chunk(...)`` 语义不变（list 可迭代）。
        if tool_call_id:
            if is_timeout:
                service.transition(
                    tool_call_id,
                    EventType.TOOL_CALL_TIMEOUT,
                )
            elif is_rejected:
                service.transition(
                    tool_call_id,
                    EventType.TOOL_CALL_REJECTED,
                )
            elif is_error:
                service.transition(
                    tool_call_id,
                    EventType.TOOL_CALL_FAILED,
                    error=str(message.content) if message.content else None,
                )
            else:
                service.transition(
                    tool_call_id,
                    EventType.TOOL_CALL_COMPLETED,
                    result=message.content,
                )

        return [{"type": "tool_result", "data": tool_info}]

    return []


def process_stream_chunk(
    chunk,
    tool_calls_map: dict[str, dict],
    current_message_content: str,
    weather_tool_names: set | None = None,
    tool_call_count: dict[str, int] | None = None,
    lcp_func=None,
    accumulated_reasoning: dict[str, str] | None = None,
    tool_args_accumulator: dict[str, str] | None = None,
    mode: str = "agent",
    enable_deep_thinking: bool = False,
):
    # 调用方保证以下容器参数非 None（StreamContext 字段有 default_factory）
    assert tool_call_count is not None
    assert accumulated_reasoning is not None
    assert tool_args_accumulator is not None
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
            logger.debug(f"过滤终止信号消息: {message.content[:50]}")
            return

        # agent 模式下，当检测到 tool_calls 时清除缓冲的中间内容
        # （这些内容是模型在决定调用工具前的"自言自语"，不应发送给用户）
        tool_calls = getattr(message, "tool_calls", [])
        tool_call_chunks = getattr(message, "tool_call_chunks", None)
        if mode == "agent" and (tool_calls or tool_call_chunks) and accumulated_reasoning is not None:
            if accumulated_reasoning.get("_pending_content"):
                logger.debug(
                    f"Agent 模式: 检测到 tool_calls，清除缓冲的中间内容 ({len(accumulated_reasoning['_pending_content'])} 字符)"
                )
                accumulated_reasoning["_pending_content"] = ""
                _sync_pending_to_stream_state(accumulated_reasoning)

        yield from _handle_ai_message_chunk(
            message,
            tool_calls_map,
            tool_call_count,
            lcp_func,
            current_message_content,
            accumulated_reasoning,
            tool_args_accumulator,
            mode,
            enable_deep_thinking,
        )
    elif isinstance(message, ToolMessage):
        yield from _handle_tool_message_chunk(message, tool_calls_map)


def build_context_info(usage_tracker, token_detail_tracker, stream_start_time=None):
    context_info = usage_tracker.get_usage_info()
    token_summary = token_detail_tracker.get_summary()
    context_info["tokens"] = token_summary["tokens"]
    context_info["tokenDetail"] = token_detail_tracker.get_token_detail()
    context_info["model"] = usage_tracker.model_id
    context_info["total_tokens"] = usage_tracker.get_total_tokens()
    if stream_start_time is not None:
        context_info["response_time"] = round(time.time() - stream_start_time, 2)
    return context_info


def update_usage_and_tokens(cb, usage_tracker, token_detail_tracker=None):
    usage_tracker.add_input_tokens(cb.prompt_tokens)
    usage_tracker.add_output_tokens(cb.completion_tokens)
    if token_detail_tracker:
        token_detail_tracker.update_from_metadata(
            {
                "usage_metadata": {
                    "input_tokens": cb.prompt_tokens,
                    "output_tokens": cb.completion_tokens,
                }
            }
        )
        token_detail_tracker.finish_record()


def sync_usage_from_messages(all_messages, usage_tracker, token_detail_tracker=None):
    seen_ids = set()
    for msg in reversed(all_messages):
        if not isinstance(msg, AIMessage):
            continue
        msg_id = getattr(msg, "id", None)
        if msg_id and msg_id in seen_ids:
            continue
        if msg_id:
            seen_ids.add(msg_id)
        resp_meta = getattr(msg, "response_metadata", {}) or {}
        token_usage = resp_meta.get("token_usage", {})
        if token_usage:
            usage_tracker.add_input_tokens(token_usage.get("prompt_tokens", 0))
            usage_tracker.add_output_tokens(token_usage.get("completion_tokens", 0))
            if token_detail_tracker:
                token_detail_tracker.update_from_metadata(
                    {
                        "usage_metadata": {
                            "input_tokens": token_usage.get("prompt_tokens", 0),
                            "output_tokens": token_usage.get("completion_tokens", 0),
                        }
                    }
                )
        usage_meta = resp_meta.get("usage_metadata", {})
        if usage_meta:
            usage_tracker.update_from_metadata({"usage_metadata": usage_meta})
            if token_detail_tracker:
                token_detail_tracker.update_from_metadata({"usage_metadata": usage_meta})


def finalize_tool_calls(
    all_messages: list,
    tool_calls_map: dict[str, dict],
    tool_args_accumulator: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    if tool_args_accumulator:
        for key, accumulated_str in tool_args_accumulator.items():
            if not accumulated_str or not accumulated_str.strip():
                continue
            if key not in tool_calls_map:
                continue
            existing_params = tool_calls_map[key].get("parameters", {})
            if isinstance(existing_params, dict) and existing_params and existing_params != {}:
                continue
            try:
                parsed_args = _json.loads(accumulated_str)
                if isinstance(parsed_args, dict) and parsed_args:
                    tool_calls_map[key]["parameters"] = parsed_args
                    tool_info = dict(tool_calls_map[key])
                    tool_info["status"] = _map_state_to_status(tool_info.get("state", ""))
                    tool_info.pop("_index", None)
                    events.append({"type": "tool", "data": tool_info})
                    logger.debug(f"[FINALIZE-TOOL] 从 accumulator 解析参数成功: key={key}, args={parsed_args}")
            except (_json.JSONDecodeError, ValueError):
                recovered = _try_parse_concatenated_json(accumulated_str)
                if recovered:
                    tool_calls_map[key]["parameters"] = recovered
                    tool_info = dict(tool_calls_map[key])
                    tool_info["status"] = _map_state_to_status(tool_info.get("state", ""))
                    tool_info.pop("_index", None)
                    events.append({"type": "tool", "data": tool_info})
                    logger.info(f"[FINALIZE-TOOL] 从拼接 JSON 中恢复参数成功: key={key}")
                else:
                    logger.debug(f"[FINALIZE-TOOL] accumulator JSON 解析失败: key={key}, str={accumulated_str!r}")

    needs_update = any(
        not tc.get("parameters") or (isinstance(tc.get("parameters"), dict) and tc.get("parameters") == {})
        for tc in tool_calls_map.values()
    )

    if needs_update:
        ai_chunks = [msg for msg in all_messages if isinstance(msg, AIMessage)]
        if ai_chunks:
            chunks_by_id: dict[str, Any] = {}
            chunks_no_id = None
            for chunk in ai_chunks:
                msg_id = getattr(chunk, "id", None) or ""
                if msg_id:
                    if msg_id in chunks_by_id:
                        chunks_by_id[msg_id] = chunks_by_id[msg_id] + chunk
                    else:
                        chunks_by_id[msg_id] = chunk
                elif chunks_no_id is not None:
                    chunks_no_id = chunks_no_id + chunk
                else:
                    chunks_no_id = chunk

            complete_messages = list(chunks_by_id.values())
            if chunks_no_id is not None:
                complete_messages.append(chunks_no_id)

            for complete_msg in complete_messages:
                tool_calls = getattr(complete_msg, "tool_calls", [])
                for tool_call in tool_calls:
                    args = tool_call.get("args", {})
                    if not args or not isinstance(args, dict) or args == {}:
                        continue

                    tool_id = tool_call.get("id", "")
                    tool_name = tool_call.get("name", "")

                    for key, tc in tool_calls_map.items():
                        existing_params = tc.get("parameters", {})
                        if isinstance(existing_params, dict) and existing_params and existing_params != {}:
                            continue
                        if (tc.get("id") == tool_id and tool_id) or (
                            tc.get("name") == tool_name and tool_name and not tc.get("id")
                        ):
                            tc["parameters"] = args
                            tool_info = dict(tc)
                            tool_info["status"] = _map_state_to_status(tool_info.get("state", ""))
                            tool_info.pop("_index", None)
                            events.append({"type": "tool", "data": tool_info})
                            logger.info(f"[FINALIZE-TOOL] 从累积消息提取参数成功: name={tool_name}, args={args}")
                            break

    return events


def _detect_tool_error(result_content: str | list[Any]) -> bool:
    if not result_content or not isinstance(result_content, str):
        return False
    error_prefixes = ["错误：", "Error:", "ERROR:", "FAILED", "失败:", "异常:", "Exception:"]
    return any(result_content.strip().startswith(prefix) for prefix in error_prefixes)


def _detect_tool_timeout(message: ToolMessage) -> bool:
    """检测 ToolMessage 是否表示审批超时。

    通过 content 中包含 "审批超时" 关键字判断，用于触发 TOOL_CALL_TIMEOUT 终态事件。
    content 非 str 时返回 False（健壮性，兼容 list/None 等异常 content 类型）。

    Args:
        message: ToolMessage 实例（langchain_core.messages.ToolMessage）

    Returns:
        bool: True 表示工具因审批超时失败
    """
    content = getattr(message, "content", None)
    if not isinstance(content, str):
        return False
    return "审批超时" in content


def _detect_tool_rejected(message: ToolMessage) -> bool:
    """检测 ToolMessage 是否表示用户已拒绝。

    通过 content 中包含 "用户已拒绝" 关键字判断，用于触发 TOOL_CALL_REJECTED 终态事件。
    content 非 str 时返回 False（健壮性，兼容 list/None 等异常 content 类型）。

    Args:
        message: ToolMessage 实例（langchain_core.messages.ToolMessage）

    Returns:
        bool: True 表示工具被用户拒绝
    """
    content = getattr(message, "content", None)
    if not isinstance(content, str):
        return False
    return "用户已拒绝" in content


def is_tool_call_failure(exc: Exception) -> bool:
    error_msg = str(exc).lower()
    tool_call_failure_patterns = [
        "failed to call a function",
        "failed_generation",
        "tool call failed",
        "function call",
    ]
    if any(p in error_msg for p in tool_call_failure_patterns):
        return True
    try:
        from openai import (
            BadRequestError as OpenAIBadRequest,
        )
        from openai import (
            PermissionDeniedError as OpenAIPermissionDenied,
        )

        if isinstance(exc, OpenAIPermissionDenied):
            return True
        if isinstance(exc, OpenAIBadRequest):
            return True
    except ImportError:
        pass
    try:
        from groq import PermissionDeniedError as GroqPermissionDenied

        if isinstance(exc, GroqPermissionDenied):
            return True
    except ImportError:
        pass
    return bool("403" in error_msg and "forbidden" in error_msg)


def extract_interrupt_ids(intr: Any) -> tuple:
    """从 LangGraph interrupt 对象提取 (value, graph_interrupt_id, langgraph_resume_id)

    兼容三种形态：
    - langgraph.types.Interrupt 实例（取 .value 和 .id）
    - dict（取 "value" 和 "id" 键）
    - 其他原始值（value=intr 本身，id 为空串）

    返回的 langgraph_resume_id 当前与 graph_interrupt_id 相同，
    保留独立字段以便未来支持 resume 协议的扩展。
    """
    try:
        from langgraph.types import Interrupt
    except ImportError:
        Interrupt = None

    if Interrupt is not None and isinstance(intr, Interrupt):
        interrupt_value = intr.value
        graph_interrupt_id = getattr(intr, "id", "") or ""
    elif isinstance(intr, dict):
        interrupt_value = intr.get("value", intr)
        graph_interrupt_id = intr.get("id", "") or ""
    else:
        interrupt_value = intr
        graph_interrupt_id = ""

    langgraph_resume_id = graph_interrupt_id
    return interrupt_value, graph_interrupt_id, langgraph_resume_id


def parse_approval_interrupt(
    interrupt_value: Any,
    graph_interrupt_id: str = "",
    langgraph_resume_id: str = "",
    tool_calls_map: dict[str, dict] | None = None,
    tool_args_accumulator: dict[str, str] | None = None,
    used_tool_call_ids: set | None = None,
) -> list[dict[str, Any]]:
    """解析审批中断值为前端 approval 事件数据列表

    将 ApprovalMiddleware 产生的 interrupt_value 转换为前端可渲染的 approval_data。
    ApprovalMiddleware 在 after_model 钩子批量拦截需要审批的 tool_calls，
    一次 interrupt 携带所有审批请求（批量格式）。
    若 interrupt_value 中携带 operation，则尝试匹配 tool_calls_map /
    tool_args_accumulator 中的 llm_tool_call_id，便于前端关联工具调用卡片。

    Args:
        interrupt_value: interrupt 的值（dict，包含 _approval/requests/_meta 等）
        graph_interrupt_id: graph 节点的 interrupt id
        langgraph_resume_id: resume 协议使用的 id（当前与 graph_interrupt_id 相同）
        tool_calls_map: 工具调用映射（用于匹配 llm_tool_call_id）
        tool_args_accumulator: 工具参数累积器（流式参数更完整）
        used_tool_call_ids: 已使用的 tool_call_id 集合（用于去重，避免重复审批）

    Returns:
        approval_data 列表（批量格式下为多个元素，每个对应一个 tool_call 的审批请求）
    """
    if not isinstance(interrupt_value, dict):
        return []

    tool_calls_map = tool_calls_map or {}
    tool_args_accumulator = tool_args_accumulator or {}
    used_tool_call_ids = used_tool_call_ids or set()

    tool_name = interrupt_value.get("tool_name", "unknown")
    action = interrupt_value.get("action", "confirm")
    interrupt_id = graph_interrupt_id or interrupt_value.get("interrupt_id", "")

    approval_data = {
        "tool_name": tool_name,
        "tool_call_id": interrupt_id,
        "interrupt_id": interrupt_id,
        "graph_interrupt_id": graph_interrupt_id,
        "langgraph_resume_id": langgraph_resume_id,
        "title": interrupt_value.get("title", "确认操作"),
        "description": interrupt_value.get("description", ""),
        "action": action,
        "danger_level": interrupt_value.get("danger_level", "medium"),
        "state": "pending",
    }

    # 透传 operation（统一字段，兼容旧 command）
    op = interrupt_value.get("operation") or interrupt_value.get("command") or ""
    if op:
        approval_data["operation"] = op
        # 匹配 llm_tool_call_id：同时检查 tool_args_accumulator（累积的完整参数）
        # 和 tool_calls_map（可能不完整），避免流式传输中参数未累积完导致匹配失败
        matched = False
        for tc_key, tc_info in tool_calls_map.items():
            if tc_info.get("name") != tool_name:
                continue
            # 跳过已审批的工具调用
            if tc_key in used_tool_call_ids or tc_info.get("id") in used_tool_call_ids:
                continue
            # 先检查 tool_args_accumulator 中的累积参数（更完整）
            accumulated_args = tool_args_accumulator.get(tc_key, "")
            if accumulated_args:
                try:
                    parsed_args = (
                        _json.loads(accumulated_args) if isinstance(accumulated_args, str) else accumulated_args
                    )
                    if any(str(v) == op for v in (parsed_args or {}).values()):
                        approval_data["llm_tool_call_id"] = tc_info.get("id") or tc_key
                        matched = True
                        break
                except (_json.JSONDecodeError, TypeError):
                    pass
            # 再检查 tool_calls_map 中的 parameters
            tc_params = tc_info.get("parameters", {})
            if any(str(v) == op for v in tc_params.values()):
                approval_data["llm_tool_call_id"] = tc_info.get("id") or tc_key
                matched = True
                break
        # 回退：同名工具中最后一个（interrupt 总是最新的调用）
        if not matched:
            for tc_key, tc_info in reversed(list(tool_calls_map.items())):
                if tc_info.get("name") == tool_name:
                    if tc_key in used_tool_call_ids or tc_info.get("id") in used_tool_call_ids:
                        continue
                    approval_data["llm_tool_call_id"] = tc_info.get("id") or tc_key
                    break

    # 透传 extra（工具自定义数据）
    if interrupt_value.get("extra"):
        approval_data["extra"] = interrupt_value["extra"]
    # 透传 input_placeholder（CONFIRM_WITH_INPUT 模式）
    if interrupt_value.get("input_placeholder"):
        approval_data["input_placeholder"] = interrupt_value["input_placeholder"]

    return [approval_data]


def _publish_tool_lifecycle_event(
    event_type: EventType,
    tool_info: dict[str, Any],
    session_id: str | None,
    message_id: str | None,
) -> None:
    """发布工具调用生命周期事件到统一 tool_call_lifecycle.service。

    封装 ``service.register`` + ``service.transition`` 调用，提供单一入口
    供 chat 模块（stream 流式 / regenerate 重新生成）发布工具调用事件。

    跳过逻辑（任一缺失即跳过，不调用 register/transition）：
        - ``session_id`` 为 None / 空串
        - ``tool_info['id']`` 为 None / 空串（tool_call_id）
        - ``tool_info['name']`` 为 None / 空串（tool_name）

    register 行为（始终调用，幂等）：
        - ``ToolCallContext.tool_call_id`` = ``tool_info['id']``
        - ``ToolCallContext.tool_name`` = ``tool_info['name']``
        - ``ToolCallContext.module`` = ``EventSource.CHAT``
        - ``ToolCallContext.module_id`` = ``session_id``
        - ``ToolCallContext.message_id`` = ``str(message_id) if message_id is not None else ''``
        - ``ToolCallContext.parameters`` = ``tool_info.get('parameters') or {}``（始终为 dict）

    bind_message_id 行为：
        - 仅当 ``message_id`` 非 None 时调用（补全 message_id）
        - ``message_id`` 为 None 时跳过（register 已设为空串，无需补全）

    transition 行为：
        - ``parameters``：空 dict / None → None（falsy 判断）；非空 dict → 原样透传
        - ``result``：仅 ``EventType.TOOL_CALL_COMPLETED`` 传 ``tool_info.get('result')``
        - ``error``：仅 ``EventType.TOOL_CALL_FAILED`` 传 ``tool_info.get('error')``

    Args:
        event_type: ``EventType`` 枚举成员（如 ``TOOL_CALL_INPUT_READY``）
        tool_info: 工具调用信息 dict，必须包含 ``id`` / ``name``，
            可选包含 ``parameters`` / ``result`` / ``error``
        session_id: 会话 ID（用于 ``ToolCallContext.module_id``）
        message_id: 消息 ID（用于 ``ToolCallContext.message_id`` 与 ``bind_message_id``）

    契约对齐：``apps/chat/tests/test_stream_helpers_tool_events.py``
    """
    # 跳过逻辑：session_id / tool_call_id / tool_name 任一缺失即跳过
    if not session_id:
        return
    tool_call_id = tool_info.get("id") if isinstance(tool_info, dict) else None
    tool_name = tool_info.get("name") if isinstance(tool_info, dict) else None
    if not tool_call_id or not tool_name:
        return

    # message_id 处理：None → 空串（register），不调用 bind_message_id
    # 非 None → str 化后传给 register，并调用 bind_message_id 补全
    resolved_message_id = "" if message_id is None else str(message_id)

    # register：始终调用，parameters 始终为 dict（空时为 {}）
    parameters = tool_info.get("parameters") or {}
    ctx = ToolCallContext(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        module=EventSource.CHAT,
        module_id=session_id,
        message_id=resolved_message_id,
        parameters=parameters,
    )
    service.register(ctx)

    # bind_message_id：仅当 message_id 非 None 时调用（补全 message_id）
    if message_id is not None:
        service.bind_message_id(tool_call_id, resolved_message_id)

    # transition：parameters 始终作为 kwarg 传递
    # 空 dict / None → None（falsy 判断）；非空 dict → 原样透传
    # 契约：测试断言 kwargs['parameters'] is None（需显式传 None，不能省略）
    transition_parameters = parameters if parameters else None

    # result 仅 COMPLETED 事件透传（None 时也省略，与 _handle_tool_message_chunk 一致）
    result = tool_info.get("result") if event_type == EventType.TOOL_CALL_COMPLETED else None

    # error 仅 FAILED 事件透传
    error = tool_info.get("error") if event_type == EventType.TOOL_CALL_FAILED else None

    service.transition(
        tool_call_id,
        event_type,
        parameters=transition_parameters,
        result=result,
        error=error,
    )


def merge_existing_approval_fields(
    persisted_tool_calls: list[dict[str, Any]],
    existing_tool_calls: list[dict[str, Any]],
) -> None:
    """按 tool_call_id 索引合并旧 approval 字段到新 tool_calls 列表。

    用于 ``_save_resume_content`` / ``ChatMessageUpdateView.patch`` 等场景：
    持久化的 tool_calls 来自 LLM 输出（无 approval 字段），
    existing tool_calls 来自数据库（保留审批终态），
    需要将 existing 的 approval 字段合并回 persisted，避免数据丢失。

    匹配策略（按优先级）：
        1. ``tool_call_id`` 字段（与 build_persisted_tool_calls / approval payload 一致）
        2. ``id`` 字段（降级，兼容仅含 id 的旧数据）

    合并规则：
        - 仅当 persisted 条目**缺少** approval 字段时，从 existing 补充
        - persisted 已有 approval 不被覆盖
        - existing 无 approval 字段的不进入索引
        - existing 中多余的条目（无 persisted 对应）被丢弃
        - 长度不等时不跳过，仅合并索引中存在的条目

    Args:
        persisted_tool_calls: 新 tool_calls 列表（将被原地修改）
        existing_tool_calls: 旧 tool_calls 列表（只读，提供 approval 字段）

    契约对齐：``apps/chat/tests/test_stream_helpers_tool_events.py::MergeExistingApprovalFieldsTests``
    """
    # 非 list 直接返回（健壮性，不抛异常）
    if not isinstance(persisted_tool_calls, list) or not isinstance(existing_tool_calls, list):
        return
    # 空列表直接返回
    if not persisted_tool_calls or not existing_tool_calls:
        return

    # 构建 existing 索引：tool_call_id（优先）或 id（降级）→ approval
    existing_index: dict[str, dict[str, Any]] = {}
    for existing_tc in existing_tool_calls:
        if not isinstance(existing_tc, dict):
            continue
        # 仅当 existing 有 approval 字段时才进入索引
        if "approval" not in existing_tc:
            continue
        # tool_call_id 优先，id 降级
        key = existing_tc.get("tool_call_id") or existing_tc.get("id")
        if key:
            existing_index[key] = existing_tc["approval"]

    # 遍历 persisted，按 tool_call_id / id 在 existing_index 中查找匹配
    for persisted_tc in persisted_tool_calls:
        if not isinstance(persisted_tc, dict):
            continue
        # persisted 已有 approval → 不覆盖
        if "approval" in persisted_tc:
            continue
        # tool_call_id 优先，id 降级
        key = persisted_tc.get("tool_call_id") or persisted_tc.get("id")
        if key and key in existing_index:
            persisted_tc["approval"] = existing_index[key]
