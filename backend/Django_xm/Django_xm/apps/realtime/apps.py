"""realtime 应用配置。"""

from django.apps import AppConfig


class RealtimeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "Django_xm.apps.realtime"
    verbose_name = "实时同步"
