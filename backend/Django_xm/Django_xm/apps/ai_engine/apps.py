from django.apps import AppConfig


def _warmup_caches(sender, **kwargs):
    """post_migrate 信号回调：预加载缓存

    Django 推荐在 post_migrate 而非 AppConfig.ready() 中访问数据库，
    避免 "Accessing the database during app initialization is discouraged" 警告。
    """
    from Django_xm.apps.ai_engine.services.registry_service import warmup_cache
    warmup_cache()

    from Django_xm.apps.ai_engine.models import warmup_system_config_cache
    warmup_system_config_cache()


class AiEngineConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'Django_xm.apps.ai_engine'
    verbose_name = 'AI引擎模块'

    def ready(self):
        import Django_xm.apps.ai_engine.signals

        # allowed_objects 已在 checkpointer_factory.py 模块级别设置，
        # 此处仅做兜底确保 Reviver 已初始化
        try:
            from langgraph.checkpoint.serde import jsonplus as _jsonplus
            from langchain_core.load.load import Reviver
            if not isinstance(getattr(_jsonplus, 'LC_REVIVER', None), Reviver):
                _jsonplus.LC_REVIVER = Reviver(allowed_objects="messages")
        except Exception:
            pass

        from Django_xm.apps.ai_engine.capabilities.setup import setup_default_capabilities
        setup_default_capabilities()

        from Django_xm.apps.ai_engine.config import settings as app_cfg
        if app_cfg.llm_cache_enabled:
            from Django_xm.apps.cache_manager.services.cache_service import setup_langchain_cache
            setup_langchain_cache(enabled=True)

        # 注册 post_migrate 信号，在数据库迁移完成后预加载缓存
        # 不在 ready() 中直接调用，避免 Django RuntimeWarning
        from django.db.models.signals import post_migrate
        post_migrate.connect(_warmup_caches, sender=self)
