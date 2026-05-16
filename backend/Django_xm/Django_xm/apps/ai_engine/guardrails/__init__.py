"""
Guardrails 模块 - 提供输入输出安全检查和结构化输出功能
"""

from typing import Optional, Sequence

from langchain.agents.middleware import AgentMiddleware

from .content_filters import ContentFilter, ContentSafetyLevel, FilterResult
from .input_validators import InputValidator, InputValidationResult
from .output_validators import OutputValidator, OutputValidationResult
from .schemas import (
    RAGResponse,
    StudyPlan,
    StudyPlanStep,
    DifficultyLevel,
    ResearchReport,
    ResearchSection,
    Quiz,
    QuizQuestion,
    QuizAnswer,
    QuestionType,
)
from .middleware import (
    GuardrailsMiddleware,
    PIIMiddleware,
    HumanInTheLoopMiddleware,
    SkillsMiddleware,
    RateLimitMiddleware,
    PermissionMiddleware,
    create_guardrails_runnable,
    create_guardrails_middleware,
    create_pii_middleware,
    create_human_in_the_loop_middleware,
    create_skills_middleware,
    create_rate_limit_middleware,
    create_permission_middleware,
    build_middleware_stack,
)


def create_standard_guardrails(
    enable_input_validation: bool = True,
    enable_output_validation: bool = True,
    strict_mode: bool = False,
    require_sources: bool = False,
    validate_tool_calls: bool = True,
    raise_on_error: bool = True,
    extra_middleware: Optional[Sequence[AgentMiddleware]] = None,
) -> list:
    """
    创建标准 Guardrails 中间件栈

    统一 SafeDeepResearchAgent 和 SafeRAGAgent 的 guardrails 初始化逻辑，
    消除重复代码，确保一致的 ContentFilter 配置。

    Args:
        enable_input_validation: 是否启用输入验证
        enable_output_validation: 是否启用输出验证
        strict_mode: 严格模式
        require_sources: 输出验证是否要求来源引用
        validate_tool_calls: 是否验证工具调用
        raise_on_error: 验证失败时是否抛出异常
        extra_middleware: 额外的中间件列表

    Returns:
        中间件列表
    """
    content_filter = ContentFilter(
        enable_pii_detection=True,
        enable_content_safety=True,
        enable_injection_detection=True,
        mask_pii=True,
    )

    input_validator = InputValidator(
        content_filter=content_filter,
        strict_mode=strict_mode,
    ) if enable_input_validation else None

    output_validator = OutputValidator(
        content_filter=content_filter,
        require_sources=require_sources,
        strict_mode=strict_mode,
    ) if enable_output_validation else None

    guardrails_middleware = create_guardrails_middleware(
        input_validator=input_validator,
        output_validator=output_validator,
        strict_mode=strict_mode,
        validate_tool_calls=validate_tool_calls,
        raise_on_error=raise_on_error,
    )

    middleware_list = [guardrails_middleware]
    if extra_middleware:
        middleware_list.extend(extra_middleware)

    return middleware_list

__all__ = [
    "ContentFilter",
    "ContentSafetyLevel",
    "FilterResult",
    "InputValidator",
    "InputValidationResult",
    "OutputValidator",
    "OutputValidationResult",
    "RAGResponse",
    "StudyPlan",
    "StudyPlanStep",
    "DifficultyLevel",
    "ResearchReport",
    "ResearchSection",
    "Quiz",
    "QuizQuestion",
    "QuizAnswer",
    "QuestionType",
    "GuardrailsMiddleware",
    "PIIMiddleware",
    "HumanInTheLoopMiddleware",
    "SkillsMiddleware",
    "RateLimitMiddleware",
    "PermissionMiddleware",
    "create_guardrails_runnable",
    "create_guardrails_middleware",
    "create_pii_middleware",
    "create_human_in_the_loop_middleware",
    "create_skills_middleware",
    "create_rate_limit_middleware",
    "create_permission_middleware",
    "build_middleware_stack",
    "create_standard_guardrails",
]
