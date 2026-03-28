"""
Guardrails 中间件 - 将 Guardrails 集成到 LangChain Runnable 中
"""

from typing import Optional, Any, Dict, Callable
from langchain_core.runnables import RunnableSerializable, RunnableLambda
from langchain_core.messages import BaseMessage, AIMessage, HumanMessage

from .input_validators import InputValidator
from .output_validators import OutputValidator
from .content_filters import ContentFilter

import logging

logger = logging.getLogger(__name__)


class GuardrailsMiddleware:
    def __init__(
        self,
        input_validator: Optional[InputValidator] = None,
        output_validator: Optional[OutputValidator] = None,
        on_input_error: Optional[Callable] = None,
        on_output_error: Optional[Callable] = None,
        raise_on_error: bool = True,
    ):
        self.input_validator = input_validator or InputValidator()
        self.output_validator = output_validator or OutputValidator()
        self.on_input_error = on_input_error
        self.on_output_error = on_output_error
        self.raise_on_error = raise_on_error

    def validate_input(self, input_data: Any) -> Any:
        text = self._extract_text(input_data)

        result = self.input_validator.validate(text)

        if not result.is_valid:
            logger.warning(f"输入验证失败: {result.errors}")

            if self.on_input_error:
                return self.on_input_error(input_data, result)

            if self.raise_on_error:
                raise ValueError(f"输入验证失败: {', '.join(result.errors)}")

            return self._create_error_message("输入验证失败", result.errors)

        if result.warnings:
            logger.info(f"输入验证警告: {result.warnings}")

        return self._replace_text(input_data, result.filtered_input)

    def validate_output(self, output_data: Any, context: Optional[Dict] = None) -> Any:
        text = self._extract_text(output_data)

        sources = None
        if context and "sources" in context:
            sources = context["sources"]

        result = self.output_validator.validate(text, sources=sources, context=context)

        if not result.is_valid:
            logger.warning(f"输出验证失败: {result.errors}")

            if self.on_output_error:
                return self.on_output_error(output_data, result)

            if self.raise_on_error:
                raise ValueError(f"输出验证失败: {', '.join(result.errors)}")

            return self._create_error_message("输出验证失败", result.errors)

        if result.warnings:
            logger.info(f"输出验证警告: {result.warnings}")

        return self._replace_text(output_data, result.filtered_output)

    def _extract_text(self, data: Any) -> str:
        if isinstance(data, str):
            return data
        elif isinstance(data, dict):
            if "text" in data:
                return data["text"]
            elif "content" in data:
                return data["content"]
            elif "query" in data:
                return data["query"]
            return str(data)
        elif hasattr(data, "content"):
            return data.content
        return str(data)

    def _replace_text(self, data: Any, new_text: str) -> Any:
        if isinstance(data, str):
            return new_text
        elif isinstance(data, dict):
            if "text" in data:
                data["text"] = new_text
            elif "content" in data:
                data["content"] = new_text
            elif "query" in data:
                data["query"] = new_text
            return data
        elif hasattr(data, "content"):
            data.content = new_text
            return data
        return new_text

    def _create_error_message(self, error_type: str, errors: list) -> str:
        return f"{error_type}: {', '.join(errors)}"


def create_guardrails_runnable(
    runnable: RunnableSerializable,
    input_validator: Optional[InputValidator] = None,
    output_validator: Optional[OutputValidator] = None,
) -> RunnableSerializable:
    middleware = GuardrailsMiddleware(
        input_validator=input_validator,
        output_validator=output_validator,
        raise_on_error=False,
    )

    def validate_input_fn(input_data: Any) -> Any:
        return middleware.validate_input(input_data)

    def validate_output_fn(output_data: Any) -> Any:
        return middleware.validate_output(output_data)

    input_guardrail = RunnableLambda(validate_input_fn)
    output_guardrail = RunnableLambda(validate_output_fn)

    return input_guardrail | runnable | output_guardrail