"""
计算器工具
提供安全的数学表达式计算功能
"""

import re
from typing import Union
from langchain_core.tools import tool

import logging

logger = logging.getLogger(__name__)


def _safe_eval(expression: str) -> Union[float, int, str]:
    expression = expression.replace(" ", "")

    if not re.match(r'^[\d+\-*/().]+$', expression):
        return "错误：表达式包含不允许的字符。只支持数字和基本运算符 (+, -, *, /, ())"

    if expression.count('(') != expression.count(')'):
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
        return f"错误：计算失败 - {str(e)}"


@tool
def calculator(expression: str) -> str:
    """
    计算数学表达式

    支持基本的数学运算：加法(+)、减法(-)、乘法(*)、除法(/)

    **注意：进行复杂计算或需要精确结果时，可以使用 Python 代码执行器**

    Args:
        expression: 数学表达式字符串

    Returns:
        计算结果的字符串表示

    Example:
        >>> calculator("2 + 3 * 4")
        '14'
        >>> calculator("(10 + 5) / 3")
        '5'
    """
    logger.info(f"🧮 计算表达式: {expression}")
    result = _safe_eval(expression)
    logger.debug(f"🧮 计算结果: {result}")
    return str(result)


def get_calculator_tools():
    return [calculator]