from django.apps import AppConfig


def _warmup_caches(sender=None, **kwargs):
    """预加载缓存：从数据库加载 registry / SystemConfig 到进程内缓存。

    同步上下文才能访问 ORM；异步上下文（FastAPI 执行服务 agent 构建）依赖此缓存。
    预热必须在 ``apps.ready`` 为 True 后执行（否则触发 Django
    "Accessing the database during app initialization" RuntimeWarning），
    故统一由各进程入口调用：
    - Web 进程：asgi.py / wsgi.py 顶层（get_*_application() 之后）
    - FastAPI 执行服务（8001）：main.py lifespan（sync_to_async）
    - 迁移后：post_migrate 信号（本模块注册）
    """
    from Django_xm.apps.ai_engine.services.registry_service import warmup_cache

    warmup_cache()

    from Django_xm.apps.ai_engine.models import warmup_system_config_cache

    warmup_system_config_cache()


class AiEngineConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "Django_xm.apps.ai_engine"
    verbose_name = "AI引擎模块"

    def ready(self):

        # LangSmith 追踪配置统一入口（Task 14.1 / 16.3 / 15.6a）
        # 抽取自 agent_factory.py 与 base_builder.py 的重复实现
        # 归属 ai_engine（LangSmith 追踪是 AI 引擎职责，不应放在 core）
        from Django_xm.apps.ai_engine.services.langsmith_setup import configure_langsmith

        configure_langsmith()

        # allowed_objects 已在 checkpointer_factory.py 模块级别设置，
        # 此处仅做兜底确保 Reviver 已初始化
        try:
            from langchain_core.load.load import Reviver
            from langgraph.checkpoint.serde import jsonplus as _jsonplus

            if not isinstance(getattr(_jsonplus, "LC_REVIVER", None), Reviver):
                _jsonplus.LC_REVIVER = Reviver(allowed_objects="messages")
        except Exception:  # noqa: S110  # 兜底初始化，Reviver 可能已在别处设置或 langgraph 版本不兼容
            pass

        from Django_xm.apps.ai_engine.capabilities.setup import setup_default_capabilities

        setup_default_capabilities()

        from Django_xm.apps.ai_engine.config import settings as app_cfg

        if app_cfg.llm_cache_enabled:
            from Django_xm.apps.cache_manager.services.cache_service import setup_langchain_cache

            setup_langchain_cache(enabled=True)

        # 缓存预热由各进程入口完成（apps.ready 为 True 后执行，避免 Django
        # "Accessing the database during app initialization" RuntimeWarning）：
        # - Web 进程：asgi.py / wsgi.py 顶层
        # - FastAPI 执行服务（8001）：main.py lifespan（sync_to_async）
        # post_migrate 仍注册，用于迁移完成后重建缓存。
        from django.db.models.signals import post_migrate

        post_migrate.connect(_warmup_caches, sender=self)
