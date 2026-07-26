"""Builder 注册中心单元测试

验证:
1. 装饰器注册成功（8 个 AgentType 全部注册到正确的 Builder 类）
2. 重复注册同一 AgentType 抛出 ValueError
3. 一个装饰器可注册多个 AgentType（SubAgentBuilder 注册 3 个）
4. get_registered_builders() 返回副本，修改副本不影响原注册表
5. clear_registry() 可清空注册表（仅测试用）

运行方式:
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    conda activate langchain_xm
    python -m pytest Django_xm/apps/agent_hub/tests/test_builder_registry.py -v
"""
from __future__ import annotations

import os
import unittest

# Django 环境初始化（兼容 pytest 和 unittest 直接运行）
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django  # noqa: E402
import django.apps  # noqa: E402,F401

if not django.apps.apps.ready:
    django.setup()

from Django_xm.apps.agent_hub.config import AgentType  # noqa: E402
from Django_xm.apps.agent_hub.builders._registry import (  # noqa: E402
    _builder_registry,
    clear_registry,
    get_registered_builders,
    register_builder,
)
# 导入 builder 模块以触发 @register_builder 装饰器
from Django_xm.apps.agent_hub.builders.base_builder import BaseAgentBuilder  # noqa: E402,F401
from Django_xm.apps.agent_hub.builders.deep_builder import DeepAgentBuilder  # noqa: E402,F401
from Django_xm.apps.agent_hub.builders.custom_builder import CustomWorkflowBuilder  # noqa: E402,F401
from Django_xm.apps.agent_hub.builders.subagent_builder import SubAgentBuilder  # noqa: E402,F401


# 期望的 AgentType -> Builder 类映射
_EXPECTED_MAPPING = {
    AgentType.BASE: BaseAgentBuilder,
    AgentType.RAG: BaseAgentBuilder,
    AgentType.SAFE_RAG: BaseAgentBuilder,
    AgentType.DEEP_RESEARCH: DeepAgentBuilder,
    AgentType.DEEP_RESEARCH_CUSTOM: CustomWorkflowBuilder,
    AgentType.WEB_RESEARCHER: SubAgentBuilder,
    AgentType.DOC_ANALYST: SubAgentBuilder,
    AgentType.REPORT_WRITER: SubAgentBuilder,
}


