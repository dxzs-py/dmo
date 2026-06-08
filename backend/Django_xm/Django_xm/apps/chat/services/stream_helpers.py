"""
流式聊天辅助工具方法

从 chat_service.py 拆分出的通用流式处理逻辑：
- 消息块解析
- 用量/Token 更新
- 上下文信息构建
- 深度思考内容提取（兼容 DeepSeek/Ollama/Anthropic）
"""
import json as _json
import re as _re
import time
import logging
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, ToolMessage

from Django_xm.apps.ai_engine.services.token_counter import TokenUsageCallbackHandler

logger = logging.getLogger(__name__)


def _sync_pending_to_stream_state(accumulated_reasoning: Dict[str, str]):
    """将 _pending_content 同步到 data['_stream_state']，确保 generate() finally 能兜底刷新。

    当 async generator 被强制关闭（aclose/GeneratorExit）时，
    try/except 之后的代码不会执行，导致 _pending_content 无法通过正常路径刷新。
    通过共享状态字典，views_chat.py 的 generate() finally 块可以兜底刷新。
    """
    stream_state = accumulated_reasoning.get("_stream_state")
    if stream_state is not None:
        stream_state["pending_content"] = accumulated_reasoning.get("_pending_content", "")


def extract_thinking_content(chunk, provider_id: str = "") -> Optional[str]:
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


def _fix_groq_tool_call(tool_call: Dict[str, Any]) -> Dict[str, Any]:
    raw_name = tool_call.get("name", "")
    if " " not in raw_name and "{" not in raw_name:
        return tool_call

    fixed = dict(tool_call)
    match = _re.match(r'^(\w+)\s*(\{.*\})?\s*$', raw_name, _re.DOTALL)
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


def _try_parse_concatenated_json(s: str) -> Optional[dict]:
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
        next_brace = s.find('{', pos)
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


def _find_tool_call_key_by_index(tool_calls_map: Dict[str, Dict], index: int) -> Optional[str]:
    for key, tc in tool_calls_map.items():
        if tc.get("_index") == index:
            return key
    return None


def _migrate_key_if_needed(tool_calls_map: Dict[str, Dict], old_key: str, new_key: str) -> str:
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
    tool_calls_map: Dict[str, Dict],
    tool_call_count: Dict[str, int],
    lcp_func,
    current_message_content: str,
    accumulated_reasoning: Dict[str, str],
    tool_args_accumulator: Dict[str, str],
    mode: str,
    enable_deep_thinking: bool = False,
):
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
                updated_params = _extract_tool_params(tool_call)
                if updated_params:
                    tool_calls_map[dedup_key]["parameters"] = updated_params
                    tool_info = dict(tool_calls_map[dedup_key])
                    tool_info["status"] = _map_state_to_status(tool_info.get("state", ""))
                    yield {'type': 'tool', 'data': tool_info}
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
                        get_tool_usage_guard,
                        ToolUsageDecision,
                        ToolUsageStatus,
                    )
                    # 拼装参数用于 guard 检查
                    current_params = (
                        tool_calls_map[dedup_key].get("parameters", {})
                        if dedup_key in tool_calls_map
                        else _extract_tool_params(tool_call)
                    )
                    if not current_params:
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
                    sse_data = (
                        decision.sse_event.get("data", {}) if decision.sse_event else {}
                    )
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
                        common_data["short_circuit_response"] = (
                            decision.short_circuit_response
                        )
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
                        common_data["short_circuit_response"] = (
                            decision.short_circuit_response
                        )
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

            tool_info = {
                "id": tool_id,
                "name": tool_name,
                "type": f"tool-call-{tool_name}",
                "state": "input-available",
                "status": "running",
                "parameters": _extract_tool_params(tool_call),
                "result": None,
                "error": None,
            }
            tool_calls_map[dedup_key] = tool_info
            yield {'type': 'tool', 'data': tool_info}

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
                same_name_pending = [k for k, tc in tool_calls_map.items()
                                     if tc.get("name") == tc_name and tc.get("state") == "input-available"]
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
                    new_tool_info = {
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
                    yield {'type': 'tool', 'data': dict(new_tool_info)}
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
                        yield {'type': 'tool', 'data': tool_info}
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
                        yield {'type': 'tool', 'data': dict(new_tool_info)}
            except (_json.JSONDecodeError, ValueError):
                pass

    # 统一思考内容提取（兼容 DeepSeek/Ollama/Anthropic）
    # 仅当深度思考启用时才发送 reasoning 事件，避免关闭思考后仍显示"推理中"
    thinking_text = extract_thinking_content(message, mode if isinstance(mode, str) else "")
    if thinking_text:
        if accumulated_reasoning is not None:
            prev = accumulated_reasoning.get("content", "") or ""
            accumulated_reasoning["content"] = prev + thinking_text
        if mode in ('agent', 'chat') and enable_deep_thinking:
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
        if mode == 'agent' and has_tool_calls:
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
    tool_calls_map: Dict[str, Dict],
):
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
            if tool_name.startswith("knowledge_base_") and isinstance(message.content, str) and len(message.content) > 500:
                # 截取前200字符作为预览，标记为已摘要
                preview = message.content[:200].rstrip() + "..."
                tool_info["result"] = preview
                tool_info["_summarized"] = True
            else:
                tool_info["result"] = message.content
            tool_info["error"] = None
        yield {'type': 'tool_result', 'data': tool_info}


