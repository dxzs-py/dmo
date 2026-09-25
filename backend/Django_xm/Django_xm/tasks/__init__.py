"""
Celery 统一任务模块
按业务领域分文件管理所有异步任务

注意：此模块使用延迟导入，避免在 Celery 初始化阶段触发 Django app 依赖。
直接通过具体模块导入即可，如：from Django_xm.tasks.rag_tasks import upload_documents_task
"""
