"""预检快速失败模式单元测试

验证:
1. fail_fast_on_preflight=False（默认）时，预检失败仅 warning 并继续创建 agent
2. fail_fast_on_preflight=True 时，预检失败抛出 PreflightCheckError 且不调用 builder.build
3. preflight.check 抛出普通 Exception 时仍被忽略，继续创建（保持向后兼容）
4. 预检通过（passed=True）时无论 fail_fast_on_preflight 取值都正常创建

运行方式:
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    conda activate langchain_xm
    python -m pytest Django_xm/apps/agent_hub/tests/test_preflight_fail_fast.py -v
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Django 环境初始化（兼容 pytest 和 unittest 直接运行）
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django  # noqa: E402
import django.apps  # noqa: E402,F401

if not django.apps.apps.ready:
    django.setup()

from Django_xm.apps.agent_hub.config import AgentConfig, AgentType  # noqa: E402
from Django_xm.apps.agent_hub.exceptions import PreflightCheckError  # noqa: E402
from Django_xm.apps.agent_hub.factory import AgentFactory  # noqa: E402
from Django_xm.apps.agent_hub.preflight import (  # noqa: E402
    ExecutionPreflight,
    PreflightResult,
)


def _make_config(fail_fast: bool = False) -> AgentConfig:
    """构造一个最小化的 BASE 类型 AgentConfig"""
    return AgentConfig(
        agent_type=AgentType.BASE,
        fail_fast_on_preflight=fail_fast,
    )


def _make_mock_builder() -> MagicMock:
    """构造一个 mock builder，build 返回带 .graph 属性的对象"""
    builder = MagicMock()
    # factory.create 在 build 成功后通过 hasattr(agent, "graph") 判断返回值
    agent_obj = MagicMock()
    agent_obj.graph = MagicMock()
    builder.build = AsyncMock(return_value=agent_obj)
    return builder


class TestPreflightFailFast(unittest.IsolatedAsyncioTestCase):
    """预检快速失败模式测试"""

    async def test_default_behavior_warning_only(self):
        """fail_fast_on_preflight=False 时预检失败仅 warning，继续创建 agent"""
        config = _make_config(fail_fast=False)
        mock_builder = _make_mock_builder()
        fake_result = PreflightResult(
            passed=False, issues=["LLM 服务不可用"], warnings=[]
        )

        with patch.object(
            ExecutionPreflight, "check", new_callable=AsyncMock, return_value=fake_result
        ), patch.object(
            AgentFactory, "_get_builders", return_value={AgentType.BASE: mock_builder}
        ):
            await AgentFactory.create(config)

        # builder.build 应被调用，说明创建流程继续
        mock_builder.build.assert_awaited_once_with(config)
        # _preflight_issues 应被写入
        self.assertEqual(config._preflight_issues, ["LLM 服务不可用"])

    async def test_fail_fast_raises_preflight_check_error(self):
        """fail_fast_on_preflight=True 时预检失败抛出 PreflightCheckError，不调用 builder.build"""
        config = _make_config(fail_fast=True)
        mock_builder = _make_mock_builder()
        fake_result = PreflightResult(
            passed=False, issues=["LLM 服务不可用", "Redis 连接失败"], warnings=[]
        )

        with patch.object(
            ExecutionPreflight, "check", new_callable=AsyncMock, return_value=fake_result
        ), patch.object(
            AgentFactory, "_get_builders", return_value={AgentType.BASE: mock_builder}
        ):
            with self.assertRaises(PreflightCheckError) as ctx:
                await AgentFactory.create(config)

        # 异常应携带 issues 列表
        self.assertEqual(ctx.exception.issues, ["LLM 服务不可用", "Redis 连接失败"])
        # builder.build 不应被调用
        mock_builder.build.assert_not_called()

    async def test_preflight_exception_ignored(self):
        """preflight.check 抛出普通 Exception 时仍忽略，继续创建

        即使 fail_fast_on_preflight=True，普通异常（非 PreflightCheckError）
        仍被 try/except 吞掉，保持向后兼容行为。
        """
        config = _make_config(fail_fast=True)
        mock_builder = _make_mock_builder()

        with patch.object(
            ExecutionPreflight,
            "check",
            new_callable=AsyncMock,
            side_effect=RuntimeError("预检内部异常"),
        ), patch.object(
            AgentFactory, "_get_builders", return_value={AgentType.BASE: mock_builder}
        ):
            # 不应抛出
            await AgentFactory.create(config)

        mock_builder.build.assert_awaited_once_with(config)

    async def test_preflight_passed_normal_creation(self):
        """预检通过（passed=True）时正常创建，无论 fail_fast_on_preflight 取值"""
        for fail_fast in (False, True):
            with self.subTest(fail_fast=fail_fast):
                config = _make_config(fail_fast=fail_fast)
                mock_builder = _make_mock_builder()
                fake_result = PreflightResult(passed=True, issues=[], warnings=[])

                with patch.object(
                    ExecutionPreflight,
                    "check",
                    new_callable=AsyncMock,
                    return_value=fake_result,
                ), patch.object(
                    AgentFactory,
                    "_get_builders",
                    return_value={AgentType.BASE: mock_builder},
                ):
                    await AgentFactory.create(config)

                mock_builder.build.assert_awaited_once_with(config)
                # 预检通过时 _preflight_issues 保持 None
                self.assertIsNone(config._preflight_issues)


if __name__ == "__main__":
    unittest.main()
