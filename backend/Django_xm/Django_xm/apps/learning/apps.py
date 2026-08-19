from django.apps import AppConfig


class LearningConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "Django_xm.apps.learning"
    verbose_name = "学习工作流模块"

    def ready(self):
        # 注册 WorkflowSession 实时事件信号（task_created/task_status_changed/task_deleted）
        from . import signals  # noqa: F401
