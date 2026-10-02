"""resolve_mcp_servers 合并收敛测试（审计 B⑧）。

覆盖：
- 合并去重键 name，系统级优先（同名用户级被跳过）；
- exclude_user_stdio=True 仅排除用户自建 stdio，系统级 stdio 保留（S-04 精确保留）；
- user_id 为空仅返回系统级；
- 用户级查询失败降级为仅系统级（不阻断装配）；
- 两入口（agent 运行时装配 _load_mcp_tools_async 的 server 集合 / 管理台白名单
  _get_allowed_server_names）对同一数据产出一致。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.tools.tests.test_mcp_resolve_servers
"""

import os
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase

from Django_xm.apps.tools.mcp.resolve_servers import resolve_mcp_servers
from Django_xm.apps.tools.models import McpServerConfig, ToolCategory

# 固定的系统级 MCP 配置（stdio + http 各一），避免依赖真实默认值与环境变量
FAKE_SYSTEM_SERVERS = [
    {
        "name": "sys-stdio",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@demo/server"],
        "enabled": True,
    },
    {
        "name": "sys-http",
        "transport": "http",
        "url": "https://mcp.example-public.com/mcp",
        "enabled": True,
    },
]


class ResolveMcpServersTests(TestCase):
    """resolve_mcp_servers 单一合并函数行为。"""

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="rsuser", password="pw123456")
        self.category, _ = ToolCategory.objects.get_or_create(code="general", defaults={"name": "通用"})

    @staticmethod
    def _patch_system_servers():
        """将系统级 MCP 配置 patch 为固定列表（sys-stdio + sys-http）。"""
        return mock.patch(
            "Django_xm.apps.tools.mcp.config.get_system_mcp_servers",
            return_value=[dict(s) for s in FAKE_SYSTEM_SERVERS],
        )

    def _create_user_servers(self, user):
        """创建用户级 stdio + sse Server 各一（绕过 serializer 校验）。"""
        McpServerConfig.objects.create(
            user=user,
            name="u-stdio",
            transport="stdio",
            command="npx",
            args=["-y", "@demo/server"],
            source="user",
            tool_type="mcp",
            category=self.category,
            status="active",
        )
        McpServerConfig.objects.create(
            user=user,
            name="u-sse",
            transport="sse",
            url="https://mcp.example-public.com/sse",
            source="user",
            tool_type="mcp",
            category=self.category,
            status="active",
        )

    def test_dedup_by_name_system_priority(self):
        """同名 Server 以系统级为准（用户级同名条目被跳过，不产生重复键）。"""
        McpServerConfig.objects.create(
            user=self.user,
            name="sys-stdio",
            transport="stdio",
            command="evil.exe",
            args=[],
            source="user",
            tool_type="mcp",
            category=self.category,
            status="active",
        )
        with self._patch_system_servers():
            servers = resolve_mcp_servers(user_id=self.user.id)
        names = [s["name"] for s in servers]
        self.assertEqual(names.count("sys-stdio"), 1)
        sys_entry = next(s for s in servers if s["name"] == "sys-stdio")
        self.assertEqual(sys_entry.get("command"), "npx")  # 系统级可信配置，非用户 evil.exe

    def test_exclude_user_stdio_keeps_system_stdio(self):
        """exclude_user_stdio=True：仅排除用户自建 stdio，系统级 stdio 保留（S-04）。"""
        self._create_user_servers(self.user)
        with self._patch_system_servers():
            servers = resolve_mcp_servers(user_id=self.user.id, exclude_user_stdio=True)
        names = [s["name"] for s in servers]
        self.assertIn("sys-stdio", names)
        self.assertNotIn("u-stdio", names)
        self.assertIn("u-sse", names)

    def test_no_exclude_keeps_user_stdio(self):
        """exclude_user_stdio=False（agent 运行时默认）：用户级 stdio 保留
        （安全过滤由调用方白名单负责，与原 _load_mcp_tools_async 行为一致）。"""
        self._create_user_servers(self.user)
        with self._patch_system_servers():
            servers = resolve_mcp_servers(user_id=self.user.id)
        names = [s["name"] for s in servers]
        self.assertIn("u-stdio", names)
        self.assertIn("u-sse", names)

    def test_none_user_returns_system_only(self):
        """user_id 为空：仅返回系统级配置。"""
        self._create_user_servers(self.user)
        with self._patch_system_servers():
            servers = resolve_mcp_servers()
        self.assertEqual([s["name"] for s in servers], ["sys-stdio", "sys-http"])

    def test_inactive_user_server_excluded(self):
        """用户级 status != active 的 Server 不参与合并（与原两处查询条件一致）。"""
        self._create_user_servers(self.user)
        McpServerConfig.objects.filter(user=self.user, name="u-sse").update(status="disabled")
        with self._patch_system_servers():
            servers = resolve_mcp_servers(user_id=self.user.id)
        names = [s["name"] for s in servers]
        self.assertNotIn("u-sse", names)

    def test_user_query_failure_degrades_to_system(self):
        """用户级配置查询失败：warning 降级为仅系统级，不阻断装配。"""
        with self._patch_system_servers(), mock.patch(
            "Django_xm.apps.tools.mcp.resolve_servers.collect_user_mcp_servers",
            side_effect=RuntimeError("db down"),
        ):
            servers = resolve_mcp_servers(user_id=self.user.id)
        self.assertEqual([s["name"] for s in servers], ["sys-stdio", "sys-http"])

    def test_two_entrypoints_consistent(self):
        """两入口对同一数据产出一致：运行时装配的 server 集合（exclude 自建 stdio）
        与管理台白名单 _get_allowed_server_names 完全一致。"""
        self._create_user_servers(self.user)
        from Django_xm.apps.tools.managers import McpToolManager

        with self._patch_system_servers():
            resolved = resolve_mcp_servers(user_id=self.user.id, exclude_user_stdio=True)
            allowed = McpToolManager._get_allowed_server_names(self.user)
        self.assertEqual(allowed, [s["name"] for s in resolved])
        self.assertEqual(set(allowed), {"sys-stdio", "sys-http", "u-sse"})
