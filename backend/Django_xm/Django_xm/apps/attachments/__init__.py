"""
Attachments 子应用 - 附件管理

提供附件内容加载、文档记忆、生命周期管理等服务。
附件模型（ChatAttachment）仍保留在 chat 子应用中，因其与 ChatSession/ChatMessage 存在 FK 关系。
"""


def __getattr__(name):
    if name in ("AttachmentService", "DocumentMemoryService", "AttachmentLifecycleService"):
        if name == "AttachmentService":
            from .services.attachment_content_service import AttachmentService
            return AttachmentService
        elif name == "DocumentMemoryService":
            from .services.document_memory_service import DocumentMemoryService
            return DocumentMemoryService
        elif name == "AttachmentLifecycleService":
            from .services.attachment_lifecycle import AttachmentLifecycleService
            return AttachmentLifecycleService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AttachmentService",
    "DocumentMemoryService",
    "AttachmentLifecycleService",
]
