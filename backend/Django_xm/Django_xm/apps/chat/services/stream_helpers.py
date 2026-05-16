"""
流式聊天辅助工具方法

从 chat_service.py 拆分出的通用流式处理逻辑：
- 消息块解析
- 用量/成本更新
- 上下文信息构建
"""
import json as _json
import re as _re
import time
import logging
from typing import Dict, Any, List, Optional

from langchain_core.messages import AIMessage, ToolMessage

from Django_xm.apps.ai_engine.services.token_counter import TokenUsageCallbackHandler

logger = logging.getLogger(__name__)

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
        return new_key
    return old_key


def process_stream_chunk(
    chunk,
    tool_calls_map: Dict[str, Dict],
    current_message_content: str,
    weather_tool_names: set = None,
    tool_call_count: Dict[str, int] = None,
    lcp_func=None,
    accumulated_reasoning: Dict[str, str] = None,
    tool_args_accumulator: Dict[str, str] = None,
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []

    if chunk is None:
        return events

    if isinstance(chunk, tuple) and len(chunk) == 2:
        message, metadata = chunk
    else:
        message = chunk
        metadata = {}

    if message is None:
        return events

    if isinstance(message, AIMessage):
        tool_calls = getattr(message, "tool_calls", [])
        tool_call_chunks = getattr(message, "tool_call_chunks", None)
        if tool_calls:
            logger.info(f"[STREAM-TOOL] tool_calls={tool_calls}")
        if tool_call_chunks:
            logger.info(f"[STREAM-TOOL] tool_call_chunks={tool_call_chunks}")
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
                        tool_calls_map = _migrate_key_if_needed(tool_calls_map, name_key, tool_id)
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
                        events.append({'type': 'tool', 'data': tool_info})
                    continue

                if tool_call_count is not None:
                    if tool_name not in tool_call_count:
                        tool_call_count[tool_name] = 0
                    tool_call_count[tool_name] += 1
                    if tool_call_count[tool_name] > 3:
                        logger.warning(f"工具 {tool_name} 被调用了 {tool_call_count[tool_name]} 次，可能存在循环调用")

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
                events.append({'type': 'tool', 'data': tool_info})

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
                elif tc_name:
                    for key, tc in tool_calls_map.items():
                        if tc.get("name") == tc_name and tc.get("state") == "input-available":
                            dedup_key = key
                            break
                    if not dedup_key:
                        dedup_key = tc_name
                elif tc_index is not None:
                    dedup_key = _find_tool_call_key_by_index(tool_calls_map, tc_index)

                if not dedup_key:
                    if tc_args_str and tool_calls_map:
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
                            events.append({'type': 'tool', 'data': tool_info})
                except (_json.JSONDecodeError, ValueError):
                    pass

        additional_kwargs = getattr(message, "additional_kwargs", {})
        reasoning_content = additional_kwargs.get("reasoning_content")
        if reasoning_content and isinstance(reasoning_content, str) and reasoning_content.strip():
            if accumulated_reasoning is not None:
                prev = accumulated_reasoning.get("content", "") or ""
                accumulated_reasoning["content"] = prev + reasoning_content.strip()
            events.append({
                "type": "reasoning",
                "data": {
                    "content": (accumulated_reasoning.get("content", "") or "").strip(),
                    "duration": 0,
                },
            })

        if message.content and not tool_calls:
            if lcp_func:
                lcp = lcp_func(current_message_content, message.content)
            else:
                lcp = 0
            if lcp < len(message.content):
                new_content = message.content[lcp:]
                events.append({"type": "chunk", "content": new_content})

    elif isinstance(message, ToolMessage):
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
                tool_info["result"] = message.content
                tool_info["error"] = None
            events.append({'type': 'tool_result', 'data': tool_info})

    return events


def build_context_info(usage_tracker, cost_tracker, stream_start_time=None):
    context_info = usage_tracker.get_usage_info()
    cost_info = cost_tracker.get_summary()
    context_info['cost'] = cost_info
    context_info['model'] = usage_tracker.model_id
    context_info['total_tokens'] = usage_tracker.get_total_tokens()
    if stream_start_time is not None:
        context_info['response_time'] = round(time.time() - stream_start_time, 2)
    return context_info


def update_usage_and_cost(cb, usage_tracker, cost_tracker=None):
    usage_tracker.add_input_tokens(cb.prompt_tokens)
    usage_tracker.add_output_tokens(cb.completion_tokens)
    if cost_tracker:
        cost_tracker.update_from_metadata(
            {'usage_metadata': {
                'input_tokens': cb.prompt_tokens,
                'output_tokens': cb.completion_tokens,
            }}
        )
        cost_tracker.finish_record()


def sync_usage_from_messages(all_messages, usage_tracker, cost_tracker=None):
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
            if cost_tracker:
                cost_tracker.update_from_metadata(
                    {'usage_metadata': {
                        'input_tokens': token_usage.get('prompt_tokens', 0),
                        'output_tokens': token_usage.get('completion_tokens', 0),
                    }}
                )
        usage_meta = resp_meta.get('usage_metadata', {})
        if usage_meta:
            usage_tracker.update_from_metadata({'usage_metadata': usage_meta})
            if cost_tracker:
                cost_tracker.update_from_metadata({'usage_metadata': usage_meta})


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
