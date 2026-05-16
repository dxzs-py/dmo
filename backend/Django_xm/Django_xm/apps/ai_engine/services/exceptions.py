"""
统一异常处理模块

集成 LangChain 官方异常类型，提供项目级别的异常分类和处理策略。

LangChain 官方异常层次：
- langchain_core.exceptions.LangChainException (基类)
  - OutputParserException: 输出解析失败
  - ToolException: 工具执行失败
  - CallbackManagerError: 回调管理错误

自定义异常层次：
- LCAgentException (项目基类)
  - ModelCallError: 模型调用失败
  - AgentExecutionError: Agent 执行失败
  - RAGRetrievalError: RAG 检索失败
  - GuardrailsValidationError: Guardrails 验证失败
  - RateLimitExceededError: 速率限制超限
  - CheckpointError: 状态持久化失败
"""

from typing import Optional, Any, Dict

from langchain_core.exceptions import (
    LangChainException,
    OutputParserException,
)
from langchain_core.tools import ToolException


class LCAgentException(Exception):
    """项目级 Agent 异常基类"""

    def __init__(
        self,
        message: str,
        error_code: str = "AGENT_ERROR",
        details: Optional[Dict[str, Any]] = None,
        recoverable: bool = True,
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.details = details or {}
        self.recoverable = recoverable

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_code": self.error_code,
            "message": self.message,
            "details": self.details,
            "recoverable": self.recoverable,
        }


class ModelCallError(LCAgentException):
    """模型调用失败"""

    def __init__(self, message: str, model_name: str = "", **kwargs):
        super().__init__(
            message,
            error_code="MODEL_CALL_ERROR",
            details={"model_name": model_name, **kwargs},
            recoverable=True,
        )


class AgentExecutionError(LCAgentException):
    """Agent 执行失败"""

    def __init__(self, message: str, agent_type: str = "", **kwargs):
        super().__init__(
            message,
            error_code="AGENT_EXECUTION_ERROR",
            details={"agent_type": agent_type, **kwargs},
            recoverable=True,
        )


class RAGRetrievalError(LCAgentException):
    """RAG 检索失败"""

    def __init__(self, message: str, index_name: str = "", **kwargs):
        super().__init__(
            message,
            error_code="RAG_RETRIEVAL_ERROR",
            details={"index_name": index_name, **kwargs},
            recoverable=True,
        )


class GuardrailsValidationError(LCAgentException):
    """Guardrails 验证失败"""

    def __init__(self, message: str, validation_type: str = "", **kwargs):
        super().__init__(
            message,
            error_code="GUARDRAILS_VALIDATION_ERROR",
            details={"validation_type": validation_type, **kwargs},
            recoverable=False,
        )


class RateLimitExceededError(LCAgentException):
    """速率限制超限"""

    def __init__(self, message: str = "API 速率限制已超限，请稍后重试", **kwargs):
        super().__init__(
            message,
            error_code="RATE_LIMIT_EXCEEDED",
            recoverable=True,
            **kwargs,
        )


class CheckpointError(LCAgentException):
    """状态持久化失败"""

    def __init__(self, message: str, backend: str = "", **kwargs):
        super().__init__(
            message,
            error_code="CHECKPOINT_ERROR",
            details={"backend": backend, **kwargs},
            recoverable=True,
        )


def classify_exception(exc: Exception) -> LCAgentException:
    """
    将任意异常分类为项目级异常

    将 LangChain 官方异常和通用异常统一映射为 LCAgentException 体系。

    Args:
        exc: 原始异常

    Returns:
        分类后的 LCAgentException
    """
    if isinstance(exc, LCAgentException):
        return exc

    if isinstance(exc, OutputParserException):
        return GuardrailsValidationError(
            message=f"输出解析失败: {exc}",
            validation_type="output_parser",
            details={"original_error": str(exc)},
        )

    if isinstance(exc, ToolException):
        return AgentExecutionError(
            message=f"工具执行失败: {exc}",
            agent_type="tool",
            details={"original_error": str(exc)},
        )

    if isinstance(exc, LangChainException):
        return AgentExecutionError(
            message=f"LangChain 异常: {exc}",
            details={"original_type": type(exc).__name__},
        )

    try:
        from langgraph.errors import GraphRecursionError
        if isinstance(exc, GraphRecursionError):
            return AgentExecutionError(
                message=f"Agent 执行超出最大迭代次数: {exc}",
                agent_type="langgraph",
                details={"original_type": type(exc).__name__, "recursion_limit": True},
                recoverable=True,
            )
    except ImportError:
        pass

    try:
        from openai import (
            RateLimitError as OpenAIRateLimitError,
            AuthenticationError as OpenAIAuthError,
            APIConnectionError as OpenAIConnectionError,
            APITimeoutError as OpenAITimeoutError,
            BadRequestError as OpenAIBadRequestError,
        )
        if isinstance(exc, OpenAIRateLimitError):
            return RateLimitExceededError(
                message=f"OpenAI 速率限制: {exc}",
                details={"original_type": type(exc).__name__, "provider": "openai"},
            )
        if isinstance(exc, OpenAIAuthError):
            return ModelCallError(
                message=f"OpenAI 认证失败: {exc}",
                details={"original_type": type(exc).__name__, "auth_error": True},
                recoverable=False,
            )
        if isinstance(exc, OpenAIConnectionError):
            return ModelCallError(
                message=f"OpenAI 连接失败: {exc}",
                details={"original_type": type(exc).__name__, "connection_error": True},
            )
        if isinstance(exc, OpenAITimeoutError):
            return ModelCallError(
                message=f"OpenAI 请求超时: {exc}",
                details={"original_type": type(exc).__name__, "timeout": True},
            )
        if isinstance(exc, OpenAIBadRequestError):
            return ModelCallError(
                message=f"OpenAI 请求参数错误: {exc}",
                details={"original_type": type(exc).__name__, "bad_request": True},
            )
    except ImportError:
        pass

    error_msg = str(exc).lower()

    if "rate" in error_msg and "limit" in error_msg:
        return RateLimitExceededError(
            message=f"API 速率限制: {exc}",
            details={"original_type": type(exc).__name__},
        )

    if isinstance(exc, TimeoutError):
        return ModelCallError(
            message=f"模型调用超时: {exc}",
            details={"original_type": type(exc).__name__, "timeout": True},
        )

    if isinstance(exc, (ConnectionError, OSError)):
        return ModelCallError(
            message=f"网络连接失败: {exc}",
            details={"original_type": type(exc).__name__, "connection_error": True},
        )

    if "timeout" in error_msg or "timed out" in error_msg:
        return ModelCallError(
            message=f"模型调用超时: {exc}",
            details={"original_type": type(exc).__name__, "timeout": True},
        )

    if "auth" in error_msg or "api_key" in error_msg or "unauthorized" in error_msg:
        return ModelCallError(
            message=f"认证失败: {exc}",
            details={"original_type": type(exc).__name__, "auth_error": True},
            recoverable=False,
        )

    return LCAgentException(
        message=f"未预期错误: {exc}",
        error_code="UNEXPECTED_ERROR",
        details={"original_type": type(exc).__name__},
        recoverable=False,
    )
