"""Agent 执行韧性组件

为代理模式和深度研究模式提供统一的重试、降级、异常捕获、回退能力。

架构分层：
- 创建阶段（agent_hub 层）：模型 fallback、框架降级、工具加载降级
- 执行阶段（本模块）：重试、降级、异常捕获、回退、超时管理

位于 agent_hub/services/ 目录，与 agent_hub 的 Agent 创建流程紧密配合。
"""

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ============================================================================
# 配置
# ============================================================================


@dataclass
class ResilienceConfig:
    """统一韧性执行配置

    合并 agent 执行层与模型调用层的韧性配置，作为全应用唯一的 ResilienceConfig。
    - agent 执行层：retry_with_backoff / ExecutionTimeoutManager 使用
    - 模型调用层：ResilientInvoker / CircuitBreaker 使用
    """

    # ===== agent 执行层配置 =====
    max_retries: int = 3
    initial_retry_interval: float = 2.0
    max_retry_interval: float = 30.0
    retry_backoff_factor: float = 2.0
    # 重复工具调用检测：相同 tool_name + 相同 parameters 在窗口内超过阈值时注入提示
    duplicate_tool_call_threshold: int = 3  # 触发阈值（窗口内相同调用次数）
    duplicate_tool_call_window: int = 300  # 检测窗口（秒，默认 5 分钟）
    # 执行超时
    soft_timeout: float | None = None  # 警告阈值（秒），None 表示不限制
    hard_timeout: float | None = None  # 强制终止阈值（秒），None 表示不限制

    # ===== 模型调用层配置 =====
    backoff_seconds: tuple[float, ...] = (0.5, 1.0, 2.0)
    circuit_breaker_threshold: int = 3
    circuit_breaker_cooldown: float = 30.0


def get_resilience_config(**overrides) -> ResilienceConfig:
    """获取韧性配置，支持 Django settings 覆盖

    优先级：overrides > Django settings > 默认值
    """
    try:
        from django.conf import settings

        # settings 中 backoff_seconds 可能是 list，统一转换为 tuple
        backoff = getattr(settings, "MODEL_BACKOFF_SECONDS", (0.5, 1.0, 2.0))
        if backoff is not None and not isinstance(backoff, tuple):
            backoff = tuple(backoff)
        defaults: dict[str, Any] = {
            # ===== agent 执行层 =====
            "max_retries": getattr(settings, "AGENT_MAX_RETRIES", 3),
            "initial_retry_interval": getattr(settings, "AGENT_INITIAL_RETRY_INTERVAL", 2.0),
            "max_retry_interval": getattr(settings, "AGENT_MAX_RETRY_INTERVAL", 30.0),
            "retry_backoff_factor": getattr(settings, "AGENT_RETRY_BACKOFF_FACTOR", 2.0),
            "duplicate_tool_call_threshold": getattr(settings, "AGENT_DUPLICATE_TOOL_CALL_THRESHOLD", 3),
            "duplicate_tool_call_window": getattr(settings, "AGENT_DUPLICATE_TOOL_CALL_WINDOW", 300),
            "soft_timeout": getattr(settings, "AGENT_SOFT_TIMEOUT", None),
            "hard_timeout": getattr(settings, "AGENT_HARD_TIMEOUT", None),
            # ===== 模型调用层 =====
            "backoff_seconds": backoff,
            "circuit_breaker_threshold": getattr(settings, "MODEL_CIRCUIT_BREAKER_THRESHOLD", 3),
            "circuit_breaker_cooldown": getattr(settings, "MODEL_CIRCUIT_BREAKER_COOLDOWN", 30.0),
        }
    except Exception:
        defaults = {}
    defaults.update(overrides)
    return ResilienceConfig(**defaults)


# ============================================================================
# 异常决策
# ============================================================================


class ErrorAction(Enum):
    """异常处理动作"""

    RETRY = "retry"  # 可恢复，重试
    DEGRADE = "degrade"  # 需降级（减少工具/简化执行）
    FALLBACK = "fallback"  # 回退到无工具纯对话
    FAIL = "fail"  # 不可恢复，直接失败


