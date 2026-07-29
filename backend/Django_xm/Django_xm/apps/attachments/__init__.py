"""
Attachments 子应用 - 附件管理

提供附件内容加载、文档记忆、生命周期管理等服务。
附件模型（ChatAttachment）仍保留在 chat 子应用中，因其与 ChatSession/ChatMessage 存在 FK 关系。
"""


def __getattr__(name):
    """延迟导入，避免 Django app registry 未就绪时触发循环导入"""
    _services = {
        "AttachmentService": ".services.attachment_content_service",
        "DocumentMemoryService": ".services.document_memory_service",
        "AttachmentLifecycleService": ".services.attachment_lifecycle",
    }
    if name in _services:
        import importlib

        module = importlib.import_module(_services[name], __package__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AttachmentLifecycleService",
    "AttachmentService",
    "DocumentMemoryService",
]
