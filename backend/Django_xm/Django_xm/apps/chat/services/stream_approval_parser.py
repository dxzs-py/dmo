"""审批中断解析辅助（Task 15.2 从 stream_helpers.py 拆分）。

集中处理 LangGraph Interrupt 到前端 approval 事件的解析与合并：
- ``extract_interrupt_ids``：统一提取审批 ID 三字段（三模块共享）
- ``_parse_single_approval_request``：解析单个审批请求 dict
- ``parse_approval_interrupt``：解析审批中断值为 approval 事件数据列表
- ``merge_existing_approval_fields``：按 tool_call_id 索引合并旧 approval 字段

依赖方向：本模块仅依赖标准库与 ``langgraph.types``（延迟导入），不依赖其他 stream_* 子模块。
"""

import json as _json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def extract_interrupt_ids(intr: Any) -> tuple:
    """从 LangGraph Interrupt 对象中统一提取审批 ID 三字段（三模块共享）。

    集中处理 intr 类型差异（Interrupt 对象 vs dict）与 _meta.graph_interrupt_id 读取，
    避免每个调用方重复实现导致行为不一致。

    返回字段语义：
    - interrupt_value: interrupt 的 value（透传给 parse_approval_interrupt）
    - graph_interrupt_id: 批次 ID（ApprovalMiddleware 生成，嵌入 _meta.graph_interrupt_id；
      无 _meta 时回退为 intr.id，用于 DB 查询和前端 grouping）
    - langgraph_resume_id: LangGraph 的 intr.id（Command(resume=...) 恢复 KEY，不可与批次 ID 混用）

    Args:
        intr: LangGraph Interrupt 对象或 dict

    Returns:
        (interrupt_value, graph_interrupt_id, langgraph_resume_id) 三元组
    """
    # 延迟导入 Interrupt 类型，仅用于 isinstance 检查；导入失败时回退为 dict 路径
    _InterruptType: type | None = None
    try:
        from langgraph.types import Interrupt

        _InterruptType = Interrupt
    except ImportError:
        pass

    if _InterruptType is not None and isinstance(intr, _InterruptType):
        # mypy 无法基于 type | None 变量收窄 intr 类型，使用 getattr 安全访问
        interrupt_value = getattr(intr, "value", intr)
        intr_id = getattr(intr, "id", "") or ""
    elif isinstance(intr, dict):
        interrupt_value = intr.get("value", intr)
        intr_id = intr.get("id", "") or ""
    else:
        interrupt_value = intr
        intr_id = ""

    # 优先从 _meta 读取批次 UUID（ApprovalMiddleware 生成）；无 _meta 时回退为 intr.id
    if isinstance(interrupt_value, dict):
        meta = interrupt_value.get("_meta") or {}
        batch_id = (meta.get("graph_interrupt_id") if isinstance(meta, dict) else "") or intr_id
    else:
        batch_id = intr_id

    # 记录 intr 元信息用于 stream 中断调试
    logger.debug(
        f"[extract_interrupt_ids] intr_type={type(intr).__name__}, "
        f"intr_id={intr_id!r}, batch_id={batch_id!r}, "
        f"value_type={type(interrupt_value).__name__}"
    )

    return interrupt_value, batch_id, intr_id


