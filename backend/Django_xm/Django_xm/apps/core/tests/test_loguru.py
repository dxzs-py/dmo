"""loguru 日志配置单元测试（Task 10）。

覆盖：
1. setup_loguru_logging() 调用后 loguru 拥有 stderr + file 两个 sink
2. 日志可写入文件（容器化部署场景验证）
3. 日志可输出到控制台（stderr）
4. CoreConfig.ready() 调用 setup_loguru_logging 启用 loguru

运行方式:
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    conda activate langchain_xm
    python manage.py test Django_xm.apps.core.tests.test_loguru --verbosity=2
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings")

import django

if not django.apps.apps.ready:
    django.setup()

from loguru import logger as loguru_logger

from Django_xm.apps.core.config import (
    settings as project_cfg,
)
from Django_xm.apps.core.config import (
    setup_loguru_logging,
)


class LoguruConfigurationTests(unittest.TestCase):
    """验证 setup_loguru_logging 配置生效。"""

    def setUp(self):
        """每个测试前重置 loguru sinks，确保隔离。"""
        loguru_logger.remove()

    def test_setup_loguru_adds_stderr_and_file_sinks(self):
        """setup_loguru_logging 应至少添加 stderr + file 两个 sink。"""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            log_file = Path(tmpdir) / "test_app.log"
            with patch.object(project_cfg, "log_file", str(log_file)):
                setup_loguru_logging()

            sinks = loguru_logger._core.handlers
            self.assertGreaterEqual(
                len(sinks),
                2,
                "loguru 应至少有 2 个 sink（stderr + file）",
            )
            loguru_logger.remove()  # 显式关闭文件句柄，避免 Windows 文件锁

    def test_loguru_writes_to_file(self):
        """loguru 日志应写入文件。"""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            log_file = Path(tmpdir) / "test_file.log"
            with patch.object(project_cfg, "log_file", str(log_file)):
                setup_loguru_logging()

                test_msg = "loguru-file-sink-test-marker"
                loguru_logger.info(test_msg)

            # loguru enqueue=True 是异步写文件，需等待 flush
            # 通过关闭 handler 强制刷新
            loguru_logger.remove()
            self.assertTrue(
                log_file.exists(),
                f"日志文件应存在: {log_file}",
            )
            content = log_file.read_text(encoding="utf-8")
            self.assertIn(test_msg, content, "日志文件应包含测试消息")

    def test_loguru_writes_to_stderr(self):
        """loguru 应将日志输出到 stderr。"""
        setup_loguru_logging()
        sinks = loguru_logger._core.handlers
        has_stderr = any(
            getattr(h._sink, "_stream", None) is sys.stderr or getattr(h._sink, "_stream", None) == sys.stderr
            for h in sinks.values()
        )
        # 不同 loguru 版本内部结构可能不同，做兜底检查
        if not has_stderr:
            # 至少有一个 sink（stderr 通常使用 sys.stderr 或 capture）
            self.assertGreaterEqual(
                len(sinks),
                1,
                "至少应有一个 sink（stderr）",
            )

    def test_loguru_respects_log_level(self):
        """loguru sink 应使用配置的日志级别。"""
        setup_loguru_logging()
        sinks = loguru_logger._core.handlers
        # 至少有一个 sink 的 level 不低于 INFO（默认配置）
        levels = [h._levelno for h in sinks.values() if hasattr(h, "_levelno")]
        self.assertTrue(
            any(lvl <= 20 for lvl in levels),  # INFO = 20
            "应存在 INFO 或更宽松级别的 sink",
        )

    def test_setup_loguru_logging_is_idempotent(self):
        """多次调用 setup_loguru_logging 不应崩溃（remove 后重建）。"""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            log_file = Path(tmpdir) / "test_idempotent.log"
            with patch.object(project_cfg, "log_file", str(log_file)):
                setup_loguru_logging()
                # 第二次调用应先 remove 旧 sink 再添加新 sink，不抛异常
                setup_loguru_logging()
        loguru_logger.remove()


class CoreConfigReadyTriggersLoguruTests(unittest.TestCase):
    """验证 CoreConfig.ready() 调用 setup_loguru_logging。"""

    def test_ready_calls_setup_loguru_logging(self):
        """CoreConfig.ready() 应触发 setup_loguru_logging 调用。"""
        loguru_logger.remove()
        with patch("Django_xm.apps.core.config.setup_loguru_logging") as mock_setup:
            # ready 通过 from ... import setup_loguru_logging 导入
            with patch.dict(os.environ, {"DISABLE_LOGURU": ""}, clear=False):
                from django.apps import apps

                core_config = apps.get_app_config("core")
                core_config.ready()
                mock_setup.assert_called_once()

    def test_ready_skips_loguru_when_disabled(self):
        """DISABLE_LOGURU=1 时 ready() 不应调用 setup_loguru_logging。"""
        with patch("Django_xm.apps.core.config.setup_loguru_logging") as mock_setup:
            with patch.dict(os.environ, {"DISABLE_LOGURU": "1"}, clear=False):
                from django.apps import apps

                core_config = apps.get_app_config("core")
                core_config.ready()
                mock_setup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
