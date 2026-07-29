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
    logger.setLevel(level)
    return logger
