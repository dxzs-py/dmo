"""配置收编测试（ARCH-05 / 波次 3C）

验证 SANDBOX_* / ATTACHMENT_* 12 键与 MCP_SSRF_ALLOW_PRIVATE_ADDRESSES
经 ProjectSettings（apps/core/config.py）单源读取：
1. env 变量 → ProjectSettings 字段解析（类型 + 值，键名一致映射）；
2. env 缺省（且不给 .env 文件）→ Pydantic 默认值与原 os.environ 直读默认一致；
3. Django settings 通道（base.py 装配）与 project_cfg 真相源等价。
"""

from unittest import mock

from django.conf import settings as django_settings
from django.test import SimpleTestCase

from Django_xm.apps.core.config import ProjectSettings, settings as project_cfg

# 12 收编键：env 键名 -> (ProjectSettings 字段名, 期望默认值)
SANDBOX_ATTACHMENT_KEYS = {
    "SANDBOX_ENABLED": ("sandbox_enabled", False),
    "SANDBOX_IMAGE": ("sandbox_image", "python:3.11-slim"),
    "SANDBOX_MEMORY": ("sandbox_memory", "512m"),
    "SANDBOX_CPUS": ("sandbox_cpus", 1),
    "SANDBOX_NETWORK": ("sandbox_network", "none"),
    "SANDBOX_SESSION_TIMEOUT": ("sandbox_session_timeout", 3600),
    "ATTACHMENT_DEFAULT_RETENTION_DAYS": ("attachment_default_retention_days", 30),
    "ATTACHMENT_CLEANUP_HOUR": ("attachment_cleanup_hour", 3),
    "ATTACHMENT_STORAGE_WARNING_THRESHOLD": ("attachment_storage_warning_threshold", 80.0),
    "ATTACHMENT_STORAGE_CRITICAL_THRESHOLD": ("attachment_storage_critical_threshold", 95.0),
    "ATTACHMENT_DEDUP_ENABLED": ("attachment_dedup_enabled", True),
    "ATTACHMENT_MAX_TOTAL_SIZE_MB": ("attachment_max_total_size_mb", 5120),
}


class ProjectSettingsEnvParsingTests(SimpleTestCase):
    """env → ProjectSettings 字段解析（键名一致映射，类型正确）。"""

    def test_env_overrides_are_parsed_with_correct_types(self):
        # env 变量优先级高于 .env 文件（pydantic-settings 语义），
        # 显式 setenv 后新建实例即可隔离真实 .env 的影响
        env_overrides = {
            "SANDBOX_ENABLED": "true",
            "SANDBOX_IMAGE": "python:3.12-slim",
            "SANDBOX_MEMORY": "1g",
            "SANDBOX_CPUS": "2",
            "SANDBOX_NETWORK": "bridge",
            "SANDBOX_SESSION_TIMEOUT": "7200",
            "ATTACHMENT_DEFAULT_RETENTION_DAYS": "90",
            "ATTACHMENT_CLEANUP_HOUR": "5",
            "ATTACHMENT_STORAGE_WARNING_THRESHOLD": "70.5",
            "ATTACHMENT_STORAGE_CRITICAL_THRESHOLD": "90.5",
            "ATTACHMENT_DEDUP_ENABLED": "false",
            "ATTACHMENT_MAX_TOTAL_SIZE_MB": "10240",
        }
        with mock.patch.dict("os.environ", env_overrides):
            cfg = ProjectSettings(_env_file=None)

        self.assertTrue(cfg.sandbox_enabled)
        self.assertEqual(cfg.sandbox_image, "python:3.12-slim")
        self.assertEqual(cfg.sandbox_memory, "1g")
        self.assertEqual(cfg.sandbox_cpus, 2)
        self.assertEqual(cfg.sandbox_network, "bridge")
        self.assertEqual(cfg.sandbox_session_timeout, 7200)
        self.assertEqual(cfg.attachment_default_retention_days, 90)
        self.assertEqual(cfg.attachment_cleanup_hour, 5)
        self.assertEqual(cfg.attachment_storage_warning_threshold, 70.5)
        self.assertEqual(cfg.attachment_storage_critical_threshold, 90.5)
        self.assertFalse(cfg.attachment_dedup_enabled)
        self.assertEqual(cfg.attachment_max_total_size_mb, 10240)

    def test_mcp_ssrf_env_bridge_defaults_false_and_parses_true(self):
        # 缺省（不读 .env 文件）→ False
        cfg = ProjectSettings(_env_file=None)
        self.assertFalse(cfg.mcp_ssrf_allow_private_addresses)

        # env 显式 true → True（env 桥生效）
        with mock.patch.dict("os.environ", {"MCP_SSRF_ALLOW_PRIVATE_ADDRESSES": "true"}):
            cfg_on = ProjectSettings(_env_file=None)
        self.assertTrue(cfg_on.mcp_ssrf_allow_private_addresses)

    def test_defaults_match_legacy_os_environ_defaults(self):
        # env 全部清空 + 不读 .env 文件 → 默认值必须与原 os.environ 直读默认一致
        with mock.patch.dict("os.environ", {}, clear=True):
            cfg = ProjectSettings(_env_file=None)

        for env_key, (field_name, default) in SANDBOX_ATTACHMENT_KEYS.items():
            self.assertEqual(
                getattr(cfg, field_name),
                default,
                msg=f"默认值漂移: {env_key} -> {field_name}",
            )
        self.assertFalse(cfg.mcp_ssrf_allow_private_addresses)