def process_stream_chunk(
    chunk,
    tool_calls_map: Dict[str, Dict],
    current_message_content: str,
    weather_tool_names: set = None,
    tool_call_count: Dict[str, int] = None,
    lcp_func=None,
    accumulated_reasoning: Dict[str, str] = None,
    tool_args_accumulator: Dict[str, str] = None,
    mode: str = "agent",
    enable_deep_thinking: bool = False,
):
    if chunk is None:
        return

    if isinstance(chunk, tuple) and len(chunk) == 2:
        message, metadata = chunk
    else:
        message = chunk
        metadata = {}

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
        if mode == 'agent' and (tool_calls or tool_call_chunks) and accumulated_reasoning is not None:
            if accumulated_reasoning.get("_pending_content"):
                logger.debug(f"Agent 模式: 检测到 tool_calls，清除缓冲的中间内容 ({len(accumulated_reasoning['_pending_content'])} 字符)")
                accumulated_reasoning["_pending_content"] = ""
                _sync_pending_to_stream_state(accumulated_reasoning)

        yield from _handle_ai_message_chunk(
            message, tool_calls_map, tool_call_count, lcp_func,
            current_message_content, accumulated_reasoning,
            tool_args_accumulator, mode, enable_deep_thinking,
        )
    elif isinstance(message, ToolMessage):
        yield from _handle_tool_message_chunk(message, tool_calls_map)


def build_context_info(usage_tracker, token_detail_tracker, stream_start_time=None):
    context_info = usage_tracker.get_usage_info()
    token_summary = token_detail_tracker.get_summary()
    context_info['tokens'] = token_summary['tokens']
    context_info['tokenDetail'] = token_detail_tracker.get_token_detail()
    context_info['model'] = usage_tracker.model_id
    context_info['total_tokens'] = usage_tracker.get_total_tokens()
    if stream_start_time is not None:
        context_info['response_time'] = round(time.time() - stream_start_time, 2)
    return context_info


def update_usage_and_tokens(cb, usage_tracker, token_detail_tracker=None):
    usage_tracker.add_input_tokens(cb.prompt_tokens)
    usage_tracker.add_output_tokens(cb.completion_tokens)
    if token_detail_tracker:
        token_detail_tracker.update_from_metadata(
            {'usage_metadata': {
                'input_tokens': cb.prompt_tokens,
                'output_tokens': cb.completion_tokens,
            }}
        )
        token_detail_tracker.finish_record()


