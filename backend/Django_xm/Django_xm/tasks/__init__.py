"""
Celery 统一任务模块
按业务领域分文件管理所有异步任务

注意：此模块使用延迟导入，避免在 Celery 初始化阶段触发 Django app 依赖。
直接通过具体模块导入即可，如：from Django_xm.tasks.rag_tasks import upload_documents_task
"""

def __getattr__(name):
    _LAZY_MAP = {
        "cleanup_old_task_records": ".base",
        "check_stale_tasks": ".base",
        "cleanup_expired_attachments": ".chat_tasks",
        "index_old_attachments": ".chat_tasks",
        "check_storage_alerts": ".chat_tasks",
    }
    if name in _LAZY_MAP:
        import importlib

        module = importlib.import_module(_LAZY_MAP[name], __package__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "check_stale_tasks",
    "check_storage_alerts",
    "cleanup_expired_attachments",
    "cleanup_old_task_records",
    "index_old_attachments",
]
