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

from typing import Any

from langchain_core.exceptions import (
    LangChainException,
    OutputParserException,
)
from langchain_core.tools import ToolException

from Django_xm.apps.core.exceptions import BaseAppError
from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)


class LCAgentException(BaseAppError):
    """项目级 Agent 异常基类"""

    DEFAULT_USER_MESSAGE = "抱歉，处理您的请求时出现错误"

    def __init__(
        self,
        message: str,
        error_code: str = "AGENT_ERROR",
        details: dict[str, Any] | None = None,
        recoverable: bool = True,
        user_message: str | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.details = details or {}
        self.recoverable = recoverable
        self.user_message = user_message or self.DEFAULT_USER_MESSAGE

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_code": self.error_code,
            "message": self.message,
            "user_message": self.user_message,
            "details": self.details,
            "recoverable": self.recoverable,
        }


class ModelCallError(LCAgentException):
    """模型调用失败"""

    DEFAULT_USER_MESSAGE = "模型服务暂时不可用，请稍后重试"

    def __init__(self, message: str, model_name: str = "", **kwargs):
        super().__init__(
            message,
            error_code="MODEL_CALL_ERROR",
            details={"model_name": model_name, **kwargs},
            recoverable=True,
        )


class AgentExecutionError(LCAgentException):
    """Agent 执行失败"""

    DEFAULT_USER_MESSAGE = "智能体执行失败，请稍后重试"

    def __init__(self, message: str, agent_type: str = "", **kwargs):
        super().__init__(
            message,
            error_code="AGENT_EXECUTION_ERROR",
            details={"agent_type": agent_type, **kwargs},
            recoverable=True,
        )


class RAGRetrievalError(LCAgentException):
    """RAG 检索失败"""

    DEFAULT_USER_MESSAGE = "知识检索失败，请稍后重试"

    def __init__(self, message: str, index_name: str = "", **kwargs):
        super().__init__(
            message,
            error_code="RAG_RETRIEVAL_ERROR",
            details={"index_name": index_name, **kwargs},
            recoverable=True,
        )


class GuardrailsValidationError(LCAgentException):
    """Guardrails 验证失败"""

    DEFAULT_USER_MESSAGE = "内容验证未通过，请调整后重试"

    def __init__(self, message: str, validation_type: str = "", **kwargs):
        super().__init__(
            message,
            error_code="GUARDRAILS_VALIDATION_ERROR",
            details={"validation_type": validation_type, **kwargs},
            recoverable=False,
        )


class RateLimitExceededError(LCAgentException):
    """速率限制超限"""

    DEFAULT_USER_MESSAGE = "请求频率超限，请稍后再试"

    def __init__(self, message: str = "API 速率限制已超限，请稍后重试", **kwargs):
        super().__init__(
            message,
            error_code="RATE_LIMIT_EXCEEDED",
            recoverable=True,
            **kwargs,
        )


class CheckpointError(LCAgentException):
    """状态持久化失败"""

    DEFAULT_USER_MESSAGE = "状态保存失败，请稍后重试"

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
    返回的异常对象包含面向用户的友好消息。

    Args:
        exc: 原始异常

    Returns:
        分类后的 LCAgentException，包含 user_message 属性
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
    except ImportError as e:
        logger.debug("langgraph 未安装，跳过 GraphRecursionError 分类: %s", e)

    try:
        from openai import (
            APIConnectionError as OpenAIConnectionError,
        )
        from openai import (
            APITimeoutError as OpenAITimeoutError,
        )
        from openai import (
            AuthenticationError as OpenAIAuthError,
        )
        from openai import (
            BadRequestError as OpenAIBadRequestError,
        )
        from openai import (
            RateLimitError as OpenAIRateLimitError,
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
                user_message="认证失败，请检查配置后重试",
            )
        if isinstance(exc, OpenAIConnectionError):
            return ModelCallError(
                message=f"OpenAI 连接失败: {exc}",
                details={"original_type": type(exc).__name__, "connection_error": True},
                user_message="网络连接失败，请检查网络后重试",
            )
        if isinstance(exc, OpenAITimeoutError):
            return ModelCallError(
                message=f"OpenAI 请求超时: {exc}",
                details={"original_type": type(exc).__name__, "timeout": True},
                user_message="请求超时，请稍后重试",
            )
        if isinstance(exc, OpenAIBadRequestError):
            return ModelCallError(
                message=f"OpenAI 请求参数错误: {exc}",
                details={"original_type": type(exc).__name__, "bad_request": True},
            )
    except ImportError as e:
        logger.debug("openai 未安装，跳过 OpenAI 异常分类: %s", e)

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
            user_message="请求超时，请稍后重试",
        )

    if isinstance(exc, (ConnectionError, OSError)):
        return ModelCallError(
            message=f"网络连接失败: {exc}",
            details={"original_type": type(exc).__name__, "connection_error": True},
            user_message="网络连接失败，请检查网络后重试",
        )

    if "timeout" in error_msg or "timed out" in error_msg:
        return ModelCallError(
            message=f"模型调用超时: {exc}",
            details={"original_type": type(exc).__name__, "timeout": True},
            user_message="请求超时，请稍后重试",
        )

    if "auth" in error_msg or "api_key" in error_msg or "unauthorized" in error_msg:
        return ModelCallError(
            message=f"认证失败: {exc}",
            details={"original_type": type(exc).__name__, "auth_error": True},
            recoverable=False,
            user_message="认证失败，请检查配置后重试",
        )

    return LCAgentException(
        message=f"未预期错误: {exc}",
        error_code="UNEXPECTED_ERROR",
        details={"original_type": type(exc).__name__},
        recoverable=False,
        user_message="服务暂时不可用，请稍后重试",
    )
