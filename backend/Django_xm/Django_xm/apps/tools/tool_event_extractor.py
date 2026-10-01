"""工具事件提取公共模块（全系统唯一实现）

从流式消息（AIMessage / AIMessageChunk / ToolMessage）中提取工具调用生命周期事件，
供 research（deep agents）、chat 子代理、chat 主链路统一复用，消除多处重复且不一致的检测逻辑
（spec unify-tool-call-pipeline 任务 2：有状态唯一提取器）。

事件类型（字符串值，与 EventType 枚举一致）：
- 'tool_call_pending':   工具调用参数已就绪（AIMessage 阶段或 ToolMessage 阶段补发）
- 'tool_call_completed': 工具执行完成（成功）
- 'tool_call_failed':    工具执行失败

设计原则：
- 有状态：``ToolEventExtractor`` 内部持有 ``accumulated_messages`` / ``seen_tool_call_ids`` /
  无 id 暂存缓冲，对外只暴露 ``feed(message) -> list[dict]``。
- 纯提取：不调用任何回调，只返回事件列表，由调用方决定如何发布。
- 无上下文依赖：只处理传入的 message，不依赖 thread_id / subagent_type 等。
- 可独立测试：顶层仅依赖 stdlib，Django_xm.* 全部懒加载，模块可在无 Django 运行时下导入。
- 幂等去重：通过 ``seen_tool_call_ids`` 保证同一 ``tool_call_id`` 的 PENDING 只发射一次。

兼容入口：
- ``extract_tool_events_from_message(msg, seen_tool_call_ids, accumulated_messages)``
  为无状态适配函数，委托到同一份 ``ToolEventExtractor`` 核心逻辑（保留兼容入口，
  禁止双份实现）。
"""

import json
import logging
import re
from typing import Any

from Django_xm.common.messages import content_to_str

logger = logging.getLogger(__name__)

# 审批注入的终态 ToolMessage（超时/拒绝）关键字：命中时跳过 FAILED/COMPLETED 终态判定。
# 与 stream_tool_state（已移除）的 _detect_tool_timeout / _detect_tool_rejected 语义一致。
_APPROVAL_FINAL_KEYWORDS = ("审批超时", "用户已拒绝")

# 工具输出错误前缀（本地化，全系统唯一声明源）：命中即判定为 FAILED。
# - 覆盖旧 chat stream_tool_state._detect_tool_error 的完整前缀集合（中文/英文）
# - "Error" 同时覆盖 "Error:" 形式与 research adapter 原有的裸 "Error" 前缀
_TOOL_ERROR_PREFIXES = (
    "错误：",
    "Error",
    "ERROR:",
    "FAILED",
    "失败:",
    "异常:",
    "Exception:",
)


def _is_error_content(content: Any) -> bool:
    """判断工具输出内容是否为错误（本地化前缀唯一判定）。

    仅 str 参与判定（list/None 等返回 False）；先 strip 再按前缀匹配，
    容忍首尾空白（对齐旧 stream_tool_state._detect_tool_error 语义）。
    前缀集合为全系统唯一声明源（``_TOOL_ERROR_PREFIXES``）。
    """
    if not isinstance(content, str) or not content:
        return False
    stripped = content.strip()
    return any(stripped.startswith(prefix) for prefix in _TOOL_ERROR_PREFIXES)


# Groq 等 provider 会把内联参数拼进 tool_call.name（形如 "name{...json...}"）。
_GROQ_NAME_PATTERN = re.compile(r"^(\w+)\s*(\{.*\})?\s*$", re.DOTALL)


def _tcc_get(tcc: Any, field: str, default: Any = None) -> Any:
    """读取 tool_call_chunk 字段，兼容 dict 与带属性的对象（ToolCallChunk）。"""
    if isinstance(tcc, dict):
        return tcc.get(field, default)
    return getattr(tcc, field, default)


def _normalize_args_str(value: Any) -> str:
    """将 chunk args 归一化为字符串。

    args 可能是 str（分片/完整 JSON）或 dict/list（个别 provider 直接给出完整对象）。
    统一转 str 后拼接，避免 ``str + dict`` 拼接 TypeError 导致参数聚合失败。
    """
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)
    return str(value)


