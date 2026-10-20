"""token budget 分区重构回归测试（"system" 单键拆分为 system_static/system_dynamic）。

覆盖：
- 全部内置模板使用拆分后的 7 个分区键，旧 "system" 键零残留，比例总和为 1.0；
- allocate 产物的分区预算与模板比例严格对应（含 10% 输出预留）；
- 分区配额在 allocate 时按比例固定：MCP 工具增多（tools/system_static 用量
  上涨）不挤压 history 预算，各分区超限告警相互隔离；
- 自定义模板：新键模板生效；旧键模板经 _resolve_template 唯一门禁拒绝回退
  default（禁止兼容读取）；
- trim_messages_to_budget 以新分区体系下的 history 预算为裁剪目标仍正常工作。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python -m pytest Django_xm/apps/context_manager/tests/test_token_budget_sections.py -q
"""

import os
import unittest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from Django_xm.apps.context_manager.services.token_budget import (
    BUDGET_TEMPLATES,
    OUTPUT_RESERVE_RATIO,
    VALID_SECTIONS,
    TokenBudgetManager,
    trim_messages_to_budget,
)

# 不在 TokenEstimator 模型表内的名字：绕过自定义表/内置表命中，
# 确定性回退默认上限 128000（未知模型 warning 为预期日志）
_UNKNOWN_MODEL = "no-such-model-xyz-128k"
_MODEL_LIMIT = 128000
_EXPECTED_SECTIONS = {
    "system_static",
    "system_dynamic",
    "memory",
    "tools",
    "history",
    "state",
    "query",
}


def _total_budget() -> int:
    return int(_MODEL_LIMIT * (1 - OUTPUT_RESERVE_RATIO))


class TemplateSectionKeysTests(unittest.TestCase):
    """内置模板分区键与比例完整性。"""

    def test_all_templates_use_split_keys(self):
        """所有内置模板必须使用拆分后的分区键，旧 "system" 键禁止残留。"""
        for name, ratios in BUDGET_TEMPLATES.items():
            with self.subTest(template=name):
                self.assertEqual(set(ratios.keys()), _EXPECTED_SECTIONS)
                self.assertNotIn("system", ratios)
                self.assertAlmostEqual(sum(ratios.values()), 1.0, places=6)

    def test_valid_sections_derived_from_default(self):
        """VALID_SECTIONS 由 default 模板派生，含两个 system 拆分键。"""
        self.assertEqual(VALID_SECTIONS, frozenset(_EXPECTED_SECTIONS))
        self.assertIn("system_static", VALID_SECTIONS)
        self.assertIn("system_dynamic", VALID_SECTIONS)


class AllocateSectionsTests(unittest.TestCase):
    """allocate 产物与模板比例的对应关系。"""

    def test_allocate_creates_split_sections(self):
        mgr = TokenBudgetManager()
        mgr.allocate(_UNKNOWN_MODEL)
        self.assertEqual(mgr.model_limit, _MODEL_LIMIT)
        usage = mgr.get_usage()
        self.assertEqual(set(usage.keys()), _EXPECTED_SECTIONS)
        total = _total_budget()
        self.assertEqual(usage["system_static"]["budget"], int(total * 0.05))
        self.assertEqual(usage["system_dynamic"]["budget"], int(total * 0.03))
        self.assertEqual(usage["tools"]["budget"], int(total * 0.08))
        self.assertEqual(usage["history"]["budget"], int(total * 0.59))

    def test_check_unknown_section_reports_over(self):
        """未分配分区查询返回全量超限标记（防御口径不变）。"""
        mgr = TokenBudgetManager()
        mgr.allocate(_UNKNOWN_MODEL)
        result = mgr.check("nonexistent", 100)
        self.assertFalse(result.within_budget)
        self.assertEqual(result.over_by, 100)


