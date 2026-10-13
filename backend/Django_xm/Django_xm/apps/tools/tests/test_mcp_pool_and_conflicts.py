"""Task 7（C4/C6）测试：MCP 客户端池删除 + 工具名冲突前缀消解。

覆盖：
- C4：get_mcp_tools 每次现场构造 MultiServerMCPClient（无池化缓存），
  headers/auth_token 变更立即生效；池相关模块级符号已全部删除。
- C6：_resolve_mcp_tool_name_conflicts 重名加 server 前缀（含序号兜底）；
  get_tools_for_request_async 中用户选中原名能命中带前缀工具（不静默排除），
  与内置工具同名时两者并存。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.tools.tests.test_mcp_pool_and_conflicts --noinput
"""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.test import SimpleTestCase

from Django_xm.async_utils import run_async
from Django_xm.apps.tools.mcp import get_mcp_tools

MCP_URL = "https://mcp.example-public.com/mcp"


def _fake_mcp_tool(name, server):
    return SimpleNamespace(
        name=name,
        description="demo",
        metadata={"is_mcp_tool": True, "mcp_server_name": server},
    )


class GetMcpToolsFreshConnectionTests(SimpleTestCase):
    """C4：客户端池已删除，get_mcp_tools 每次现场建连，凭证变更立即生效。"""

    def test_auth_token_change_takes_effect_immediately(self):
        captured = []

        def client_factory(connections):
            captured.append(dict(connections))
            return SimpleNamespace(
                get_tools=AsyncMock(return_value=[_fake_mcp_tool("tool-a", "srv")])
            )

        with (
            patch("Django_xm.apps.tools.mcp.is_mcp_available", return_value=True),
            patch("langchain_mcp_adapters.client.MultiServerMCPClient", side_effect=client_factory),
        ):
            run_async(
                get_mcp_tools(server_url=MCP_URL, transport="http", auth_token="old-token", server_name="srv")
            )
            run_async(
                get_mcp_tools(server_url=MCP_URL, transport="http", auth_token="new-token", server_name="srv")
            )

        # 两次调用各自现场构造 client（池化实现下第二次会命中缓存，只构造一次）
        self.assertEqual(len(captured), 2)
        server_key = f"http:{MCP_URL}"
        self.assertEqual(captured[0][server_key]["headers"]["Authorization"], "Bearer old-token")
        self.assertEqual(captured[1][server_key]["headers"]["Authorization"], "Bearer new-token")

    def test_pool_symbols_removed(self):
        """结构性守卫：池相关模块级符号（缓存/锁/TTL/cleanup/atexit 钩子）已全部删除。"""
        import Django_xm.apps.tools.mcp as mcp_module

        for legacy in (
            "_mcp_client_pool",
            "_mcp_client_timestamps",
            "_mcp_pool_lock",
            "_MCP_CLIENT_TTL_SECONDS",
            "_get_pool_lock",
            "_get_or_create_client",
            "_remove_stale_client",
            "cleanup_mcp_clients",
            "get_pooled_client_count",
            "_sync_cleanup_on_exit",
        ):
            self.assertFalse(hasattr(mcp_module, legacy), f"残留池符号: {legacy}")


class McpToolNameConflictTests(SimpleTestCase):
    """C6：重名 MCP 工具加 server 前缀，不静默排除；原名 selected 可命中。"""

    def test_conflicts_get_server_prefix(self):
        from Django_xm.apps.tools import _resolve_mcp_tool_name_conflicts

        tools = [
            _fake_mcp_tool("search", "alpha"),
            _fake_mcp_tool("search", "beta"),
            _fake_mcp_tool("fetch", "beta"),
        ]
        resolved = _resolve_mcp_tool_name_conflicts(tools, set())
        self.assertEqual([t.name for t in resolved], ["search", "beta__search", "fetch"])
        self.assertNotIn("mcp_original_name", resolved[0].metadata)
        self.assertEqual(resolved[1].metadata.get("mcp_original_name"), "search")
        self.assertTrue(resolved[1].metadata.get("mcp_renamed"))

    def test_conflict_with_known_name_gets_prefix(self):
        """与内置/技能/自定义等已知名冲突：加 server 前缀（修复前被永久排除）。"""
        from Django_xm.apps.tools import _resolve_mcp_tool_name_conflicts

        resolved = _resolve_mcp_tool_name_conflicts([_fake_mcp_tool("search", "alpha")], {"search"})
        self.assertEqual(resolved[0].name, "alpha__search")
        self.assertEqual(resolved[0].metadata.get("mcp_original_name"), "search")

    def test_double_conflict_gets_numeric_suffix(self):
        from Django_xm.apps.tools import _resolve_mcp_tool_name_conflicts

        resolved = _resolve_mcp_tool_name_conflicts(
            [_fake_mcp_tool("search", "alpha")], {"search", "alpha__search"}
        )
        self.assertEqual(resolved[0].name, "alpha__search__1")

    def test_selected_original_name_maps_to_prefixed_tool(self):
        """跨 server 同名：用户选中原名，两个 server 的工具都可用（修复前静默丢弃后者）。"""
        from Django_xm.apps.tools import get_tools_for_request_async

        mcp_tools = [_fake_mcp_tool("search", "alpha"), _fake_mcp_tool("search", "beta")]
        with (
            patch(
                "Django_xm.apps.tools._load_mcp_tools_async",
                new_callable=AsyncMock,
                return_value=mcp_tools,
            ),
            patch("Django_xm.apps.tools.get_all_tools", return_value=[]),
            patch("Django_xm.apps.tools.SkillRegistryService.get_presets", return_value=[]),
            patch(
                "Django_xm.apps.tools.SkillProvider.get_skill_tools",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            tools = run_async(
                get_tools_for_request_async(use_tools=True, use_mcp=True, selected_tools=["search"])
            )

        names = [t.name for t in tools]
        self.assertIn("search", names)  # 首个 server 保留原名
        self.assertIn("beta__search", names)  # 第二个 server 加前缀，经 mcp_original_name 命中

    def test_builtin_conflict_still_loads_prefixed_mcp_tool(self):
        """与内置工具同名：内置保留原名，MCP 工具加前缀并存（修复前被永久排除）。"""
        from Django_xm.apps.tools import get_tools_for_request_async

        builtin = SimpleNamespace(name="search", description="builtin", metadata={"tier": "core"})
        mcp_tools = [_fake_mcp_tool("search", "alpha")]
        with (
            patch(
                "Django_xm.apps.tools._load_mcp_tools_async",
                new_callable=AsyncMock,
                return_value=mcp_tools,
            ),
            patch("Django_xm.apps.tools.get_all_tools", return_value=[builtin]),
            patch("Django_xm.apps.tools.SkillRegistryService.get_presets", return_value=[]),
            patch(
                "Django_xm.apps.tools.SkillProvider.get_skill_tools",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            tools = run_async(
                get_tools_for_request_async(use_tools=True, use_mcp=True, selected_tools=["search"])
            )

        names = [t.name for t in tools]
        self.assertIn("search", names)
        self.assertIn("alpha__search", names)
