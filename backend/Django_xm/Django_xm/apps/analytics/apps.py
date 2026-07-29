from django.apps import AppConfig


class AnalyticsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "Django_xm.apps.analytics"
    verbose_name = "数据分析"

    def ready(self):
        from .signals import register_signals

        register_signals()
