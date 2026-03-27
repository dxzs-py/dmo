"""
工具模块
提供各种工具供 Agent 使用，包括时间、计算、网络搜索、天气查询等

在 LangChain 1.0.3 中，使用 @tool 装饰器定义工具
所有工具都遵循 LangChain 的工具接口规范
"""

from datetime import datetime
from typing import Optional, List, Literal
from langchain_core.tools import tool

from .config import settings, get_logger

logger = get_logger(__name__)


@tool
def get_current_time() -> str:
    """
    获取当前时间

    Returns:
        当前时间字符串，格式为 "YYYY-MM-DD HH:MM:SS"
    """
    return f"当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"


@tool
def get_current_date() -> str:
    """
    获取当前日期

    Returns:
        当前日期字符串，格式为 "YYYY-MM-DD"
    """
    return f"当前日期: {datetime.now().strftime('%Y-%m-%d')}"


@tool
def calculator(expression: str) -> str:
    """
    计算器工具，用于执行基本的数学运算

    Args:
        expression: 数学表达式，如 "2+2"、"sqrt(16)"、"3.14 * 2"

    Returns:
        计算结果字符串

    Example:
        >>> calculator.invoke({"expression": "2+2"})
        '计算结果: 4'
    """
    try:
        result = eval(expression)
        return f"计算结果: {result}"
    except Exception as e:
        return f"计算错误: {str(e)}"


def web_search(query: str) -> str:
    """
    在互联网上搜索信息

    使用 Tavily 搜索引擎查找最新、最相关的信息。

    Args:
        query: 搜索查询字符串

    Returns:
        搜索结果摘要
    """
    logger.info(f"🔍 执行网络搜索: {query}")

    try:
        if not settings.tavily_api_key:
            logger.warning("⚠️ Tavily API Key 未设置，无法执行搜索")
            return (
                "抱歉，网络搜索功能暂时不可用（未配置 Tavily API Key）。"
                "请在 .env 文件中设置 TAVILY_API_KEY。"
            )

        search_tool = create_tavily_search_tool()
        results = search_tool.invoke({"query": query})

        if not results:
            return f"未找到关于 '{query}' 的相关信息。"

        formatted_results = [f"找到 {len(results)} 条搜索结果：\n"]

        for i, result in enumerate(results, 1):
            title = result.get("title", "无标题")
            content = result.get("content", "")
            url = result.get("url", "")

            if len(content) > 200:
                content = content[:200] + "..."

            formatted_results.append(f"\n{i}. {title}")
            if content:
                formatted_results.append(f"   内容: {content}")
            if url:
                formatted_results.append(f"   来源: {url}")

        return "\n".join(formatted_results)

    except Exception as e:
        error_msg = f"搜索时发生错误: {str(e)}"
        logger.error(f"❌ {error_msg}")
        return f"抱歉，{error_msg}"


def web_search_simple(query: str) -> str:
    """
    简单的网络搜索（快速模式）

    Args:
        query: 搜索查询字符串

    Returns:
        搜索结果摘要
    """
    logger.info(f"🔍 执行快速搜索: {query}")

    try:
        if not settings.tavily_api_key:
            return "网络搜索功能暂时不可用（未配置 API Key）"

        search_tool = create_tavily_search_tool(
            max_results=3,
            search_depth="basic"
        )

        results = search_tool.invoke({"query": query})

        if not results:
            return f"未找到关于 '{query}' 的相关信息。"

        formatted_results = [f"快速搜索结果（{len(results)} 条）：\n"]

        for i, result in enumerate(results, 1):
            title = result.get("title", "无标题")
            url = result.get("url", "")
            formatted_results.append(f"{i}. {title} - {url}")

        return "\n".join(formatted_results)

    except Exception as e:
        logger.error(f"❌ 快速搜索失败: {e}")
        return f"搜索失败: {str(e)}"


def create_tavily_search_tool(
    max_results: Optional[int] = None,
    search_depth: str = "advanced",
    include_domains: Optional[List[str]] = None,
    exclude_domains: Optional[List[str]] = None,
):
    """
    创建 Tavily 搜索工具实例

    Args:
        max_results: 返回的最大结果数，默认使用配置值
        search_depth: 搜索深度，"basic" 或 "advanced"
        include_domains: 限制搜索的域名列表
        exclude_domains: 排除的域名列表

    Returns:
        配置好的 Tavily 搜索工具实例
    """
    if not settings.tavily_api_key:
        raise ValueError(
            "Tavily API Key 未设置！请在环境变量或 .env 文件中设置 TAVILY_API_KEY"
        )

    max_results = max_results or settings.tavily_max_results

    try:
        from langchain_tavily import TavilySearchResults as TavilySearch
    except ImportError:
        from langchain_community.tools.tavily_search import TavilySearchResults as TavilySearch

    tool_kwargs = {
        "max_results": max_results,
        "api_key": settings.tavily_api_key,
        "search_depth": search_depth,
    }

    if include_domains is not None:
        tool_kwargs["include_domains"] = include_domains
    if exclude_domains is not None:
        tool_kwargs["exclude_domains"] = exclude_domains

    return TavilySearch(**tool_kwargs)


