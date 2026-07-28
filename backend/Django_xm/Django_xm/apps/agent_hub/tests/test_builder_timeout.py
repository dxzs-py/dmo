"""builder.build() 超时控制单元测试

验证:
1. 正常创建（< 30s）成功：mock _build_internal 立即返回，验证 build 返回正确结果
2. 超时抛出 asyncio.TimeoutError：mock _build_internal sleep 10s，build_timeout=0.1
3. build_timeout=None 时不限制：mock _build_internal sleep 0.1s，build_timeout=None
4. 自定义超时：build_timeout=60 时通过 mock 验证 asyncio.wait_for 的 timeout 参数为 60

测试覆盖 4 个 builder：
- BaseAgentBuilder
- DeepAgentBuilder
- CustomWorkflowBuilder
- SubAgentBuilder

运行方式:
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    conda activate langchain_xm
    python -m pytest Django_xm/apps/agent_hub/tests/test_builder_timeout.py -v
"""
from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

# Django 环境初始化（兼容 pytest 和 unittest 直接运行）
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django
import django.apps

if not django.apps.apps.ready:
    django.setup()

from Django_xm.apps.agent_hub.builders.base_builder import BaseAgentBuilder
from Django_xm.apps.agent_hub.builders.custom_builder import CustomWorkflowBuilder
from Django_xm.apps.agent_hub.builders.deep_builder import DeepAgentBuilder
from Django_xm.apps.agent_hub.builders.subagent_builder import SubAgentBuilder
from Django_xm.apps.agent_hub.config import AgentConfig, AgentType

# 4 个 builder 类及其 operation_name 期望
_BUILDERS = [
    (BaseAgentBuilder, "BaseAgentBuilder.build"),
    (DeepAgentBuilder, "DeepAgentBuilder.build"),
    (CustomWorkflowBuilder, "CustomWorkflowBuilder.build"),
    (SubAgentBuilder, "SubAgentBuilder.build"),
]


def _make_config(build_timeout):
    """构造最小化的 BASE AgentConfig，仅指定 build_timeout"""
    return AgentConfig(
        agent_type=AgentType.BASE,
        build_timeout=build_timeout,
    )


