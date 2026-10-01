from django.apps import AppConfig


class UsersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "Django_xm.apps.users"
    verbose_name = "用户管理"

    def ready(self):
        # 本 app 无信号处理，无需注册。
        # 历史：曾有 signals.py（监听 chat 的 post_save 更新 User.total_* 计数器）
        # 因从未被任何模块 import / ready 未激活而成为死代码，
        # 相关死字段已于 2026-10-01 一并移除（统计改由 analytics 实时聚合）。
        # 若未来新增 signals.py，必须在此显式 import 激活（参照 core/apps.py）。
        pass
