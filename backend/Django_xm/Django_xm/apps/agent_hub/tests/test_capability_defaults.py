"""能力默认表（AGENT_CAPABILITIES_DEFAULT）单一权威来源同步断言测试（ARCH-06）。

背景：历史上 agent_hub/config.py 与 ai_engine/config.py 各存一份默认能力表
且已漂移（ai_engine 版缺 rag/safe_rag），导致 registry fallback 在不同
加载路径得到不同能力集合。收敛后唯一权威表位于
ai_engine/config.py 的 Settings.AGENT_CAPABILITIES_DEFAULT。

本测试放在 agent_hub（依赖方向 agent_hub→ai_engine 合法；
ai_engine→agent_hub 被 importlinter 契约禁止，故不能放 ai_engine 侧）。

覆盖：
1. 权威表引用的每个 capability 键均已在 CapabilityRegistry 注册（无悬空引用；
   反方向不成立——groq_compat 已注册但不属于任何默认表，属正常现象）；
2. agent_hub 侧引用与 ai_engine 侧为同一对象（双源硬编码已删除）；
3. registry fallback 与权威表逐键一致，未知 agent_type 返回空列表；
4. AgentConfig.resolve_defaults 对 rag/safe_rag 也能解析出默认能力
   （回归审计发现的漂移路径）。

运行：
    python manage.py test Django_xm.apps.agent_hub.tests.test_capability_defaults
"""

import unittest

from Django_xm.apps.ai_engine import config as ai_engine_config
from Django_xm.apps.ai_engine.capabilities.registry import registry

from .. import config as agent_hub_config


class CapabilityDefaultsSyncTests(unittest.TestCase):
    """默认能力表双源合并同步断言。"""

    def test_authoritative_table_has_rag_and_safe_rag(self):
        """权威表必须包含 rag / safe_rag 键（回归本次审计发现的漂移点）。"""
        table = ai_engine_config.settings.AGENT_CAPABILITIES_DEFAULT
        self.assertIn("rag", table)
        self.assertIn("safe_rag", table)

    def test_referenced_capabilities_are_registered(self):
        """权威表引用的每个 capability 键均已注册（无悬空引用）。

        依赖 Django app ready 已执行 setup_default_capabilities()。
        """
        registered = set(registry.list_capabilities())
        referenced = {
            name
            for caps in ai_engine_config.settings.AGENT_CAPABILITIES_DEFAULT.values()
            for name in caps
        }
        self.assertTrue(referenced, "权威表不应为空")
        self.assertLessEqual(referenced, registered)

    def test_agent_hub_reuses_same_table_object(self):
        """agent_hub 侧与 ai_engine 侧引用同一权威表对象（不允许本地副本）。"""
        self.assertIs(
            agent_hub_config.AGENT_CAPABILITIES_DEFAULT,
            ai_engine_config.settings.AGENT_CAPABILITIES_DEFAULT,
        )

    def test_registry_fallback_matches_authoritative_table(self):
        """registry fallback 与权威表逐键一致，未知 agent_type 返回空列表。"""
        table = ai_engine_config.settings.AGENT_CAPABILITIES_DEFAULT
        for agent_type, expected in table.items():
            self.assertEqual(
                registry.get_default_capabilities(agent_type),
                expected,
                msg=f"registry fallback 与权威表不一致: agent_type={agent_type}",
            )
        self.assertEqual(registry.get_default_capabilities("nonexistent"), [])

    def test_resolve_defaults_covers_rag_types(self):
        """AgentConfig.resolve_defaults 对 rag/safe_rag 解析出权威表对应能力。"""
        table = ai_engine_config.settings.AGENT_CAPABILITIES_DEFAULT
        for type_value in ("rag", "safe_rag"):
            with self.subTest(agent_type=type_value):
                config = agent_hub_config.AgentConfig(
                    agent_type=agent_hub_config.AgentType(type_value)
                )
                config.resolve_defaults()
                self.assertEqual(config.capabilities, table[type_value])