def classify_and_decide(
    error: Exception,
    attempt: int,
    max_retries: int,
) -> tuple[ErrorAction, Any]:
    """统一异常分类 + 决策

    Args:
        error: 原始异常
        attempt: 当前重试次数（0-based）
        max_retries: 最大重试次数

    Returns:
        (ErrorAction, LCAgentException) 元组
    """
    from Django_xm.apps.ai_engine.services.exceptions import classify_exception

    classified = classify_exception(error)

    # 不可恢复错误 → 直接失败
    if not classified.recoverable:
        return (ErrorAction.FAIL, classified)

    # 速率限制 → 重试（速率限制通常短暂可恢复）
    if classified.error_code == "RATE_LIMIT_EXCEEDED":
        if attempt < max_retries:
            return (ErrorAction.RETRY, classified)
        return (ErrorAction.DEGRADE, classified)

    # 模型调用错误 → 先重试，重试耗尽后降级
    if classified.error_code == "MODEL_CALL_ERROR":
        if attempt < max_retries:
            return (ErrorAction.RETRY, classified)
        return (ErrorAction.DEGRADE, classified)

    # Agent 执行错误（含 GraphRecursionError）→ 降级
    if classified.error_code == "AGENT_EXECUTION_ERROR":
        # GraphRecursionError 不重试，直接降级
        details = getattr(classified, "details", {}) or {}
        if details.get("recursion_limit"):
            return (ErrorAction.DEGRADE, classified)
        if attempt < max_retries:
            return (ErrorAction.RETRY, classified)
        return (ErrorAction.DEGRADE, classified)

    # Checkpoint 错误 → 回退到无工具模式
    if classified.error_code == "CHECKPOINT_ERROR":
        return (ErrorAction.FALLBACK, classified)

    # 其他可恢复错误 → 先重试，重试耗尽后回退
    if attempt < max_retries:
        return (ErrorAction.RETRY, classified)
    return (ErrorAction.FALLBACK, classified)


# ============================================================================
# 降级等级
# ============================================================================


class DegradationLevel(Enum):
    """降级等级"""

    FULL = "full"  # 完整工具集
    REDUCED_TOOLS = "reduced"  # 减少工具（移除非核心工具）
    NO_TOOLS = "no_tools"  # 无工具纯对话
    MINIMAL = "minimal"  # 最小化（仅系统提示 + 直接回答）


# 核心工具名称（降级时保留）
_CORE_TOOL_NAMES = frozenset(
    {
        "file_reader",
        "attachment_reader",
        "todo_write",
        "todo_read",
    }
)

# 可移除的工具类别（降级时优先移除）
_REMOVABLE_TOOL_CATEGORIES = {
    "reduced": frozenset(
        {
            # skill 工具（前缀 skill_）
            "skill_baidu-search",
            "skill_agent-browser",
            "skill_ontology",
            # MCP 工具
            "sequentialthinking",
            # 代理工具
            "spawn_sub_agent",
            # 翻译工具
            "translate_text",
            "detect_language",
        }
    ),
}


def get_degraded_tools(
    tools: list[Any],
    level: DegradationLevel,
    critical_tool_names: set | None = None,
) -> list[Any]:
    """根据降级等级返回工具子集

    Args:
        tools: 原始工具列表
        level: 降级等级
        critical_tool_names: 额外的关键工具名称（降级时保留）

    Returns:
        降级后的工具列表
    """
    if level == DegradationLevel.FULL:
        return tools

    if level in (DegradationLevel.NO_TOOLS, DegradationLevel.MINIMAL):
        return []

    # REDUCED_TOOLS: 保留核心工具 + critical 工具
    keep_names = set(_CORE_TOOL_NAMES)
    if critical_tool_names:
        keep_names.update(critical_tool_names)

    removable = _REMOVABLE_TOOL_CATEGORIES.get("reduced", frozenset())

    result = []
    for tool in tools:
        tool_name = getattr(tool, "name", getattr(tool, "__name__", str(tool)))
        if tool_name in keep_names:
            result.append(tool)
        elif tool_name.startswith("skill_") and tool_name in removable:
            continue  # 移除 skill 工具
        elif tool_name in removable:
            continue  # 移除可移除工具
        else:
            result.append(tool)  # 保留其他工具

    logger.info(f"工具降级 {level.value}: {len(tools)} → {len(result)} 个工具")
    return result


# ============================================================================
# 重试工具
# ============================================================================


def calculate_backoff(attempt: int, config: ResilienceConfig) -> float:
    """计算指数退避间隔

    Args:
        attempt: 重试次数（1-based）
        config: 韧性配置

    Returns:
        等待秒数
    """
    interval = config.initial_retry_interval * (config.retry_backoff_factor ** (attempt - 1))
    return min(interval, config.max_retry_interval)


