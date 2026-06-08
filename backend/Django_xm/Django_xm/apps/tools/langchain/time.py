from datetime import datetime
from langchain_core.tools import BaseTool
from pydantic import BaseModel
import logging

from Django_xm.apps.tools.errors import TOOL_VERSION

logger = logging.getLogger(__name__)


class GetCurrentTimeInput(BaseModel):
    pass


class GetCurrentDateInput(BaseModel):
    pass


class GetCurrentTimeTool(BaseTool):
    name: str = "get_current_time"
    version: str = TOOL_VERSION
    metadata: dict = {"tier": "core", "visibility": "core", "category": "basic"}
    description: str = (
        "获取当前系统时间，返回格式化的日期和时间（YYYY-MM-DD HH:MM:SS）。"
        "适用场景：需要知道当前确切时间、计算时间差、安排定时任务、判断时间先后顺序。"
        "不适用：查询天气（天气工具内部已含日期信息）、执行数学计算、搜索网络信息。"
        "参数：无。"
    )
    args_schema: type[BaseModel] = GetCurrentTimeInput

    def _run(self) -> str:
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.debug(f"🕐 获取当前时间: {current_time}")
        return f"当前时间是：{current_time}"

    async def _arun(self) -> str:
        return self._run()


class GetCurrentDateTool(BaseTool):
    name: str = "get_current_date"
    version: str = TOOL_VERSION
    metadata: dict = {"tier": "core", "visibility": "core", "category": "basic"}
    description: str = (
        "获取当前日期，返回格式化的日期（YYYY-MM-DD）及星期几。"
        "适用场景：需要知道今天是几号、星期几、判断日期范围、安排日程。"
        "不适用：查询天气（天气工具内部已含日期信息）、执行数学计算。"
        "参数：无。"
    )
    args_schema: type[BaseModel] = GetCurrentDateInput

    def _run(self) -> str:
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        weekday = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][now.weekday()]
        logger.debug(f"📅 获取当前日期: {date_str} {weekday}")
        return f"今天是：{date_str} ({weekday})"

    async def _arun(self) -> str:
        return self._run()


get_current_time = GetCurrentTimeTool()
get_current_date = GetCurrentDateTool()


def get_time_tools():
    return [get_current_time, get_current_date]
