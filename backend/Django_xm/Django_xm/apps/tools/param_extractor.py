"""统一的工具参数提取模块

提供单一入口 extract_tool_params，替代散落在 stream_helpers / adapter /
subagent_support 等多处的重复实现。支持 dict 和非 dict（带属性的对象）输入。

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
        normalized["function"] = {"arguments": tool_call.function}
    if hasattr(tool_call, "parameters"):
        normalized["parameters"] = tool_call.parameters
    if hasattr(tool_call, "input"):
        normalized["input"] = tool_call.input
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
                field,
                list(result.keys()),
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


# 内部字段前缀：以 `_` 开头的键为流式累积器内部状态（如 `_index`、`_stream_state`），
# 不应泄漏到工具参数 / 前端事件中。
_INTERNAL_FIELD_PREFIX = "_"


def strip_internal_fields(params):
    """递归剔除参数字典中以 `_` 开头的内部字段。

    用于在将工具参数发布到 SSE 事件 / 前端 approval 数据前，清理流式累积过程中
    注入的内部状态字段（如 `_index`），避免内部状态污染外部契约。

    - dict：递归剔除所有以 `_` 开头的键，返回新字典（不修改原字典）
    - list：递归处理每个元素，返回新列表
    - 其他类型：原样返回

    Args:
        params: 任意值，通常是 extract_tool_params 返回的 dict

    Returns:
        与输入同类型的清理后值
    """
    if isinstance(params, dict):
        return {
            k: strip_internal_fields(v)
            for k, v in params.items()
            if not (isinstance(k, str) and k.startswith(_INTERNAL_FIELD_PREFIX))
        }
    if isinstance(params, list):
        return [strip_internal_fields(item) for item in params]
    return params
