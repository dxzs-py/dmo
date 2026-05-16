"""
MCP 接口适配层与数据转换中间件

提供:
1. 工具调用拦截器 - 日志记录、参数校验、结果转换
2. 数据格式转换 - MCP 工具结果统一为 Agent 可消费格式
3. 错误处理 - 统一异常捕获和重试机制
4. 服务注册 - MCP 工具动态注册与发现
"""

import time
import json
import traceback
from typing import Any, Callable, Dict, List, Optional, Tuple
from functools import wraps

from langchain_core.tools import BaseTool

from Django_xm.apps.ai_engine.config import get_logger

logger = get_logger(__name__)


class ToolCallLog:
    def __init__(self):
        self.records: List[Dict[str, Any]] = []

    def record(
        self,
        tool_name: str,
        args: Dict[str, Any],
        result: Any = None,
        error: Optional[str] = None,
        duration_ms: float = 0,
    ):
        entry = {
            "tool_name": tool_name,
            "args": args,
            "result": str(result)[:500] if result else None,
            "error": error,
            "duration_ms": round(duration_ms, 2),
            "timestamp": time.time(),
        }
        self.records.append(entry)
        if len(self.records) > 1000:
            self.records = self.records[-500:]

    def get_recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self.records[-limit:]

    def get_by_tool(self, tool_name: str, limit: int = 20) -> List[Dict[str, Any]]:
        filtered = [r for r in self.records if r["tool_name"] == tool_name]
        return filtered[-limit:]


_tool_call_log = ToolCallLog()


def get_tool_call_log() -> ToolCallLog:
    return _tool_call_log


