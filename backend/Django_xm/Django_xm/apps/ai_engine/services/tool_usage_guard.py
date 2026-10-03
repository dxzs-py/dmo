"""
ToolUsageGuard - 工具调用使用防护服务

借鉴 Cloud Code / Claude Code 的分层防护体系：
- PreToolUse：内容去重（ARCH-12 收敛后本服务仅负责去重与循环检测）
- PostToolUse：配额追踪（循环阻断、连续阻断计数）
- Token Budget：未来可扩展

限流职责边界（ARCH-12 限流双轨收敛）：
- agent 工具调用频率限制统一由 ai_engine.guardrails.RateLimitMiddleware 承担
  （唯一限流器，阈值单源维护于 capabilities.builtin.RateLimitCapability）。
- 本服务不再做任何频率限制，仅保留重复调用去重（dedup）与无进展循环检测。

设计原则：
1. **不限制工具能力**：合理的多文件写入、增量更新、章节拆分都应被允许
2. **不粗暴中断**：循环先识别再阻断，最后才交由 termination_judge
3. **不浪费资源**：相同内容短时重复写入直接去重，不计调用次数
4. **不掩盖循环**：增量 < 5% 的"打磨式"写入被识别为循环并阻断
5. **用户友好**：所有阻断/警告都通过 SSE 事件告知前端
6. **普适防护**：所有工具都进入通用 dedup + 通用 loop 流程
   - 通过 TOOL_FINGERPRINTS 注册表定义每个工具的"资源标识"和"有效负载"
   - 通过 TOOL_NO_DEDUP 白名单豁免状态型工具

核心 API：
    decision = await guard.check(
        tool_name="fs_write_file",
        args={"relative_path": "notes/a.md", "content": "..."},
        thread_id="abc",
    )
    # decision.status: ALLOW / DEDUP / WARN / BLOCK
    # decision.sse_event: 可直接 yield 给前端的 dict
    # decision.short_circuit_response: DEDUP/BLOCK 时返回给 ToolMessage 的字符串
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)


# ============== 工具指纹注册表 ==============
# 借鉴 Claude Code 的 `.claude/skills` 本地发现机制 + 集中路由表
# 每条目定义某个工具的：
#   - tool_names: 匹配的工具名列表
#   - resource_extractor: 从 args 提取资源标识（用于 dedup 维度）
#   - payload_extractor: 从 args 提取有效负载字符串（用于 hash 判定"实质内容是否变化"）
#   - loop_strategy: payload_hash（同工具同参数循环）/ content_diff（仅 fs_write_file 用增量）


def _f_str(args: dict[str, Any], key: str, default: str = "") -> str:
    """从 args 安全取字符串字段"""
    val = args.get(key, default)
    return str(val) if val is not None else default


def _f_combine(*parts: Any, sep: str = "|") -> str:
    """拼接多个字段为指纹片段"""
    return sep.join(str(p) if p is not None else "" for p in parts)


def _f_text_hash(args: dict[str, Any], key: str) -> str:
    """对长文本字段做 hash 截断（避免 payload 过长）"""
    import hashlib as _hl

    text = args.get(key, "")
    if not isinstance(text, str):
        text = str(text)
    return _hl.sha1(text.encode("utf-8", errors="ignore"), usedforsecurity=False).hexdigest()[:16]


# 默认 fingerprint 函数：序列化整个 args
def _default_resource_extractor(args: dict[str, Any]) -> str:
    sorted_items = sorted((str(k), str(v)) for k, v in args.items() if k not in ("thread_id",))
    return json.dumps(sorted_items, ensure_ascii=False, sort_keys=True)[:500]


def _default_payload_extractor(args: dict[str, Any]) -> str:
    return _default_resource_extractor(args)


@dataclass
class ToolFingerprint:
    """工具指纹配置"""

    tool_names: tuple[str, ...]
    resource_extractor: Callable[[dict[str, Any]], str]
    payload_extractor: Callable[[dict[str, Any]], str] = _default_payload_extractor
    loop_strategy: str = "payload_hash"  # payload_hash / content_diff / none
    description: str = ""


# 工具指纹注册表
TOOL_FINGERPRINTS: list[ToolFingerprint] = [
    # 文件系统
    ToolFingerprint(
        tool_names=("fs_write_file",),
        resource_extractor=lambda a: _f_str(a, "relative_path") or _f_str(a, "path"),
        payload_extractor=lambda a: _f_str(a, "content"),
        loop_strategy="content_diff",
        description="写文件 - 严格 diff_ratio 增量检测",
    ),
    ToolFingerprint(
        tool_names=("fs_read_file",),
        resource_extractor=lambda a: _f_str(a, "relative_path") or _f_str(a, "path"),
        payload_extractor=_default_resource_extractor,
        loop_strategy="payload_hash",
        description="读文件 - 同 path 重复读视为冗余",
    ),
    ToolFingerprint(
        tool_names=("fs_list_files",),
        resource_extractor=lambda a: _f_str(a, "subdirectory", "notes"),
        payload_extractor=_default_resource_extractor,
        loop_strategy="payload_hash",
        description="列文件 - 同子目录+pattern 视为同资源",
    ),
    ToolFingerprint(
        tool_names=("fs_search_files",),
        resource_extractor=lambda a: _f_combine(
            _f_str(a, "subdirectory", "notes"),
            _f_text_hash(a, "keyword"),
        ),
        payload_extractor=_default_resource_extractor,
        loop_strategy="payload_hash",
        description="搜文件 - keyword 决定资源维度",
    ),
    # 计算 / 翻译
    ToolFingerprint(
        tool_names=("calculator",),
        resource_extractor=lambda a: _f_str(a, "expression"),
        payload_extractor=_default_resource_extractor,
        loop_strategy="payload_hash",
        description="计算 - 同表达式重复调用直接去重",
    ),
    ToolFingerprint(
        tool_names=("translate_text",),
        resource_extractor=lambda a: _f_combine(_f_str(a, "target_lang"), _f_text_hash(a, "text")),
        payload_extractor=_default_resource_extractor,
        loop_strategy="payload_hash",
        description="翻译 - 同文本+目标语言视为同资源",
    ),
    ToolFingerprint(
        tool_names=("detect_language",),
        resource_extractor=lambda a: _f_text_hash(a, "text"),
        payload_extractor=_default_resource_extractor,
        loop_strategy="payload_hash",
        description="语言检测 - 同文本重复检测视为冗余",
    ),
    # 搜索
    ToolFingerprint(
        tool_names=("web_search", "duckduckgo_search", "tavily_search"),
        resource_extractor=lambda a: _f_combine(_f_str(a, "query"), _f_str(a, "max_results", "5")),
        # 包含整个 args 作为 payload（参数变化时 payload 也变化，dedup 不命中 → 触发 loop）
        payload_extractor=_default_resource_extractor,
        loop_strategy="payload_hash",
        description="网络搜索 - 同 query 视为同资源，参数变化时去重不命中",
    ),
    ToolFingerprint(
        tool_names=("web_fetch",),
        resource_extractor=lambda a: _f_str(a, "url"),
        # 包含整个 args（如果调用方传 cache_buster 之类的，会影响 payload）
        payload_extractor=_default_resource_extractor,
        loop_strategy="payload_hash",
        description="网页抓取 - 同 URL 反复拉取视为循环",
    ),
    # 天气
    ToolFingerprint(
        tool_names=("weather_query",),
        resource_extractor=lambda a: _f_combine(_f_str(a, "city"), _f_str(a, "date", "today")),
        payload_extractor=_default_resource_extractor,
        loop_strategy="payload_hash",
        description="天气 - 同城市+日期视为同资源",
    ),
    # 知识库
    ToolFingerprint(
        tool_names=(
            "knowledge_base_search",
            "knowledge_base_query",
            "knowledge_base_list",
            "knowledge_base_get",
        ),
        resource_extractor=lambda a: _f_combine(_f_str(a, "query"), _f_str(a, "knowledge_base_id", "default")),
        payload_extractor=_default_resource_extractor,
        loop_strategy="payload_hash",
        description="知识库 - 同 query 视为同资源，参数变化时去重不命中",
    ),
    # 待办
    ToolFingerprint(
        tool_names=("todo_write",),
        resource_extractor=lambda a: _f_str(a, "action", "create"),
        payload_extractor=lambda a: _f_str(a, "content"),
        loop_strategy="payload_hash",
        description="todo 写入 - 同 action+content 视为重复",
    ),
    # 附件 / 文件读取
    ToolFingerprint(
        tool_names=("attachment_reader",),
        resource_extractor=lambda a: _f_str(a, "attachment_id") or _f_str(a, "filename"),
        payload_extractor=_default_resource_extractor,
        loop_strategy="payload_hash",
        description="附件读取 - 同 ID 重复读视为冗余",
    ),
    ToolFingerprint(
        tool_names=("file_reader",),
        resource_extractor=lambda a: _f_str(a, "path") or _f_str(a, "file_path"),
        payload_extractor=_default_resource_extractor,
        loop_strategy="payload_hash",
        description="文件读取 - 同 path 重复读视为冗余",
    ),
    # 顺序思考
    ToolFingerprint(
        tool_names=("sequentialthinking", "sequential_thinking"),
        resource_extractor=lambda a: _f_str(a, "thought_id") or _f_str(a, "step"),
        payload_extractor=lambda a: _f_str(a, "thought"),
        loop_strategy="payload_hash",
        description="顺序思考 - 同 thought_id 视为重复",
    ),
    # Skill 工具：浏览器、知识图谱等
    ToolFingerprint(
        tool_names=("skill_agent-browser",),
        resource_extractor=lambda a: _f_str(a, "query"),
        payload_extractor=lambda a: _f_str(a, "query") + _f_str(a, "mode"),
        loop_strategy="payload_hash",
        description="浏览器技能 - 同 query 视为重复",
    ),
    ToolFingerprint(
        tool_names=("skill_ontology",),
        resource_extractor=lambda a: _f_str(a, "query"),
        payload_extractor=lambda a: _f_str(a, "query") + _f_str(a, "mode"),
        loop_strategy="payload_hash",
        description="知识图谱技能 - 同 query 视为重复",
    ),
]


# 工具白名单：始终 ALLOW，不做任何去重或循环检测
TOOL_NO_DEDUP: tuple[str, ...] = (
    "get_current_time",
    "get_current_date",
    "project_info",
    "system_status",
    "spawn_sub_agent",
    "todo_read",  # 读取自身状态，每次都应该返回最新
    "sequentialthinking",  # 思考型工具，每次思考都不同，详见 fingerprint 自身
    "sequential_thinking",
)


def _get_fingerprint(tool_name: str) -> ToolFingerprint:
    """查找工具对应的 fingerprint 配置，未找到则返回默认"""
    for fp in TOOL_FINGERPRINTS:
        if tool_name in fp.tool_names:
            return fp
    # 默认 fallback：使用 args 全量序列化
    return ToolFingerprint(
        tool_names=(tool_name,),
        resource_extractor=_default_resource_extractor,
        payload_extractor=_default_payload_extractor,
        loop_strategy="payload_hash",
        description="默认 fingerprint",
    )


class ToolUsageStatus(StrEnum):
    """工具调用使用决策状态"""

    ALLOW = "allow"  # 允许执行
    DEDUP = "dedup"  # 去重命中（短时相同内容），返回无变更响应
    BLOCK = "block"  # 硬阻断（超过阈值或打磨循环），返回阻断原因


@dataclass
class ToolUsageDecision:
    """工具调用使用决策结果"""

    status: ToolUsageStatus
    reason: str = ""
    sse_event: dict[str, Any] | None = None
    short_circuit_response: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class _ThreadSafeLRU:
    """简单的线程安全 LRU 容器"""

    def __init__(self, max_size: int):
        self._max_size = max_size
        self._data: OrderedDict[Any, Any] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Any) -> Any | None:
        with self._lock:
            if key not in self._data:
                return None
            self._data.move_to_end(key)
            return self._data[key]

    def set(self, key: Any, value: Any) -> None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = value
            while len(self._data) > self._max_size:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class ToolUsageGuard:
    """工具调用使用防护服务

    按 thread_id 隔离状态：
    - _file_cache: (thread, path) -> (content_hash, timestamp)         [fs_write_file 专用]
    - _resource_cache: (thread, tool, resource_key) -> (payload_hash, ts)  [通用]
    - _resource_recent: (thread, tool, resource_key) -> Deque[(ts, payload_hash)]  [通用 loop]
    - _path_recent_contents: (thread, path) -> Deque[(ts, content)]    [fs_write_file diff]
    - _consecutive_blocked: thread -> int

    速率窗口（_rate_windows）已随 ARCH-12 限流收敛移除，频率限制由
    guardrails.RateLimitMiddleware 唯一承担。
    """

    def __init__(
        self,
        dedup_window_seconds: int = 30,
        dedup_cache_size: int = 1000,
        same_path_max: int = 6,
        same_path_diff_ratio: float = 0.05,
        blocked_consecutive_max: int = 2,
        same_resource_max: int = 6,
        general_dedup_enabled: bool = True,
        no_dedup_tools: tuple[str, ...] | None = None,
    ):
        self.dedup_window_seconds = dedup_window_seconds
        self.dedup_cache_size = dedup_cache_size
        self.same_path_max = same_path_max
        self.same_path_diff_ratio = same_path_diff_ratio
        self.blocked_consecutive_max = blocked_consecutive_max
        self.same_resource_max = same_resource_max
        self.general_dedup_enabled = general_dedup_enabled
        self.no_dedup_tools = no_dedup_tools or TOOL_NO_DEDUP

        self._file_cache = _ThreadSafeLRU(dedup_cache_size)
        # 通用资源级缓存：(thread, tool, resource_key) -> (payload_hash, timestamp)
        self._resource_cache: _ThreadSafeLRU = _ThreadSafeLRU(dedup_cache_size)
        # 通用资源循环历史：(thread, tool, resource_key) -> Deque[(ts, payload_hash)]
        self._resource_recent: dict[tuple[str, str, str], deque[tuple[float, str]]] = {}
        # 保留最近 N 次同 path 的 content 用于 diff 检测（仅 fs_write_file）
        self._path_recent_contents: dict[tuple[str, str], deque[tuple[float, str]]] = {}
        self._consecutive_blocked: dict[str, int] = {}
        self._lock = threading.Lock()

    # ============== 公开接口 ==============

    def check(
        self,
        tool_name: str,
        args: dict[str, Any],
        thread_id: str,
    ) -> ToolUsageDecision:
        """同步检查工具调用使用情况

        Returns:
            ToolUsageDecision 包含 status, sse_event, short_circuit_response

        主流程（按优先级）：
        1. 白名单工具 → 直接 ALLOW
        2. 通用 dedup：基于 (tool, resource_key) + payload_hash
        3. 通用 loop：同 resource_key 调用次数 ≥ same_resource_max 且最近 3 次 payload_hash 相同
        4. fs_write_file 专用 loop：content_diff 增量 < 5% 视为打磨循环
        5. ALLOW + 记录调用

        注意（ARCH-12）：频率限制不在此处，由 guardrails.RateLimitMiddleware 唯一承担。
        """
        now = time.time()
        thread_id = thread_id or "default"

        # 0. 白名单：始终 ALLOW（不计入去重）
        if tool_name in self.no_dedup_tools:
            return ToolUsageDecision(
                status=ToolUsageStatus.ALLOW,
                reason="whitelisted tool",
            )

        # 1. 提取 fingerprint 资源信息
        if self.general_dedup_enabled:
            fp = _get_fingerprint(tool_name)
            try:
                resource_key = fp.resource_extractor(args or {})
                payload = fp.payload_extractor(args or {})
            except Exception as e:
                logger.debug(f"fingerprint 提取失败 {tool_name}: {e}，跳过 dedup")
                resource_key = ""
                payload = ""
        else:
            resource_key = ""
            payload = ""

        # 2. 通用资源循环检测（在 dedup 之前，让循环计数先累加）
        # fs_write_file 跳过通用 loop，自己有更严格的 content_diff 检测
        if self.general_dedup_enabled and resource_key and tool_name != "fs_write_file":
            loop_decision = self._check_general_resource_loop(thread_id, tool_name, resource_key, payload, now)
            if loop_decision is not None:
                return loop_decision

        # 3. 通用 dedup（resource_key + payload_hash）
        if self.general_dedup_enabled and resource_key:
            dedup = self._check_general_dedup(thread_id, tool_name, resource_key, payload, now)
            if dedup is not None:
                return dedup

        # 4. fs_write_file 专用 diff 循环（严格增量检测）
        if tool_name == "fs_write_file":
            fs_loop = self._check_fs_write_file_loop(thread_id, args, now)
            if fs_loop is not None:
                return fs_loop

        # 5. 通过：记录调用
        self._record_call(
            thread_id,
            tool_name,
            now,
            resource_key=resource_key,
            payload=payload,
        )
        return ToolUsageDecision(status=ToolUsageStatus.ALLOW)

    def is_in_hard_stop_state(self, thread_id: str) -> bool:
        """给 termination_judge 用：是否已经连续 blocked 超过阈值"""
        with self._lock:
            return self._consecutive_blocked.get(thread_id, 0) >= self.blocked_consecutive_max

    # ============== 内部方法（通用指纹机制） ==============

    def _check_general_dedup(
        self,
        thread_id: str,
        tool_name: str,
        resource_key: str,
        payload: str,
        now: float,
    ) -> ToolUsageDecision | None:
        """通用资源级去重：相同 (tool, resource_key) + 相同 payload_hash 命中 → DEDUP"""
        cache_key = (thread_id, tool_name, resource_key)
        entry = self._resource_cache.get(cache_key)
        if entry is None:
            return None
        prev_hash, prev_ts = entry
        if now - prev_ts > self.dedup_window_seconds:
            return None
        curr_hash = self._content_hash(payload)
        if curr_hash == prev_hash:
            preview = (payload or "")[:100]
            sse_event = {
                "type": "tool_usage_warning",
                "data": {
                    "severity": "info",
                    "tool_name": tool_name,
                    "reason": "duplicate_resource",
                    "message": (
                        f"工具 {tool_name} 在 {self.dedup_window_seconds}s 内已对同一资源 "
                        f"'{resource_key}' 执行过相同 payload，已自动跳过"
                    ),
                    "resource_key": resource_key,
                    "payload_preview": preview,
                },
            }
            return ToolUsageDecision(
                status=ToolUsageStatus.DEDUP,
                reason="short-window duplicate resource",
                sse_event=sse_event,
                short_circuit_response=(
                    f"[系统提示] 工具 {tool_name} 在 {self.dedup_window_seconds}s 内已对同一资源 "
                    f"'{resource_key}' 执行过相同 payload，本次调用被自动跳过。"
                    f"如确需再次执行，请在 {self.dedup_window_seconds}s 后再试。"
                ),
                metadata={
                    "tool_name": tool_name,
                    "resource_key": resource_key,
                    "payload_hash": curr_hash,
                },
            )
        return None

    def _check_general_resource_loop(
        self,
        thread_id: str,
        tool_name: str,
        resource_key: str,
        payload: str,
        now: float,
    ) -> ToolUsageDecision | None:
        """通用资源循环检测：同 resource_key 累计调用 ≥ same_resource_max
        且最近 3 次 payload_hash 全部相同 → BLOCK（"无进展"循环）
        """
        key = (thread_id, tool_name, resource_key)
        payload_hash = self._content_hash(payload or "")
        with self._lock:
            recent = self._resource_recent.setdefault(key, deque())
            recent.append((now, payload_hash))
            # 清理窗口外
            cutoff = now - self.dedup_window_seconds
            while recent and recent[0][0] < cutoff:
                recent.popleft()
            call_count = len(recent)

        if call_count < self.same_resource_max:
            return None

        # 最近 3 次 payload_hash 是否全部相同
        recent_hashes = [h for _, h in list(recent)[-3:]]
        if len(recent_hashes) < 3:
            return None
        if recent_hashes[0] != recent_hashes[1] or recent_hashes[1] != recent_hashes[2]:
            return None

        # 全部相同 → 阻断
        with self._lock:
            self._consecutive_blocked[thread_id] = self._consecutive_blocked.get(thread_id, 0) + 1
            consecutive = self._consecutive_blocked[thread_id]
        sse_event = {
            "type": "tool_usage_blocked",
            "data": {
                "severity": "warn",
                "tool_name": tool_name,
                "reason": "no_progress_loop",
                "message": (
                    f"工具 {tool_name} 对资源 '{resource_key}' 已连续 {call_count} 次调用，"
                    f"最近 3 次 payload 哈希完全相同，疑似无进展循环，本次被阻断"
                ),
                "resource_key": resource_key,
                "call_count": call_count,
            },
        }
        return ToolUsageDecision(
            status=ToolUsageStatus.BLOCK,
            reason="no-progress loop detected",
            sse_event=sse_event,
            short_circuit_response=(
                f"[系统提示] 工具 {tool_name} 对资源 '{resource_key}' 已连续 {call_count} 次调用，"
                f"最近 3 次 payload 完全相同（疑似循环），本次被系统阻断。"
                f"请停止调用 {tool_name}，基于已有结果继续任务或结束对话。"
            ),
            metadata={
                "tool_name": tool_name,
                "resource_key": resource_key,
                "call_count": call_count,
                "consecutive_blocked": consecutive,
            },
        )

    def _check_fs_write_file_loop(
        self,
        thread_id: str,
        args: dict[str, Any],
        now: float,
    ) -> ToolUsageDecision | None:
        """fs_write_file 专用 diff 循环检测

        在通用 fingerprint dedup 之外，fs_write_file 仍做更严格的
        "内容增量 < 5% 视为打磨循环" 检测。
        """
        path = args.get("relative_path") or args.get("path")
        content = args.get("content")
        if path is None or content is None:
            return None

        key = (thread_id, path)
        with self._lock:
            recent = self._path_recent_contents.setdefault(key, deque())
            recent.append((now, content))
            cutoff = now - self.dedup_window_seconds
            while recent and recent[0][0] < cutoff:
                recent.popleft()
            call_count = len(recent)

        if call_count < self.same_path_max:
            return None

        recent_contents = [c for _, c in list(recent)[-3:]]
        if len(recent_contents) < 2:
            return None
        diffs = []
        for i in range(1, len(recent_contents)):
            d = self._text_diff_ratio(recent_contents[i - 1], recent_contents[i])
            diffs.append(d)
        if not diffs:
            return None
        avg_diff = sum(diffs) / len(diffs)
        if avg_diff < self.same_path_diff_ratio:
            with self._lock:
                self._consecutive_blocked[thread_id] = self._consecutive_blocked.get(thread_id, 0) + 1
                consecutive = self._consecutive_blocked[thread_id]
            sse_event = {
                "type": "tool_usage_blocked",
                "data": {
                    "severity": "warn",
                    "tool_name": "fs_write_file",
                    "reason": "polishing_loop",
                    "message": (
                        f"检测到对 {path} 连续 {call_count} 次写入，最近 3 次内容平均增量 "
                        f"仅 {avg_diff:.1%}（阈值 {self.same_path_diff_ratio:.0%}），"
                        "疑似打磨循环，本次被阻断"
                    ),
                    "resource_key": path,
                    "path": path,
                    "call_count": call_count,
                    "avg_diff_ratio": round(avg_diff, 4),
                },
            }
            return ToolUsageDecision(
                status=ToolUsageStatus.BLOCK,
                reason="polishing loop detected",
                sse_event=sse_event,
                short_circuit_response=(
                    f"[系统提示] 检测到对文件 {path} 连续多次写入但内容几乎无变化，"
                    f"（共 {call_count} 次，最近 3 次平均增量 {avg_diff:.1%}），"
                    "本次写入被系统阻断。请先用 fs_read_file 确认文件当前内容，"
                    "再决定是否需要继续写入或结束任务。"
                ),
                metadata={
                    "resource_key": path,
                    "path": path,
                    "call_count": call_count,
                    "avg_diff_ratio": round(avg_diff, 4),
                    "consecutive_blocked": consecutive,
                },
            )
        return None

    # ============== 内部方法 ==============

    def _record_call(
        self,
        thread_id: str,
        tool_name: str,
        now: float,
        resource_key: str = "",
        payload: str = "",
        path: str | None = None,  # 兼容旧调用
        content: str | None = None,  # 兼容旧调用
    ) -> None:
        """记录一次成功调用（写入通用资源缓存 + 文件缓存）

        ARCH-12：不再写入速率窗口（频率限制由 RateLimitMiddleware 承担）。

        参数优先级：resource_key/payload 优先（新版指纹机制），
        path/content 作为旧版兼容入口（仅 fs_write_file 专用）。
        """
        with self._lock:
            # 连续成功时重置 consecutive_blocked 计数
            self._consecutive_blocked[thread_id] = 0

        # 新版：通用资源缓存（所有工具，包括 fs_write_file）
        # fs_write_file 同时写入两份：_resource_cache（让通用 dedup 工作）+ _file_cache（让 diff_ratio 工作）
        if resource_key:
            cache_key = (thread_id, tool_name, resource_key)
            self._resource_cache.set(cache_key, (self._content_hash(payload or ""), now))
        # 兼容：fs_write_file 专用文件缓存（额外一份，用于 _check_fs_write_file_loop 的 content_diff）
        if tool_name == "fs_write_file":
            p = path or resource_key
            c = content or payload
            if p is not None and c is not None:
                file_cache_key = (thread_id, p)
                self._file_cache.set(file_cache_key, (self._content_hash(c), now))

    @staticmethod
    def _content_hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()

    @staticmethod
    def _text_diff_ratio(a: str, b: str) -> float:
        """简单字符级 diff 比例：返回 (a-b 增量) / max(len(a), len(b))

        0 表示完全相同，1 表示完全不同。
        使用 difflib 快速估算，避免 O(n²) 长字符串比较。
        """
        if not a and not b:
            return 0.0
        if not a or not b:
            return 1.0
        # 对长字符串截断采样
        max_len = 20000
        if len(a) > max_len:
            a = a[:max_len]
        if len(b) > max_len:
            b = b[:max_len]
        import difflib

        matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
        # 计算非匹配字符数
        diff_chars = 0
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag != "equal":
                diff_chars += (i2 - i1) + (j2 - j1)
        base = max(len(a), len(b))
        return diff_chars / (2 * base) if base else 0.0


# ============== 全局单例 ==============

_guard_instance: ToolUsageGuard | None = None
_guard_lock = threading.Lock()


def get_tool_usage_guard() -> ToolUsageGuard:
    """获取 ToolUsageGuard 单例（首次调用时按 settings 初始化）"""
    global _guard_instance  # noqa: PLW0603 - 模块级单例惰性初始化
    with _guard_lock:
        if _guard_instance is not None:
            return _guard_instance
        try:
            from Django_xm.apps.ai_engine.config import settings

            _guard_instance = ToolUsageGuard(
                dedup_window_seconds=int(getattr(settings, "tool_usage_dedup_window_seconds", 30)),
                dedup_cache_size=int(getattr(settings, "tool_usage_dedup_cache_size", 1000)),
                same_path_max=int(getattr(settings, "tool_usage_same_path_max", 6)),
                same_path_diff_ratio=float(getattr(settings, "tool_usage_same_path_diff_ratio", 0.05)),
                blocked_consecutive_max=int(getattr(settings, "tool_usage_blocked_consecutive_max", 2)),
                same_resource_max=int(getattr(settings, "tool_usage_same_resource_max", 6)),
                general_dedup_enabled=bool(getattr(settings, "tool_usage_general_dedup_enabled", True)),
            )
        except Exception as e:
            logger.warning(f"ToolUsageGuard 初始化失败，使用默认配置: {e}")
            _guard_instance = ToolUsageGuard()
        return _guard_instance
