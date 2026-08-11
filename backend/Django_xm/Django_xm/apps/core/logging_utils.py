"""
日志工具模块

提供统一的 logger 获取接口，避免各模块重复配置 handler。
从 core.config.get_logger 迁移而来，降低 knowledge 等模块对 ai_engine 的依赖。
"""

import logging


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """获取配置好的 logger 实例"""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(lineno)d %(message)s"))
        logger.addHandler(handler)
    # 阻断向根 logger 传播，避免同一日志同时由本 handler 与根 logger 重复输出
    # （与 core.config.get_logger 行为对齐；否则 splitters/index_service 等
    #  模块的日志会在 stderr 与 django.log 中重复出现）
    logger.propagate = False
    logger.setLevel(level)
    return logger
