"""统一工具结果与错误处理

合并了原 errors.py 和 result_types.py，提供唯一的工具返回格式。
- ToolResult: 统一返回格式（成功/失败 + 元数据）
- StandardToolResult: 轻量结果格式（状态枚举 + 来源标记）
- ToolError / ToolErrorCode: 结构化错误
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel

# 工具版本常量
TOOL_VERSION = "1.0.0"


class ToolErrorCode(StrEnum):
    TIMEOUT = "TIMEOUT"
    RATE_LIMIT = "RATE_LIMIT"
    INVALID_INPUT = "INVALID_INPUT"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    NETWORK_ERROR = "NETWORK_ERROR"


class ToolStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    PARTIAL = "partial"


class ToolError(BaseModel):
    code: ToolErrorCode
    message: str
    tool_name: str
    retryable: bool = False
    suggestion: str = ""


class ToolResult(BaseModel):
    """统一工具返回格式（含错误详情）"""

    success: bool
    content: str | None = None
    error: ToolError | None = None
    metadata: dict = {}

    def to_str(self) -> str:
        if self.success and self.content is not None:
            return self.content
        if self.error is not None:
            parts = [f"[{self.error.code.value}] {self.error.message}"]
            if self.error.suggestion:
                parts.append(f"建议: {self.error.suggestion}")
            if self.error.retryable:
                parts.append("此错误可重试")
            return " | ".join(parts)
        return ""


class StandardToolResult(BaseModel):
    """轻量工具返回格式（状态枚举 + 来源标记）

    适用于工具内部使用，可转换为 ToolResult 或直接输出字符串。
    """

    content: str
    status: ToolStatus = ToolStatus.SUCCESS
    metadata: dict[str, Any] = {}
    source: str | None = None

    def to_tool_message(self) -> str:
        """转换为工具消息字符串"""
        if self.status == ToolStatus.ERROR:
            return f"错误: {self.content}"
        return self.content

    def to_tool_result(self, tool_name: str = "") -> ToolResult:
        """转换为 ToolResult"""
        if self.status == ToolStatus.ERROR:
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message=self.content,
                    tool_name=tool_name,
                ),
                metadata=self.metadata,
            )
        return ToolResult(success=True, content=self.content, metadata=self.metadata)


def create_tool_result(content: str, metadata: dict | None = None) -> ToolResult:
    return ToolResult(success=True, content=content, metadata=metadata or {})


def create_tool_error(
    code: ToolErrorCode,
    message: str,
    tool_name: str,
    retryable: bool = False,
    suggestion: str = "",
) -> ToolResult:
    return ToolResult(
        success=False,
        error=ToolError(
            code=code,
            message=message,
            tool_name=tool_name,
            retryable=retryable,
            suggestion=suggestion,
        ),
    )


def exception_to_tool_error(exc: Exception, tool_name: str) -> ToolResult:
    if isinstance(exc, TimeoutError):
        return create_tool_error(ToolErrorCode.TIMEOUT, str(exc), tool_name, retryable=True, suggestion="请稍后重试")
    if isinstance(exc, ConnectionError):
        return create_tool_error(
            ToolErrorCode.NETWORK_ERROR, str(exc), tool_name, retryable=True, suggestion="请检查网络连接后重试"
        )
    if isinstance(exc, (ValueError, TypeError)):
        return create_tool_error(ToolErrorCode.INVALID_INPUT, str(exc), tool_name, suggestion="请检查输入参数")
    if isinstance(exc, FileNotFoundError):
        return create_tool_error(ToolErrorCode.NOT_FOUND, str(exc), tool_name, suggestion="请确认资源路径是否正确")
    if isinstance(exc, PermissionError):
        return create_tool_error(ToolErrorCode.PERMISSION_DENIED, str(exc), tool_name, suggestion="请检查权限配置")
    if isinstance(exc, RuntimeError) and "频率超限" in str(exc):
        return create_tool_error(
            ToolErrorCode.RATE_LIMIT, str(exc), tool_name, retryable=True, suggestion="请降低调用频率后重试"
        )
    return create_tool_error(ToolErrorCode.EXECUTION_ERROR, str(exc), tool_name, suggestion="请检查工具配置和参数")
