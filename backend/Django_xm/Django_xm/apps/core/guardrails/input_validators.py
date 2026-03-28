"""
输入验证器 - 验证用户输入的合法性和安全性
"""

from typing import Optional, Dict, Any
from dataclasses import dataclass
from .content_filters import ContentFilter, ContentSafetyLevel


@dataclass
class InputValidationResult:
    """输入验证结果"""
    is_valid: bool
    filtered_input: str
    errors: list[str]
    warnings: list[str]
    metadata: Dict[str, Any]


class InputValidator:
    """输入验证器"""
    
    def __init__(
        self,
        content_filter: Optional[ContentFilter] = None,
        min_length: int = 1,
        max_length: int = 50000,
        allow_empty: bool = False,
        strict_mode: bool = False,
    ):
        """
        初始化输入验证器
        
        Args:
            content_filter: 内容过滤器实例
            min_length: 最小长度
            max_length: 最大长度
            allow_empty: 是否允许空输入
            strict_mode: 严格模式（任何警告都视为错误）
        """
        self.content_filter = content_filter or ContentFilter()
        self.min_length = min_length
        self.max_length = max_length
        self.allow_empty = allow_empty
        self.strict_mode = strict_mode
    
    def validate(self, user_input: str) -> InputValidationResult:
        """
        验证用户输入
        
        Args:
            user_input: 用户输入文本
            
        Returns:
            InputValidationResult: 验证结果
        """
        errors = []
        warnings = []
        metadata = {}
        
        # 1. 检查空输入
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
        
        # 2. 检查长度
        input_length = len(user_input)
        metadata["input_length"] = input_length
        
        if input_length < self.min_length:
            errors.append(f"输入长度不足（最少 {self.min_length} 字符）")
        
        if input_length > self.max_length:
            errors.append(f"输入长度超限（最多 {self.max_length} 字符）")
        
        # 3. 内容安全检查
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
        
        # 4. 返回结果
        is_valid = len(errors) == 0
        
        return InputValidationResult(
            is_valid=is_valid,
            filtered_input=filter_result.filtered_content if is_valid else user_input,
            errors=errors,
            warnings=warnings,
            metadata=metadata,
        )
    
    def validate_or_raise(self, user_input: str) -> str:
        """
        验证输入，如果失败则抛出异常
        
        Args:
            user_input: 用户输入
            
        Returns:
            str: 过滤后的输入
            
        Raises:
            ValueError: 验证失败时抛出
        """
        result = self.validate(user_input)
        
        if not result.is_valid:
            error_msg = "输入验证失败:\n" + "\n".join(f"- {err}" for err in result.errors)
            raise ValueError(error_msg)
        
        return result.filtered_input


# 创建默认验证器实例
default_validator = InputValidator()
strict_validator = InputValidator(strict_mode=True)
