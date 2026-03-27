"""
langchain_core 模块

提供 LangChain 核心功能封装，包括：
- 配置管理 (config)
- 模型封装 (models)
- 提示词模板 (prompts)
- 工具函数 (tools)
- 代理框架 (agents)
- 深度研究 (deep_research)
"""

from .config import settings, get_logger, Settings
from .models import get_chat_model, get_streaming_model, get_structured_output_model, get_model_by_preset
from .prompts import (
    SYSTEM_PROMPTS,
    WRITER_GUIDELINES,
    get_system_prompt,
    create_custom_prompt,
    get_prompt_with_tools,
)
from .tools import (
    get_current_time,
    get_current_date,
    calculator,
    web_search,
    web_search_simple,
    create_tavily_search_tool,
    get_weather,
    get_weather_forecast,
    get_daily_weather,
    get_tools_for_request,
)
from .agents import (
    BaseAgent,
    create_base_agent,
)
from .deep_research import (
    DeepResearchAgent,
    create_deep_research_agent,
    should_use_deep_research,
    ResearchState,
)

__all__ = [
    "settings",
    "get_logger",
    "Settings",
    "get_chat_model",
    "get_streaming_model",
    "get_structured_output_model",
    "get_model_by_preset",
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
    "get_weather",
    "get_weather_forecast",
    "get_daily_weather",
    "get_tools_for_request",
    "BaseAgent",
    "create_base_agent",
    "DeepResearchAgent",
    "create_deep_research_agent",
    "should_use_deep_research",
    "ResearchState",
]
