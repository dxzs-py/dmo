from django.apps import AppConfig


class KnowledgeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "Django_xm.apps.knowledge"
    verbose_name = "知识库模块"

    def ready(self):

        # 注册向量存储状态提供者到 core 的状态注册表（Task 15.3）
        from Django_xm.apps.core.services.status_registry import register_status_provider
        from Django_xm.apps.knowledge.services.status_provider import VectorStoreStatusProvider

        register_status_provider(VectorStoreStatusProvider())
        # 注意：PGVector 预热不在 ready() 中执行（Django 禁止 AppConfig.ready()
        # 访问数据库，会触发 RuntimeWarning）。改为：
        # - runserver/manage.py 进程：PGVectorBackend._get_pgvector_class() 首次调用懒预热
        # - celery worker 进程：worker_ready 信号预热（tasks/signals.py）
