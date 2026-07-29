"""
MCP 接口适配层与数据转换中间件

提供:
1. 工具调用拦截器 - 日志记录、参数校验、结果转换
2. 数据格式转换 - MCP 工具结果统一为 Agent 可消费格式
3. 错误处理 - 统一异常捕获和重试机制
4. 服务注册 - MCP 工具动态注册与发现
"""

import json
import time
from collections.abc import Callable
from datetime import datetime
from functools import wraps
from typing import Any, ClassVar, Optional

from django.utils import timezone
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from Django_xm.apps.analytics.services.tool_analytics import (
    ToolAnalyticsService,
    ToolUsageRecord,
)
from Django_xm.apps.core.config import get_logger
from Django_xm.apps.tools.errors import (
    ToolErrorCode,
    ToolResult,
    create_tool_error,
    create_tool_result,
    exception_to_tool_error,
)

logger = get_logger(__name__)


class ToolCallLog:
    def __init__(self):
        self.records: list[dict[str, Any]] = []

    def record(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: Any = None,
        error: str | None = None,
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

    def get_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.records[-limit:]

    def get_by_tool(self, tool_name: str, limit: int = 20) -> list[dict[str, Any]]:
        filtered = [r for r in self.records if r["tool_name"] == tool_name]
        return filtered[-limit:]


_tool_call_log = ToolCallLog()


def get_tool_call_log() -> ToolCallLog:
    return _tool_call_log


def logging_interceptor(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    logger.debug(f"MCP 工具调用: {tool_name}, 参数: {json.dumps(args, ensure_ascii=False)[:200]}")
    return args


def create_validation_interceptor(
    required_params: dict[str, type] | None = None,
    max_arg_length: int = 10000,
) -> Callable:
    def validation_interceptor(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
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
                        ) from None

        for key, value in args.items():
            if isinstance(value, str) and len(value) > max_arg_length:
                args[key] = value[:max_arg_length]
                logger.warning(f"工具 '{tool_name}' 参数 '{key}' 已截断至 {max_arg_length} 字符")

        return args

    return validation_interceptor


def create_retry_interceptor(
    max_retries: int = 2,
    retry_delay: float = 1.0,
    retryable_exceptions: tuple = (ConnectionError, TimeoutError),
) -> Callable:
    def retry_interceptor(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        args["_max_retries"] = max_retries
        args["_retry_delay"] = retry_delay
        args["_retryable_exceptions"] = retryable_exceptions
        return args

    return retry_interceptor


def create_rate_limit_interceptor(
    calls_per_minute: int = 30,
) -> Callable:
    _call_times: list[float] = []

    def rate_limit_interceptor(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        _call_times.append(now)

        recent = [t for t in _call_times if now - t < 60]
        _call_times.clear()
        _call_times.extend(recent)

        if len(recent) >= calls_per_minute:
            raise RuntimeError(
                f"MCP 工具调用频率超限: {tool_name} (限制: {calls_per_minute}/分钟, 当前: {len(recent)}/分钟)"
            )

        return args

    return rate_limit_interceptor


class ToolResultAdapter:
    @staticmethod
    def adapt(result: Any, source: str = "local") -> str:
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            return json.dumps(result, ensure_ascii=False)
        return str(result)

    @staticmethod
    def adapt_with_metadata(result: Any, source: str = "local", tool_name: str = "") -> dict:
        return {
            "content": ToolResultAdapter.adapt(result, source),
            "source": source,
            "tool_name": tool_name,
            "timestamp": timezone.now().isoformat(),
        }


def normalize_tool_result(result: Any, source: str = "local") -> str:
    if isinstance(result, ToolResult):
        return result.to_str()
    adapted = ToolResultAdapter.adapt(result, source)
    return adapted


def normalize_to_tool_result(result: Any, tool_name: str = "", source: str = "local") -> ToolResult:
    if isinstance(result, ToolResult):
        return result
    if isinstance(result, str) and result.startswith(("错误", "错误：", "错误:")):
        return create_tool_error(
            code=ToolErrorCode.EXECUTION_ERROR,
            message=result,
            tool_name=tool_name,
            retryable=False,
        )
    adapted = ToolResultAdapter.adapt(result, source)
    return create_tool_result(content=adapted, metadata={"source": source})


def wrap_tool_with_middleware(
    tool: BaseTool,
    interceptors: list[Callable] | None = None,
    normalize_result: bool = True,
    log_calls: bool = True,
) -> BaseTool:
    original_run = tool._run
    original_arun = tool._arun if hasattr(tool, "_arun") else None

    interceptors = interceptors or []
    tool_source = (getattr(tool, "metadata", None) or {}).get("source", "local")

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
                tool_result = normalize_to_tool_result(result, tool_name=tool.name, source=tool_source)
                result = tool_result.to_str()

            return result
        except Exception as e:
            error = str(e)
            tool_result = exception_to_tool_error(e, tool_name=tool.name)
            result = tool_result.to_str()
            logger.exception(f"MCP 工具执行失败: {tool.name} - {error}")
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
                try:
                    analytics = ToolAnalyticsService()
                    analytics.record(
                        ToolUsageRecord(
                            tool_name=tool.name,
                            timestamp=timezone.now(),
                            success=error is None,
                            duration_ms=duration,
                            error_code=None,
                        )
                    )
                except Exception:
                    # 分析记录失败不影响工具执行主流程
                    logger.debug("记录工具 %s 使用分析失败（同步）", tool.name)

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
                    tool_result = normalize_to_tool_result(result, tool_name=tool.name, source=tool_source)
                    result = tool_result.to_str()

                return result
            except Exception as e:
                error = str(e)
                tool_result = exception_to_tool_error(e, tool_name=tool.name)
                result = tool_result.to_str()
                logger.exception(f"MCP 工具异步执行失败: {tool.name} - {error}")
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
                    try:
                        analytics = ToolAnalyticsService()
                        analytics.record(
                            ToolUsageRecord(
                                tool_name=tool.name,
                                timestamp=timezone.now(),
                                success=error is None,
                                duration_ms=duration,
                                error_code=None,
                            )
                        )
                    except Exception:
                        # 分析记录失败不影响工具执行主流程
                        logger.debug("记录工具 %s 使用分析失败（异步）", tool.name)

        tool._arun = wrapped_arun

    return tool


class ToolVersionInfo(BaseModel):
    tool_name: str
    version: str = "1.0.0"
    last_updated: datetime = timezone.now()
    changelog: list[str] = []


class MCPToolRegistry:
    _instance: Optional["MCPToolRegistry"] = None
    _tools: ClassVar[dict[str, dict[str, Any]]] = {}
    _versions: ClassVar[dict[str, ToolVersionInfo]] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def register(
        self,
        tool: BaseTool,
        source: str = "unknown",
        metadata: dict[str, Any] | None = None,
        version: str | None = None,
    ):
        effective_version = version or "1.0.0"
        if version is None and metadata:
            effective_version = metadata.get("version", "1.0.0")
        if version is None and hasattr(tool, "metadata") and tool.metadata:
            effective_version = tool.metadata.get("version", effective_version)

        self._tools[tool.name] = {
            "tool": tool,
            "source": source,
            "metadata": metadata or {},
            "registered_at": time.time(),
            "version": effective_version,
        }

        existing = self._versions.get(tool.name)
        if existing:
            if effective_version != existing.version:
                existing.version = effective_version
                existing.last_updated = timezone.now()
                existing.changelog.append(f"版本更新至 {effective_version}")
        else:
            self._versions[tool.name] = ToolVersionInfo(
                tool_name=tool.name,
                version=effective_version,
                last_updated=timezone.now(),
            )

        logger.debug(f"MCP 工具已注册: {tool.name} (来源: {source}, 版本: {effective_version})")

    def register_tool(self, tool: BaseTool, source: str = "local", version: str = "1.0.0"):
        self.register(tool, source=source, version=version)

    def get_tool_version(self, tool_name: str) -> ToolVersionInfo | None:
        return self._versions.get(tool_name)

    def update_tool_version(self, tool_name: str, new_version: str, changelog: str = ""):
        info = self._versions.get(tool_name)
        if not info:
            logger.warning(f"更新版本失败: 工具 '{tool_name}' 未注册")
            return
        old_version = info.version
        info.version = new_version
        info.last_updated = timezone.now()
        entry_text = changelog or f"版本从 {old_version} 更新至 {new_version}"
        info.changelog.append(entry_text)
        if tool_name in self._tools:
            self._tools[tool_name]["version"] = new_version
        logger.debug(f"MCP 工具版本已更新: {tool_name} ({old_version} -> {new_version})")

    def list_versions(self) -> dict[str, ToolVersionInfo]:
        return dict(self._versions)

    def unregister(self, tool_name: str):
        if tool_name in self._tools:
            del self._tools[tool_name]
        if tool_name in self._versions:
            del self._versions[tool_name]
        logger.debug(f"MCP 工具已注销: {tool_name}")

    def get(self, tool_name: str) -> BaseTool | None:
        entry = self._tools.get(tool_name)
        return entry["tool"] if entry else None

    def get_all(self) -> list[BaseTool]:
        return [entry["tool"] for entry in self._tools.values()]

    def get_by_source(self, source: str) -> list[BaseTool]:
        return [entry["tool"] for entry in self._tools.values() if entry["source"] == source]

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "source": entry["source"],
                "description": entry["tool"].description[:100] if entry["tool"].description else "",
                "metadata": entry["metadata"],
                "registered_at": entry["registered_at"],
                "version": entry.get("version", "1.0.0"),
            }
            for name, entry in self._tools.items()
        ]

    def clear(self):
        self._tools.clear()
        self._versions.clear()


def get_tool_registry() -> MCPToolRegistry:
    return MCPToolRegistry()


def create_tool_error_message(
    tool_call_id: str,
    error_code: ToolErrorCode,
    message: str,
    tool_name: str,
    retryable: bool = False,
    suggestion: str = "",
) -> ToolMessage:
    tool_result = create_tool_error(
        code=error_code,
        message=message,
        tool_name=tool_name,
        retryable=retryable,
        suggestion=suggestion,
    )
    return ToolMessage(
        content=tool_result.to_str(),
        tool_call_id=tool_call_id,
        status="error",
    )


__all__ = [
    "MCPToolRegistry",
    "ToolCallLog",
    "ToolResultAdapter",
    "ToolVersionInfo",
    "create_rate_limit_interceptor",
    "create_retry_interceptor",
    "create_tool_error_message",
    "create_validation_interceptor",
    "get_tool_call_log",
    "get_tool_registry",
    "logging_interceptor",
    "normalize_to_tool_result",
    "normalize_tool_result",
    "wrap_tool_with_middleware",
]
