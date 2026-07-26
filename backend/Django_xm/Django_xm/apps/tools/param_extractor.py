"""统一的工具参数提取模块

提供单一入口 extract_tool_params，替代散落在 stream_helpers / official_deep_agent /
subagent_patch 等多处的重复实现。支持 dict 和非 dict（带属性的对象）输入。

支持的 tool_call 格式：
    1. LangChain tool_call: {"name": ..., "args": {...}, "id": ...}
    2. AIMessageChunk tool_call_chunk: {"name": ..., "args": "...", "id": ..., "index": ...}
    3. MCP tool_call: {"function": {"name": ..., "arguments": "{...}"}}
    4. OpenAI function call: {"function": {"name": ..., "arguments": "..."}, "id": ...}
    5. 内部存储格式: {"parameters": {...}} / {"input": {...}}
    6. 非 dict 对象（带 .args / .function / .arguments 属性）
"""

import json
import logging

logger = logging.getLogger(__name__)


def _parse_args_value(value):
    """将参数值解析为 dict。

    支持的输入：
        - dict: 非空时直接返回
        - str: 尝试 JSON 解析为非空 dict

    Returns:
        dict | None: 解析成功返回非空 dict，否则 None
    """
    if isinstance(value, dict) and value:
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict) and parsed:
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def _normalize_tool_call(tool_call):
    """将非 dict 的 tool_call 对象归一化为 dict。

    兼容 LangChain ToolCallChunk 等 TypedDict / 对象，提取
    args / function / arguments / parameters / input 属性。
    """
    if isinstance(tool_call, dict):
        return tool_call

    normalized = {
        "args": getattr(tool_call, "args", None),
        "arguments": getattr(tool_call, "arguments", None),
    }
    if hasattr(tool_call, "function"):
        normalized["function"] = {"arguments": getattr(tool_call, "function")}
    if hasattr(tool_call, "parameters"):
        normalized["parameters"] = getattr(tool_call, "parameters")
    if hasattr(tool_call, "input"):
        normalized["input"] = getattr(tool_call, "input")
    return normalized


def extract_tool_params(tool_call) -> dict:
    """统一的工具参数提取入口，支持多种格式的 tool_call。

    支持的格式：
        1. LangChain tool_call: {"name": ..., "args": {...}, "id": ...}
        2. AIMessageChunk tool_call_chunk: {"name": ..., "args": "...", "id": ..., "index": ...}
        3. MCP tool_call: {"function": {"name": ..., "arguments": "{...}"}}
        4. OpenAI function call: {"function": {"name": ..., "arguments": "..."}, "id": ...}

    提取优先级（依次 fallback）：
        1. args（dict 直接返回；str 尝试 JSON 解析）
        2. parameters / input（内部存储 / 部分 agent 格式）
        3. function.arguments（MCP / OpenAI 格式，arguments 通常为 JSON 字符串）
        4. 顶层 arguments

    Args:
        tool_call: dict 或带属性的对象，包含工具调用信息。

    Returns:
        dict: 工具参数字典，解析失败返回空字典 {}。
    """
    if tool_call is None:
        return {}

    tool_call = _normalize_tool_call(tool_call)

    # 1. args（LangChain 标准格式）
    result = _parse_args_value(tool_call.get("args"))
    if result is not None:
        logger.debug(
            "[extract_tool_params] 从 args 提取: keys=%s",
            list(result.keys()),
        )
        return result

    # 2. parameters（内部存储格式）/ input（部分 agent 格式）
    for field in ("parameters", "input"):
        result = _parse_args_value(tool_call.get(field))
        if result is not None:
            logger.debug(
                "[extract_tool_params] 从 %s 提取: keys=%s",
                field, list(result.keys()),
            )
            return result

    # 3. function.arguments（MCP / OpenAI 格式）
    function_info = tool_call.get("function") or {}
    if isinstance(function_info, dict):
        result = _parse_args_value(function_info.get("arguments"))
        if result is not None:
            logger.debug(
                "[extract_tool_params] 从 function.arguments 提取: keys=%s",
                list(result.keys()),
            )
            return result

    # 4. 顶层 arguments
    result = _parse_args_value(tool_call.get("arguments"))
    if result is not None:
        logger.debug(
            "[extract_tool_params] 从 arguments 提取: keys=%s",
            list(result.keys()),
        )
        return result

    logger.debug(
        "[extract_tool_params] 无有效参数，返回空字典: tool_call_keys=%s",
        list(tool_call.keys()) if isinstance(tool_call, dict) else "non-dict",
    )
    return {}
