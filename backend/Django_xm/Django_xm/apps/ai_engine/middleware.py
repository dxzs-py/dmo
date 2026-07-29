import logging

from django.http import JsonResponse

logger = logging.getLogger(__name__)


class AIExceptionMiddleware:
    """
    AI 服务全局异常处理中间件

    捕获 LangChain/OpenAI/Anthropic 等AI服务异常，
    返回统一格式的 JSON 响应，避免泄露内部错误信息。
    """

    EXCEPTION_MAP = None

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    @classmethod
    def _build_exception_map(cls):
        if cls.EXCEPTION_MAP is not None:
            return cls.EXCEPTION_MAP

        mapping = {}

        try:
            from openai import (
                APIConnectionError,
                APIError,
                APITimeoutError,
                AuthenticationError,
                BadRequestError,
                RateLimitError,
            )

            mapping[RateLimitError] = (429, "AI 服务请求频率超限，请稍后重试", "AI_RATE_LIMIT")
            mapping[AuthenticationError] = (401, "AI 服务认证失败", "AI_AUTH_ERROR")
            mapping[BadRequestError] = (400, "AI 请求参数错误", "AI_BAD_REQUEST")
            mapping[APITimeoutError] = (504, "AI 服务响应超时", "AI_TIMEOUT")
            mapping[APIConnectionError] = (502, "AI 服务连接失败", "AI_CONNECTION_ERROR")
            mapping[APIError] = (502, "AI 服务暂时不可用", "AI_SERVICE_ERROR")
        except ImportError:
            pass

        try:
            from langchain_core.exceptions import OutputParserException

            mapping[OutputParserException] = (422, "AI 输出解析失败", "AI_OUTPUT_PARSE_ERROR")
        except ImportError:
            pass

        try:
            from langgraph.errors import GraphRecursionError

            mapping[GraphRecursionError] = (422, "Agent 执行超出最大迭代次数", "AI_RECURSION_LIMIT")
        except ImportError:
            pass

        try:
            from httpx import ConnectError, TimeoutException

            mapping[TimeoutException] = (504, "AI 服务响应超时", "AI_TIMEOUT")
            mapping[ConnectError] = (502, "AI 服务连接失败", "AI_CONNECTION_ERROR")
        except ImportError:
            pass

        cls.EXCEPTION_MAP = mapping
        return mapping

    def process_exception(self, request, exception):
        if not request.path.startswith("/api/"):
            return None

        exception_map = self._build_exception_map()

        for exc_class, (status_code, message, error_code) in exception_map.items():
            if isinstance(exception, exc_class):
                logger.error(
                    f"[AI Exception] {error_code}: {type(exception).__name__} - {exception}",
                    exc_info=True,
                )
                return JsonResponse(
                    {
                        "code": status_code,
                        "message": message,
                        "error": error_code,
                    },
                    status=status_code,
                )

        error_name = type(exception).__name__
        ai_related = any(
            keyword in error_name.lower()
            for keyword in ["langchain", "langgraph", "openai", "anthropic", "agent", "llm", "embedding"]
        )

        if ai_related:
            logger.error(
                f"[AI Exception] Unhandled: {error_name} - {exception}",
                exc_info=True,
            )
            return JsonResponse(
                {
                    "code": 500,
                    "message": "AI 服务内部错误，请稍后重试",
                    "error": "AI_INTERNAL_ERROR",
                },
                status=500,
            )

        return None
