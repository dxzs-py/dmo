"""子代理运行时异常（统一出口，上层业务按类型捕获）。"""


class SubAgentError(Exception):
    """子代理运行时基类异常。"""


class SubAgentNotFoundError(SubAgentError):
    """子代理实例不存在。"""


class SubAgentNestingLimitError(SubAgentError):
    """子代理嵌套深度超限。"""


class SubAgentRoundLimitError(SubAgentError):
    """子代理单实例轮次超限。"""


class SubAgentInvalidStateError(SubAgentError):
    """子代理当前状态不允许该操作（如对非中断状态执行 resume）。"""
