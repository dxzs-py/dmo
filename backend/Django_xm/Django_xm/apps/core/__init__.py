"""
core模块

提供 LangChain 核心功能：
- 配置管理 (config)
- 模型封装 (llm_models)
- 提示词模板 (prompts)
- 工具函数 (tools)
- 输入输出验证 (guardrails)
"""

from .config import settings, get_logger, Settings
from .llm_models import get_chat_model, get_streaming_model, get_structured_output_model, get_model_by_preset, get_model_string
from .prompts import SYSTEM_PROMPTS, WRITER_GUIDELINES, get_system_prompt, create_custom_prompt, get_prompt_with_tools
from .tools import (
    get_current_time, get_current_date, calculator,
    web_search, create_tavily_search_tool, web_search_simple,
    weather_query, get_daily_weather,
    BASIC_TOOLS, ADVANCED_TOOLS, ALL_TOOLS,
    get_tools_for_request,
)
from .guardrails import (
    ContentFilter,
    ContentSafetyLevel,
    FilterResult,
    InputValidator,
    InputValidationResult,
    OutputValidator,
    OutputValidationResult,
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
    GuardrailsMiddleware,
    create_guardrails_runnable,
)

__all__ = [
    "settings",
    "get_logger",
    "Settings",
    "get_chat_model",
    "get_streaming_model",
    "get_structured_output_model",
    "get_model_by_preset",
    "get_model_string",
    "SYSTEM_PROMPTS",
    "WRITER_GUIDELINES",
    "get_system_prompt",
    "create_custom_prompt",
    "get_prompt_with_tools",
    "get_current_time",
    "get_current_date",
    "calculator",
    "web_search",
    "web_search_simple",
    "create_tavily_search_tool",
    "weather_query",
    "get_daily_weather",
    "BASIC_TOOLS",
    "ADVANCED_TOOLS",
    "ALL_TOOLS",
    "get_tools_for_request",
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
    "create_guardrails_runnable",
]