class DjangoSettingsChannelEquivalenceTests(SimpleTestCase):
    """base.py Django settings 通道与 project_cfg 真相源等价（12 键 + SSRF 桥）。"""

    def test_attachment_settings_attributes_come_from_project_cfg(self):
        self.assertEqual(django_settings.ATTACHMENT_DEFAULT_RETENTION_DAYS, project_cfg.attachment_default_retention_days)
        self.assertEqual(django_settings.ATTACHMENT_CLEANUP_HOUR, project_cfg.attachment_cleanup_hour)
        self.assertEqual(
            django_settings.ATTACHMENT_STORAGE_WARNING_THRESHOLD,
            project_cfg.attachment_storage_warning_threshold,
        )
        self.assertEqual(
            django_settings.ATTACHMENT_STORAGE_CRITICAL_THRESHOLD,
            project_cfg.attachment_storage_critical_threshold,
        )
        self.assertEqual(django_settings.ATTACHMENT_DEDUP_ENABLED, project_cfg.attachment_dedup_enabled)
        self.assertEqual(django_settings.ATTACHMENT_MAX_TOTAL_SIZE_MB, project_cfg.attachment_max_total_size_mb)

    def test_sandbox_config_values_come_from_project_cfg(self):
        sandbox = django_settings.SANDBOX_CONFIG
        # 结构键名保持不变（消费方 common/sandbox/executor.py 依赖）
        for key in ("ENABLED", "DOCKER_IMAGE", "MEMORY_LIMIT", "CPU_LIMIT", "NETWORK_MODE", "SESSION_TIMEOUT", "WORK_DIR_MOUNT"):
            self.assertIn(key, sandbox)
        self.assertEqual(sandbox["DOCKER_IMAGE"], project_cfg.sandbox_image)
        self.assertEqual(sandbox["MEMORY_LIMIT"], project_cfg.sandbox_memory)
        self.assertEqual(sandbox["CPU_LIMIT"], project_cfg.sandbox_cpus)
        self.assertEqual(sandbox["NETWORK_MODE"], project_cfg.sandbox_network)
        self.assertEqual(sandbox["SESSION_TIMEOUT"], project_cfg.sandbox_session_timeout)
        # ENABLED：test.py（REL-13）会在测试设置中强制覆写为 False，
        # 故此处断言布尔类型且取值来自 project_cfg 或测试覆写
        self.assertIsInstance(sandbox["ENABLED"], bool)
        self.assertIn(sandbox["ENABLED"], (project_cfg.sandbox_enabled, False))

    def test_mcp_ssrf_exempt_setting_available_on_django_settings(self):
        # common/net/guards.py 经 getattr(settings, SSRF_EXEMPT_SETTING, False) 读取
        self.assertEqual(
            getattr(django_settings, "MCP_SSRF_ALLOW_PRIVATE_ADDRESSES", False),
            project_cfg.mcp_ssrf_allow_private_addresses,
        )
        self.assertFalse(project_cfg.mcp_ssrf_allow_private_addresses)