def _parse_single_approval_request(
    request: dict,
    graph_interrupt_id: str = "",
    langgraph_resume_id: str = "",
    tool_calls_map: dict[str, dict] | None = None,
    tool_args_accumulator: dict[str, str] | None = None,
    used_tool_call_ids: set | None = None,
) -> dict[str, Any] | None:
    """解析单个审批请求 dict 为前端 approval 事件数据。

    兼容两种输入：
    - 批量格式下 requests 列表的元素（含 tool_call_id / tool_name / args 等）
    - 单工具旧格式（interrupt_value 本身即单个请求 dict）

    关键字段映射：
    - interrupt_id 优先取 request.tool_call_id（前端用此 ID 调用 resume 端点），
      回退到 graph_interrupt_id / request.interrupt_id 以兼容旧格式。
    - graph_interrupt_id 取入参（批量格式下为批次 ID，前端用此 ID 分组）。

    若 request 携带 operation，则尝试匹配 tool_calls_map /
    tool_args_accumulator 中的 llm_tool_call_id，便于前端关联工具调用卡片。

    Args:
        request: 单个审批请求 dict
        graph_interrupt_id: 批次/图节点 interrupt id（批量格式下为批次 ID）
        langgraph_resume_id: resume 协议使用的 id
        tool_calls_map: 工具调用映射（用于匹配 llm_tool_call_id）
        tool_args_accumulator: 工具参数累积器（流式参数更完整）
        used_tool_call_ids: 已使用的 tool_call_id 集合（用于去重，避免重复审批）

    Returns:
        approval_data dict；输入非 dict 时返回 None
    """
    if not isinstance(request, dict):
        return None

    tool_calls_map = tool_calls_map or {}
    tool_args_accumulator = tool_args_accumulator or {}
    used_tool_call_ids = used_tool_call_ids or set()

    tool_name = request.get("tool_name", "unknown")
    action = request.get("action", "confirm")
    # interrupt_id 优先使用 tool_call_id（批量格式下每个工具独立 ID），
    # 回退到 graph_interrupt_id（旧格式批次 ID）再回退到 request.interrupt_id
    interrupt_id = request.get("tool_call_id") or graph_interrupt_id or request.get("interrupt_id", "")

    approval_data = {
        "tool_name": tool_name,
        "tool_call_id": interrupt_id,
        "interrupt_id": interrupt_id,
        "graph_interrupt_id": graph_interrupt_id,
        "langgraph_resume_id": langgraph_resume_id,
        "title": request.get("title", "确认操作"),
        "description": request.get("description", ""),
        "action": action,
        "danger_level": request.get("danger_level", "medium"),
        "state": "pending",
    }

    # 透传 operation（统一字段，兼容旧 command）
    op = request.get("operation") or request.get("command") or ""
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
    if request.get("extra"):
        approval_data["extra"] = request["extra"]
    # 透传 input_placeholder（CONFIRM_WITH_INPUT 模式）
    if request.get("input_placeholder"):
        approval_data["input_placeholder"] = request["input_placeholder"]

    # 提取 parameters（工具参数），供前端审批卡片展示
    # 优先级：request.args > tool_args_accumulator(by llm_tc_id) >
    # tool_calls_map.parameters(by llm_tc_id) > tool_calls_map.parameters(by tool_name) >
    # operation 构造
    parameters = request.get("args")
    llm_tc_id = approval_data.get("llm_tool_call_id", "")
    if not parameters:
        # 从 tool_args_accumulator 提取（流式累积的完整参数）
        if llm_tc_id and llm_tc_id in tool_args_accumulator:
            accumulated = tool_args_accumulator.get(llm_tc_id, "")
            if accumulated:
                try:
                    parameters = _json.loads(accumulated) if isinstance(accumulated, str) else accumulated
                except (_json.JSONDecodeError, TypeError):
                    pass
    if not parameters and llm_tc_id:
        # 从 tool_calls_map 按 llm_tool_call_id 提取
        for tc_key, tc_info in tool_calls_map.items():
            if tc_info.get("id") == llm_tc_id or tc_key == llm_tc_id:
                parameters = tc_info.get("parameters", {})
                break
    if not parameters:
        # 从 tool_calls_map 按工具名匹配
        for _tc_key, tc_info in tool_calls_map.items():
            if tc_info.get("name") == tool_name:
                parameters = tc_info.get("parameters", {})
                break
    if not parameters and op:
        # 回退：从 operation 构造最小参数
        parameters = {"command": op}
    approval_data["parameters"] = parameters if isinstance(parameters, dict) else {}

    return approval_data


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

    # 批量格式检测：ApprovalMiddleware 产生
    # {"_approval": True, "requests": [...], "_meta": {"graph_interrupt_id": ...}}
    # 每个 request 携带自身的 tool_call_id / tool_name / args 等，需独立解析。
    # 注意：空 requests 列表也走批量分支并返回 []（避免误入单工具分支产生 unknown 项）。
    if interrupt_value.get("_approval") is True and isinstance(interrupt_value.get("requests"), list):
        batch_meta = interrupt_value.get("_meta") or {}
        # 优先使用 LangGraph 实际 interrupt ID（来自 extract_interrupt_ids，用于 resume 校验），
        # 回退到 _meta.graph_interrupt_id（ApprovalMiddleware 生成的 UUID，仅用于前端 grouping）
        batch_graph_interrupt_id = graph_interrupt_id or batch_meta.get("graph_interrupt_id", "")
        result: list[dict[str, Any]] = []
        for request in interrupt_value["requests"]:
            if not isinstance(request, dict):
                continue
            approval_data = _parse_single_approval_request(
                request,
                graph_interrupt_id=batch_graph_interrupt_id,
                langgraph_resume_id=langgraph_resume_id,
                tool_calls_map=tool_calls_map,
                tool_args_accumulator=tool_args_accumulator,
                used_tool_call_ids=used_tool_call_ids,
            )
            if approval_data:
                result.append(approval_data)
        return result

    # 单工具旧格式：interrupt_value 本身即单个审批请求 dict，保持向后兼容
    approval_data = _parse_single_approval_request(
        interrupt_value,
        graph_interrupt_id=graph_interrupt_id,
        langgraph_resume_id=langgraph_resume_id,
        tool_calls_map=tool_calls_map,
        tool_args_accumulator=tool_args_accumulator,
        used_tool_call_ids=used_tool_call_ids,
    )
    return [approval_data] if approval_data else []


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
