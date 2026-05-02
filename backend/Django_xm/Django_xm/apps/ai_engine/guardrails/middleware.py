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
    """Guardrails 中间件类"""
    
    def __init__(
        self,
        input_validator: Optional[InputValidator] = None,
        output_validator: Optional[OutputValidator] = None,
        on_input_error: Optional[Callable] = None,
        on_output_error: Optional[Callable] = None,
        raise_on_error: bool = True,
    ):
        """
        初始化 Guardrails 中间件
        
        Args:
            input_validator: 输入验证器
            output_validator: 输出验证器
            on_input_error: 输入错误回调函数
            on_output_error: 输出错误回调函数
            raise_on_error: 是否在错误时抛出异常
        """
        self.input_validator = input_validator or InputValidator()
        self.output_validator = output_validator or OutputValidator()
        self.on_input_error = on_input_error
        self.on_output_error = on_output_error
        self.raise_on_error = raise_on_error
    
    def validate_input(self, input_data: Any) -> Any:
        """
        验证输入数据
        
        Args:
            input_data: 输入数据（可能是字符串或消息）
            
        Returns:
            验证后的数据
        """
        # 提取文本内容
        text = self._extract_text(input_data)
        
        # 验证
        result = self.input_validator.validate(text)
        
        if not result.is_valid:
            logger.warning(f"输入验证失败: {result.errors}")
            
            if self.on_input_error:
                return self.on_input_error(input_data, result)
            
            if self.raise_on_error:
                raise ValueError(f"输入验证失败: {', '.join(result.errors)}")
            
            # 返回错误消息
            return self._create_error_message("输入验证失败", result.errors)
        
        # 记录警告
        if result.warnings:
            logger.info(f"输入验证警告: {result.warnings}")
        
        # 返回过滤后的内容
        return self._replace_text(input_data, result.filtered_input)
    
    def validate_output(self, output_data: Any, context: Optional[Dict] = None) -> Any:
        """
        验证输出数据
        
        Args:
            output_data: 输出数据
            context: 上下文信息（如 sources）
            
        Returns:
            验证后的数据
        """
        # 提取文本内容
        text = self._extract_text(output_data)
        
        # 提取 sources（如果有）
        sources = None
        if context and "sources" in context:
            sources = context["sources"]
        
        # 验证
        result = self.output_validator.validate(text, sources=sources, context=context)
        
        if not result.is_valid:
            logger.warning(f"输出验证失败: {result.errors}")
            
            if self.on_output_error:
                return self.on_output_error(output_data, result)
            
            if self.raise_on_error:
                raise ValueError(f"输出验证失败: {', '.join(result.errors)}")
            
            # 返回错误消息
            return self._create_error_message("输出验证失败", result.errors)
        
        # 记录警告
        if result.warnings:
            logger.info(f"输出验证警告: {result.warnings}")
        
        # 返回过滤后的内容
        return self._replace_text(output_data, result.filtered_output)
    
    def _extract_text(self, data: Any) -> str:
        """从各种数据类型中提取文本"""
        if isinstance(data, str):
            return data
        elif isinstance(data, BaseMessage):
            return data.content
        elif isinstance(data, dict):
            # 尝试从字典中提取文本
            if "content" in data:
                return data["content"]
            elif "text" in data:
                return data["text"]
            elif "answer" in data:
                return data["answer"]
            else:
                return str(data)
        else:
            return str(data)
    
    def _replace_text(self, data: Any, new_text: str) -> Any:
        """用新文本替换数据中的文本"""
        if isinstance(data, str):
            return new_text
        elif isinstance(data, HumanMessage):
            return HumanMessage(content=new_text)
        elif isinstance(data, AIMessage):
            return AIMessage(content=new_text)
        elif isinstance(data, BaseMessage):
            # 保持原类型
            return data.__class__(content=new_text)
        elif isinstance(data, dict):
            # 更新字典
            result = data.copy()
            if "content" in result:
                result["content"] = new_text
            elif "text" in result:
                result["text"] = new_text
            elif "answer" in result:
                result["answer"] = new_text
            return result
        else:
            return new_text
    
    def _create_error_message(self, title: str, errors: list) -> str:
        """创建错误消息"""
        error_text = f"{title}:\n"
        for error in errors:
            error_text += f"- {error}\n"
        return error_text


