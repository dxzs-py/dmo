from django.apps import AppConfig


class CacheManagerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'Django_xm.apps.cache_manager'
    verbose_name = '缓存管理'

    def ready(self):
        import Django_xm.apps.cache_manager.signals
