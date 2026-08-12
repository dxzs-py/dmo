"""工具事件提取公共模块

从流式消息（AIMessage / AIMessageChunk / ToolMessage）中提取工具调用生命周期事件，
供 subagent_patch 和 official_deep_agent 复用，消除两处重复且不一致的检测逻辑。

事件类型（字符串值，与 EventType 枚举一致）：
- 'tool_call_input_ready': 工具调用参数已就绪（AIMessage 阶段或 ToolMessage 阶段补发）
- 'tool_call_completed':   工具执行完成（成功）
- 'tool_call_failed':      工具执行失败

设计原则：
- 纯函数：不调用任何回调，只返回事件列表，由调用方决定如何发布
- 无上下文依赖：只处理传入的 message，不依赖 thread_id / subagent_type 等
- 可独立测试：顶层仅依赖 stdlib，Django_xm.* 全部懒加载，模块可在无 Django 运行时下导入
- 幂等去重：通过 seen_tool_call_ids 集合保证同一 tool_call_id 的 INPUT_READY 事件只发射一次

来源：
- 核心提取逻辑（AIMessage/AIMessageChunk 处理 + tool_call_chunks 聚合 + 补发）：
  subagent_patch.py L394-575
- TOOL_CALL_FAILED 检测逻辑：official_deep_agent.py L748-776
  （subagent_patch.py 原本只发 COMPLETED，此处补全 FAILED 分支）
"""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def extract_tool_events_from_message(
    message: Any,
    seen_tool_call_ids: set,
    accumulated_messages: list,
) -> list[dict[str, Any]]:
    """从单条流式消息中提取工具事件。

    处理逻辑（与 subagent_patch.py L394-575 一致，并补全 FAILED 分支）：

    1. AIMessage / AIMessageChunk 含 tool_calls：
       - 将 message 追加到 accumulated_messages
       - 遍历 tool_calls，用 seen_tool_call_ids 去重
       - args 为空时不发射、不去重（等后续 chunk 或 ToolMessage 补发）
       - args 非空时加入 seen_tool_call_ids，发射 TOOL_CALL_PENDING
       注意：AIMessageChunk 是 AIMessage 的子类，isinstance(msg, AIMessage)
       对两者均成立，无需单独处理 chunk。

    2. ToolMessage：
       - 将 message 追加到 accumulated_messages
       - 从累积 AIMessageChunk 聚合 tool_call_chunks 的 args 字符串：
         * 遍历 accumulated_messages 的 tool_call_chunks
         * 按 index 分组拼接 args 字符串
         * 按 index 匹配 tool_call_id，解析完整 JSON
         * 解析失败时 fallback 到 parse_partial_json
         * 仍失败时 fallback 到 AIMessage.tool_calls 的 extract_tool_params
       - 若 AIMessageChunk 阶段未发射（tc_id 不在 seen_tool_call_ids），
         补发 TOOL_CALL_PENDING
       - 检测工具执行状态（status=='error' 或 content 以 'Error' 开头）：
         * 失败：发射 TOOL_CALL_FAILED（携带 error）
         * 成功：发射 TOOL_CALL_COMPLETED（携带 result）

    Args:
        message: 流式消息，预期为 AIMessage / AIMessageChunk / ToolMessage 之一。
                 其他类型将被忽略（返回空列表）。
        seen_tool_call_ids: 已发射 INPUT_READY 的 tool_call_id 集合。
                            本函数会就地修改此集合（新增已发射的 id）。
        accumulated_messages: 累积的消息列表，用于 ToolMessage 阶段回查参数。
                              本函数会就地追加当前 message（若为 AIMessage/ToolMessage）。

    Returns:
        事件列表，按时间顺序排列。每个事件为 dict，字段如下：
        - event_type: str  ('tool_call_input_ready' / 'tool_call_completed' / 'tool_call_failed')
        - tool_call_id: str
        - tool_name: str
        - parameters: dict
        - result: str   (仅 'tool_call_completed' 事件携带)
        - error: str    (仅 'tool_call_failed' 事件携带)

        调用方应遍历事件列表并调用 on_tool_event 回调发布，例如：

            for evt in extract_tool_events_from_message(msg, seen, accumulated):
                result = on_tool_event(
                    evt['event_type'],
                    evt['tool_call_id'],
                    evt['tool_name'],
                    parameters=evt['parameters'],
                    **{'result': evt['result']} if 'result' in evt else
                      **{'error': evt['error']} if 'error' in evt else {},
                )
    """
    # 懒加载：避免模块导入时触发 Django 运行时初始化，保证模块可独立测试
    from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

    from Django_xm.apps.tools.param_extractor import extract_tool_params, strip_internal_fields
    from Django_xm.common.event_schema import EventType

    events: list[dict[str, Any]] = []

    # ---- 分支 1：AIMessage / AIMessageChunk ----
    # 注意：AIMessageChunk 是 AIMessage 的子类，isinstance(msg, AIMessage) 对两者均成立。
    # 原官方 deep_agent 用 `not isinstance(message, AIMessageChunk)` 排除 chunk，
    # 导致 deepagents astream（只产出 AIMessageChunk）的 INPUT_READY 事件从未转发。
    #
    # 关键：所有 AIMessage/AIMessageChunk 都加入 accumulated_messages，即使 tool_calls
    # 为空（但 tool_call_chunks 可能有内容）。流式 chunk 可能因 args 不完整导致
    # tool_calls 返回空列表，但 tool_call_chunks 仍携带 args 分片，ToolMessage
    # 阶段需要从累积的 tool_call_chunks 聚合完整参数。若不加入累积列表，会导致
    # 聚合丢失分片，参数解析失败，前端显示为 {}。
    #
    # ⚠️ 关键 bug 修复（Task 6.8）：
    # AIMessageChunk.tool_calls 属性内部使用 parse_partial_json 解析 args 字符串，
    # 对不完整的 JSON 可能返回非空但残缺的 dict（如 '{"file_path": "/san' 被解析为
    # {"file_path": "/san"}）。若直接使用 .tool_calls 发射 INPUT_READY，会携带
    # 不完整参数过早发射，前端展示残缺参数。
    # 修复：对 AIMessageChunk，直接从 tool_call_chunks 读取原始 args 字符串，
    # 用严格 json.loads 解析（仅完整 JSON 才成功），避免 parse_partial_json 的
    # 宽容解析。完整 AIMessage 的 .tool_calls 的 args 已是 dict，可直接使用。
    if isinstance(message, AIMessage):
        accumulated_messages.append(message)
        if isinstance(message, AIMessageChunk):
            # AIMessageChunk：从 tool_call_chunks 用严格 JSON 解析 args
            tccs = getattr(message, "tool_call_chunks", None) or []
            for tcc in tccs:
                if isinstance(tcc, dict):
                    tc_id = tcc.get("id") or ""
                    tc_name = tcc.get("name") or ""
                    tc_args_str = tcc.get("args") or ""
                else:
                    tc_id = getattr(tcc, "id", "") or ""
                    tc_name = getattr(tcc, "name", "") or ""
                    tc_args_str = getattr(tcc, "args", "") or ""
                # 去重：同一 tool_call_id 只发射一次
                if not tc_id or tc_id in seen_tool_call_ids:
                    continue
                # 严格 JSON 解析：仅完整 JSON 才发射 INPUT_READY。
                # args 为空或不完整时跳过，等后续 chunk 或 ToolMessage 阶段聚合补发。
                if not tc_args_str or not tc_args_str.strip():
                    continue
                try:
                    parsed_args = json.loads(tc_args_str)
                except (json.JSONDecodeError, ValueError):
                    continue  # args 不完整，等后续 chunk 或 ToolMessage 阶段补发
                # 解析为 dict / list 时才发射
                if isinstance(parsed_args, dict) and parsed_args:
                    tc_args = strip_internal_fields(parsed_args)
                    if not tc_args:
                        continue
                    seen_tool_call_ids.add(tc_id)
                    events.append(
                        {
                            "event_type": EventType.TOOL_CALL_PENDING,
                            "tool_call_id": tc_id,
                            "tool_name": tc_name or "unknown",
                            "parameters": tc_args,
                        }
                    )
                elif isinstance(parsed_args, list) and parsed_args:
                    seen_tool_call_ids.add(tc_id)
                    events.append(
                        {
                            "event_type": EventType.TOOL_CALL_PENDING,
                            "tool_call_id": tc_id,
                            "tool_name": tc_name or "unknown",
                            "parameters": {"items": parsed_args},
                        }
                    )
        # 完整 AIMessage（非 chunk）：tool_calls 的 args 已是完整 dict，直接使用
        elif getattr(message, "tool_calls", None):
            for tc in message.tool_calls:
                tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                tc_name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
                # 去重：同一 tool_call_id 只发射一次
                if not tc_id or tc_id in seen_tool_call_ids:
                    continue
                tc_args = strip_internal_fields(extract_tool_params(tc))
                # args 为空时不发射、不去重，
                # 等后续 chunk 或 ToolMessage 阶段补发。
                if not tc_args:
                    continue
                seen_tool_call_ids.add(tc_id)
                events.append(
                    {
                        "event_type": EventType.TOOL_CALL_PENDING,
                        "tool_call_id": tc_id,
                        "tool_name": tc_name or "unknown",
                        "parameters": tc_args,
                    }
                )

    # ---- 分支 2：ToolMessage ----
    elif isinstance(message, ToolMessage):
        accumulated_messages.append(message)
        tc_id = getattr(message, "tool_call_id", "") or ""
        tool_name = getattr(message, "name", "") or ""
        content = getattr(message, "content", "")
        if isinstance(content, list):
            try:
                content_str = json.dumps(content, ensure_ascii=False)
            except Exception:
                content_str = str(content)
        else:
            content_str = str(content) if content else ""
        if not tc_id:
            return events

        # 从累积 AIMessageChunk 中聚合 tool_call_chunks，获取完整参数。
        # 每个 chunk 的 tool_calls args 可能不完整（parse_partial_json 返回 {}），
        # 需要聚合所有 chunk 的 tool_call_chunks 的 args 字符串，按 cc_id
        # 分组拼接后解析完整 JSON。
        # 注意：LangChain 的 tool_call_chunks.index 是"当前 LLM 调用内的序号"，
        # 每轮 LLM 调用都从 0 开始。accumulated_messages 跨整个任务期间持续累积，
        # 若按 index 聚合会导致跨轮次 index=0 被多个 cc_id 共享，args 字符串被
        # 错误拼接。因此改为按 cc_id（tool_call_id）聚合，保证每个 tool_call_id
        # 独立累积 args，跨 LLM 调用轮次不会混合。
        tool_parameters: dict = {}
        try:
            chunk_args_by_id: dict = {}
            for prev_msg in accumulated_messages:
                tccs = getattr(prev_msg, "tool_call_chunks", None)
                if not tccs:
                    continue
                for tcc in tccs:
                    if isinstance(tcc, dict):
                        cc_id = tcc.get("id") or ""
                        cc_args = tcc.get("args") or ""
                    else:
                        cc_id = getattr(tcc, "id", "") or ""
                        cc_args = getattr(tcc, "args", "") or ""
                    if not cc_id:
                        continue  # 跳过无 id 的 chunk（无法归属）
                    if cc_args:
                        # 类型归一化：args 可能是 str（分片/完整 JSON）或 dict/list
                        # （个别 provider 直接在 chunk 中给出完整对象）。统一转 str
                        # 后拼接，避免 `str + dict` 拼接 TypeError 导致参数聚合失败
                        # （根因：write_todos 等工具参数恒为空 []）。
                        if isinstance(cc_args, (dict, list)):
                            cc_args = json.dumps(cc_args, ensure_ascii=False)
                        else:
                            cc_args = str(cc_args)
                        chunk_args_by_id[cc_id] = chunk_args_by_id.get(cc_id, "") + cc_args
            # 直接按 tc_id 取累积的 args
            agg_args_str = chunk_args_by_id.get(tc_id, "")
            if agg_args_str and agg_args_str.strip():
                try:
                    parsed = json.loads(agg_args_str)
                    if isinstance(parsed, dict):
                        tool_parameters = parsed
                    elif isinstance(parsed, list):
                        tool_parameters = {"items": parsed}
                except (json.JSONDecodeError, ValueError):
                    try:
                        from langchain_core.utils.json import parse_partial_json

                        parsed = parse_partial_json(agg_args_str)
                        if isinstance(parsed, dict) and parsed:
                            tool_parameters = parsed
                    except Exception:
                        # 部分JSON解析失败时回退到从单个 AIMessage 提取
                        logger.debug("parse_partial_json 解析失败，回退到 AIMessage 提取")
            # 从单个 AIMessage.tool_calls 提取（聚合未命中时的 fallback）
            if not tool_parameters:
                for prev_msg in accumulated_messages:
                    if not getattr(prev_msg, "tool_calls", None):
                        continue
                    for tc in prev_msg.tool_calls:
                        prev_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
                        if str(prev_id) == str(tc_id):
                            tool_parameters = strip_internal_fields(extract_tool_params(tc))
                            break
                    if tool_parameters:
                        break
        except Exception as e:
            logger.debug(
                f"[ToolEventExtractor] 聚合 tool_call_chunks 参数失败: tc_id={tc_id}, tool={tool_name}, err={e}"
            )

        # 补发 tool 事件：若 AIMessageChunk 阶段因 args 不完整未发射，
        # 此处从累积消息回查到完整参数后补发，确保父 SSE 流收到带
        # 完整 parameters 的 tool (input) 事件，再转发 tool_result。
        if tc_id not in seen_tool_call_ids:
            seen_tool_call_ids.add(tc_id)
            events.append(
                {
                    "event_type": EventType.TOOL_CALL_PENDING,
                    "tool_call_id": tc_id,
                    "tool_name": tool_name,
                    "parameters": tool_parameters,
                }
            )

        # 检测工具执行状态：status=='error' 或 content 以 'Error' 开头
        # （来源：official_deep_agent.py L753-756）
        is_error = getattr(message, "status", None) == "error" or (
            isinstance(content, str) and content.startswith("Error")
        )
        if is_error:
            events.append(
                {
                    "event_type": EventType.TOOL_CALL_FAILED,
                    "tool_call_id": tc_id,
                    "tool_name": tool_name,
                    "parameters": tool_parameters,
                    "error": content if isinstance(content, str) else str(content),
                }
            )
        else:
            events.append(
                {
                    "event_type": EventType.TOOL_CALL_COMPLETED,
                    "tool_call_id": tc_id,
                    "tool_name": tool_name,
                    "parameters": tool_parameters,
                    "result": content_str,
                }
            )

    return events
