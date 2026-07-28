"""
Core models - Django 基础设施

聚合 core app 的所有模型定义，供 Django 应用发现：
- BaseModel：带软删除的抽象基类
- AuditModel：带创建人和更新人的审计模型
- CeleryTaskRecord：Celery 任务持久化记录

调用方应直接从本模块或子模块导入（Django_xm.apps.core.models / .base_models / .task_models）。
"""

from .base_models import AuditModel, BaseModel
from .task_models import CeleryTaskRecord

__all__ = [
    "AuditModel",
    "BaseModel",
    "CeleryTaskRecord",
]
