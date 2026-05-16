from django.apps import AppConfig


class KnowledgeConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'Django_xm.apps.knowledge'
    verbose_name = '知识库模块'

    def ready(self):
        import Django_xm.apps.knowledge.signals  # noqa: F401
