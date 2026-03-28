"""
输入验证器 - 验证用户输入的合法性和安全性
"""

from typing import Optional, Dict, Any, List
from dataclasses import dataclass

from .content_filters import ContentFilter, ContentSafetyLevel


@dataclass
class InputValidationResult:
    is_valid: bool
    filtered_input: str
    errors: List[str]
    warnings: List[str]
    metadata: Dict[str, Any]


class InputValidator:
    def __init__(
        self,
        content_filter: Optional[ContentFilter] = None,
        min_length: int = 1,
        max_length: int = 50000,
        allow_empty: bool = False,
        strict_mode: bool = False,
    ):
        self.content_filter = content_filter or ContentFilter()
        self.min_length = min_length
        self.max_length = max_length
        self.allow_empty = allow_empty
        self.strict_mode = strict_mode

    def validate(self, user_input: str) -> InputValidationResult:
        errors = []
        warnings = []
        metadata = {}

        if not user_input or not user_input.strip():
            if not self.allow_empty:
                errors.append("输入不能为空")
                return InputValidationResult(
                    is_valid=False,
                    filtered_input="",
                    errors=errors,
                    warnings=warnings,
                    metadata=metadata,
                )
            else:
                return InputValidationResult(
                    is_valid=True,
                    filtered_input="",
                    errors=[],
                    warnings=["输入为空"],
                    metadata={},
                )

        input_length = len(user_input)
        metadata["input_length"] = input_length

        if input_length < self.min_length:
            errors.append(f"输入长度不足（最少 {self.min_length} 字符）")

        if input_length > self.max_length:
            errors.append(f"输入长度超限（最多 {self.max_length} 字符）")

        filter_result = self.content_filter.filter_input(user_input)
        metadata["safety_level"] = filter_result.safety_level.value
        metadata["filter_details"] = filter_result.details

        if not filter_result.is_safe:
            errors.extend(filter_result.issues)
        elif filter_result.safety_level == ContentSafetyLevel.WARNING:
            if self.strict_mode:
                errors.extend(filter_result.issues)
            else:
                warnings.extend(filter_result.issues)

        is_valid = len(errors) == 0

        return InputValidationResult(
            is_valid=is_valid,
            filtered_input=filter_result.filtered_content,
            errors=errors,
            warnings=warnings,
            metadata=metadata,
        )