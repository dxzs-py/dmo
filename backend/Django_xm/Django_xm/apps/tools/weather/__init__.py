import httpx
from datetime import datetime, timedelta
from typing import Optional, Literal
from langchain_core.tools import tool
import logging

logger = logging.getLogger(__name__)


def get_amap_key() -> Optional[str]:
    try:
        from Django_xm.apps.ai_engine.config import settings
        return getattr(settings, 'amap_key', None)
    except ImportError:
        import os
        return os.environ.get("AMAP_KEY")


def _get_weather_impl(
    city: str,
    extensions: Literal["base", "all"] = "base"
) -> str:
    amap_key = get_amap_key()
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
        error_msg = "天气查询超时，请稍后重试"
        logger.error(error_msg)
        return f"错误：{error_msg}"
    except httpx.HTTPStatusError as e:
        error_msg = f"HTTP 请求失败: {e.response.status_code}"
        logger.error(error_msg)
        return f"错误：{error_msg}"
    except Exception as e:
        error_msg = f"天气查询异常: {str(e)}"
        logger.error(error_msg)
        return f"错误：{error_msg}"


def _format_live_weather(data: dict) -> str:
    weather_info = data.get("lives", [{}])[0]
    if not weather_info:
        return "未获取到天气信息"

    city = weather_info.get("city", "未知")
    weather = weather_info.get("weather", "未知")
    temperature = weather_info.get("temperature", "未知")
    wind_direction = weather_info.get("winddirection", "未知")
    wind_power = weather_info.get("windpower", "未知")
    humidity = weather_info.get("humidity", "未知")
    report_time = weather_info.get("reporttime", "未知")

    return (
        f"📍 {city}\n"
        f"🌡️ 当前温度: {temperature}°C\n"
        f"🌤️ 天气状况: {weather}\n"
        f"💨 风向: {wind_direction}风 ({wind_power}级)\n"
        f"💧 湿度: {humidity}%\n"
        f"🕐 发布时间: {report_time}"
    )


def _format_forecast_weather(data: dict) -> str:
    forecasts = data.get("forecasts", [{}])
    if not forecasts:
        return "未获取到天气预报信息"

    forecast_info = forecasts[0]
    city = forecast_info.get("city", "未知")
    report_time = forecast_info.get("reporttime", "未知")
    casts = forecast_info.get("casts", [])

    result = [f"📍 {city} 天气预报 (发布时间: {report_time})\n"]

    day_names = ["今天", "明天", "后天"]

    for i, cast in enumerate(casts[:4]):
        if i < len(day_names):
            day_name = day_names[i]
        else:
            day_name = f"第{i+1}天"

        date = cast.get("date", "")
        day_weather = cast.get("dayweather", "未知")
        night_weather = cast.get("nightweather", "未知")
        day_temp = cast.get("daytemp", "未知")
        night_temp = cast.get("nighttemp", "未知")
        day_wind = cast.get("daywind", "未知")
        night_wind = cast.get("nightwind", "未知")
        day_power = cast.get("daypower", "未知")
        night_power = cast.get("nightpower", "未知")

        result.append(
            f"\n{day_name} ({date}):\n"
            f"  🌤️ 白天: {day_weather}, {day_temp}°C, {day_wind}风 {day_power}级\n"
            f"  🌙 夜间: {night_weather}, {night_temp}°C, {night_wind}风 {night_power}级"
        )

    return "\n".join(result)


@tool
def weather_query(city: str, extensions: Literal["base", "all"] = "base") -> str:
    """查询指定城市的天气信息

    可以查询当前天气或未来几天的天气预报。

    **重要提示：天气工具内部已经知道当前日期，不需要在调用前获取当前时间！**

    Args:
        city: 城市名称（如"北京"、"上海"、"杭州"）或城市编码
        extensions: 天气类型，"base"获取实时天气，"all"获取未来天气预报

    Returns:
        格式化的天气信息字符串
    """
    logger.info(f"🌤️ 调用天气查询工具: city={city}")
    return _get_weather_impl(city, extensions)


def get_weather_tools():
    return [weather_query]


WEATHER_TOOLS = get_weather_tools()
