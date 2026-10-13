from django.apps import AppConfig


class ContextManagerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "Django_xm.apps.context_manager"
    verbose_name = "上下文管理模块"

    # 环 1 注入接线说明（spec eliminate-priority-debts 4.1）：本 app 的跨层
    # provider 由各能力提供方在其 AppConfig.ready() 注册——
    # - ai_engine ready()：压缩/评估模型解析链、Store 工厂、ToolUsageGuard；
    # - knowledge ready()：Embedding 服务（本 app 禁止静态 import knowledge，
    #   因 knowledge.config re-export ai_engine 构成传递违规）；
    # - attachments ready()：文档记忆上下文构建。
    # 本 app 自身保持零跨 app 静态依赖。
