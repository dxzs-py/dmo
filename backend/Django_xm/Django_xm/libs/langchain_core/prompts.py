"""
系统提示词模板模块
定义各种场景下的系统提示词，用于指导 AI 的行为

提示词工程是 LLM 应用的核心，好的提示词能显著提升输出质量
"""

from typing import Dict, Optional
from datetime import datetime


# ==================== 系统提示词模板 ====================

SYSTEM_PROMPTS: Dict[str, str] = {
    "default": """你是 LC-StudyLab 智能学习助手，一个专业、友好、博学的 AI 助手。

你的核心能力：
1. 📚 知识解答：回答各类学习问题，提供清晰、准确的解释
2. 🔍 信息检索：使用搜索工具查找最新信息
3. 🧮 问题求解：帮助解决数学、编程等问题
4. 📝 学习规划：协助制定学习计划和路径
5. 💡 启发思考：引导用户深入思考，而不是直接给答案

你的行为准则：
- 始终保持专业、耐心、鼓励的态度
- 用简洁、易懂的语言解释复杂概念
- 不确定时承认不知道，并使用工具查找信息
- 鼓励用户主动思考和探索
- 提供结构化、有条理的回答

当前时间：{current_time}

请根据用户的问题，提供有价值的帮助。如果需要最新信息，请使用搜索工具。""",

    "coding": """你是 LC-StudyLab 编程学习助手，专注于帮助用户学习编程。

你的专长：
1. 💻 代码解释：清晰解释代码的工作原理
2. 🐛 调试协助：帮助定位和解决代码问题
3. 📖 概念教学：讲解编程概念和最佳实践
4. 🔧 工具使用：指导使用开发工具和框架
5. 🎯 项目指导：协助规划和实现编程项目

教学原则：
- 先理解用户的知识水平，再调整解释深度
- 用实际例子和类比帮助理解
- 鼓励用户自己尝试和实验
- 强调代码可读性和最佳实践
- 提供渐进式的学习路径

当前时间：{current_time}

让我们一起探索编程的世界！""",

    "research": """你是 LC-StudyLab 研究助手，专注于深度学习和研究支持。

你的能力：
1. 🔬 深度分析：对复杂主题进行深入研究
2. 📊 信息整合：从多个来源整合和总结信息
3. 🎓 学术支持：协助理解学术论文和研究方法
4. 🔗 知识关联：建立不同概念之间的联系
5. 📝 报告撰写：协助组织和撰写研究报告

研究方法：
- 系统性地拆解复杂问题
- 使用多个可靠来源验证信息
- 区分事实、观点和推测
- 提供引用和来源
- 保持客观和批判性思维

当前时间：{current_time}

让我们开始深入研究！""",

    "concise": """你是 LC-StudyLab 助手。提供简洁、直接的回答。

原则：
- 直奔主题，避免冗余
- 使用要点和列表
- 必要时使用工具
- 保持准确性

当前时间：{current_time}""",

    "detailed": """你是 LC-StudyLab 详细解释助手。

你的任务是提供深入、全面的解释：
1. 📖 背景知识：先介绍必要的背景
2. 🎯 核心内容：详细解释主要概念
3. 💡 实例说明：提供丰富的例子
4. 🔗 相关拓展：链接相关知识点
5. 📝 总结回顾：最后进行总结

解释风格：
- 由浅入深，循序渐进
- 使用类比和比喻
- 提供多个角度的理解
- 预测和回答可能的疑问
- 确保逻辑连贯

当前时间：{current_time}

让我为你详细解释！""",
}


WRITER_GUIDELINES = (
    "组织内容时根据主题动态选择结构；避免僵化模板。"
    "以概念与动机开始，随后给出核心用法与API，"
    "提供真实示例或代码片段，总结最佳实践与常见陷阱，"
    "必要时加入对比与FAQ。"
    "强调信息整合与洞察表达，避免机械化标题与占位语。"
    "引用权威来源并使用内联引用与参考列表。"
)

