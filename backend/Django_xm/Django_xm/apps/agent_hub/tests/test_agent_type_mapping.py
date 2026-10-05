"""AgentType → CapabilityRegistry 键映射（type_map）单一真源断言测试（spec E2）。

背景：历史上 tool_resolver.py 与 middleware.py 各持一份一字不差的
_get_agent_type_str 映射（arch 审计 §5.2 定案），新增 AgentType 漏改
任一副本会静默 fallback "base"。收敛后唯一权威定义位于
agent_hub/config.py 的 AGENT_TYPE_CAPABILITY_KEY + resolve_agent_type_str，
两消费方（tool_resolver / middleware）改为 import 引用，重复定义已物理删除。

覆盖：
1. 映射键集合与 AgentType 枚举成员一一对应（防漂移：新增枚举值不更新
   映射时本测试失败，杜绝静默 fallback）；
2. 映射值能通过在役能力的 is_compatible 检查（映射存在的意义：
   CapabilityRegistry 消费方键空间）；
3. 两消费方引用的是 config 权威对象（is 断言），且旧私有名无残留副本；
4. 行为表驱动：base/rag/safe_rag → "base"，deep_research → "deep_research"；
   未知 agent_type 值回退 "base"（与收敛前行为一致）。

运行：
    python manage.py test Django_xm.apps.agent_hub.tests.test_agent_type_mapping
"""

import unittest
from types import SimpleNamespace

from Django_xm.apps.ai_engine import config as ai_engine_config
from Django_xm.apps.ai_engine.capabilities.registry import registry

from .. import config as agent_hub_config
from .. import middleware as agent_hub_middleware
from .. import tool_resolver


class AgentTypeCapabilityKeyTests(unittest.TestCase):
    """type_map 单源收敛断言（权威定义：agent_hub/config.py）。"""

    def test_map_keys_cover_all_agent_type_members(self):
        """映射键集合 == AgentType 枚举成员集合（防漂移核心断言）。"""
        expected = {member.value for member in agent_hub_config.AgentType}
        self.assertEqual(set(agent_hub_config.AGENT_TYPE_CAPABILITY_KEY), expected)

    def test_mapped_values_pass_capability_compatibility(self):
        """每个映射值对其默认能力表行内的在役能力均通过 is_compatible。

        依赖 Django app ready 已执行 setup_default_capabilities()。
        若未来把新枚举值映射到 CapabilityRegistry 不认识的键
        （如 "rag"——无任何能力兼容），本测试失败。
        """
        table = ai_engine_config.settings.AGENT_CAPABILITIES_DEFAULT
        checked = 0
        for mapped_value in set(agent_hub_config.AGENT_TYPE_CAPABILITY_KEY.values()):
            for cap_name in table.get(mapped_value, []):
                cap = registry.get(cap_name)
                if cap is None:
                    continue
                with self.subTest(mapped_value=mapped_value, capability=cap_name):
                    self.assertTrue(
                        cap.is_compatible(mapped_value),
                        msg=f"映射值 {mapped_value!r} 不被能力 {cap_name} 兼容",
                    )
                checked += 1
        self.assertGreater(checked, 0, "至少应覆盖一个在役能力兼容性检查")

    def test_consumers_import_authoritative_symbol(self):
        """两消费方引用 config 权威对象（is 断言），旧私有名无残留副本。"""
        self.assertIs(
            tool_resolver.resolve_agent_type_str,
            agent_hub_config.resolve_agent_type_str,
        )
        self.assertIs(
            agent_hub_middleware.resolve_agent_type_str,
            agent_hub_config.resolve_agent_type_str,
        )
        self.assertFalse(hasattr(tool_resolver, "_get_agent_type_str"))
        self.assertFalse(hasattr(agent_hub_middleware, "_get_agent_type_str"))

    def test_resolve_agent_type_str_table_driven(self):
        """表驱动：各 AgentType 成员解析结果与收敛前两份副本一致。"""
        cases = [
            (agent_hub_config.AgentType.BASE, "base"),
            (agent_hub_config.AgentType.RAG, "base"),
            (agent_hub_config.AgentType.SAFE_RAG, "base"),
            (agent_hub_config.AgentType.DEEP_RESEARCH, "deep_research"),
        ]
        for agent_type, expected in cases:
            with self.subTest(agent_type=agent_type):
                config = agent_hub_config.AgentConfig(agent_type=agent_type)
                self.assertEqual(agent_hub_config.resolve_agent_type_str(config), expected)

    def test_unknown_agent_type_falls_back_to_base(self):
        """未知 agent_type 值静默回退 "base"（收敛前后行为一致）。"""
        stub_config = SimpleNamespace(agent_type=SimpleNamespace(value="future_type"))
        self.assertEqual(agent_hub_config.resolve_agent_type_str(stub_config), "base")


if __name__ == "__main__":
    unittest.main()