class TestBuilderRegistry(unittest.TestCase):
    """Builder 注册中心测试"""

    def test_all_eight_agent_types_registered(self):
        """测试现有 8 个 AgentType 全部注册"""
        registry = get_registered_builders()
        for agent_type in AgentType:
            self.assertIn(
                agent_type, registry,
                f"AgentType.{agent_type.name} 未注册",
            )
        self.assertEqual(len(registry), len(AgentType))

    def test_decorator_registration_success(self):
        """测试装饰器注册成功：每个 AgentType 映射到正确的 Builder 类"""
        registry = get_registered_builders()
        for agent_type, expected_cls in _EXPECTED_MAPPING.items():
            self.assertIs(
                registry[agent_type], expected_cls,
                f"AgentType.{agent_type.name} 应注册到 "
                f"{expected_cls.__name__}，实际注册到 "
                f"{registry[agent_type].__name__}",
            )

    def test_register_multiple_types_in_one_decorator(self):
        """测试一个装饰器注册多个 AgentType

        SubAgentBuilder 通过一个 @register_builder 装饰器注册了
        WEB_RESEARCHER、DOC_ANALYST、REPORT_WRITER 三个 AgentType，
        三者应映射到同一个类对象。
        """
        registry = get_registered_builders()
        self.assertIs(registry[AgentType.WEB_RESEARCHER], SubAgentBuilder)
        self.assertIs(registry[AgentType.DOC_ANALYST], SubAgentBuilder)
        self.assertIs(registry[AgentType.REPORT_WRITER], SubAgentBuilder)

        # 同理，BaseAgentBuilder 注册了 BASE/RAG/SAFE_RAG
        self.assertIs(registry[AgentType.BASE], BaseAgentBuilder)
        self.assertIs(registry[AgentType.RAG], BaseAgentBuilder)
        self.assertIs(registry[AgentType.SAFE_RAG], BaseAgentBuilder)

    def test_duplicate_registration_raises_value_error(self):
        """测试重复注册同一 AgentType 抛出 ValueError

        AgentType.BASE 已被 BaseAgentBuilder 注册，
        再用新类注册应抛出 ValueError。
        """
        with self.assertRaises(ValueError) as ctx:
            @register_builder(AgentType.BASE)
            class _DuplicateBuilder:
                pass

        self.assertIn("BASE", str(ctx.exception))
        self.assertIn("BaseAgentBuilder", str(ctx.exception))

    def test_get_registered_builders_returns_copy(self):
        """测试 get_registered_builders() 返回副本

        修改返回的字典不应影响全局注册表。
        """
        original = get_registered_builders()
        self.assertGreaterEqual(len(original), 8)

        # 修改副本
        original.pop(AgentType.BASE, None)
        original[AgentType.BASE] = object  # 覆盖

        # 原注册表应不受影响
        current = get_registered_builders()
        self.assertIs(current[AgentType.BASE], BaseAgentBuilder)
        self.assertEqual(len(current), len(AgentType))

    def test_clear_registry(self):
        """测试 clear_registry() 清空注册表

        使用 try/finally 确保测试后恢复注册表，避免污染其他测试。
        """
        # 备份当前注册表
        backup = dict(_builder_registry)
        try:
            clear_registry()
            self.assertEqual(len(get_registered_builders()), 0)
            self.assertEqual(len(_builder_registry), 0)
        finally:
            # 恢复注册表
            _builder_registry.update(backup)

        # 验证恢复成功
        self.assertEqual(len(get_registered_builders()), len(AgentType))

    def test_register_builder_rejects_non_agent_type(self):
        """测试 register_builder 拒绝非 AgentType 值"""
        with self.assertRaises(TypeError):
            @register_builder("not_an_agent_type")  # type: ignore[arg-type]
            class _BadBuilder:
                pass

    def test_register_builder_requires_at_least_one_type(self):
        """测试 register_builder 无参数时抛出 ValueError"""
        with self.assertRaises(ValueError):
            @register_builder()  # type: ignore[call-arg]
            class _NoTypeBuilder:
                pass


class TestFactoryUsesRegistry(unittest.TestCase):
    """验证 AgentFactory._get_builders 基于注册表工作"""

    def test_factory_builders_match_registry(self):
        """测试 factory 的 builders 与注册表一致"""
        from Django_xm.apps.agent_hub.factory import AgentFactory

        builders = AgentFactory._get_builders()
        registry = get_registered_builders()

        # 每个 AgentType 都有对应的 builder 实例
        for agent_type in AgentType:
            self.assertIn(agent_type, builders)
            # 实例的类型应与注册表中的类一致
            self.assertIsInstance(builders[agent_type], registry[agent_type])

    def test_factory_shares_instance_for_same_builder_class(self):
        """测试同一 Builder 类的多个 AgentType 共享同一实例

        例如 BASE/RAG/SAFE_RAG 应共享同一个 BaseAgentBuilder 实例。
        """
        from Django_xm.apps.agent_hub.factory import AgentFactory

        builders = AgentFactory._get_builders()
        self.assertIs(builders[AgentType.BASE], builders[AgentType.RAG])
        self.assertIs(builders[AgentType.RAG], builders[AgentType.SAFE_RAG])

        self.assertIs(
            builders[AgentType.WEB_RESEARCHER],
            builders[AgentType.DOC_ANALYST],
        )
        self.assertIs(
            builders[AgentType.DOC_ANALYST],
            builders[AgentType.REPORT_WRITER],
        )


if __name__ == "__main__":
    unittest.main()