class TestBuilderTimeout(unittest.IsolatedAsyncioTestCase):
    """builder.build() 超时控制测试"""

    async def test_normal_build_success(self):
        """正常创建：_build_internal 立即返回，build 应正确返回结果"""
        expected_agent = object()  # 哨兵对象，用于验证返回值一致性

        for builder_cls, op_name in _BUILDERS:
            with self.subTest(builder=builder_cls.__name__):
                builder = builder_cls()
                config = _make_config(build_timeout=30.0)

                with patch.object(
                    builder,
                    "_build_internal",
                    new_callable=AsyncMock,
                    return_value=expected_agent,
                ) as mock_internal:
                    result = await builder.build(config)

                self.assertIs(result, expected_agent)
                mock_internal.assert_awaited_once_with(config)

    async def test_timeout_raises_asyncio_timeout_error(self):
        """超时：_build_internal sleep 10s，build_timeout=0.1，应抛出 asyncio.TimeoutError"""
        for builder_cls, op_name in _BUILDERS:
            with self.subTest(builder=builder_cls.__name__):
                builder = builder_cls()
                config = _make_config(build_timeout=0.1)

                async def _slow_build(_config):
                    await asyncio.sleep(10)
                    return object()

                with patch.object(
                    builder,
                    "_build_internal",
                    new_callable=AsyncMock,
                    side_effect=_slow_build,
                ), self.assertRaises(asyncio.TimeoutError):
                    await builder.build(config)

    async def test_build_timeout_none_no_limit(self):
        """build_timeout=None 时不限制：_build_internal sleep 0.1s 后正常返回"""
        expected_agent = object()

        for builder_cls, op_name in _BUILDERS:
            with self.subTest(builder=builder_cls.__name__):
                builder = builder_cls()
                config = _make_config(build_timeout=None)

                async def _brief_build(_config):
                    await asyncio.sleep(0.1)
                    return expected_agent

                with patch.object(
                    builder,
                    "_build_internal",
                    new_callable=AsyncMock,
                    side_effect=_brief_build,
                ) as mock_internal:
                    result = await builder.build(config)

                self.assertIs(result, expected_agent)
                mock_internal.assert_awaited_once_with(config)

    async def test_custom_timeout_passed_to_wait_for(self):
        """自定义超时：build_timeout=60 时 asyncio.wait_for 接收 timeout=60"""
        expected_agent = object()

        for builder_cls, op_name in _BUILDERS:
            with self.subTest(builder=builder_cls.__name__):
                builder = builder_cls()
                config = _make_config(build_timeout=60.0)

                # 捕获传入 asyncio.wait_for 的 timeout 参数
                captured_kwargs = {}

                real_wait_for = asyncio.wait_for

                async def _spy_wait_for(coro, **kwargs):
                    captured_kwargs.update(kwargs)
                    return await real_wait_for(coro, **kwargs)

                with patch.object(
                    builder,
                    "_build_internal",
                    new_callable=AsyncMock,
                    return_value=expected_agent,
                ), patch(
                    "Django_xm.apps.agent_hub.builders._common.asyncio.wait_for",
                    side_effect=_spy_wait_for,
                ):
                    result = await builder.build(config)

                self.assertIs(result, expected_agent)
                self.assertEqual(captured_kwargs.get("timeout"), 60.0)

    async def test_default_timeout_when_config_missing_attr(self):
        """config 没有 build_timeout 属性时，使用默认值 30.0

        使用 SimpleLikeConfig 模拟无 build_timeout 属性的对象，验证 getattr 容错。
        """
        expected_agent = object()

        class _BareConfig:
            """最小配置对象，故意不包含 build_timeout 属性"""

            agent_type = AgentType.BASE

            def validate(self):
                pass

            def resolve_defaults(self):
                pass

        for builder_cls, op_name in _BUILDERS:
            with self.subTest(builder=builder_cls.__name__):
                builder = builder_cls()
                config = _BareConfig()

                captured_kwargs = {}
                real_wait_for = asyncio.wait_for

                async def _spy_wait_for(coro, **kwargs):
                    captured_kwargs.update(kwargs)
                    return await real_wait_for(coro, **kwargs)

                with patch.object(
                    builder,
                    "_build_internal",
                    new_callable=AsyncMock,
                    return_value=expected_agent,
                ), patch(
                    "Django_xm.apps.agent_hub.builders._common.asyncio.wait_for",
                    side_effect=_spy_wait_for,
                ):
                    result = await builder.build(config)

                self.assertIs(result, expected_agent)
                # 默认超时应为 30.0
                self.assertEqual(captured_kwargs.get("timeout"), 30.0)

    async def test_operation_name_in_logs(self):
        """超时时日志中应包含正确的 operation_name"""
        for builder_cls, op_name in _BUILDERS:
            with self.subTest(builder=builder_cls.__name__):
                builder = builder_cls()
                config = _make_config(build_timeout=0.1)

                async def _slow_build(_config):
                    await asyncio.sleep(10)
                    return object()

                with patch.object(
                    builder,
                    "_build_internal",
                    new_callable=AsyncMock,
                    side_effect=_slow_build,
                ), self.assertLogs(
                    "Django_xm.apps.agent_hub.builders._common",
                    level="ERROR",
                ) as cm, self.assertRaises(asyncio.TimeoutError):
                    await builder.build(config)

                # 至少一条日志包含 operation_name
                self.assertTrue(
                    any(op_name in line for line in cm.output),
                    f"日志中未找到 operation_name={op_name}，实际输出: {cm.output}",
                )


if __name__ == "__main__":
    unittest.main()
