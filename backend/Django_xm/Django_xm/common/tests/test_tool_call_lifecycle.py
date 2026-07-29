"""工具调用生命周期服务端到端测试（Phase E3 嵌套字段传播）。

覆盖 ``Django_xm.common.tool_call_lifecycle`` 与 ``Django_xm.common.realtime_sync.publish_tool_call``
的子 agent 嵌套层级字段端到端传播链路：

传播链路：
    subagent_patch.atask
        → configurable 注入 parent_tool_call_id/depth/agent_name/agent_path/risk_ceiling
        → adapter._on_tool_event 透传到 evt dict
        → adapter._publish_tool_event 注册 ToolCallContext（嵌套字段）
        → service.register 合并嵌套字段（幂等，仅补全空字段）
        → service.transition_async 从 context 透传到 publish_tool_call payload
        → publish_tool_call 写入 payload（仅非空字段）
        → 前端 handleSessionEvent 映射到 toolData

本测试聚焦后端段（register/transition_async/publish_tool_call），subagent_patch 段
在 test_subagent_patch.py 的 NestedFieldPropagationTests 中覆盖。

测试要点：
1. register 幂等合并：主 agent 空字段 + 子 agent 非空字段 → 保留非空值
2. transition_async 从 context 透传嵌套字段到 publish_tool_call
3. publish_tool_call payload 仅含非空嵌套字段（主 agent payload 简洁）
4. _normalize_risk_ceiling 处理 RiskLevel 枚举/字符串/None/非法值
5. auto_approved 标记持久化（True 一旦写入不被覆盖回 False）

运行方式：
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    conda activate langchain_xm
    python -m pytest Django_xm/common/tests/test_tool_call_lifecycle.py -v
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

# Django 环境初始化
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django
import django.apps

if not django.apps.apps.ready:
    django.setup()


class _DictCache:
    """dict 后端的 cache 替身，模拟 django.core.cache 的 get/set/delete。

    tool_call_lifecycle.service 通过 django.core.cache 持久化上下文，
    测试用 dict 替身避免依赖真实 Redis。
    """

    def __init__(self):
        self._store: dict[str, object] = {}

    def get(self, key, default=None):
        return self._store.get(key, default)

    def set(self, key, value, timeout=None):
        self._store[key] = value

    def delete(self, key):
        self._store.pop(key, None)

    def clear(self):
        self._store.clear()


class RegisterNestedFieldsTests(unittest.TestCase):
    """service.register 子 agent 嵌套层级字段合并行为。"""

    def setUp(self):
        self.cache = _DictCache()
        self._cache_patcher = patch("Django_xm.common.tool_call_lifecycle.cache", self.cache)
        self._cache_patcher.start()
        # 每个测试清空，确保独立
        self.cache.clear()

    def tearDown(self):
        self._cache_patcher.stop()

    def _make_ctx(self, **overrides):
        """构造 ToolCallContext，默认子 agent 场景。"""
        from Django_xm.common.event_schema import EventSource
        from Django_xm.common.tool_call_lifecycle import ToolCallContext

        defaults = {
            "tool_call_id": "tc-sub-1",
            "tool_name": "web_search",
            "module": EventSource.DEEP_RESEARCH,
            "module_id": "task-1",
            "message_id": "",
            "parameters": {"query": "test"},
            "parent_tool_call_id": "tc-parent-task",
            "depth": 1,
            "agent_name": "web-researcher",
            "agent_path": ["main", "web-researcher"],
            "risk_ceiling": "controlled",
        }
        defaults.update(overrides)
        return ToolCallContext(**defaults)

    def test_register_subagent_writes_all_nested_fields(self):
        """子 agent 首次注册：所有嵌套字段写入 context。"""
        from Django_xm.common.tool_call_lifecycle import service

        service.register(self._make_ctx())
        ctx = service.get_context("tc-sub-1")

        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["parent_tool_call_id"], "tc-parent-task")
        self.assertEqual(ctx["depth"], 1)
        self.assertEqual(ctx["agent_name"], "web-researcher")
        self.assertEqual(ctx["agent_path"], ["main", "web-researcher"])
        self.assertEqual(ctx["risk_ceiling"], "controlled")

    def test_register_main_agent_has_empty_nested_fields(self):
        """主 agent 注册：嵌套字段为空（depth=0, agent_name=''), payload 保持简洁。"""
        from Django_xm.common.event_schema import EventSource
        from Django_xm.common.tool_call_lifecycle import ToolCallContext, service

        service.register(
            ToolCallContext(
                tool_call_id="tc-main-1",
                tool_name="shell_exec",
                module=EventSource.CHAT,
                module_id="session-1",
                parameters={"command": "ls"},
                # 主 agent 不传嵌套字段，使用默认空值
            )
        )
        ctx = service.get_context("tc-main-1")

        self.assertEqual(ctx["parent_tool_call_id"], "")
        self.assertEqual(ctx["depth"], 0)
        self.assertEqual(ctx["agent_name"], "")
        self.assertEqual(ctx["agent_path"], [])
        self.assertEqual(ctx["risk_ceiling"], "")

    def test_register_idempotent_merges_nested_fields(self):
        """register 幂等：已存在的非空嵌套字段不被覆盖回空。

        场景：子 agent 先注册（含嵌套字段），后 chat 模块以主 agent 视角
        再次 register 同一 tool_call_id（嵌套字段为空），不应清空已有字段。
        """
        from Django_xm.common.event_schema import EventSource
        from Django_xm.common.tool_call_lifecycle import ToolCallContext, service

        # 1. 子 agent 先注册（含嵌套字段）
        service.register(self._make_ctx())
        # 2. 模拟 chat 模块以默认空字段再次 register（主 agent 视角）
        service.register(
            ToolCallContext(
                tool_call_id="tc-sub-1",
                tool_name="web_search",
                module=EventSource.CHAT,
                module_id="session-1",
                parameters={"query": "test"},
            )
        )
        ctx = service.get_context("tc-sub-1")

        # 嵌套字段应保留子 agent 首次注册的非空值（不被空值覆盖）
        self.assertEqual(ctx["parent_tool_call_id"], "tc-parent-task")
        self.assertEqual(ctx["depth"], 1)
        self.assertEqual(ctx["agent_name"], "web-researcher")
        self.assertEqual(ctx["agent_path"], ["main", "web-researcher"])
        self.assertEqual(ctx["risk_ceiling"], "controlled")

    def test_register_auto_approved_stays_true(self):
        """auto_approved 一旦为 True 不被覆盖回 False（SAFE 级审计持久化）。"""
        from Django_xm.common.tool_call_lifecycle import service

        # 1. SAFE 级首次注册（auto_approved=True）
        service.register(self._make_ctx(auto_approved=True))
        # 2. 后续 register 不带 auto_approved（默认 False）
        service.register(self._make_ctx(auto_approved=False))
        ctx = service.get_context("tc-sub-1")

        self.assertTrue(ctx["auto_approved"])


class TransitionAsyncNestedFieldsTests(unittest.IsolatedAsyncioTestCase):
    """service.transition_async 从 context 透传嵌套字段到 publish_tool_call。

    端到端验证：register（含嵌套字段）→ transition_async → publish_tool_call 调用参数。
    """

    def setUp(self):
        self.cache = _DictCache()
        self._cache_patcher = patch("Django_xm.common.tool_call_lifecycle.cache", self.cache)
        self._cache_patcher.start()
        self.cache.clear()

    def tearDown(self):
        self._cache_patcher.stop()

    async def test_transition_async_propagates_nested_fields_to_payload(self):
        """子 agent 工具事件：transition_async 透传嵌套字段到 publish_tool_call。"""
        from Django_xm.common.event_schema import EventSource, EventType
        from Django_xm.common.tool_call_lifecycle import ToolCallContext, service

        # 注册子 agent 上下文（含嵌套字段）
        service.register(
            ToolCallContext(
                tool_call_id="tc-sub-prop",
                tool_name="web_search",
                module=EventSource.DEEP_RESEARCH,
                module_id="task-prop",
                parameters={"query": "langchain"},
                parent_tool_call_id="tc-parent",
                depth=1,
                agent_name="web-researcher",
                agent_path=["main", "web-researcher"],
                risk_ceiling="controlled",
            )
        )

        with patch(
            "Django_xm.common.tool_call_lifecycle.publish_tool_call",
            new_callable=AsyncMock,
        ) as mock_publish:
            await service.transition_async(
                "tc-sub-prop",
                EventType.TOOL_CALL_RUNNING,
                parameters={"query": "langchain"},
            )

        mock_publish.assert_awaited_once()
        kwargs = mock_publish.call_args.kwargs

        # 核心字段
        self.assertEqual(kwargs["tool_call_id"], "tc-sub-prop")
        self.assertEqual(kwargs["tool_name"], "web_search")
        self.assertEqual(kwargs["module"], EventSource.DEEP_RESEARCH)
        # 嵌套字段透传
        self.assertEqual(kwargs["parent_tool_call_id"], "tc-parent")
        self.assertEqual(kwargs["depth"], 1)
        self.assertEqual(kwargs["agent_name"], "web-researcher")
        self.assertEqual(kwargs["agent_path"], ["main", "web-researcher"])
        self.assertEqual(kwargs["risk_ceiling"], "controlled")

    async def test_transition_async_main_agent_has_no_nested_fields(self):
        """主 agent 工具事件：transition_async 嵌套字段为 None（payload 简洁）。"""
        from Django_xm.common.event_schema import EventSource, EventType
        from Django_xm.common.tool_call_lifecycle import ToolCallContext, service

        # 注册主 agent 上下文（无嵌套字段）
        service.register(
            ToolCallContext(
                tool_call_id="tc-main-prop",
                tool_name="shell_exec",
                module=EventSource.CHAT,
                module_id="session-prop",
                parameters={"command": "ls"},
            )
        )

        with patch(
            "Django_xm.common.tool_call_lifecycle.publish_tool_call",
            new_callable=AsyncMock,
        ) as mock_publish:
            await service.transition_async(
                "tc-main-prop",
                EventType.TOOL_CALL_RUNNING,
                parameters={"command": "ls"},
            )

        kwargs = mock_publish.call_args.kwargs
        # 主 agent 嵌套字段应为 None（publish_tool_call 不写入 payload）
        self.assertIsNone(kwargs["parent_tool_call_id"])
        self.assertIsNone(kwargs["depth"])
        self.assertIsNone(kwargs["agent_name"])
        self.assertIsNone(kwargs["agent_path"])
        self.assertIsNone(kwargs["risk_ceiling"])

    async def test_transition_async_propagates_auto_approved(self):
        """SAFE 级自动通过：transition_async 透传 auto_approved=True。"""
        from Django_xm.common.event_schema import EventSource, EventType
        from Django_xm.common.tool_call_lifecycle import ToolCallContext, service

        service.register(
            ToolCallContext(
                tool_call_id="tc-safe",
                tool_name="ls",
                module=EventSource.DEEP_RESEARCH,
                module_id="task-safe",
                parameters={"path": "."},
                auto_approved=True,
                depth=1,
                agent_name="web-researcher",
                risk_ceiling="safe",
            )
        )

        with patch(
            "Django_xm.common.tool_call_lifecycle.publish_tool_call",
            new_callable=AsyncMock,
        ) as mock_publish:
            await service.transition_async(
                "tc-safe",
                EventType.TOOL_CALL_RUNNING,
            )

        kwargs = mock_publish.call_args.kwargs
        self.assertTrue(kwargs["auto_approved"])
        self.assertEqual(kwargs["risk_ceiling"], "safe")


class PublishToolCallNestedFieldsTests(unittest.IsolatedAsyncioTestCase):
    """publish_tool_call payload 嵌套字段写入行为（端到端最末段）。"""

    async def test_publish_tool_call_includes_nested_fields(self):
        """子 agent 场景：publish_tool_call payload 含所有非空嵌套字段。"""
        from Django_xm.common.event_schema import EventSource, EventType
        from Django_xm.common.realtime_sync import publish_tool_call

        with patch(
            "Django_xm.common.realtime_sync.publish_event",
            new_callable=AsyncMock,
        ) as mock_publish_event:
            await publish_tool_call(
                EventType.TOOL_CALL_RUNNING,
                tool_call_id="tc-pub-sub",
                tool_name="web_search",
                module=EventSource.DEEP_RESEARCH,
                module_id="task-pub",
                parameters={"query": "test"},
                parent_tool_call_id="tc-parent",
                depth=1,
                agent_name="web-researcher",
                agent_path=["main", "web-researcher"],
                risk_ceiling="controlled",
                auto_approved=False,
            )

        mock_publish_event.assert_awaited_once()
        payload = mock_publish_event.call_args.args[1]

        self.assertEqual(payload["parent_tool_call_id"], "tc-parent")
        self.assertEqual(payload["depth"], 1)
        self.assertEqual(payload["agent_name"], "web-researcher")
        self.assertEqual(payload["agent_path"], ["main", "web-researcher"])
        self.assertEqual(payload["risk_ceiling"], "controlled")

    async def test_publish_tool_call_main_agent_payload_has_no_nested_fields(self):
        """主 agent 场景：payload 不含嵌套字段（保持简洁）。"""
        from Django_xm.common.event_schema import EventSource, EventType
        from Django_xm.common.realtime_sync import publish_tool_call

        with patch(
            "Django_xm.common.realtime_sync.publish_event",
            new_callable=AsyncMock,
        ) as mock_publish_event:
            await publish_tool_call(
                EventType.TOOL_CALL_RUNNING,
                tool_call_id="tc-pub-main",
                tool_name="shell_exec",
                module=EventSource.CHAT,
                module_id="session-pub",
                parameters={"command": "ls"},
                # 主 agent 不传嵌套字段
            )

        payload = mock_publish_event.call_args.args[1]
        # 主 agent payload 不应含嵌套字段
        self.assertNotIn("parent_tool_call_id", payload)
        self.assertNotIn("depth", payload)
        self.assertNotIn("agent_name", payload)
        self.assertNotIn("agent_path", payload)
        self.assertNotIn("risk_ceiling", payload)
        self.assertNotIn("auto_approved", payload)

    async def test_publish_tool_call_auto_approved_written(self):
        """SAFE 级自动通过：auto_approved=True 写入 payload。"""
        from Django_xm.common.event_schema import EventSource, EventType
        from Django_xm.common.realtime_sync import publish_tool_call

        with patch(
            "Django_xm.common.realtime_sync.publish_event",
            new_callable=AsyncMock,
        ) as mock_publish_event:
            await publish_tool_call(
                EventType.TOOL_CALL_RUNNING,
                tool_call_id="tc-auto",
                tool_name="ls",
                module=EventSource.DEEP_RESEARCH,
                module_id="task-auto",
                parameters={"path": "."},
                auto_approved=True,
                depth=1,
                agent_name="web-researcher",
                risk_ceiling="safe",
            )

        payload = mock_publish_event.call_args.args[1]
        self.assertTrue(payload["auto_approved"])
        self.assertEqual(payload["risk_ceiling"], "safe")
        self.assertEqual(payload["depth"], 1)

    async def test_publish_tool_call_depth_zero_omitted(self):
        """depth=0（主 agent）不写入 payload（仅 depth>0 才写入）。"""
        from Django_xm.common.event_schema import EventSource, EventType
        from Django_xm.common.realtime_sync import publish_tool_call

        with patch(
            "Django_xm.common.realtime_sync.publish_event",
            new_callable=AsyncMock,
        ) as mock_publish_event:
            await publish_tool_call(
                EventType.TOOL_CALL_RUNNING,
                tool_call_id="tc-depth0",
                tool_name="ls",
                module=EventSource.CHAT,
                module_id="session-d",
                parameters={},
                depth=0,
            )

        payload = mock_publish_event.call_args.args[1]
        self.assertNotIn("depth", payload)


class NormalizeRiskCeilingTests(unittest.TestCase):
    """_normalize_risk_ceiling 风险上限规范化。"""

    def test_normalize_risk_level_enum(self):
        """RiskLevel 枚举 → .value 字符串。"""
        from Django_xm.common.risk_levels import RiskLevel
        from Django_xm.common.tool_call_lifecycle import _normalize_risk_ceiling

        self.assertEqual(_normalize_risk_ceiling(RiskLevel.SAFE), "safe")
        self.assertEqual(_normalize_risk_ceiling(RiskLevel.CONTROLLED), "controlled")
        self.assertEqual(_normalize_risk_ceiling(RiskLevel.HIGH), "high")

    def test_normalize_valid_string(self):
        """合法字符串原样返回。"""
        from Django_xm.common.tool_call_lifecycle import _normalize_risk_ceiling

        self.assertEqual(_normalize_risk_ceiling("safe"), "safe")
        self.assertEqual(_normalize_risk_ceiling("controlled"), "controlled")
        self.assertEqual(_normalize_risk_ceiling("high"), "high")

    def test_normalize_none(self):
        """None 透传为 None。"""
        from Django_xm.common.tool_call_lifecycle import _normalize_risk_ceiling

        self.assertIsNone(_normalize_risk_ceiling(None))

    def test_normalize_empty_string(self):
        """空字符串返回 None（不写入 payload）。"""
        from Django_xm.common.tool_call_lifecycle import _normalize_risk_ceiling

        self.assertIsNone(_normalize_risk_ceiling(""))

    def test_normalize_invalid_string_returns_none(self):
        """非法 risk_ceiling 字符串返回 None（不污染 payload）。"""
        from Django_xm.common.tool_call_lifecycle import _normalize_risk_ceiling

        self.assertIsNone(_normalize_risk_ceiling("extreme"))
        self.assertIsNone(_normalize_risk_ceiling("UNKNOWN"))


if __name__ == "__main__":
    unittest.main()