def sync_usage_from_messages(all_messages, usage_tracker, token_detail_tracker=None):
    seen_ids = set()
    for msg in reversed(all_messages):
        if not isinstance(msg, AIMessage):
            continue
        msg_id = getattr(msg, 'id', None)
        if msg_id and msg_id in seen_ids:
            continue
        if msg_id:
            seen_ids.add(msg_id)
        resp_meta = getattr(msg, 'response_metadata', {}) or {}
        token_usage = resp_meta.get('token_usage', {})
        if token_usage:
            usage_tracker.add_input_tokens(token_usage.get('prompt_tokens', 0))
            usage_tracker.add_output_tokens(token_usage.get('completion_tokens', 0))
            if token_detail_tracker:
                token_detail_tracker.update_from_metadata(
                    {'usage_metadata': {
                        'input_tokens': token_usage.get('prompt_tokens', 0),
                        'output_tokens': token_usage.get('completion_tokens', 0),
                    }}
                )
        usage_meta = resp_meta.get('usage_metadata', {})
        if usage_meta:
            usage_tracker.update_from_metadata({'usage_metadata': usage_meta})
            if token_detail_tracker:
                token_detail_tracker.update_from_metadata({'usage_metadata': usage_meta})


def finalize_tool_calls(
    all_messages: List,
    tool_calls_map: Dict[str, Dict],
    tool_args_accumulator: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []

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
                    events.append({'type': 'tool', 'data': tool_info})
                    logger.debug(f"[FINALIZE-TOOL] 从 accumulator 解析参数成功: key={key}, args={parsed_args}")
            except (_json.JSONDecodeError, ValueError):
                recovered = _try_parse_concatenated_json(accumulated_str)
                if recovered:
                    tool_calls_map[key]["parameters"] = recovered
                    tool_info = dict(tool_calls_map[key])
                    tool_info["status"] = _map_state_to_status(tool_info.get("state", ""))
                    tool_info.pop("_index", None)
                    events.append({'type': 'tool', 'data': tool_info})
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
            chunks_by_id: Dict[str, Any] = {}
            chunks_no_id = None
            for chunk in ai_chunks:
                msg_id = getattr(chunk, 'id', None) or ''
                if msg_id:
                    if msg_id in chunks_by_id:
                        chunks_by_id[msg_id] = chunks_by_id[msg_id] + chunk
                    else:
                        chunks_by_id[msg_id] = chunk
                else:
                    if chunks_no_id is not None:
                        chunks_no_id = chunks_no_id + chunk
                    else:
                        chunks_no_id = chunk

            complete_messages = list(chunks_by_id.values())
            if chunks_no_id is not None:
                complete_messages.append(chunks_no_id)

            for complete_msg in complete_messages:
                tool_calls = getattr(complete_msg, 'tool_calls', [])
                for tool_call in tool_calls:
                    args = tool_call.get('args', {})
                    if not args or not isinstance(args, dict) or args == {}:
                        continue

                    tool_id = tool_call.get('id', '')
                    tool_name = tool_call.get('name', '')

                    for key, tc in tool_calls_map.items():
                        existing_params = tc.get('parameters', {})
                        if isinstance(existing_params, dict) and existing_params and existing_params != {}:
                            continue
                        if (tc.get('id') == tool_id and tool_id) or \
                           (tc.get('name') == tool_name and tool_name and not tc.get('id')):
                            tc['parameters'] = args
                            tool_info = dict(tc)
                            tool_info["status"] = _map_state_to_status(tool_info.get("state", ""))
                            tool_info.pop("_index", None)
                            events.append({'type': 'tool', 'data': tool_info})
                            logger.info(f"[FINALIZE-TOOL] 从累积消息提取参数成功: name={tool_name}, args={args}")
                            break

    return events


def _detect_tool_error(result_content: str) -> bool:
    if not result_content or not isinstance(result_content, str):
        return False
    error_prefixes = ["错误：", "Error:", "ERROR:", "FAILED", "失败:", "异常:", "Exception:"]
    return any(result_content.strip().startswith(prefix) for prefix in error_prefixes)


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
            PermissionDeniedError as OpenAIPermissionDenied,
            BadRequestError as OpenAIBadRequest,
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
    if "403" in error_msg and "forbidden" in error_msg:
        return True
    return False