def logging_interceptor(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    logger.debug(f"MCP 工具调用: {tool_name}, 参数: {json.dumps(args, ensure_ascii=False)[:200]}")
    return args


def create_validation_interceptor(
    required_params: Optional[Dict[str, type]] = None,
    max_arg_length: int = 10000,
) -> Callable:
    def validation_interceptor(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if required_params:
            for param, expected_type in required_params.items():
                if param not in args:
                    raise ValueError(f"工具 '{tool_name}' 缺少必需参数: {param}")
                if not isinstance(args[param], expected_type):
                    try:
                        args[param] = expected_type(args[param])
                    except (ValueError, TypeError):
                        raise TypeError(
                            f"工具 '{tool_name}' 参数 '{param}' 类型错误: "
                            f"期望 {expected_type.__name__}, 实际 {type(args[param]).__name__}"
                        )

        for key, value in args.items():
            if isinstance(value, str) and len(value) > max_arg_length:
                args[key] = value[:max_arg_length]
                logger.warning(f"工具 '{tool_name}' 参数 '{key}' 已截断至 {max_arg_length} 字符")

        return args

    return validation_interceptor


def create_retry_interceptor(
    max_retries: int = 2,
    retry_delay: float = 1.0,
    retryable_exceptions: Tuple = (ConnectionError, TimeoutError),
) -> Callable:
    def retry_interceptor(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        args["_max_retries"] = max_retries
        args["_retry_delay"] = retry_delay
        args["_retryable_exceptions"] = retryable_exceptions
        return args

    return retry_interceptor


def create_rate_limit_interceptor(
    calls_per_minute: int = 30,
) -> Callable:
    _call_times: List[float] = []

    def rate_limit_interceptor(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        now = time.time()
        _call_times.append(now)

        recent = [t for t in _call_times if now - t < 60]
        _call_times.clear()
        _call_times.extend(recent)

        if len(recent) >= calls_per_minute:
            raise RuntimeError(
                f"MCP 工具调用频率超限: {tool_name} "
                f"(限制: {calls_per_minute}/分钟, 当前: {len(recent)}/分钟)"
            )

        return args

    return rate_limit_interceptor


def normalize_tool_result(result: Any) -> str:
    if isinstance(result, str):
        return result

    if isinstance(result, dict):
        try:
            return json.dumps(result, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            return str(result)

    if isinstance(result, list):
        try:
            return json.dumps(result, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            return "\n".join(str(item) for item in result)

    if isinstance(result, (int, float, bool)):
        return str(result)

    return str(result)


def wrap_tool_with_middleware(
    tool: BaseTool,
    interceptors: Optional[List[Callable]] = None,
    normalize_result: bool = True,
    log_calls: bool = True,
) -> BaseTool:
    original_run = tool._run
    original_arun = tool._arun if hasattr(tool, '_arun') else None

    interceptors = interceptors or []

    @wraps(original_run)
    def wrapped_run(*args, **kwargs):
        start_time = time.time()
        error = None
        result = None

        try:
            for interceptor in interceptors:
                kwargs = interceptor(tool.name, kwargs) or kwargs

            result = original_run(*args, **kwargs)

            if normalize_result:
                result = normalize_tool_result(result)

            return result
        except Exception as e:
            error = str(e)
            logger.error(f"MCP 工具执行失败: {tool.name} - {error}")
            raise
        finally:
            if log_calls:
                duration = (time.time() - start_time) * 1000
                _tool_call_log.record(
                    tool_name=tool.name,
                    args=kwargs,
                    result=result,
                    error=error,
                    duration_ms=duration,
                )

    tool._run = wrapped_run

    if original_arun:
        @wraps(original_arun)
        async def wrapped_arun(*args, **kwargs):
            start_time = time.time()
            error = None
            result = None

            try:
                for interceptor in interceptors:
                    kwargs = interceptor(tool.name, kwargs) or kwargs

                result = await original_arun(*args, **kwargs)

                if normalize_result:
                    result = normalize_tool_result(result)

                return result
            except Exception as e:
                error = str(e)
                logger.error(f"MCP 工具异步执行失败: {tool.name} - {error}")
                raise
            finally:
                if log_calls:
                    duration = (time.time() - start_time) * 1000
                    _tool_call_log.record(
                        tool_name=tool.name,
                        args=kwargs,
                        result=result,
                        error=error,
                        duration_ms=duration,
                    )

        tool._arun = wrapped_arun

    return tool


class MCPToolRegistry:
    _instance: Optional["MCPToolRegistry"] = None
    _tools: Dict[str, Dict[str, Any]] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def register(
        self,
        tool: BaseTool,
        source: str = "unknown",
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self._tools[tool.name] = {
            "tool": tool,
            "source": source,
            "metadata": metadata or {},
            "registered_at": time.time(),
        }
        logger.debug(f"MCP 工具已注册: {tool.name} (来源: {source})")

    def unregister(self, tool_name: str):
        if tool_name in self._tools:
            del self._tools[tool_name]
            logger.debug(f"MCP 工具已注销: {tool_name}")

    def get(self, tool_name: str) -> Optional[BaseTool]:
        entry = self._tools.get(tool_name)
        return entry["tool"] if entry else None

    def get_all(self) -> List[BaseTool]:
        return [entry["tool"] for entry in self._tools.values()]

    def get_by_source(self, source: str) -> List[BaseTool]:
        return [
            entry["tool"]
            for entry in self._tools.values()
            if entry["source"] == source
        ]

    def list_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": name,
                "source": entry["source"],
                "description": entry["tool"].description[:100] if entry["tool"].description else "",
                "metadata": entry["metadata"],
                "registered_at": entry["registered_at"],
            }
            for name, entry in self._tools.items()
        ]

    def clear(self):
        self._tools.clear()


def get_tool_registry() -> MCPToolRegistry:
    return MCPToolRegistry()


__all__ = [
    "ToolCallLog",
    "get_tool_call_log",
    "logging_interceptor",
    "create_validation_interceptor",
    "create_retry_interceptor",
    "create_rate_limit_interceptor",
    "normalize_tool_result",
    "wrap_tool_with_middleware",
    "MCPToolRegistry",
    "get_tool_registry",
]
