"""工具调用状态管理与失败检测（Task 15.2 从 stream_helpers.py 拆分）。

集中处理流式结束时工具调用参数补全与失败检测：
- ``finalize_tool_calls``：流式结束时补全工具调用参数并广播生命周期事件
- ``_detect_tool_error``：检测 ToolMessage content 是否为错误
- ``_detect_tool_timeout``：检测审批超时（content 含"审批超时"）
- ``_detect_tool_rejected``：检测用户拒绝（content 含"用户已拒绝"）
- ``is_tool_call_failure``：判断异常是否为工具调用失败

依赖方向：
- 模块级导入 ``stream_chunk_processors._map_state_to_status`` /
  ``_try_parse_concatenated_json``（纯函数，无反向模块级依赖）
- 模块级导入 ``stream_tool_lifecycle._broadcast_tool_input_ready``
- ``stream_chunk_processors`` 对本模块的依赖通过延迟导入实现（见该模块），
  因此本模块在模块级导入 stream_chunk_processors 不会形成加载期循环。
"""

import json as _json
import logging
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from .stream_chunk_processors import _map_state_to_status, _try_parse_concatenated_json
from .stream_tool_lifecycle import _broadcast_tool_input_ready

logger = logging.getLogger(__name__)


def finalize_tool_calls(
    all_messages: list,
    tool_calls_map: dict[str, dict],
    tool_args_accumulator: dict[str, str] | None = None,
    *,
    session_id: str | None = None,
    message_id: str | None = None,
) -> list[dict[str, Any]]:
    """流式结束时补全工具调用参数并广播生命周期事件。

    Args:
        all_messages: 流期间累积的所有消息（含 AIMessage / ToolMessage）
        tool_calls_map: 工具调用映射（key=tool_call_id 或 dedup key）
        tool_args_accumulator: 工具参数 JSON 字符串累积器
        session_id: 会话 ID（用于广播 INPUT_READY 事件到非触发浏览器；
            None 时跳过广播，仅补全参数）
        message_id: 关联的助手消息 ID（用于 ToolCallContext.message_id 绑定；
            None 时不绑定）
    """
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
                    # 广播 INPUT_READY 事件到非触发浏览器
                    _broadcast_tool_input_ready(tool_info, session_id, message_id)
                    logger.debug(f"[FINALIZE-TOOL] 从 accumulator 解析参数成功: key={key}, args={parsed_args}")
            except (_json.JSONDecodeError, ValueError):
                recovered = _try_parse_concatenated_json(accumulated_str)
                if recovered:
                    tool_calls_map[key]["parameters"] = recovered
                    tool_info = dict(tool_calls_map[key])
                    tool_info["status"] = _map_state_to_status(tool_info.get("state", ""))
                    tool_info.pop("_index", None)
                    events.append({"type": "tool", "data": tool_info})
                    _broadcast_tool_input_ready(tool_info, session_id, message_id)
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
            # LangChain BaseMessage 的 __add__ 用于合并流式 chunk，
            # 其返回类型在 stub 中声明不精确，累加器统一用 Any 持有。
            chunks_no_id: Any = None
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

                    for _key, tc in tool_calls_map.items():
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
                            # 广播 INPUT_READY 事件到非触发浏览器
                            _broadcast_tool_input_ready(tool_info, session_id, message_id)
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
