"""B1 回归保护：prod LOGGING 必须以 base 为底增量覆盖。

背景（audit-supplement-remediate B1）：原 prod.py 整体替换 LOGGING，丢失
redact_sensitive 脱敏过滤器与全部 Django_xm.apps.* logger——生产 JWT 可进日志、
应用日志全丢。本测试锁定：过滤器/iloggers/生产文件名三要素不得再退化。

直接导入 settings.prod 模块断言（不切换 DJANGO_SETTINGS_MODULE）。
"""

from django.test import SimpleTestCase

from Django_xm.settings import base as base_settings
from Django_xm.settings import prod as prod_settings

APP_LOGGERS = (
    "Django_xm.apps.chat",
    "Django_xm.apps.ai_engine",
    "Django_xm.apps.approvals",
    "Django_xm.apps.agent_hub",
    "Django_xm.apps.tools",
    "Django_xm.apps.research",
    "Django_xm.services.fastapi_service",
)


class ProdLoggingConfigTests(SimpleTestCase):
    """prod LOGGING 增量覆盖契约。"""

    def test_redact_filter_present_on_file_handler(self):
        file_handler = prod_settings.LOGGING["handlers"]["file"]
        self.assertIn("redact_sensitive", file_handler.get("filters", []))

    def test_redact_filter_factory_path(self):
        factory = prod_settings.LOGGING["filters"]["redact_sensitive"]["()"]
        self.assertEqual(factory, "Django_xm.common.logging_filters.RedactSensitiveFilter")

    def test_app_loggers_restored_with_file_handler(self):
        loggers = prod_settings.LOGGING["loggers"]
        for name in APP_LOGGERS:
            self.assertIn(name, loggers, f"生产 LOGGING 缺失 app logger: {name}")
            self.assertIn("file", loggers[name]["handlers"], f"{name} 未接 file handler")

    def test_console_keeps_filters(self):
        console = prod_settings.LOGGING["handlers"]["console"]
        self.assertIn("require_debug_true", console.get("filters", []))
        self.assertIn("redact_sensitive", console.get("filters", []))

    def test_prod_file_name_and_rotation(self):
        file_handler = prod_settings.LOGGING["handlers"]["file"]
        self.assertTrue(file_handler["filename"].replace("\\", "/").endswith("_prod.log"))
        self.assertEqual(file_handler["maxBytes"], 100 * 1024 * 1024)
        self.assertEqual(file_handler["backupCount"], 14)

    def test_prod_is_incremental_not_replacement(self):
        """prod LOGGING 必须包含 base 的全部 logger（整体替换会丢）。"""
        base_loggers = set(base_settings.LOGGING["loggers"])
        prod_loggers = set(prod_settings.LOGGING["loggers"])
        self.assertTrue(base_loggers.issubset(prod_loggers))

    def test_deep_copy_isolation(self):
        """prod 修改不得污染 base.LOGGING（deepcopy 隔离契约）。"""
        self.assertNotEqual(
            prod_settings.LOGGING["handlers"]["file"]["filename"],
            base_settings.LOGGING["handlers"]["file"]["filename"],
        )
        # base 的轮转参数保持原值（10MB/5 份），未被 prod 的 100MB/14 份覆盖
        self.assertEqual(base_settings.LOGGING["handlers"]["file"]["maxBytes"], 10 * 1024 * 1024)
        self.assertEqual(prod_settings.LOGGING["handlers"]["file"]["maxBytes"], 100 * 1024 * 1024)

    def test_prod_debug_false(self):
        self.assertFalse(prod_settings.DEBUG)
