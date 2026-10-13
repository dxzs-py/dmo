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

        # 注册跨应用信号 receiver：用户注销/删除、会话删除 → Celery 清理
        # checkpoint/Store。必须显式导入，receiver 装饰器才会生效。
        # 环 1 依赖倒置接线（spec eliminate-priority-debts 4.1）：context_manager
        # 禁止 import ai_engine（.importlinter 契约），引擎侧能力在组合根注册为
        # 运行期 provider——压缩/评估模型解析链（compression_bridge）、Store 工厂、
        # ToolUsageGuard 单例。失败快速抛出（接线缺失会导致压缩静默降级）。
        from Django_xm.apps.ai_engine.services import compression_bridge
        from Django_xm.apps.ai_engine.services.checkpointer_factory import get_store
        from Django_xm.apps.ai_engine.services.tool_usage_guard import get_tool_usage_guard
        from Django_xm.apps.context_manager.services.runtime_providers import (
            set_compression_model_provider,
            set_evaluation_model_provider,
            set_store_provider,
            set_tool_usage_guard_provider,
        )

        from . import signals  # noqa: F401

        set_compression_model_provider(compression_bridge.resolve_compression_model)
        set_evaluation_model_provider(compression_bridge.resolve_evaluation_model)
        set_store_provider(get_store)
        set_tool_usage_guard_provider(get_tool_usage_guard)

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

        # TokenEstimator 自定义模型窗口表接线（配置驱动，admin 可维护）：
        # common 层禁止依赖 apps（.importlinter 契约），故由引擎侧注入 loader，
        # 读取 SystemConfig `model_context_limits`（值形如 {"模型名或前缀": 窗口tokens}）。
        # admin 编辑该键后 ≤60s（TokenEstimator TTL）自动生效，无需改代码。
        from Django_xm.common.token_estimator import TokenEstimator

        def _load_custom_model_limits() -> dict[str, int] | None:
            from Django_xm.apps.ai_engine.models import SystemConfig

            return SystemConfig.get_value("model_context_limits", default={}) or {}

        TokenEstimator.set_custom_limits_loader(_load_custom_model_limits)

        # 缓存预热由各进程入口完成（apps.ready 为 True 后执行，避免 Django
        # "Accessing the database during app initialization" RuntimeWarning）：
        # - Web 进程：asgi.py / wsgi.py 顶层
        # - FastAPI 执行服务（8001）：main.py lifespan（sync_to_async）
        # post_migrate 仍注册，用于迁移完成后重建缓存。
        from django.db.models.signals import post_migrate

        post_migrate.connect(_warmup_caches, sender=self)
