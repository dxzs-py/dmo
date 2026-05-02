from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'Django_xm.apps.core'
    verbose_name = 'LangChain核心模块'

    def ready(self):
        import Django_xm.apps.core.permission_models
        import Django_xm.apps.core.signals
