"""统一 ResilienceConfig 单元测试

验证合并后的 ResilienceConfig 同时承载 agent 执行层与模型调用层配置，
默认值与原有两个独立类一致，且支持 Django settings 覆盖两组字段。

运行方式:
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    conda activate langchain_xm
    python -m pytest Django_xm/apps/ai_engine/tests/test_unified_resilience_config.py -v
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

# Django 环境初始化（兼容 pytest 和 unittest 直接运行）
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django
import django.apps

if not django.apps.apps.ready:
    django.setup()

from Django_xm.apps.agent_hub.services.agent_resilience import (
    ResilienceConfig,
    get_resilience_config,
)

# ============================================================================
# 默认值测试
# ============================================================================


class UnifiedResilienceConfigDefaultsTestCase(unittest.TestCase):
    """验证统一配置的默认值与原有两个独立类一致"""

    def test_default_instance_contains_all_fields(self):
        """默认实例包含 agent 层 + 模型层所有字段"""
        config = ResilienceConfig()

        # agent 执行层字段
        self.assertTrue(hasattr(config, "max_retries"))
        self.assertTrue(hasattr(config, "initial_retry_interval"))
        self.assertTrue(hasattr(config, "max_retry_interval"))
        self.assertTrue(hasattr(config, "retry_backoff_factor"))
        self.assertTrue(hasattr(config, "soft_timeout"))
        self.assertTrue(hasattr(config, "hard_timeout"))

        # 模型调用层字段
        self.assertTrue(hasattr(config, "backoff_seconds"))
        self.assertTrue(hasattr(config, "circuit_breaker_threshold"))
        self.assertTrue(hasattr(config, "circuit_breaker_cooldown"))

    def test_agent_layer_defaults_match_original(self):
        """agent 执行层默认值与原 agent_resilience.ResilienceConfig 一致"""
        config = ResilienceConfig()
        self.assertEqual(config.max_retries, 3)
        self.assertEqual(config.initial_retry_interval, 2.0)
        self.assertEqual(config.max_retry_interval, 30.0)
        self.assertEqual(config.retry_backoff_factor, 2.0)
        self.assertIsNone(config.soft_timeout)
        self.assertIsNone(config.hard_timeout)

    def test_model_layer_defaults_match_original(self):
        """模型调用层默认值与原 resilient_invoker.ResilienceConfig 一致"""
        config = ResilienceConfig()
        self.assertEqual(config.backoff_seconds, (0.5, 1.0, 2.0))
        self.assertEqual(config.circuit_breaker_threshold, 3)
        self.assertEqual(config.circuit_breaker_cooldown, 30.0)

    def test_backoff_seconds_is_tuple(self):
        """backoff_seconds 默认为 tuple 类型"""
        config = ResilienceConfig()
        self.assertIsInstance(config.backoff_seconds, tuple)


# ============================================================================
# get_resilience_config 测试
# ============================================================================


class GetResilienceConfigTestCase(unittest.TestCase):
    """验证 get_resilience_config() 返回包含所有字段的实例"""

    def test_returns_instance_with_all_fields(self):
        """get_resilience_config 返回的实例包含所有字段"""
        config = get_resilience_config()
        self.assertIsInstance(config, ResilienceConfig)

        # agent 层
        self.assertEqual(config.max_retries, 3)
        self.assertEqual(config.initial_retry_interval, 2.0)
        self.assertEqual(config.max_retry_interval, 30.0)
        self.assertEqual(config.retry_backoff_factor, 2.0)
        self.assertIsNone(config.soft_timeout)
        self.assertIsNone(config.hard_timeout)

        # 模型层
        self.assertEqual(config.backoff_seconds, (0.5, 1.0, 2.0))
        self.assertEqual(config.circuit_breaker_threshold, 3)
        self.assertEqual(config.circuit_breaker_cooldown, 30.0)

    def test_overrides_apply_to_both_layers(self):
        """overrides 同时覆盖 agent 层与模型层字段"""
        config = get_resilience_config(
            max_retries=5,
            initial_retry_interval=4.0,
            backoff_seconds=(1.0, 2.0, 4.0),
            circuit_breaker_threshold=7,
            circuit_breaker_cooldown=60.0,
        )
        self.assertEqual(config.max_retries, 5)
        self.assertEqual(config.initial_retry_interval, 4.0)
        self.assertEqual(config.backoff_seconds, (1.0, 2.0, 4.0))
        self.assertEqual(config.circuit_breaker_threshold, 7)
        self.assertEqual(config.circuit_breaker_cooldown, 60.0)


# ============================================================================
# Django settings 覆盖测试
# ============================================================================


class DjangoSettingsOverrideTestCase(unittest.TestCase):
    """验证 Django settings 能覆盖两组字段"""

    def test_settings_override_agent_layer(self):
        """settings 覆盖 agent 执行层字段"""
        with patch("django.conf.settings") as mock_settings:
            mock_settings.AGENT_MAX_RETRIES = 5
            mock_settings.AGENT_INITIAL_RETRY_INTERVAL = 1.5
            mock_settings.AGENT_MAX_RETRY_INTERVAL = 20.0
            mock_settings.AGENT_RETRY_BACKOFF_FACTOR = 1.5
            mock_settings.AGENT_SOFT_TIMEOUT = 10.0
            mock_settings.AGENT_HARD_TIMEOUT = 30.0
            # 屏蔽模型层字段，避免 MagicMock 自动属性污染
            mock_settings.MODEL_BACKOFF_SECONDS = (0.5, 1.0, 2.0)
            mock_settings.MODEL_CIRCUIT_BREAKER_THRESHOLD = 3
            mock_settings.MODEL_CIRCUIT_BREAKER_COOLDOWN = 30.0

            config = get_resilience_config()
            self.assertEqual(config.max_retries, 5)
            self.assertEqual(config.initial_retry_interval, 1.5)
            self.assertEqual(config.max_retry_interval, 20.0)
            self.assertEqual(config.retry_backoff_factor, 1.5)
            self.assertEqual(config.soft_timeout, 10.0)
            self.assertEqual(config.hard_timeout, 30.0)

    def test_settings_override_model_layer(self):
        """settings 覆盖模型调用层字段"""
        with patch("django.conf.settings") as mock_settings:
            mock_settings.MODEL_BACKOFF_SECONDS = [1.0, 2.0, 4.0]
            mock_settings.MODEL_CIRCUIT_BREAKER_THRESHOLD = 5
            mock_settings.MODEL_CIRCUIT_BREAKER_COOLDOWN = 60.0
            # 屏蔽 agent 层字段，避免 MagicMock 自动属性污染
            mock_settings.AGENT_MAX_RETRIES = 3
            mock_settings.AGENT_INITIAL_RETRY_INTERVAL = 2.0
            mock_settings.AGENT_MAX_RETRY_INTERVAL = 30.0
            mock_settings.AGENT_RETRY_BACKOFF_FACTOR = 2.0
            mock_settings.AGENT_SOFT_TIMEOUT = None
            mock_settings.AGENT_HARD_TIMEOUT = None

            config = get_resilience_config()
            self.assertEqual(config.backoff_seconds, (1.0, 2.0, 4.0))
            self.assertEqual(config.circuit_breaker_threshold, 5)
            self.assertEqual(config.circuit_breaker_cooldown, 60.0)

    def test_settings_override_both_layers(self):
        """settings 同时覆盖 agent 层与模型层字段"""
        with patch("django.conf.settings") as mock_settings:
            # agent 层
            mock_settings.AGENT_MAX_RETRIES = 7
            mock_settings.AGENT_INITIAL_RETRY_INTERVAL = 3.0
            mock_settings.AGENT_MAX_RETRY_INTERVAL = 45.0
            mock_settings.AGENT_RETRY_BACKOFF_FACTOR = 2.5
            mock_settings.AGENT_SOFT_TIMEOUT = 15.0
            mock_settings.AGENT_HARD_TIMEOUT = 45.0
            # 模型层
            mock_settings.MODEL_BACKOFF_SECONDS = [2.0, 4.0, 8.0]
            mock_settings.MODEL_CIRCUIT_BREAKER_THRESHOLD = 10
            mock_settings.MODEL_CIRCUIT_BREAKER_COOLDOWN = 90.0

            config = get_resilience_config()
            # agent 层
            self.assertEqual(config.max_retries, 7)
            self.assertEqual(config.initial_retry_interval, 3.0)
            self.assertEqual(config.max_retry_interval, 45.0)
            self.assertEqual(config.retry_backoff_factor, 2.5)
            self.assertEqual(config.soft_timeout, 15.0)
            self.assertEqual(config.hard_timeout, 45.0)
            # 模型层
            self.assertEqual(config.backoff_seconds, (2.0, 4.0, 8.0))
            self.assertEqual(config.circuit_breaker_threshold, 10)
            self.assertEqual(config.circuit_breaker_cooldown, 90.0)

    def test_overrides_take_precedence_over_settings(self):
        """overrides 优先级高于 settings"""
        with patch("django.conf.settings") as mock_settings:
            mock_settings.AGENT_MAX_RETRIES = 5
            mock_settings.MODEL_CIRCUIT_BREAKER_THRESHOLD = 10
            # 屏蔽其他字段
            mock_settings.AGENT_INITIAL_RETRY_INTERVAL = 2.0
            mock_settings.AGENT_MAX_RETRY_INTERVAL = 30.0
            mock_settings.AGENT_RETRY_BACKOFF_FACTOR = 2.0
            mock_settings.AGENT_SOFT_TIMEOUT = None
            mock_settings.AGENT_HARD_TIMEOUT = None
            mock_settings.MODEL_BACKOFF_SECONDS = (0.5, 1.0, 2.0)
            mock_settings.MODEL_CIRCUIT_BREAKER_COOLDOWN = 30.0

            config = get_resilience_config(
                max_retries=99,
                circuit_breaker_threshold=99,
            )
            self.assertEqual(config.max_retries, 99)
            self.assertEqual(config.circuit_breaker_threshold, 99)


# ============================================================================
# 兼容性测试
# ============================================================================


class BackwardCompatibilityTestCase(unittest.TestCase):
    """验证统一配置对原有使用方式的向后兼容性"""

    def test_agent_layer_partial_construction(self):
        """仅传入 agent 层字段仍可构造（模型层使用默认值）"""
        config = ResilienceConfig(
            max_retries=5,
            initial_retry_interval=1.0,
            soft_timeout=10.0,
        )
        self.assertEqual(config.max_retries, 5)
        self.assertEqual(config.initial_retry_interval, 1.0)
        self.assertEqual(config.soft_timeout, 10.0)
        # 模型层默认值
        self.assertEqual(config.backoff_seconds, (0.5, 1.0, 2.0))
        self.assertEqual(config.circuit_breaker_threshold, 3)
        self.assertEqual(config.circuit_breaker_cooldown, 30.0)

    def test_model_layer_partial_construction(self):
        """仅传入模型层字段仍可构造（agent 层使用默认值）"""
        config = ResilienceConfig(
            backoff_seconds=(1.0, 2.0),
            circuit_breaker_threshold=5,
            circuit_breaker_cooldown=60.0,
        )
        self.assertEqual(config.backoff_seconds, (1.0, 2.0))
        self.assertEqual(config.circuit_breaker_threshold, 5)
        self.assertEqual(config.circuit_breaker_cooldown, 60.0)
        # agent 层默认值
        self.assertEqual(config.max_retries, 3)
        self.assertEqual(config.initial_retry_interval, 2.0)
        self.assertEqual(config.max_retry_interval, 30.0)
        self.assertEqual(config.retry_backoff_factor, 2.0)
        self.assertIsNone(config.soft_timeout)
        self.assertIsNone(config.hard_timeout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
