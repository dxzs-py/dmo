"""
输出验证器 - 验证模型输出的安全性和格式正确性
"""

from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from .content_filters import ContentFilter, ContentSafetyLevel


@dataclass
class OutputValidationResult:
    """输出验证结果"""
    is_valid: bool
    filtered_output: str
    errors: List[str]
    warnings: List[str]
    metadata: Dict[str, Any]


class OutputValidator:
    """输出验证器"""
    
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
        """
        初始化输出验证器
        
        Args:
            content_filter: 内容过滤器
            require_sources: 是否要求引用来源（RAG 场景）
            require_examples: 是否要求示例
            min_length: 最小长度
            max_length: 最大长度
            check_factuality: 是否检查事实性（未实现）
            strict_mode: 严格模式
        """
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
        """
        验证模型输出
        
        Args:
            output: 模型输出文本
            sources: 引用来源列表
            context: 额外上下文信息
            
        Returns:
            OutputValidationResult: 验证结果
        """
        errors = []
        warnings = []
        metadata = {}
        context = context or {}
        
        # 1. 检查空输出
        if not output or not output.strip():
            errors.append("输出不能为空")
            return OutputValidationResult(
                is_valid=False,
                filtered_output="",
                errors=errors,
                warnings=warnings,
                metadata=metadata,
            )
        
        # 2. 检查长度
        output_length = len(output)
        metadata["output_length"] = output_length
        
        if output_length < self.min_length:
            warnings.append(f"输出长度过短（少于 {self.min_length} 字符）")
        
        if output_length > self.max_length:
            errors.append(f"输出长度超限（超过 {self.max_length} 字符）")
        
        # 3. 内容安全检查
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
        
        # 4. 检查引用来源（RAG 场景）
        if self.require_sources:
            if not sources or len(sources) == 0:
                errors.append("RAG 回答必须包含引用来源")
            else:
                metadata["sources_count"] = len(sources)
                # 检查输出是否真的使用了来源
                if not self._check_source_usage(output, sources):
                    warnings.append("输出可能未充分使用提供的来源")

        # 5. 示例检查（技术主题场景）
        if self.require_examples:
            if "```" not in output:
                errors.append("缺少示例或代码片段")
        
        # 6. 事实性检查（占位符，未实现）
        if self.check_factuality:
            warnings.append("事实性检查功能尚未实现")
        
        # 7. 返回结果
        is_valid = len(errors) == 0
        
        return OutputValidationResult(
            is_valid=is_valid,
            filtered_output=filter_result.filtered_content if is_valid else output,
            errors=errors,
            warnings=warnings,
            metadata=metadata,
        )
    
    def validate_or_raise(
        self,
        output: str,
        sources: Optional[List[str]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        验证输出，如果失败则抛出异常
        
        Args:
            output: 模型输出
            sources: 引用来源
            context: 上下文
            
        Returns:
            str: 过滤后的输出
            
        Raises:
            ValueError: 验证失败时抛出
        """
        result = self.validate(output, sources, context)
        
        if not result.is_valid:
            error_msg = "输出验证失败:\n" + "\n".join(f"- {err}" for err in result.errors)
            raise ValueError(error_msg)
        
        return result.filtered_output
    
    def _check_source_usage(self, output: str, sources: List[str]) -> bool:
        """
        检查输出是否使用了来源
        
        简单实现：检查来源中的关键词是否出现在输出中
        """
        if not sources:
            return False
        
        # 简单检查：至少有一个来源的部分内容出现在输出中
        for source in sources:
            # 提取来源中的关键词（简单实现）
            source_words = set(source.lower().split())
            output_words = set(output.lower().split())
            
            # 如果有超过 30% 的词重叠，认为使用了该来源
            if len(source_words) > 0:
                overlap = len(source_words & output_words)
                if overlap / len(source_words) > 0.3:
                    return True
        
        return False


# 创建默认验证器实例
default_validator = OutputValidator()
rag_validator = OutputValidator(require_sources=True)
strict_validator = OutputValidator(strict_mode=True)
