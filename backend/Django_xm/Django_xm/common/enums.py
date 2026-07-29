"""
公共枚举定义

跨 app 共用的枚举常量，避免各 app 重复定义相同结构的状态枚举。
"""

from django.db import models


class TaskStatus(models.TextChoices):
    """通用任务状态枚举

    适用于所有具有执行状态的任务型模型（研究任务、工作流执行等）。
    各模型可在此基础上扩展额外状态（如 WorkflowSessionStatus 的 WAITING_FOR_ANSWERS）。
    """

    PENDING = "pending", "待执行"
    RUNNING = "running", "执行中"
    COMPLETED = "completed", "已完成"
    FAILED = "failed", "失败"
    CANCELLED = "cancelled", "已取消"
