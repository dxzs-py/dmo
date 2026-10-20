"""
流式聊天辅助工具方法

从 chat_service.py 拆分出的通用流式处理逻辑：
- 消息块解析（重导出自 stream_chunk_processors.py）
- 用量/Token 更新
- 上下文信息构建
- 审批中断解析
- 持久化结果合并

架构说明：
    - **块处理**：统一由 ``stream_chunk_processors`` 实现。
    - **工具调用生命周期事件**：统一由 ``apps.tools.tool_event_extractor``
      （归一化）+ ``common.tool_event_publisher``（发布编排）实现，
      ``stream.loop`` 调用，本模块不再中转工具事件。
    - **本文件仅保留 CHAT 模块特有的聚合与审计逻辑**（用量汇总、审批解析、合并 approval 字段等）。
"""

import logging
import time
from typing import Any

# ---------------------------------------------------------------------------
# 重导出：统一块处理（Chat 模块特有的调用方通过本模块导入以保持兼容）
# ---------------------------------------------------------------------------
# extract_thinking_content 单源于 ai_engine/services/thinking.py（G1 合并），
# 本模块保留重导出契约（外部模块经此导入，__all__ 防止 F401 误删）。
from Django_xm.apps.ai_engine.services.thinking import extract_thinking_content

from .stream_chunk_processors import (
    _sync_pending_to_stream_state,
    process_stream_chunk,
)

# 重导出契约：外部模块（loop.py / fallback.py / finalizer.py 等）
# 经本模块导入上述名字，列入 __all__ 防止 F401 误删
__all__ = [
    "_sync_pending_to_stream_state",
    "build_context_info",
    "extract_interrupt_ids",
    "extract_thinking_content",
    "merge_existing_approval_fields",
    "process_stream_chunk",
    "sync_usage_tracker_model",
    "update_usage_and_tokens",
]

# ---------------------------------------------------------------------------
# 重导出：持久化（聊天/审批恢复共用）
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 重导出：工具调用生命周期事件发布（三模块通用）
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


# ===========================================================================
# 以下为 CHAT 模块独有的聚合 / 审计 / 审批相关逻辑
# ===========================================================================


def build_context_info(usage_tracker, token_detail_tracker, stream_start_time=None):
    context_info = usage_tracker.get_usage_info()
    token_summary = token_detail_tracker.get_summary()
    context_info["tokens"] = token_summary["tokens"]
    context_info["token_detail"] = token_detail_tracker.get_token_detail()
    context_info["model"] = usage_tracker.model_id
    context_info["total_tokens"] = usage_tracker.get_total_tokens()
    if stream_start_time is not None:
        context_info["response_time"] = round(time.time() - stream_start_time, 2)
    return context_info


def sync_usage_tracker_model(usage_tracker, fallback_info: Any) -> None:
    """模型降级后将 usage_tracker.model_id 同步为实际执行模型（审计 F2-1）

    chat 链对齐 research 链语义（token_counter._current_model 记实际模型）：
    model_fallback 事件产生时前端已把模型选择器同步为 actual，而 context 事件
    （build_context_info）与 ChatMessage.model 落库（_update_last_message_tokens）
    均读取 usage_tracker.model_id——三个 model_fallback 产生点必须先调用本函数，
    保证 DB 记录与用户所见一致。

    Args:
        usage_tracker: UsageTracker 实例（context_info / ChatMessage.model 数据源）
        fallback_info: model_fallback 事件 data（含 actual_model），None/空安全
    """
    if not fallback_info:
        return
    actual_model = str(fallback_info.get("actual_model") or "").strip()
    if actual_model:
        usage_tracker.model_id = actual_model


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


def extract_interrupt_ids(intr: Any) -> tuple:
    """从 LangGraph interrupt 对象提取 (value, graph_interrupt_id, langgraph_resume_id)

    兼容三种形态：
    - langgraph.types.Interrupt 实例（取 .value 和 .id）
    - dict（取 "value" 和 "id" 键）
    - 其他原始值（value=intr 本身，id 为空串）

    返回的 langgraph_resume_id 当前与 graph_interrupt_id 相同，
    保留独立字段以便未来支持 resume 协议的扩展。

    命名澄清（重要）：返回的 ``graph_interrupt_id`` 实为 **LangGraph Interrupt.id**
    （``intr.id``，resume 协议合法 key），与 ApprovalMiddleware 自造的批次 uuid
    （``interrupt_value["_meta"]["graph_interrupt_id"]``）**同名不同值**。
    ``approval_parser.parse_approval_interrupt`` 以 ``_meta`` 为准，本函数返回的
    graph_interrupt_id 在 _meta 存在时仅作兜底，不会被写入 approval_data。
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
