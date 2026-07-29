"""
Core 应用配置

在 ready() 阶段：
- 注册信号
- 启用 loguru 日志（已存在配置，仅在此调用）
"""

import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "Django_xm.apps.core"
    verbose_name = "LangChain核心模块"

    def ready(self):
        # 1. 注册 core 信号
        # 2. 启用 loguru 日志（配置已在 core/config.setup_loguru_logging 中实现）
        #    仅在非管理命令测试场景下启用，避免污染 pytest 输出
        import os

        if os.environ.get("DISABLE_LOGURU", "").lower() not in ("1", "true", "yes"):
            try:
                from Django_xm.apps.core.config import setup_loguru_logging

                setup_loguru_logging()
            except Exception as exc:
                logger.warning(f"loguru 配置失败，回退到标准 logging: {exc}")
