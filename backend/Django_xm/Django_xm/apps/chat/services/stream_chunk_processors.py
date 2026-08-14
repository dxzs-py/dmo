"""流式消息块处理（Task 15.2 从 stream_helpers.py 拆分）。

集中处理流式聊天中的消息块（chunk）解析与事件分发：
- ``_sync_pending_to_stream_state``：将 _pending_content 同步到共享 stream_state
- ``extract_thinking_content``：统一提取思考内容（兼容 DeepSeek/Ollama/Anthropic）
- ``_map_state_to_status`` / ``_STATE_TO_STATUS``：工具状态到 status 的映射
- ``_extract_tool_params`` / ``_fix_groq_tool_call`` / ``_try_parse_concatenated_json``：
    工具参数提取与修复辅助
- ``_find_tool_call_key_by_index`` / ``_migrate_key_if_needed``：tool_calls_map 键管理
- ``_handle_ai_message_chunk``：处理 AIMessage/AIMessageChunk（工具调用累积 + 内容流式）
- ``_handle_tool_message_chunk``：处理 ToolMessage（工具结果 + 生命周期事件）
- ``process_stream_chunk``：流式 chunk 分发入口

依赖方向：
- 工具调用状态定义（运行/输入就绪/输出就绪/错误等）
- 延迟导入 ``stream_tool_state._detect_tool_*``（避免与 stream_tool_state 的模块级
  反向导入形成循环依赖；stream_tool_state 模块级导入本模块的 _map_state_to_status /
  _try_parse_concatenated_json）
"""

import json as _json
import logging
import re as _re
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from Django_xm.common.event_schema import EventSource, EventType

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


# 根因修复（P-FE-5）："input-available" 表示工具输入参数已就绪，
# 但工具尚未开始执行（可能需要审批）。映射为 "pending" 而非 "running"，
# 确保 SSE tool 事件与 WebSocket tool_call_pending 事件状态一致。
# 原映射为 "running" 导致触发浏览器显示"执行中"，非触发浏览器显示"待审批"。
# 实际执行状态由 tool_call_running 事件（SAFE 级自动通过 / 审批通过后）推进。
_STATE_TO_STATUS = {
    "input-available": "pending",
    "output-available": "completed",
    "output-error": "failed",
    "rejected": "rejected",
    "timeout": "timeout",
}


def _map_state_to_status(state: str) -> str:
    return _STATE_TO_STATUS.get(state, "pending")


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
    *,
    session_id: str = "",
    message_id: str = "",
    module: EventSource = EventSource.CHAT,
    module_id: str = "",
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
        for i, raw_tool_call in enumerate(tool_calls):
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
                        tool_calls_map[dedup_key]["_index"] = i
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

                # ToolUsageGuard 检查：去重返回 DEDUP / 警告 yield warning / 阻断 yield blocked
                # chat_service 据此决定 ToolMessage 处理；所有工具统一进入 dedup + loop 流程
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
                "status": "pending",
                "parameters": {} if is_chunk else _extract_tool_params(tool_call),
                "result": None,
                "error": None,
                "_index": i,
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
                    # Task 17: 首个 tool_call_chunk 到达但 args 尚为空时，仅注册到
                    # tool_calls_map，不发布 SSE tool 事件、不广播 INPUT_READY。
                    # 等待 args 累积并 JSON 解析成功后（下方 parsed_args 分支）再发布事件，
                    # 确保工具首次出现即携带完整 parameters，避免前端展示空 {} 和状态倒退。
                    #
                    # 参数为空的工具（如 get_current_time）：模型通常发送 tc_args_str="{}"，
                    # 会在下方 parsed_args 分支正常发布事件；若模型发送空串，则由
                    # ToolMessage 结果到达时通过 _handle_tool_message_chunk 兜底发布。
                    new_tool_info: dict[str, Any] = {
                        "id": tc_id,
                        "name": tc_name,
                        "type": f"tool-call-{tc_name}",
                        "state": "input-available",
                        "status": "pending",
                        "parameters": {},
                        "result": None,
                        "error": None,
                    }
                    if tc_index is not None:
                        new_tool_info["_index"] = tc_index
                    tool_calls_map[dedup_key] = new_tool_info
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
                            "status": "pending",
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
                    "source": "deep_thinking" if enable_deep_thinking else "model_intrinsic",
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
                # message.content 可能是 str 或 list[ContentBlock]，统一转为 str 拼接
                chunk_text = message.content if isinstance(message.content, str) else str(message.content)
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


