from django.apps import AppConfig


class CacheManagerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "Django_xm.apps.cache_manager"
    verbose_name = "缓存管理"

    def ready(self):

        # 注册 Redis 状态提供者到 core 的状态注册表（Task 15.3）
        from Django_xm.apps.cache_manager.services.status_provider import RedisStatusProvider
        from Django_xm.apps.core.services.status_registry import register_status_provider

        register_status_provider(RedisStatusProvider())