def get_weather(city: str) -> str:
    """
    查询指定城市的天气信息（通用）

    Args:
        city: 城市名称

    Returns:
        天气信息字符串
    """
    return _get_weather_impl(city, extensions="base")


def get_weather_forecast(city: str) -> str:
    """
    查询指定城市未来3天的天气预报

    Args:
        city: 城市名称

    Returns:
        格式化的天气预报信息
    """
    return _get_weather_impl(city, extensions="all")


def get_daily_weather(
    city: str,
    day: Literal["today", "tomorrow", "day_after_tomorrow"] = "tomorrow"
) -> str:
    """
    查询指定城市某一天的天气预报（推荐使用）

    **重要：这个工具内部已经知道当前日期，不需要先调用 get_current_date 或 get_current_time！**

    Args:
        city: 城市名称
        day: 查询哪一天的天气
             - "today": 今天
             - "tomorrow": 明天（默认）
             - "day_after_tomorrow": 后天

    Returns:
        格式化的天气预报信息
    """
    day_offset_map = {
        "today": 0,
        "tomorrow": 1,
        "day_after_tomorrow": 2,
    }

    day_offset = day_offset_map.get(day, 1)

    return _get_daily_weather_impl(city, day_offset)


def _get_weather_impl(
    city: str,
    extensions: Literal["base", "all"] = "base"
) -> str:
    """
    天气查询的底层实现函数
    """
    amap_key = getattr(settings, 'amap_key', None)
    if not amap_key:
        error_msg = "高德地图 API Key 未设置！请在 .env 文件中设置 AMAP_KEY。"
        logger.error(error_msg)
        return f"错误：{error_msg}"

    url = "https://restapi.amap.com/v3/weather/weatherInfo"

    params = {
        "key": amap_key,
        "city": city,
        "extensions": extensions,
        "output": "JSON"
    }

    logger.info(f"🌤️ 查询天气: city={city}, extensions={extensions}")

    try:
        import httpx
        with httpx.Client(timeout=10.0) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

        if data.get("status") != "1":
            error_msg = f"天气查询失败: {data.get('info', '未知错误')}"
            logger.error(error_msg)
            return f"错误：{error_msg}"

        if extensions == "base":
            return _format_live_weather(data)
        else:
            return _format_forecast_weather(data)

    except httpx.TimeoutException:
        return "错误：天气查询超时，请稍后重试"
    except httpx.HTTPStatusError as e:
        return f"错误：HTTP 请求失败: {e.response.status_code}"
    except Exception as e:
        return f"错误：天气查询出错: {str(e)}"


def _get_daily_weather_impl(city: str, day_offset: int) -> str:
    """
    每日天气查询实现
    """
    amap_key = getattr(settings, 'amap_key', None)
    if not amap_key:
        error_msg = "高德地图 API Key 未设置！请在 .env 文件中设置 AMAP_KEY。"
        logger.error(error_msg)
        return f"错误：{error_msg}"

    url = "https://restapi.amap.com/v3/weather/weatherInfo"

    params = {
        "key": amap_key,
        "city": city,
        "extensions": "all",
        "output": "JSON"
    }

    logger.info(f"🌤️ 查询天气: city={city}, day_offset={day_offset}")

    try:
        import httpx
        with httpx.Client(timeout=10.0) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

        if data.get("status") != "1":
            error_msg = f"天气查询失败: {data.get('info', '未知错误')}"
            logger.error(error_msg)
            return f"错误：{error_msg}"

        return _format_forecast_weather(data, day_offset=day_offset)

    except httpx.TimeoutException:
        return "错误：天气查询超时，请稍后重试"
    except httpx.HTTPStatusError as e:
        return f"错误：HTTP 请求失败: {e.response.status_code}"
    except Exception as e:
        return f"错误：天气查询出错: {str(e)}"


