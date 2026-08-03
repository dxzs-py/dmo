"""
流式聊天辅助工具方法

从 chat_service.py 拆分出的通用流式处理逻辑：
- 消息块解析（重导出自 stream_chunk_processors.py）
- 工具调用生命周期（重导出自 stream_tool_lifecycle.py）
- 用量/Token 更新
- 上下文信息构建
- 审批中断解析
- 持久化结果合并

架构说明：
    - **块处理与生命周期事件**：统一由 ``stream_chunk_processors`` / ``stream_tool_lifecycle`` 实现
      （三模块通用：CHAT / DEEP_RESEARCH / LEARNING）。
    - **本文件仅保留 CHAT 模块特有的聚合与审计逻辑**（用量汇总、审批解析、合并 approval 字段等）。
"""

import json as _json
import logging
import time
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

# ---------------------------------------------------------------------------
# 重导出：统一块处理（三模块通用，Chat 模块特有的调用方通过本模块导入以保持兼容）
# ---------------------------------------------------------------------------
from .stream_chunk_processors import (
    _STATE_TO_STATUS,
    _extract_tool_params,
    _find_tool_call_key_by_index,
    _fix_groq_tool_call,
    _handle_ai_message_chunk,
    _handle_tool_message_chunk,
    _map_state_to_status,
    _migrate_key_if_needed,
    _sync_pending_to_stream_state,
    _try_parse_concatenated_json,
    extract_thinking_content,
    process_stream_chunk,
)

from .stream_tool_state import (
    _detect_tool_error,
    _detect_tool_rejected,
    _detect_tool_timeout,
)

# ---------------------------------------------------------------------------
# 重导出：工具调用生命周期事件发布（三模块通用）
# ---------------------------------------------------------------------------
from .stream_tool_lifecycle import _publish_tool_lifecycle_event, _broadcast_tool_input_ready

# ---------------------------------------------------------------------------
# 重导出：持久化（聊天/审批恢复共用）
# ---------------------------------------------------------------------------
from .stream_persistence import persist_stream_result

logger = logging.getLogger(__name__)


# ===========================================================================
# 以下为 CHAT 模块独有的聚合 / 审计 / 审批相关逻辑
# ===========================================================================


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
    """解析审批中断值为前端 approval 事件数据列表。

    委托 `common.approval_parser.parse_approval_interrupt` 完成核心解析
    （批量/单工具格式 → approval_data 列表），然后进行聊天模块特有的
    llm_tool_call_id 匹配（通过 operation 命令匹配流式工具参数）。

    Args:
        interrupt_value: interrupt 的值
        graph_interrupt_id: graph 节点的 interrupt id
        langgraph_resume_id: resume 协议使用的 id
        tool_calls_map: 工具调用映射（用于匹配 llm_tool_call_id，聊天特性）
        tool_args_accumulator: 工具参数累积器（流式参数更完整，聊天特性）
        used_tool_call_ids: 已使用的 tool_call_id 集合（去重，聊天特性）

    Returns:
        approval_data 列表
    """
    from Django_xm.common.approval_parser import parse_approval_interrupt as _base_parse

    parsed_list = _base_parse(interrupt_value, graph_interrupt_id, langgraph_resume_id)
    if not parsed_list:
        return []

    tool_calls_map = tool_calls_map or {}
    tool_args_accumulator = tool_args_accumulator or {}
    used_tool_call_ids = used_tool_call_ids or set()

    for entry in parsed_list:
        _match_llm_tool_call_id(
            entry=entry,
            tool_calls_map=tool_calls_map,
            tool_args_accumulator=tool_args_accumulator,
            used_tool_call_ids=used_tool_call_ids,
        )

    return parsed_list


def _match_llm_tool_call_id(
    entry: dict[str, Any],
    tool_calls_map: dict[str, dict],
    tool_args_accumulator: dict[str, str],
    used_tool_call_ids: set,
) -> None:
    """为 approval_data 条目匹配 llm_tool_call_id。

    通过 operation 命令匹配流式工具参数，找到对应的 LLM 工具调用 ID。
    匹配逻辑：先检查 tool_args_accumulator（更完整的累积参数），
    再检查 tool_calls_map 中的 parameters，最后回退到同名工具中最后一个。
    """
    tool_name = entry.get("tool_name", "")
    op = entry.get("operation", "")
    if not op or not tool_name:
        return

    # 先检查 tool_args_accumulator 中的累积参数（更完整）
    for tc_key, tc_info in tool_calls_map.items():
        if tc_info.get("name") != tool_name:
            continue
        if tc_key in used_tool_call_ids or tc_info.get("id") in used_tool_call_ids:
            continue
        accumulated_args = tool_args_accumulator.get(tc_key, "")
        if accumulated_args:
            try:
                import json as _json
                parsed_args = (
                    _json.loads(accumulated_args) if isinstance(accumulated_args, str) else accumulated_args
                )
                if any(str(v) == op for v in (parsed_args or {}).values()):
                    entry["llm_tool_call_id"] = tc_info.get("id") or tc_key
                    return
            except (_json.JSONDecodeError, TypeError):
                pass
        tc_params = tc_info.get("parameters", {})
        if any(str(v) == op for v in tc_params.values()):
            entry["llm_tool_call_id"] = tc_info.get("id") or tc_key
            return

    # 回退：同名工具中最后一个（interrupt 总是最新的调用）
    for tc_key, tc_info in reversed(list(tool_calls_map.items())):
        if tc_info.get("name") == tool_name:
            if tc_key in used_tool_call_ids or tc_info.get("id") in used_tool_call_ids:
                continue
            entry["llm_tool_call_id"] = tc_info.get("id") or tc_key
            break


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