async def retry_with_backoff(
    fn: Callable,
    config: ResilienceConfig,
    on_retry: Callable | None = None,
    context: dict[str, Any] | None = None,
) -> Any:
    """带指数退避的异步重试

    Args:
        fn: 异步函数
        config: 韧性配置
        on_retry: 重试回调 fn(attempt, error, backoff)
        context: 执行上下文（用于日志）

    Returns:
        fn 的返回值

    Raises:
        最后一次异常（如果所有重试都失败）
    """
    last_error = None

    for attempt in range(config.max_retries + 1):
        try:
            return await fn()
        except Exception as e:
            last_error = e
            action, classified = classify_and_decide(e, attempt, config.max_retries)

            if action != ErrorAction.RETRY:
                raise

            if attempt < config.max_retries:
                backoff = calculate_backoff(attempt + 1, config)
                logger.warning(
                    f"[Resilience] 重试 {attempt + 1}/{config.max_retries}, "
                    f"退避 {backoff:.1f}s, 错误: {classified.error_code}: {classified.message}"
                )
                if on_retry:
                    try:
                        on_retry(attempt + 1, e, backoff)
                    except Exception:
                        # 回调失败不应影响重试主流程
                        logger.debug("on_retry 回调执行失败", exc_info=True)
                await asyncio.sleep(backoff)
            else:
                raise

    raise last_error  # 不应到达这里


# ============================================================================
# 执行超时管理
# ============================================================================


class ExecutionTimeoutManager:
    """统一执行超时管理

    提供 soft_timeout（警告）和 hard_timeout 两级超时控制。
    hard_timeout 默认 None（不限制），仅作为可选兜底；
    审批等待期间应调用 pause()/resume() 暂停计时，避免用户思考时间计入 elapsed。
    """

    def __init__(
        self,
        soft_timeout: float | None = None,
        hard_timeout: float | None = None,
    ):
        self.soft_timeout = soft_timeout
        self.hard_timeout = hard_timeout
        import time

        self._start_time: float = time.monotonic()
        self._soft_timeout_triggered = False
        # 暂停计时支持：审批等待时不计入 elapsed
        self._paused_total: float = 0.0  # 累计暂停时长
        self._pause_start: float | None = None  # 当前暂停开始时间，None 表示未暂停

    @property
    def elapsed(self) -> float:
        """已执行时间（秒），排除暂停期间"""
        import time

        now = time.monotonic()
        current_pause = (now - self._pause_start) if self._pause_start is not None else 0.0
        return now - self._start_time - self._paused_total - current_pause

    def pause(self) -> None:
        """暂停计时（审批等待时调用）"""
        import time

        if self._pause_start is None:
            self._pause_start = time.monotonic()

    def resume(self) -> None:
        """恢复计时（审批完成后调用）"""
        import time

        if self._pause_start is not None:
            self._paused_total += time.monotonic() - self._pause_start
            self._pause_start = None

    @property
    def soft_timeout_triggered(self) -> bool:
        return self._soft_timeout_triggered

    def check_soft_timeout(self) -> bool:
        """检查是否触发 soft timeout（仅触发一次）"""
        if self._soft_timeout_triggered:
            return False
        if self.soft_timeout and self.elapsed >= self.soft_timeout:
            self._soft_timeout_triggered = True
            return True
        return False


# ============================================================================
# 重复工具调用检测
# ============================================================================


@dataclass
class DuplicateToolCallWarning:
    """重复工具调用警告

    当 DuplicateToolCallDetector 检测到短时间内相同 tool_name + 相同 parameters
    被反复调用时生成，调用方应将 to_prompt() 的返回值注入 agent 消息流，
    引导 agent 调整策略而非继续重试。
    """

    tool_name: str
    parameters: dict[str, Any]
    count: int
    window_seconds: int

    def to_prompt(self) -> str:
        """生成注入 agent 的提示文本

        Returns:
            引导 agent 调整策略的中文提示
        """
        return (
            f'工具 "{self.tool_name}" 在最近 {self.window_seconds} 秒内已被调用 '
            f"{self.count} 次（参数相同）。这可能是重试循环。"
            f"请调整策略：换用其他工具、修改参数，或直接基于已有信息回答。"
        )


