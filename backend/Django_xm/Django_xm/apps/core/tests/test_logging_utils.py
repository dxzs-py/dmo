"""B2 回归保护：get_logger 归流 Django LOGGING 管线。

契约（audit-supplement-remediate B2）：
1. get_logger 不再自建 handler、propagate=True（旧实现自建 StreamHandler +
   propagate=False 使 Agent 主链日志脱离脱敏与文件管线）；
2. base LOGGING 存在 "Django_xm" 兜底 logger（console+file）；
3. 未枚举命名空间的记录能沿层级到达 "Django_xm" handler。
"""

import logging

from django.test import SimpleTestCase

from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.settings import base as base_settings


class GetLoggerWiringTests(SimpleTestCase):
    def test_no_own_handler_and_propagate(self):
        name = "Django_xm.apps.core.tests.b2_dummy"
        logger = get_logger(name)
        self.assertEqual(logger.handlers, [])
        self.assertTrue(logger.propagate)

    def test_level_follows_settings(self):
        logger = get_logger("Django_xm.apps.core.tests.b2_level")
        expected = getattr(logging, getattr(base_settings, "LOG_LEVEL", "INFO"), logging.INFO)
        # core config 默认 log_level=INFO
        self.assertEqual(logger.level, expected)

    def test_base_logging_has_django_xm_fallback(self):
        fallback = base_settings.LOGGING["loggers"]["Django_xm"]
        self.assertIn("console", fallback["handlers"])
        self.assertIn("file", fallback["handlers"])
        self.assertFalse(fallback["propagate"])

    def test_record_reaches_fallback_handler(self):
        """未枚举命名空间的记录应沿层级到达 Django_xm 兜底 handler。"""
        records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        capture = _Capture(level=logging.DEBUG)
        fallback_logger = logging.getLogger("Django_xm")
        old_level = fallback_logger.level
        fallback_logger.addHandler(capture)
        fallback_logger.setLevel(logging.DEBUG)
        try:
            get_logger("Django_xm.apps.core.tests.b2_flow").info("B2 wiring check")
        finally:
            fallback_logger.removeHandler(capture)
            fallback_logger.setLevel(old_level)
        self.assertTrue(any(r.getMessage() == "B2 wiring check" for r in records))

    def test_enumerated_child_no_double_logging(self):
        """显式枚举的子 logger（propagate=False）不会重复到达兜底层。"""
        records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        capture = _Capture(level=logging.DEBUG)
        fallback_logger = logging.getLogger("Django_xm")
        fallback_logger.addHandler(capture)
        try:
            # chat logger 在 LOGGING 中 propagate=False，其记录不应到达 Django_xm
            logging.getLogger("Django_xm.apps.chat.tests.b2_no_dup").info("should not bubble")
        finally:
            fallback_logger.removeHandler(capture)
        self.assertFalse(any(r.getMessage() == "should not bubble" for r in records))