def _parse_complete_json_args(args_str: str) -> Any:
    """严格 ``json.loads``：仅完整 JSON 才成功；不完整/非法返回 ``None``。

    刻意避免 LangChain ``parse_partial_json`` 的宽容解析（会把残缺 JSON 解析成
    非空但残缺的 dict，导致携带不完整参数过早发射 PENDING）。
    """
    if not args_str or not args_str.strip():
        return None
    try:
        return json.loads(args_str)
    except (json.JSONDecodeError, ValueError):
        return None


def _is_all_empty_string_dict(value: dict) -> bool:
    """判断 dict 的 string 值是否全部为空串（LLM 流式占位，如 ``{"path":"","content":""}``）。

    仅当存在至少一个 string 值且全部为空串时返回 True；无 string 值返回 False。
    """
    string_values = [v for v in value.values() if isinstance(v, str)]
    return bool(string_values) and all(v == "" for v in string_values)


class ToolEventExtractor:
    """有状态工具事件提取器（唯一实现）。

    内部持有：
    - ``accumulated_messages``：累积的消息列表（ToolMessage 阶段回查参数）。
    - ``seen_tool_call_ids``：已发射 PENDING 的 ``tool_call_id`` 集合。
    - ``_args_by_id`` / ``_names_by_id``：按 ``tool_call_id`` 聚合的 args 字符串与工具名
      （跨 chunk 分片累积，是 ToolMessage 阶段参数聚合的权威来源）。
    - ``_index_to_id`` / ``_staged``：无 id 工具调用的暂存-迁移缓冲。

    无 id 工具调用处理（spec 任务 2 要求，唯一实现）：
    - 暂存键 = ``(轮次, "idx", index)``（有 index）或 ``(轮次, "name", name)``（无 index，按名兜底）；
      轮次标识（``_round_token``）在每次收到 ToolMessage 时自增，隔离不同 LLM 轮次的 index 复用，
      避免跨轮次把 args 错误拼接。
    - chunk 无 id 时先暂存；后续 chunk 带来真实 id（同暂存键）时归并到真实 id。
    - ToolMessage 仍无 id 时，按 ``tool_name`` + 顺序兜底归属本轮暂存项，用合成 id 发射事件。

    典型用法::

        extractor = ToolEventExtractor()
        for msg in stream:
            for evt in extractor.feed(msg):
                publish(evt)
    """

    def __init__(
        self,
        accumulated_messages: list | None = None,
        seen_tool_call_ids: set | None = None,
    ) -> None:
        # 允许调用方注入既有容器（保持与 research 相同的共享语义）；缺省自建。
        self.accumulated_messages: list = (
            accumulated_messages if accumulated_messages is not None else []
        )
        self.seen_tool_call_ids: set = (
            seen_tool_call_ids if seen_tool_call_ids is not None else set()
        )
        # 按 tool_call_id 聚合的 args 字符串（跨 chunk 分片）
        self._args_by_id: dict[str, str] = {}
        # 按 tool_call_id 记忆的工具名（后续分片可能不再带 name）
        self._names_by_id: dict[str, str] = {}
        # 暂存键 -> 真实 tool_call_id（无 id 分片归并）
        self._index_to_id: dict[Any, str] = {}
        # 无 id 暂存缓冲：暂存键 -> {"name": str, "args": str}
        self._staged: dict[Any, dict] = {}
        # LLM 轮次标识：每次收到 ToolMessage 后自增
        self._round_token = 0

    def reset(self) -> None:
        """清空全部状态（含注入的共享容器），回到初始状态。"""
        self.accumulated_messages.clear()
        self.seen_tool_call_ids.clear()
        self._args_by_id.clear()
        self._names_by_id.clear()
        self._index_to_id.clear()
        self._staged.clear()
        self._round_token = 0

    # ------------------------------------------------------------------ 对外唯一入口

    def feed(self, message: Any) -> list[dict[str, Any]]:
        """喂入单条流式消息，返回归一化完成的工具事件列表。

        Args:
            message: 预期为 AIMessage / AIMessageChunk / ToolMessage 之一；
                其他类型忽略（返回空列表）。

        Returns:
            事件列表（按时间顺序）。每个事件为 dict：
            - event_type: EventType  ('tool_call_pending' / 'tool_call_completed' / 'tool_call_failed')
            - tool_call_id: str
            - tool_name: str
            - parameters: dict
            - result: str（仅 completed）
            - error: str（仅 failed）
        """
        # 懒加载：避免模块导入时触发 Django 运行时初始化，保证模块可独立测试
        from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

        events: list[dict[str, Any]] = []
        # 注意：AIMessageChunk 是 AIMessage 的子类，isinstance(message, AIMessage) 对两者均成立。
        # 所有 AIMessage/AIMessageChunk 都加入 accumulated_messages（即使 tool_calls 为空，
        # tool_call_chunks 仍可能携带 args 分片，ToolMessage 阶段需回查聚合）。
        if isinstance(message, AIMessage):
            self.accumulated_messages.append(message)
            if isinstance(message, AIMessageChunk):
                events.extend(self._extract_from_chunk(message))
            else:
                events.extend(self._extract_from_complete_message(message))
        elif isinstance(message, ToolMessage):
            self.accumulated_messages.append(message)
            events.extend(self._extract_from_tool_message(message))
        return events

    # ------------------------------------------------------------------ AIMessageChunk

    def _extract_from_chunk(self, message: Any) -> list[dict[str, Any]]:
        """处理 AIMessageChunk：从 tool_call_chunks 聚合 args，参数完整才发射 PENDING。"""
        events: list[dict[str, Any]] = []
        for tcc in getattr(message, "tool_call_chunks", None) or []:
            tc_id = _tcc_get(tcc, "id") or ""
            tc_name = _tcc_get(tcc, "name") or ""
            tc_index = _tcc_get(tcc, "index")
            args_str = _normalize_args_str(_tcc_get(tcc, "args"))
            stage_key = self._stage_key(tc_index, tc_name)

            if tc_id:
                # 真实 id 到达：先把该暂存键下的无 id 分片归并进来（暂存分片在前）
                if stage_key is not None:
                    staged = self._staged.pop(stage_key, None)
                    if staged:
                        if not tc_name and staged.get("name"):
                            tc_name = staged["name"]
                        args_str = (staged.get("args") or "") + args_str
                    self._index_to_id[stage_key] = tc_id
                if tc_name:
                    self._names_by_id[tc_id] = tc_name
                if args_str:
                    self._args_by_id[tc_id] = self._args_by_id.get(tc_id, "") + args_str
                evt = self._maybe_emit_pending(tc_id)
                if evt is not None:
                    events.append(evt)
                continue

            # 无 id：能按暂存键解析出真实 id → 归并；否则暂存等待 id 到达
            real_id = self._index_to_id.get(stage_key) if stage_key is not None else None
            if real_id is not None:
                if tc_name and not self._names_by_id.get(real_id):
                    self._names_by_id[real_id] = tc_name
                if args_str:
                    self._args_by_id[real_id] = self._args_by_id.get(real_id, "") + args_str
                evt = self._maybe_emit_pending(real_id)
                if evt is not None:
                    events.append(evt)
            elif stage_key is not None:
                staged = self._staged.setdefault(stage_key, {"name": "", "args": ""})
                if tc_name:
                    staged["name"] = tc_name
                if args_str:
                    staged["args"] = staged["args"] + args_str
            # else：既无 id 又无 index/name，无法归属，跳过（等 ToolMessage 阶段兜底）
        return events

    def _stage_key(self, tc_index: Any, tc_name: str) -> tuple | None:
        """构造无 id 暂存的隔离键（含轮次标识，隔离跨轮次 index 复用）。"""
        if tc_index is not None:
            return (self._round_token, "idx", tc_index)
        if tc_name:
            return (self._round_token, "name", tc_name)
        return None

    def _maybe_emit_pending(self, tc_id: str) -> dict[str, Any] | None:
        """累积 args 解析为完整 JSON 且非空时发射 PENDING（同一 id 只发射一次）。"""
        from Django_xm.apps.tools.param_extractor import strip_internal_fields
        from Django_xm.common.event_schema import EventType

        if not tc_id or tc_id in self.seen_tool_call_ids:
            return None
        parsed = _parse_complete_json_args(self._args_by_id.get(tc_id, ""))
        if isinstance(parsed, dict):
            if not parsed:
                return None
            # 语义完整性检查：所有 string 值均为空串 → LLM 流式占位，跳过（不发射、不去重）
            if _is_all_empty_string_dict(parsed):
                return None
            parameters = strip_internal_fields(parsed)
            if not parameters:
                return None
        elif isinstance(parsed, list):
            if not parsed:
                return None
            parameters = {"items": parsed}
        else:
            return None

        self.seen_tool_call_ids.add(tc_id)
        return {
            "event_type": EventType.TOOL_CALL_PENDING,
            "tool_call_id": tc_id,
            "tool_name": self._names_by_id.get(tc_id) or "unknown",
            "parameters": parameters,
        }

    # ------------------------------------------------------------------ 完整 AIMessage

    def _extract_from_complete_message(self, message: Any) -> list[dict[str, Any]]:
        """处理完整 AIMessage（非 chunk）：tool_calls 的 args 已是完整 dict，直接使用。"""
        from Django_xm.apps.tools.param_extractor import extract_tool_params, strip_internal_fields
        from Django_xm.common.event_schema import EventType

        events: list[dict[str, Any]] = []
        for raw_tool_call in getattr(message, "tool_calls", None) or []:
            # Groq 名称/内联参数修复（chat 特有增强合入，唯一实现）
            tool_call = (
                self._fix_groq_tool_call(raw_tool_call)
                if isinstance(raw_tool_call, dict)
                else raw_tool_call
            )
            tc_id = tool_call.get("id") if isinstance(tool_call, dict) else getattr(tool_call, "id", None)
            tc_name = (
                tool_call.get("name") if isinstance(tool_call, dict) else getattr(tool_call, "name", None)
            )
            # 去重：同一 tool_call_id 只发射一次
            if not tc_id or tc_id in self.seen_tool_call_ids:
                continue
            tc_args = strip_internal_fields(extract_tool_params(tool_call))
            # args 为空时不发射、不去重，等后续 chunk 或 ToolMessage 阶段补发。
            if not tc_args:
                continue
            self.seen_tool_call_ids.add(tc_id)
            events.append(
                {
                    "event_type": EventType.TOOL_CALL_PENDING,
                    "tool_call_id": tc_id,
                    "tool_name": tc_name or "unknown",
                    "parameters": tc_args,
                }
            )
        return events

    @staticmethod
    def _fix_groq_tool_call(tool_call: dict[str, Any]) -> dict[str, Any]:
        """修复 Groq 等 provider 把内联参数拼进 name 的 tool_call（形如 ``"name{...json...}"``）。

        与 chat/services/stream_chunk_processors.py::_fix_groq_tool_call 等价
        （apps/tools 不能反向 import apps/chat，故在此独立实现）。
        """
        raw_name = tool_call.get("name", "")
        if not isinstance(raw_name, str) or (" " not in raw_name and "{" not in raw_name):
            return tool_call

        match = _GROQ_NAME_PATTERN.match(raw_name)
        if not match:
            return tool_call

        fixed = dict(tool_call)
        actual_name = match.group(1)
        inline_args_str = match.group(2)
        fixed["name"] = actual_name
        if inline_args_str:
            try:
                parsed_args = json.loads(inline_args_str)
                existing_args = fixed.get("args") or {}
                if not existing_args or existing_args == {}:
                    fixed["args"] = parsed_args
            except json.JSONDecodeError:
                pass
        logger.debug(f"Groq tool call 名称修复: '{raw_name}' -> '{actual_name}'")
        return fixed

    # ------------------------------------------------------------------ ToolMessage

    def _extract_from_tool_message(self, message: Any) -> list[dict[str, Any]]:
        """处理 ToolMessage：补发 PENDING + 发射 FAILED/COMPLETED 终态事件。"""
        from Django_xm.common.event_schema import EventType

        # 轮次边界：ToolMessage 到达意味着上一轮 LLM 调用结束
        round_just_ended = self._round_token
        self._round_token += 1
        self._prune_stale_rounds(round_just_ended)

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

        # 无 tool_call_id：按 tool_name + 顺序兜底归属本轮暂存的无 id 工具调用
        if not tc_id:
            return self._attribute_staged_tool_message(
                round_just_ended,
                tool_name,
                content,
                content_str,
                getattr(message, "status", None),
            )

        tool_parameters = self._resolve_tool_parameters(tc_id)

        events: list[dict[str, Any]] = []
        # 补发 PENDING：若 chunk 阶段因 args 不完整未发射，此处回查到完整参数后补发，
        # 确保父 SSE 流收到带完整 parameters 的 PENDING 事件，再转发终态。
        if tc_id not in self.seen_tool_call_ids:
            self.seen_tool_call_ids.add(tc_id)
            events.append(
                {
                    "event_type": EventType.TOOL_CALL_PENDING,
                    "tool_call_id": tc_id,
                    "tool_name": tool_name,
                    "parameters": tool_parameters,
                }
            )

        # 审批注入的终态 ToolMessage（超时/拒绝）：对应终态事件已由审批链路发布，
        # 不应再判定为 FAILED（否则触发"终态→终态"非法转换告警）。命中时跳过终态判定。
        if isinstance(content, str) and any(k in content for k in _APPROVAL_FINAL_KEYWORDS):
            return events

        is_error = getattr(message, "status", None) == "error" or _is_error_content(content)
        if is_error:
            events.append(
                {
                    "event_type": EventType.TOOL_CALL_FAILED,
                    "tool_call_id": tc_id,
                    "tool_name": tool_name,
                    "parameters": tool_parameters,
                    "error": content_to_str(content),
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

    def _resolve_tool_parameters(self, tc_id: str) -> dict:
        """解析 ToolMessage 对应的完整参数（权威来源：按 cc_id 聚合的 args 缓冲）。"""
        from Django_xm.apps.tools.param_extractor import extract_tool_params, strip_internal_fields

        tool_parameters: dict = {}
        try:
            # 权威来源：feed 期间跨 chunk 累积的 args（含无 id 分片）
            agg_args_str = self._args_by_id.get(tc_id, "")
            # 兜底：扫描 accumulated_messages 的 tool_call_chunks（按 cc_id 聚合）
            if not agg_args_str:
                agg_args_str = self._aggregate_chunk_args(tc_id)
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
                        logger.debug("parse_partial_json 解析失败，回退到 AIMessage 提取")
            # 兜底：从 AIMessage.tool_calls 直接提取（聚合未命中时）
            if not tool_parameters:
                for prev_msg in self.accumulated_messages:
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
                f"[ToolEventExtractor] 聚合 tool_call_chunks 参数失败: tc_id={tc_id}, err={e}"
            )
        return tool_parameters

    def _aggregate_chunk_args(self, tc_id: str) -> str:
        """扫描 accumulated_messages 的 tool_call_chunks，按 cc_id 聚合 args 字符串。

        注意：LangChain 的 ``tool_call_chunks.index`` 是"当前 LLM 调用内的序号"，每轮从 0 开始，
        而 accumulated_messages 跨整个任务累积；若按 index 聚合会导致跨轮次 index 复用被错误拼接。
        故按 cc_id（tool_call_id）聚合，保证每个 tool_call_id 独立累积。
        """
        chunk_args_by_id: dict[str, str] = {}
        for prev_msg in self.accumulated_messages:
            tccs = getattr(prev_msg, "tool_call_chunks", None)
            if not tccs:
                continue
            for tcc in tccs:
                cc_id = _tcc_get(tcc, "id") or ""
                cc_args = _tcc_get(tcc, "args") or ""
                if not cc_id:
                    continue  # 无 id 分片由 _staged 缓冲处理
                if cc_args:
                    chunk_args_by_id[cc_id] = chunk_args_by_id.get(cc_id, "") + _normalize_args_str(cc_args)
        return chunk_args_by_id.get(tc_id, "")

    def _attribute_staged_tool_message(
        self,
        round_just_ended: int,
        tool_name: str,
        content: Any,
        content_str: str,
        status: str | None,
    ) -> list[dict[str, Any]]:
        """ToolMessage 仍无 id：按 tool_name + 顺序兜底归属本轮暂存项，用合成 id 发射事件。"""
        from Django_xm.common.event_schema import EventType

        candidates = [
            key
            for key, staged in self._staged.items()
            if key[0] == round_just_ended and (not tool_name or staged.get("name") == tool_name)
        ]
        if not candidates:
            return []
        stage_key = min(candidates, key=lambda k: str(k[2]))
        staged = self._staged.pop(stage_key)
        synthetic_id = f"_staged_{stage_key[0]}_{stage_key[2]}"

        parameters: dict = {}
        parsed = _parse_complete_json_args(staged.get("args") or "")
        if isinstance(parsed, dict) and parsed:
            parameters = parsed
        elif isinstance(parsed, list) and parsed:
            parameters = {"items": parsed}

        events: list[dict[str, Any]] = []
        if synthetic_id not in self.seen_tool_call_ids:
            self.seen_tool_call_ids.add(synthetic_id)
            events.append(
                {
                    "event_type": EventType.TOOL_CALL_PENDING,
                    "tool_call_id": synthetic_id,
                    "tool_name": staged.get("name") or tool_name or "unknown",
                    "parameters": parameters,
                }
            )

        if isinstance(content, str) and any(k in content for k in _APPROVAL_FINAL_KEYWORDS):
            return events

        is_error = status == "error" or _is_error_content(content)
        if is_error:
            events.append(
                {
                    "event_type": EventType.TOOL_CALL_FAILED,
                    "tool_call_id": synthetic_id,
                    "tool_name": staged.get("name") or tool_name or "unknown",
                    "parameters": parameters,
                    "error": content_to_str(content),
                }
            )
        else:
            events.append(
                {
                    "event_type": EventType.TOOL_CALL_COMPLETED,
                    "tool_call_id": synthetic_id,
                    "tool_name": staged.get("name") or tool_name or "unknown",
                    "parameters": parameters,
                    "result": content_str,
                }
            )
        return events

    def _prune_stale_rounds(self, current_round: int) -> None:
        """丢弃早于当前轮次的暂存/映射项，避免跨轮次污染与内存增长。"""
        self._staged = {k: v for k, v in self._staged.items() if k[0] >= current_round}
        self._index_to_id = {k: v for k, v in self._index_to_id.items() if k[0] >= current_round}


def extract_tool_events_from_message(
    message: Any,
    seen_tool_call_ids: set,
    accumulated_messages: list,
) -> list[dict[str, Any]]:
    """从单条流式消息中提取工具事件（无状态兼容入口）。

    委托到唯一实现 ``ToolEventExtractor``，保持原有签名与行为不变。
    生产链路（chat / research）统一使用有状态 ``ToolEventExtractor``；本入口保留
    兼容，供按旧签名调用时复用。

    Args:
        message: 流式消息，预期为 AIMessage / AIMessageChunk / ToolMessage 之一。
        seen_tool_call_ids: 已发射 PENDING 的 tool_call_id 集合（就地修改）。
        accumulated_messages: 累积的消息列表，用于 ToolMessage 阶段回查参数（就地追加）。

    Returns:
        事件列表，字段与 :meth:`ToolEventExtractor.feed` 一致。
    """
    return ToolEventExtractor(
        accumulated_messages=accumulated_messages,
        seen_tool_call_ids=seen_tool_call_ids,
    ).feed(message)
