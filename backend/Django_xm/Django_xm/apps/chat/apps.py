from django.apps import AppConfig


class ChatConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "Django_xm.apps.chat"
    verbose_name = "聊天模块"

    def ready(self):
        # dj-09：激活信号注册。此前 chat.signals 从未被任何模块 import，
        # post_save/post_delete 的会话缓存失效与 ai_data_cleanup 派发均为死代码。
        from Django_xm.apps.chat.services.session_history import filter_ghost_session_created

        # 依赖反转：向 common/realtime_events 注册 user 频道历史事件过滤器，
        # 幽灵会话校验依赖 ChatSession 模型，归位 chat 业务层。
        from Django_xm.common.realtime_events import set_user_history_filter

        from . import signals  # noqa: F401

        set_user_history_filter(filter_ghost_session_created)
