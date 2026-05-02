from django.apps import AppConfig


class ChatConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'Django_xm.apps.chat'
    verbose_name = '聊天模块'

    def ready(self):
        import Django_xm.apps.chat.signals
