"""tool_call_lifecycle._normalize_risk_value 单元测试。

锁定合并后的风险字段归一化契约（risk_ceiling / risk_level 共用）：
1. 枚举值 / 合法字符串 → 返回字符串本身；
2. None / 空串 / 非法字符串 / 非字符串非枚举 → None（非法值记 warning）；
3. field_name 仅用于日志定位，不影响返回值。
"""

import logging

from django.test import SimpleTestCase

from Django_xm.common.risk_levels import RiskLevel
from Django_xm.common.tool_call_lifecycle import _normalize_risk_value

LIFECYCLE_LOGGER = "Django_xm.common.tool_call_lifecycle"


class NormalizeRiskValueTest(SimpleTestCase):
    def test_enum_value_returns_str(self):
        self.assertEqual(_normalize_risk_value(RiskLevel.SAFE, "risk_ceiling"), "safe")
        self.assertEqual(_normalize_risk_value(RiskLevel.CONTROLLED, "risk_level"), "controlled")
        self.assertEqual(_normalize_risk_value(RiskLevel.HIGH, "risk_level"), "high")

    def test_valid_string_passthrough(self):
        self.assertEqual(_normalize_risk_value("controlled", "risk_ceiling"), "controlled")

    def test_none_returns_none(self):
        self.assertIsNone(_normalize_risk_value(None, "risk_ceiling"))

    def test_empty_string_returns_none(self):
        self.assertIsNone(_normalize_risk_value("", "risk_level"))

    def test_invalid_string_returns_none_and_warns(self):
        with self.assertLogs(LIFECYCLE_LOGGER, level="WARNING") as captured:
            self.assertIsNone(_normalize_risk_value("dangerous", "risk_level"))
        self.assertIn("risk_level", captured.output[0])

    def test_non_string_non_enum_returns_none(self):
        self.assertIsNone(_normalize_risk_value(123, "risk_ceiling"))