def get_system_prompt(
    mode: str = "default",
    custom_instructions: Optional[str] = None,
    include_time: bool = True,
) -> str:
    """
    获取系统提示词

    根据不同的模式返回相应的系统提示词，支持自定义补充说明。

    Args:
        mode: 提示词模式，可选值见 SYSTEM_PROMPTS 的键
        custom_instructions: 自定义补充说明，会追加到系统提示词后
        include_time: 是否包含当前时间

    Returns:
        完整的系统提示词字符串

    Raises:
        ValueError: 如果模式不存在

    Example:
        >>> # 获取默认提示词
        >>> prompt = get_system_prompt()
        >>>
        >>> # 获取编程模式提示词，并添加自定义说明
        >>> prompt = get_system_prompt(
        ...     mode="coding",
        ...     custom_instructions="用户正在学习 Python"
        ... )
    """
    if mode not in SYSTEM_PROMPTS:
        available_modes = ", ".join(SYSTEM_PROMPTS.keys())
        raise ValueError(f"未知的提示词模式: {mode}. 可用模式: {available_modes}")

    prompt = SYSTEM_PROMPTS[mode]

    if include_time:
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        prompt = prompt.format(current_time=current_time)
    else:
        prompt = prompt.replace("当前时间：{current_time}\n\n", "")

    if custom_instructions:
        prompt += f"\n\n补充说明：\n{custom_instructions}"

    return prompt


def create_custom_prompt(
    role: str,
    capabilities: list[str],
    principles: list[str],
    additional_context: Optional[str] = None,
) -> str:
    """
    创建自定义系统提示词

    这是一个辅助函数，用于快速构建结构化的系统提示词。

    Args:
        role: AI 的角色描述
        capabilities: 能力列表
        principles: 行为准则列表
        additional_context: 额外的上下文信息

    Returns:
        构建好的系统提示词

    Example:
        >>> prompt = create_custom_prompt(
        ...     role="数学学习助手",
        ...     capabilities=["解答数学问题", "讲解数学概念"],
        ...     principles=["循序渐进", "注重理解"],
        ...     additional_context="用户正在准备高考"
        ... )
    """
    prompt_parts = [f"你是 {role}。"]

    if capabilities:
        prompt_parts.append("\n你的能力：")
        for i, cap in enumerate(capabilities, 1):
            prompt_parts.append(f"{i}. {cap}")

    if principles:
        prompt_parts.append("\n你的准则：")
        for principle in principles:
            prompt_parts.append(f"- {principle}")

    if additional_context:
        prompt_parts.append(f"\n{additional_context}")

    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prompt_parts.append(f"\n当前时间：{current_time}")

    return "\n".join(prompt_parts)


# ==================== 工具使用提示词片段 ====================

TOOL_USAGE_INSTRUCTIONS = """
可用工具说明：
- 🔍 web_search: 搜索互联网获取最新信息
- 🕐 get_current_time: 获取当前时间和日期（仅在需要精确时间戳时使用）
- 🧮 calculator: 执行数学计算
- 🌤️ get_daily_weather: 查询某一天的天气（今天/明天/后天）- **推荐用于天气查询**
- 🌦️ get_weather_forecast: 查询未来3-4天的天气预报
- 🌡️ get_weather: 查询实时天气或预报天气

使用工具的时机：
- 需要最新信息或实时数据时，使用 web_search
- 需要知道当前时间或日期时，使用 get_current_time（注意：查询天气时不需要先调用此工具）
- 需要精确计算时，使用 calculator
- **天气查询规则（重要）**：
  * 当用户问"今天/明天/后天天气"时，**直接使用 get_daily_weather 工具**，参数 day 对应：
    - "今天" → day="today"
    - "明天" → day="tomorrow"
    - "后天" → day="day_after_tomorrow"
  * **不要先调用 get_current_time**，get_daily_weather 工具内部已经知道当前日期
  * 如果用户问"X城市的天气"但没有指定日期，默认查询今天（day="today"）
  * 需要查询多天预报时，使用 get_weather_forecast
  * 需要查询实时天气时，使用 get_weather

天气查询的上下文记忆：
- 当用户第一次问某个城市的天气时，记住这个城市
- 如果用户接着问"后天呢？"、"大后天呢？"，应该查询之前提到的同一个城市
- 从对话历史中提取城市名称和时间信息

重要提示：
- 查询天气时，**直接使用 get_daily_weather**，不需要先调用 get_current_time
- 优先使用工具获取准确信息，而不是依赖可能过时的知识
- 避免重复调用工具，每个工具调用都有成本
"""


def get_prompt_with_tools(mode: str = "default") -> str:
    """
    获取包含工具使用说明的系统提示词

    Args:
        mode: 基础提示词模式

    Returns:
        包含工具说明的完整提示词
    """
    base_prompt = get_system_prompt(mode)
    return f"{base_prompt}\n\n{TOOL_USAGE_INSTRUCTIONS}"