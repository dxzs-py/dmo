from django.apps import AppConfig


class ResearchConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'Django_xm.apps.research'
    verbose_name = '深度研究模块'

    def ready(self):
        import Django_xm.apps.research.signals  # noqa: F401