class SectionIndependenceTests(unittest.TestCase):
    """分区独立性：用量增长互不挤占，超限告警相互隔离。"""

    def test_mcp_tools_growth_keeps_history_budget(self):
        """MCP 工具增多（tools/system_static 用量上涨）不得改变 history 预算。

        分区配额在 allocate 时按模板比例固定，用量只影响自身 remaining，
        这是"静态段扩容不挤压 history"的结构保证。
        """
        mgr = TokenBudgetManager()
        mgr.allocate(_UNKNOWN_MODEL)
        history_budget_before = mgr.get_section_budget("history")

        # 模拟 MCP server 增多：静态段与 tools 大量记账
        mgr.record_usage("system_static", 5000)
        mgr.record_usage("tools", 9000)

        self.assertEqual(mgr.get_section_budget("history"), history_budget_before)
        usage = mgr.get_usage()
        self.assertEqual(usage["history"]["budget"], history_budget_before)
        self.assertEqual(usage["history"]["used"], 0)
        self.assertEqual(usage["history"]["remaining"], history_budget_before)

    def test_over_alarm_isolated_per_section(self):
        """单分区超限只影响该分区的判定结果。"""
        mgr = TokenBudgetManager()
        mgr.allocate(_UNKNOWN_MODEL)

        dynamic_budget = mgr.get_section_budget("system_dynamic")
        over_result = mgr.record_usage("system_dynamic", dynamic_budget + 100)
        self.assertFalse(over_result.within_budget)
        self.assertEqual(over_result.over_by, 100)

        # 其他分区不受牵连
        static_check = mgr.check("system_static", 1000)
        self.assertTrue(static_check.within_budget)
        history_check = mgr.check("history", 1000)
        self.assertTrue(history_check.within_budget)
        usage = mgr.get_usage()
        self.assertGreater(usage["system_dynamic"]["used"], usage["system_dynamic"]["budget"])
        self.assertEqual(usage["system_static"]["used"], 0)
        self.assertEqual(usage["history"]["used"], 0)

    def test_static_and_dynamic_budgets_are_disjoint(self):
        """system_static 与 system_dynamic 是两类独立预算，不共享配额。"""
        mgr = TokenBudgetManager()
        mgr.allocate(_UNKNOWN_MODEL)
        total = _total_budget()
        static_budget = mgr.get_section_budget("system_static")
        dynamic_budget = mgr.get_section_budget("system_dynamic")
        self.assertEqual(static_budget, int(total * 0.05))
        self.assertEqual(dynamic_budget, int(total * 0.03))
        # 动态段记满不影响静态段可用额度
        mgr.record_usage("system_dynamic", dynamic_budget)
        self.assertEqual(mgr.check("system_static", static_budget).within_budget, True)


class CustomTemplateTests(unittest.TestCase):
    """自定义模板的新键生效与旧键拒绝。"""

    def setUp(self):
        self._saved = dict(TokenBudgetManager._custom_templates)

    def tearDown(self):
        TokenBudgetManager._custom_templates = self._saved

    def test_custom_template_with_new_keys_accepted(self):
        """新键自定义模板生效，静态段扩容按模板比例兑现（history 相应收缩）。"""
        TokenBudgetManager._custom_templates = {
            "mcp_heavy": {
                "system_static": 0.20,
                "system_dynamic": 0.05,
                "memory": 0.10,
                "tools": 0.15,
                "history": 0.35,
                "state": 0.05,
                "query": 0.10,
            }
        }
        mgr = TokenBudgetManager()
        mgr.allocate(_UNKNOWN_MODEL, template="mcp_heavy")
        total = _total_budget()
        self.assertEqual(mgr.get_section_budget("system_static"), int(total * 0.20))
        self.assertEqual(mgr.get_section_budget("history"), int(total * 0.35))
        # 对照：模板化扩容（而非用量增长）才会改变 history 预算
        self.assertLess(
            mgr.get_section_budget("history"),
            int(total * 0.59),
        )

    def test_old_system_key_template_rejected(self):
        """旧 "system" 键模板经 _resolve_template 唯一门禁拒绝，回退 default。"""
        TokenBudgetManager._custom_templates = {
            "legacy": {
                "system": 0.15,
                "memory": 0.10,
                "tools": 0.10,
                "history": 0.50,
                "state": 0.05,
                "query": 0.10,
            }
        }
        mgr = TokenBudgetManager()
        mgr.allocate(_UNKNOWN_MODEL, template="legacy")
        total = _total_budget()
        # 回退 default：出现的是拆分键而非旧键，比例与 default 一致
        usage = mgr.get_usage()
        self.assertEqual(set(usage.keys()), _EXPECTED_SECTIONS)
        self.assertNotIn("system", usage)
        self.assertEqual(usage["system_static"]["budget"], int(total * 0.05))
        self.assertEqual(usage["history"]["budget"], int(total * 0.59))


class TrimWithHistoryBudgetTests(unittest.TestCase):
    """A12 最终裁剪兜底与新分区体系（history 预算）的衔接。"""

    def test_trim_to_history_section_budget(self):
        mgr = TokenBudgetManager()
        mgr.allocate(_UNKNOWN_MODEL)
        history_budget = mgr.get_section_budget("history")

        # 50 条 × ~4500 tokens 的超预算历史（无受保护消息），总量必然超过 history 预算
        messages = [{"role": "user", "content": "长消息" * 1500} for _ in range(50)]
        trimmed, result = trim_messages_to_budget(messages, history_budget, keep_recent=6, model_name="")

        self.assertTrue(result.trimmed)
        self.assertGreater(result.removed_count, 0)
        self.assertLessEqual(result.trimmed_tokens, history_budget)
        # 最近 keep_recent 条始终保留
        self.assertGreaterEqual(len(trimmed), 6)
        self.assertEqual(trimmed[-6:], messages[-6:])


if __name__ == "__main__":
    unittest.main()
