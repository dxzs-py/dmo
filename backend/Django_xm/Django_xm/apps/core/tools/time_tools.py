"""
时间相关工具
提供获取当前时间、日期等功能
"""

from datetime import datetime
from langchain_core.tools import tool

import logging

logger = logging.getLogger(__name__)


@tool
def get_current_time() -> str:
    """
    获取当前时间

    返回格式化的当前日期和时间，格式为：YYYY-MM-DD HH:MM:SS

    **注意：查询天气时不需要调用此工具！天气工具内部已经知道当前日期。**

    Returns:
        当前时间的字符串表示
    """
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.debug(f"🕐 获取当前时间: {current_time}")
    return f"当前时间是：{current_time}"


@tool
def get_current_date() -> str:
    """
    获取当前日期

    返回格式化的当前日期，格式为：YYYY-MM-DD，以及星期几

    **注意：查询天气时不需要调用此工具！天气工具内部已经知道当前日期。**

    Returns:
        当前日期的字符串表示
    """
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    weekday = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][now.weekday()]
    logger.debug(f"📅 获取当前日期: {date_str} {weekday}")
    return f"今天是：{date_str} ({weekday})"


def get_time_tools():
    return [get_current_time, get_current_date]