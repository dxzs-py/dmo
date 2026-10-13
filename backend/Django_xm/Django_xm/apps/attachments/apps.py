from django.apps import AppConfig


class AttachmentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "Django_xm.apps.attachments"
    verbose_name = "附件管理"

    def ready(self):
        # 环 1 依赖倒置接线（spec eliminate-priority-debts 4.1）：ContextManager
        # 构建结构化上下文时原函数内 import DocumentMemoryService（其内部依赖
        # ai_engine.llm_factory，构成 context_manager → ai_engine 传递违规），
        # 现经 context_manager.services.runtime_providers 注入文档上下文构建器。
        from Django_xm.apps.attachments.services.document_memory_service import DocumentMemoryService
        from Django_xm.apps.context_manager.services.runtime_providers import set_document_context_provider

        def build_document_context(user_id, store):
            return DocumentMemoryService(store=store).build_document_context(user_id)

        set_document_context_provider(build_document_context)
