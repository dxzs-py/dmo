"""工具 app 配置。

启动时校验字段加密密钥（FIELD_ENCRYPTION_KEY）可用，
确保 ``McpServerConfig.auth_token`` 等加密字段可正常读写。
"""

import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class ToolsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'Django_xm.apps.tools'
    verbose_name = '工具模块'

    def ready(self):
        # 启动时校验加密密钥可用，缺失则启动失败
        from Django_xm.apps.tools.fields import validate_encryption_key
        try:
            validate_encryption_key()
        except RuntimeError as exc:
            raise RuntimeError(
                f"工具 app 启动失败：{exc}"
            ) from exc
