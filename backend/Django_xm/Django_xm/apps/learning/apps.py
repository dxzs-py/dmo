from django.apps import AppConfig


class LearningConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'Django_xm.apps.learning'
    verbose_name = '学习工作流模块'

    def ready(self):
        import Django_xm.apps.learning.signals  # noqa: F401
