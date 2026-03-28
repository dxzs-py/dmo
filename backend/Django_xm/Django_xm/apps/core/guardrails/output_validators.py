"""
输出验证器 - 验证模型输出的安全性和格式正确性
"""

from typing import Optional, Dict, Any, List
from dataclasses import dataclass

from .content_filters import ContentFilter, ContentSafetyLevel


@dataclass
class OutputValidationResult:
    is_valid: bool
    filtered_output: str
    errors: List[str]
    warnings: List[str]
    metadata: Dict[str, Any]


class OutputValidator:
    def __init__(
        self,
        content_filter: Optional[ContentFilter] = None,
        require_sources: bool = False,
        require_examples: bool = False,
        min_length: int = 1,
        max_length: int = 100000,
        check_factuality: bool = False,
        strict_mode: bool = False,
    ):
        self.content_filter = content_filter or ContentFilter()
        self.require_sources = require_sources
        self.require_examples = require_examples
        self.min_length = min_length
        self.max_length = max_length
        self.check_factuality = check_factuality
        self.strict_mode = strict_mode

    def validate(
        self,
        output: str,
        sources: Optional[List[str]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> OutputValidationResult:
        errors = []
        warnings = []
        metadata = {}
        context = context or {}

        if not output or not output.strip():
            errors.append("输出不能为空")
            return OutputValidationResult(
                is_valid=False,
                filtered_output="",
                errors=errors,
                warnings=warnings,
                metadata=metadata,
            )

        output_length = len(output)
        metadata["output_length"] = output_length

        if output_length < self.min_length:
            warnings.append(f"输出长度过短（少于 {self.min_length} 字符）")

        if output_length > self.max_length:
            errors.append(f"输出长度超限（超过 {self.max_length} 字符）")

        filter_result = self.content_filter.filter_output(output)
        metadata["safety_level"] = filter_result.safety_level.value
        metadata["filter_details"] = filter_result.details

        if not filter_result.is_safe:
            errors.extend(filter_result.issues)
        elif filter_result.safety_level == ContentSafetyLevel.WARNING:
            if self.strict_mode:
                errors.extend(filter_result.issues)
            else:
                warnings.extend(filter_result.issues)

        if self.require_sources and not sources:
            warnings.append("未提供引用来源")

        if self.require_examples and len(output) < 100:
            warnings.append("输出可能缺少示例")

        is_valid = len(errors) == 0

        return OutputValidationResult(
            is_valid=is_valid,
            filtered_output=filter_result.filtered_content,
            errors=errors,
            warnings=warnings,
            metadata=metadata,
        )