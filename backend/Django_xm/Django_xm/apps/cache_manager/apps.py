from django.apps import AppConfig


class CacheManagerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'Django_xm.apps.cache_manager'
    verbose_name = '缓存管理'

    def ready(self):
        from Django_xm.apps.ai_engine.config import settings as app_cfg
        if app_cfg.llm_cache_enabled:
            from Django_xm.apps.cache_manager.services.cache_service import setup_langchain_cache
            setup_langchain_cache(enabled=True)
