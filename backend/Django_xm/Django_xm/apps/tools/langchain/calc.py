import logging
import re

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from Django_xm.apps.tools.errors import TOOL_VERSION

logger = logging.getLogger(__name__)


def _safe_eval(expression: str) -> float | int | str:
    expression = expression.replace(" ", "")
    if not re.match(r"^[\d+\-*/().]+$", expression):
        return "错误：表达式包含不允许的字符。只支持数字和基本运算符 (+, -, *, /, ())"
    if expression.count("(") != expression.count(")"):
        return "错误：括号不匹配"
    try:
        result = eval(expression)
        if isinstance(result, float) and result.is_integer():
            return int(result)
        if isinstance(result, float):
            return round(result, 10)
        return result
    except ZeroDivisionError:
        return "错误：除数不能为零"
    except Exception as e:
        return f"错误：计算失败 - {e!s}"


class CalculatorInput(BaseModel):
    expression: str = Field(description="数学表达式字符串，支持+、-、*、/和括号")


class CalculatorTool(BaseTool):
    name: str = "calculator"
    version: str = TOOL_VERSION
    metadata: dict = Field(default_factory=lambda: {"tier": "core", "visibility": "core", "category": "basic"})
    description: str = (
        "计算数学表达式，支持加法(+)、减法(-)、乘法(*)、除法(/)和括号运算。"
        "适用场景：需要进行基本四则运算、快速计算数值结果、单位换算。"
        "不适用：复杂科学计算、符号运算、代码执行、文本处理。"
        "参数：expression-数学表达式字符串（仅支持数字和+-*/()，如'2+3*4'或'(10+5)/3'）。"
        "边界：表达式仅允许数字和基本运算符，不支持变量、函数或科学计数法。"
    )
    args_schema: type[BaseModel] = CalculatorInput

    def _run(self, expression: str) -> str:
        logger.info(f"🧮 计算表达式: {expression}")
        result = _safe_eval(expression)
        logger.debug(f"🧮 计算结果: {result}")
        return str(result)

    async def _arun(self, expression: str) -> str:
        return self._run(expression=expression)


calculator = CalculatorTool()


def get_calculator_tools():
    return [calculator]
