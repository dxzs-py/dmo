"""
日志工具模块

提供统一的 logger 获取接口。本模块的 get_logger 是全项目唯一的实现
（原 core.config.get_logger 已删除）。

接线原则（audit-supplement-remediate B2）：logger 一律交还 Django LOGGING
（settings/base.py）统一管理——handler、formatter、脱敏过滤器（redact_sensitive）
与输出目标（django.log / fastapi.log）全部由 LOGGING 配置承担；本函数只做
两件事：按 settings.log_level 设置级别兜底，并保证向 Django_xm 层级传播
（base 已配置 "Django_xm" 兜底 logger 接住未枚举命名空间）。
历史行为（自建 StreamHandler + propagate=False）使 Agent 主链日志脱离
脱敏与文件管线，已废弃。
"""

import logging


def get_logger(name: str) -> logging.Logger:
    """获取接入 Django LOGGING 管线的 logger 实例。

    - 不添加任何 handler：输出目标/格式/脱敏由 settings/base.py LOGGING 负责
    - propagate 保持 True：未在 LOGGING 显式枚举的命名空间由 "Django_xm"
      兜底 logger 接住（写入 django.log/fastapi.log）
    - 级别取 settings.log_level（缺失或非法时回退 INFO），与历史行为一致
    """
    # 延迟 import，避免与 core.config 产生模块级循环导入
    from Django_xm.apps.core.config import settings

    logger = logging.getLogger(name)
    level = getattr(logging, getattr(settings, "log_level", "INFO"), logging.INFO)
    logger.setLevel(level)
    return logger
