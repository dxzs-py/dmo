from django.apps import AppConfig


class ChatConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "Django_xm.apps.chat"
    verbose_name = "聊天模块"

    def ready(self):
        # dj-09：激活信号注册。此前 chat.signals 从未被任何模块 import，
        # post_save/post_delete 的会话缓存失效与 ai_data_cleanup 派发均为死代码。
        from . import signals  # noqa: F401