def _format_live_weather(data: dict) -> str:
    """格式化实况天气数据"""
    lives = data.get("lives", [])
    if not lives:
        return "未查询到天气数据"

    live = lives[0]

    province = live.get("province", "")
    city = live.get("city", "")
    weather = live.get("weather", "")
    temperature = live.get("temperature", "")
    winddirection = live.get("winddirection", "")
    windpower = live.get("windpower", "")
    humidity = live.get("humidity", "")
    reporttime = live.get("reporttime", "")

    result = f"""
📍 地区：{province} {city}
🌤️ 天气：{weather}
🌡️ 温度：{temperature}°C
💨 风向：{winddirection}风
💨 风力：{windpower}级
💧 湿度：{humidity}%
⏰ 更新时间：{reporttime}
""".strip()

    logger.info(f"✅ 实况天气查询成功: {city}")
    return result


def _format_forecast_weather(data: dict, day_offset: Optional[int] = None) -> str:
    """格式化预报天气数据"""
    forecasts = data.get("forecasts", [])
    if not forecasts:
        return "未查询到天气预报数据"

    forecast = forecasts[0]

    province = forecast.get("province", "")
    city = forecast.get("city", "")
    reporttime = forecast.get("reporttime", "")
    casts = forecast.get("casts", [])

    if not casts:
        return "未查询到具体预报数据"

    if day_offset is not None:
        if day_offset < 0 or day_offset >= len(casts):
            return f"错误：无法查询第 {day_offset} 天的天气"

        cast = casts[day_offset]
        date = cast.get("date", "")
        week = cast.get("week", "")
        dayweather = cast.get("dayweather", "")
        nightweather = cast.get("nightweather", "")
        daytemp = cast.get("daytemp", "")
        nighttemp = cast.get("nighttemp", "")
        daywind = cast.get("daywind", "")
        nightwind = cast.get("nightwind", "")
        daypower = cast.get("daypower", "")
        nightpower = cast.get("nightpower", "")

        day_names = ["今天", "明天", "后天"]
        day_name = day_names[day_offset] if day_offset < len(day_names) else f"{day_offset}天后"

        result = f"""📍 地区：{province} {city}
⏰ 预报发布时间：{reporttime}

📅 {day_name}（{date} 星期{week}）
  🌞 白天：{dayweather}  {daytemp}°C  {daywind}风{daypower}级
  🌙 夜间：{nightweather}  {nighttemp}°C  {nightwind}风{nightpower}级"""

        logger.info(f"✅ 预报天气查询成功: {city} {day_name}")
        return result

    result = [f"📍 地区：{province} {city}"]
    result.append(f"⏰ 预报发布时间：{reporttime}")
    result.append("")

    for idx, cast in enumerate(casts):
        date = cast.get("date", "")
        week = cast.get("week", "")
        dayweather = cast.get("dayweather", "")
        nightweather = cast.get("nightweather", "")
        daytemp = cast.get("daytemp", "")
        nighttemp = cast.get("nighttemp", "")
        daywind = cast.get("daywind", "")
        nightwind = cast.get("nightwind", "")
        daypower = cast.get("daypower", "")
        nightpower = cast.get("nightpower", "")

        day_names = ["今天", "明天", "后天"]
        day_name = day_names[idx] if idx < len(day_names) else f"{idx}天后"

        day_info = f"""
📅 {day_name}（{date} 星期{week}）
  🌞 白天：{dayweather}  {daytemp}°C  {daywind}风{daypower}级
  🌙 夜间：{nightweather}  {nighttemp}°C  {nightwind}风{nightpower}级
""".strip()

        result.append(day_info)

    logger.info(f"✅ 预报天气查询成功: {city} ({len(casts)}天)")
    return "\n".join(result)


BASIC_TOOLS = [
    get_current_time,
    get_current_date,
    calculator,
]

WEB_SEARCH_TOOLS = [
    web_search,
    web_search_simple,
]

WEATHER_TOOLS = [
    get_daily_weather,
    get_weather_forecast,
    get_weather,
]

ADVANCED_TOOLS = WEB_SEARCH_TOOLS + WEATHER_TOOLS

ALL_TOOLS = BASIC_TOOLS + ADVANCED_TOOLS


def get_tools_for_request(use_tools: bool, use_advanced_tools: bool) -> List:
    """
    根据请求参数获取工具列表

    规则：
    1. 如果用户关闭 use_tools，则不加载任何工具
    2. 天气工具只要配置了 AMAP_KEY，就默认提供
    """
    if not use_tools:
        return []

    tools: List = list(BASIC_TOOLS)

    if settings.amap_key:
        for tool in WEATHER_TOOLS:
            if tool not in tools:
                tools.append(tool)

    if settings.tavily_api_key:
        for tool in WEB_SEARCH_TOOLS:
            if tool not in tools:
                tools.append(tool)

    return tools


__all__ = [
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
    "BASIC_TOOLS",
    "ADVANCED_TOOLS",
    "WEB_SEARCH_TOOLS",
    "WEATHER_TOOLS",
    "ALL_TOOLS",
]