class DuplicateToolCallDetector:
    """检测短期内的重复工具调用

    维护 tool_call_history，记录每个 (tool_name, parameters) 组合在时间窗口内的
    调用次数。当窗口内相同调用次数超过阈值时，返回 DuplicateToolCallWarning
    供调用方注入 agent 消息流。

    设计原则：
    - 通用工具，不依赖具体 agent 框架或消息类型
    - parameters 哈希稳定（sort_keys=True + MD5）
    - 自动清理过期记录，避免内存泄漏
    - 每个 key 在一个窗口内只警告一次，避免重复注入
    - 单次 agent 执行周期内有效，不跨执行复用

    使用示例：
        detector = DuplicateToolCallDetector(window_seconds=300, threshold=3)
        warning = detector.record("web_search", {"query": "python"})
        if warning is not None:
            # 将 warning.to_prompt() 注入 agent 消息流
            ...

    集成说明：
        - adapter.py: 在 astream_research_with_interrupts 中，
          对每个 TOOL_CALL_PENDING 事件调用 record()，检测到警告时
          通过 graph.aupdate_state 注入 SystemMessage。
        - subagent_support.py: 在 SubAgentToolEventMiddleware 中同样集成，
          覆盖子智能体内部的重复调用（子智能体工具调用不冒泡到父 graph）。
    """

    def __init__(
        self,
        window_seconds: int = 300,
        threshold: int = 3,
    ):
        self.window_seconds = window_seconds
        self.threshold = threshold
        # key = f"{tool_name}:{md5(parameters)}"
        # value = {"count": int, "first_time": float, "last_time": float, "warned": bool}
        self._history: dict[str, dict[str, Any]] = {}

    def record(
        self,
        tool_name: str,
        parameters: Any,
    ) -> DuplicateToolCallWarning | None:
        """记录一次工具调用，返回警告对象（若触发阈值）或 None

        Args:
            tool_name: 工具名称
            parameters: 工具调用参数（通常为 dict，非 dict 会先归一化）

        Returns:
            DuplicateToolCallWarning 当窗口内相同调用次数首次超过阈值时返回；
            否则返回 None（包括已警告过的 key，直到窗口过期后重置）。
        """
        self._cleanup_expired()

        key = self._make_key(tool_name, parameters)
        now = time.monotonic()

        if key not in self._history:
            self._history[key] = {
                "count": 1,
                "first_time": now,
                "last_time": now,
                "warned": False,
                "tool_name": tool_name,
                "parameters": parameters if isinstance(parameters, dict) else {},
            }
            return None

        record = self._history[key]
        record["count"] += 1
        record["last_time"] = now

        # 仅在首次超过阈值时返回警告，避免重复注入
        if not record["warned"] and record["count"] > self.threshold:
            record["warned"] = True
            logger.warning(
                f"[Resilience] 检测到重复工具调用: tool={tool_name}, "
                f"count={record['count']}, threshold={self.threshold}, "
                f"window={self.window_seconds}s"
            )
            return DuplicateToolCallWarning(
                tool_name=tool_name,
                parameters=record["parameters"],
                count=record["count"],
                window_seconds=self.window_seconds,
            )

        return None

    def _make_key(self, tool_name: str, parameters: Any) -> str:
        """生成 (tool_name, parameters) 的稳定哈希 key

        使用 json.dumps(sort_keys=True) 保证参数顺序不影响哈希，
        不可序列化对象通过 default=str fallback，最终 try/except 兜底。

        Args:
            tool_name: 工具名称
            parameters: 工具参数

        Returns:
            f"{tool_name}:{md5_hex}" 格式的 key
        """
        if not isinstance(parameters, dict):
            # 非 dict 参数归一化为 dict，保证哈希逻辑统一
            params_for_hash: dict[str, Any] = {"_raw": str(parameters)}
        else:
            params_for_hash = parameters

        try:
            params_str = json.dumps(
                params_for_hash,
                sort_keys=True,
                ensure_ascii=False,
                default=str,
            )
        except (TypeError, ValueError):
            # 极端情况：default=str 仍无法序列化（如含循环引用）
            params_str = str(params_for_hash)

        digest = hashlib.md5(params_str.encode("utf-8"), usedforsecurity=False).hexdigest()
        return f"{tool_name}:{digest}"

    def _cleanup_expired(self) -> None:
        """清理超过时间窗口的记录

        以 last_time 为基准，若 now - last_time > window_seconds 则删除。
        使用 last_time（而非 first_time）确保持续调用的 key 不会被误删。
        """
        now = time.monotonic()
        expired_keys = [key for key, record in self._history.items() if now - record["last_time"] > self.window_seconds]
        for key in expired_keys:
            del self._history[key]

    def reset(self) -> None:
        """重置检测器（用于 agent 重试时复用同一 detector）"""
        self._history.clear()
