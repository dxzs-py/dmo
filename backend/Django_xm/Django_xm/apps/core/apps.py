"""
Core 应用配置

在 ready() 阶段：
- 显式激活 core 信号注册
"""

from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "Django_xm.apps.core"
    verbose_name = "LangChain核心模块"

    def ready(self):
        # 显式激活 core 信号注册（@receiver 装饰器在模块导入时执行，
        # 此前依赖其他 app 间接 import 副作用激活，属隐性依赖，dj-17 根因修复）
        import Django_xm.apps.core.signals  # noqa: F401