def _handle_tool_message_chunk(
    message: ToolMessage,
    tool_calls_map: dict[str, dict],
    *,
    session_id: str = "",
    message_id: str = "",
    module: EventSource = EventSource.CHAT,
    module_id: str = "",
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
    # 延迟导入避免与 stream_tool_state 的模块级反向导入形成循环依赖
    # （stream_tool_state 模块级导入本模块的 _map_state_to_status / _try_parse_concatenated_json）
    from .stream_tool_state import (
        _detect_tool_error,
        _detect_tool_rejected,
        _detect_tool_timeout,
    )

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
    # 根本修复：ToolMessage 可能在 AIMessageChunk 之前到达流中（LangGraph 工具并发
    # 执行的正常时序）。此时 tool_calls_map 尚无该 tool_call_id 的条目。不再静默跳过，
    # 而是从 ToolMessage.name 创建最小条目，让后续生命周期事件正常发布。
    if not tool_info and tool_call_id:
        tool_name = getattr(message, "name", "") or ""
        if tool_name:
            tool_info = {
                "id": tool_call_id,
                "name": tool_name,
                "type": f"tool-call-{tool_name}",
                "state": "output-available",
                "status": "completed",
                "parameters": {},
                "result": None,
                "error": None,
            }
            tool_calls_map[tool_call_id] = tool_info
    if tool_info:
        # 事件优先级判定：超时 > 拒绝 > 失败 > 完成
        is_timeout = _detect_tool_timeout(message)
        is_rejected = _detect_tool_rejected(message)
        content_is_error = _detect_tool_error(message.content) if not is_error else False
        # 状态计算需与事件优先级一致：拒绝/超时 ToolMessage 也带 status="error"
        # （middleware 注入），若不区分会被映射为 failed，刷新后显示"失败"而非
        # "已拒绝/已超时"。这里按终态语义单独映射。
        if is_timeout:
            new_state = "timeout"
        elif is_rejected:
            new_state = "rejected"
        elif is_error or content_is_error:
            new_state = "output-error"
        else:
            new_state = "output-available"
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

        # 标记工具生命周期事件类型，由 _publish_stream_event 统一发布（P-BE-1 根因修复）：
        # 原代码在此处调用 sync 版 service.transition（fire-and-forget），立即设置 dedup_key，
        # 导致后续 _publish_stream_event 的 async transition_async 被 dedup 跳过，
        # 事件可能延迟或丢失。改为仅标记 lifecycle_event 字段，由 _publish_stream_event
        # 作为唯一发布出口（await 确保事件可靠广播）。
        # 事件优先级：超时 > 拒绝 > 失败 > 完成（与 test_stream_helpers.py 一致）
        if tool_call_id:
            if is_timeout:
                tool_info["lifecycle_event"] = EventType.TOOL_CALL_TIMEOUT.value
            elif is_rejected:
                tool_info["lifecycle_event"] = EventType.TOOL_CALL_REJECTED.value
            elif is_error:
                tool_info["lifecycle_event"] = EventType.TOOL_CALL_FAILED.value
            else:
                tool_info["lifecycle_event"] = EventType.TOOL_CALL_COMPLETED.value

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
    *,
    session_id: str = "",
    message_id: str = "",
    module: EventSource = EventSource.CHAT,
    module_id: str = "",
):
    # 调用方保证以下容器参数非 None（StreamContext 字段有 default_factory）
    assert tool_call_count is not None  # noqa: S101
    assert accumulated_reasoning is not None  # noqa: S101
    assert tool_args_accumulator is not None  # noqa: S101
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
                    f"Agent 模式: 检测到 tool_calls，清除缓冲的中间内容 "
                    f"({len(accumulated_reasoning['_pending_content'])} 字符)"
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
            session_id=session_id,
            message_id=message_id,
            module=module,
            module_id=module_id,
        )
    elif isinstance(message, ToolMessage):
        yield from _handle_tool_message_chunk(
            message,
            tool_calls_map,
            session_id=session_id,
            message_id=message_id,
            module=module,
            module_id=module_id,
        )
