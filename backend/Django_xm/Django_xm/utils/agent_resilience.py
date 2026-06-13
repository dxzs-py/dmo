"""Agent 执行韧性公共基础组件

为代理模式和深度研究模式提供统一的重试、降级、异常捕获、回退能力。

架构分层：
- 创建阶段（agent_hub 层）：模型 fallback、框架降级、工具加载降级
- 执行阶段（本模块）：重试、降级、异常捕获、回退、超时管理
"""

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ============================================================================
# 配置
# ============================================================================

@dataclass
class ResilienceConfig:
    """韧性执行配置"""
    max_retries: int = 3
    initial_retry_interval: float = 2.0
    max_retry_interval: float = 30.0
    retry_backoff_factor: float = 2.0
    # 执行超时
    soft_timeout: Optional[float] = None   # 警告阈值（秒），None 表示不限制
    hard_timeout: Optional[float] = None   # 强制终止阈值（秒），None 表示不限制


def get_resilience_config(**overrides) -> ResilienceConfig:
    """获取韧性配置，支持 Django settings 覆盖"""
    try:
        from django.conf import settings
        defaults = {
            'max_retries': getattr(settings, 'AGENT_MAX_RETRIES', 3),
            'initial_retry_interval': getattr(settings, 'AGENT_INITIAL_RETRY_INTERVAL', 2.0),
            'max_retry_interval': getattr(settings, 'AGENT_MAX_RETRY_INTERVAL', 30.0),
            'retry_backoff_factor': getattr(settings, 'AGENT_RETRY_BACKOFF_FACTOR', 2.0),
            'soft_timeout': getattr(settings, 'AGENT_SOFT_TIMEOUT', None),
            'hard_timeout': getattr(settings, 'AGENT_HARD_TIMEOUT', None),
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
    RETRY = "retry"          # 可恢复，重试
    DEGRADE = "degrade"      # 需降级（减少工具/简化执行）
    FALLBACK = "fallback"    # 回退到无工具纯对话
    FAIL = "fail"            # 不可恢复，直接失败


def classify_and_decide(
    error: Exception,
    attempt: int,
    max_retries: int,
) -> Tuple[ErrorAction, Any]:
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
        details = getattr(classified, 'details', {}) or {}
        if details.get('recursion_limit'):
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
    FULL = "full"                # 完整工具集
    REDUCED_TOOLS = "reduced"    # 减少工具（移除非核心工具）
    NO_TOOLS = "no_tools"        # 无工具纯对话
    MINIMAL = "minimal"          # 最小化（仅系统提示 + 直接回答）


# 核心工具名称（降级时保留）
_CORE_TOOL_NAMES = frozenset({
    'file_reader', 'attachment_reader',
    'todo_write', 'todo_read',
})

# 可移除的工具类别（降级时优先移除）
_REMOVABLE_TOOL_CATEGORIES = {
    'reduced': frozenset({
        # skill 工具（前缀 skill_）
        'skill_baidu-search', 'skill_agent-browser', 'skill_ontology',
        # MCP 工具
        'sequentialthinking',
        # 代理工具
        'agent_create', 'agent_run', 'agent_list', 'agent_cleanup',
        # 翻译工具
        'translate_text', 'detect_language',
    }),
}


def get_degraded_tools(
    tools: List[Any],
    level: DegradationLevel,
    critical_tool_names: Optional[set] = None,
) -> List[Any]:
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

    if level == DegradationLevel.NO_TOOLS or level == DegradationLevel.MINIMAL:
        return []

    # REDUCED_TOOLS: 保留核心工具 + critical 工具
    keep_names = set(_CORE_TOOL_NAMES)
    if critical_tool_names:
        keep_names.update(critical_tool_names)

    removable = _REMOVABLE_TOOL_CATEGORIES.get('reduced', frozenset())

    result = []
    for tool in tools:
        tool_name = getattr(tool, 'name', getattr(tool, '__name__', str(tool)))
        if tool_name in keep_names:
            result.append(tool)
        elif tool_name.startswith('skill_') and tool_name in removable:
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
    on_retry: Optional[Callable] = None,
    context: Optional[Dict[str, Any]] = None,
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
    ctx = context or {}

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
                        pass
                await asyncio.sleep(backoff)
            else:
                raise

    raise last_error  # 不应到达这里


# ============================================================================
# 执行超时管理
# ============================================================================

class ExecutionTimeoutManager:
    """统一执行超时管理

    提供 soft_timeout（警告）和 hard_timeout（强制终止）两级超时控制。
    """

    def __init__(
        self,
        soft_timeout: Optional[float] = None,
        hard_timeout: Optional[float] = None,
    ):
        self.soft_timeout = soft_timeout
        self.hard_timeout = hard_timeout
        import time
        self._start_time: float = time.monotonic()
        self._soft_timeout_triggered = False

    @property
    def elapsed(self) -> float:
        """已执行时间（秒）"""
        import time
        return time.monotonic() - self._start_time

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

    async def execute_with_timeout(
        self,
        coro,
        on_soft_timeout: Optional[Callable] = None,
    ) -> Any:
        """带超时的执行

        Args:
            coro: 异步协程
            on_soft_timeout: soft timeout 回调

        Returns:
            协程返回值

        Raises:
            asyncio.TimeoutError: hard timeout 触发
        """
        import time
        self._start_time = time.monotonic()
        self._soft_timeout_triggered = False

        if self.hard_timeout is None and self.soft_timeout is None:
            return await coro

        timeout = self.hard_timeout or self.soft_timeout

        try:
            result = await asyncio.wait_for(coro, timeout=timeout)

            # 检查 soft timeout（执行完成但超过 soft 阈值）
            if self.check_soft_timeout() and on_soft_timeout:
                try:
                    on_soft_timeout(self.elapsed)
                except Exception:
                    pass

            return result
        except asyncio.TimeoutError:
            elapsed = self.elapsed
            logger.warning(
                f"[Resilience] 执行超时: elapsed={elapsed:.1f}s, "
                f"soft={self.soft_timeout}, hard={self.hard_timeout}"
            )
            raise
