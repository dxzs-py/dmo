"""
日志工具模块

提供统一的 logger 获取接口，避免各模块重复配置 handler。
本模块的 get_logger 是全项目唯一的实现（原 core.config.get_logger 已删除），
默认日志级别来源于 Django_xm.apps.core.config.settings.log_level。
"""

import logging


def get_logger(name: str) -> logging.Logger:
    """获取配置好的 logger 实例

    默认日志级别取自 settings.log_level（缺失或非法时回退 INFO），
    本函数是全项目唯一的 get_logger 实现。
    """
    # 延迟 import，避免与 core.config 产生模块级循环导入
    from Django_xm.apps.core.config import settings

    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(lineno)d %(message)s"))
        logger.addHandler(handler)
    # 阻断向根 logger 传播，避免同一日志同时由本 handler 与根 logger 重复输出
    # （否则 splitters/index_service 等模块的日志会在 stderr 与 django.log 中重复出现）
    logger.propagate = False
    # 默认级别来源于 settings.log_level，非法值回退 INFO
    level = getattr(logging, getattr(settings, "log_level", "INFO"), logging.INFO)
    logger.setLevel(level)
    return logger