def create_guardrails_runnable(
    runnable: RunnableSerializable,
    input_validator: Optional[InputValidator] = None,
    output_validator: Optional[OutputValidator] = None,
    validate_input: bool = True,
    validate_output: bool = True,
    raise_on_error: bool = True,
) -> RunnableSerializable:
    """
    为 Runnable 添加 Guardrails
    
    Args:
        runnable: 原始 Runnable
        input_validator: 输入验证器
        output_validator: 输出验证器
        validate_input: 是否验证输入
        validate_output: 是否验证输出
        raise_on_error: 是否在错误时抛出异常
        
    Returns:
        带 Guardrails 的 Runnable
    """
    middleware = GuardrailsMiddleware(
        input_validator=input_validator,
        output_validator=output_validator,
        raise_on_error=raise_on_error,
    )
    
    # 构建处理链
    components = []
    
    # 添加输入验证
    if validate_input:
        components.append(
            RunnableLambda(middleware.validate_input).with_config(
                {"run_name": "input_validation"}
            )
        )
    
    # 添加核心 Runnable
    components.append(runnable)
    
    # 添加输出验证
    if validate_output:
        components.append(
            RunnableLambda(middleware.validate_output).with_config(
                {"run_name": "output_validation"}
            )
        )
    
    # 串联所有组件
    if len(components) == 1:
        return components[0]
    
    result = components[0]
    for component in components[1:]:
        result = result | component
    
    return result


def create_input_filter(
    content_filter: Optional[ContentFilter] = None,
    strict_mode: bool = False,
) -> RunnableLambda:
    """
    创建输入过滤器 Runnable
    
    Args:
        content_filter: 内容过滤器
        strict_mode: 严格模式
        
    Returns:
        输入过滤器 Runnable
    """
    validator = InputValidator(
        content_filter=content_filter,
        strict_mode=strict_mode,
    )
    
    def filter_func(input_data: Any) -> Any:
        text = input_data if isinstance(input_data, str) else str(input_data)
        result = validator.validate(text)
        
        if not result.is_valid:
            raise ValueError(f"输入验证失败: {', '.join(result.errors)}")
        
        return result.filtered_input
    
    return RunnableLambda(filter_func).with_config({"run_name": "input_filter"})


def create_output_filter(
    content_filter: Optional[ContentFilter] = None,
    require_sources: bool = False,
    strict_mode: bool = False,
) -> RunnableLambda:
    """
    创建输出过滤器 Runnable
    
    Args:
        content_filter: 内容过滤器
        require_sources: 是否要求来源
        strict_mode: 严格模式
        
    Returns:
        输出过滤器 Runnable
    """
    validator = OutputValidator(
        content_filter=content_filter,
        require_sources=require_sources,
        strict_mode=strict_mode,
    )
    
    def filter_func(output_data: Any) -> Any:
        text = output_data if isinstance(output_data, str) else str(output_data)
        result = validator.validate(text)
        
        if not result.is_valid:
            raise ValueError(f"输出验证失败: {', '.join(result.errors)}")
        
        return result.filtered_output
    
    return RunnableLambda(filter_func).with_config({"run_name": "output_filter"})


# 便捷函数：创建带 Guardrails 的 Agent
def add_guardrails_to_agent(
    agent,
    enable_input_validation: bool = True,
    enable_output_validation: bool = True,
    strict_mode: bool = False,
):
    """
    为 Agent 添加 Guardrails（装饰器模式）
    
    Args:
        agent: 原始 Agent
        enable_input_validation: 启用输入验证
        enable_output_validation: 启用输出验证
        strict_mode: 严格模式
        
    Returns:
        带 Guardrails 的 Agent
    """
    return create_guardrails_runnable(
        agent,
        input_validator=InputValidator(strict_mode=strict_mode) if enable_input_validation else None,
        output_validator=OutputValidator(strict_mode=strict_mode) if enable_output_validation else None,
        validate_input=enable_input_validation,
        validate_output=enable_output_validation,
        raise_on_error=True,
    )
