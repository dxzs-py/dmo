"""MCP Server 安全加固测试（审计项 S-04 / S-07）。

覆盖：
- S-04 stdio 收敛（任意 command → 服务端子进程执行 / RCE）：
  1. serializer 层：普通用户 POST/PUT stdio Server → 400 ValidationError
     （"stdio 传输仅限管理员配置"）；staff 用户可正常创建
  2. manager 纵深：McpToolManager.add_server 直接被非管理员调用返回 FORBIDDEN
  3. 加载层：系统级 Server（config 模块 + DB source="system"）为管理员维护的
     可信配置，对所有用户保留（含 stdio，普通用户可加载 sequential-thinking
     类系统工具）；非管理员仅跳过用户自建（source="user"）的 stdio Server
- S-07 URL SSRF 收敛：回环 / RFC1918 / 169.254 链路本地（云元数据）/
  0.0.0.0/8 / DNS 解析失败一律拒绝；公网 URL（mock getaddrinfo）通过；
  settings 豁免开关 MCP_SSRF_ALLOW_PRIVATE_ADDRESSES 默认关闭

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.tools.tests.test_mcp_server_security --noinput
"""

import os
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

import ipaddress
import socket
from asgiref.sync import sync_to_async
from unittest.mock import AsyncMock, patch

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from Django_xm.async_utils import run_async
from Django_xm.apps.tools import views_mcp
from Django_xm.apps.tools.managers import McpToolManager
from Django_xm.apps.tools.models import McpServerConfig, ToolCategory
from Django_xm.apps.tools.serializers import (
    McpServerAddSerializer,
    McpServerDiscoverSerializer,
    _is_blocked_ip,
)

TOOLS_URL = "/api/v1/tools/"

# 固定的系统级 MCP 配置（stdio + http 各一），避免测试依赖真实默认值与环境变量
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


class McpSecurityTestBase:
    """公共 setUp：普通用户 + staff 用户 + 认证客户端 + general 分类。

    同时被 TestCase 与 TransactionTestCase 子类复用：加载链测试
    （StdioLoadIsolationTests）必须用 TransactionTestCase——_fetch_mcp_tools
    内的 sync_to_async 在 asgiref 共享工作线程执行 ORM 查询，该线程连接
    读不到 TestCase 事务内未提交的数据。
    """

    def setUp(self):
        self.client = APIClient()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="secuser", password="pw123456")
        self.staff = user_model.objects.create_user(username="secstaff", password="pw123456", is_staff=True)
        self.client.force_authenticate(user=self.user)
        self.category, _ = ToolCategory.objects.get_or_create(code="general", defaults={"name": "通用"})

    def tearDown(self):
        # 在 asgiref 共享工作线程内关闭该线程自己的 ORM 连接，避免测试库销毁时
        # 连接残留（ObjectInUse）——connections.close_all 仅作用于当前（工作）线程，
        # 不能传主线程的 connection 对象（Django 禁止 DatabaseWrapper 跨线程使用）
        run_async(sync_to_async(connections.close_all)())

    @staticmethod
    def _patch_system_servers():
        """将系统级 MCP 配置 patch 为固定列表（sys-stdio + sys-http）。"""
        return patch(
            "Django_xm.apps.tools.mcp.config.get_system_mcp_servers",
            return_value=[dict(s) for s in FAKE_SYSTEM_SERVERS],
        )

    def _create_user_servers(self, user):
        """直接经 ORM 创建用户级（source="user"）stdio + sse Server 各一（绕过 serializer 校验）。"""
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


class StdioAdminOnlyTests(McpSecurityTestBase, TestCase):
    """S-04：stdio 传输仅限管理员配置（serializer 层 + manager 纵深）。"""

    def test_normal_user_cannot_add_stdio_server(self):
        """普通用户 POST stdio Server → 400 ValidationError，且不入库。"""
        resp = self.client.post(
            f"{TOOLS_URL}mcp/servers/",
            {"name": "evil", "transport": "stdio", "command": "calc.exe"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.content)
        details = resp.json()["data"]["details"]
        self.assertIn("stdio 传输仅限管理员配置", str(details))
        self.assertFalse(McpServerConfig.objects.filter(name="evil").exists())

    def test_staff_user_can_add_stdio_server(self):
        """staff 用户可创建 stdio Server（入库且 status=active）。"""
        self.client.force_authenticate(user=self.staff)
        resp = self.client.post(
            f"{TOOLS_URL}mcp/servers/",
            {"name": "local-mcp", "transport": "stdio", "command": "npx", "args": ["-y", "@demo/server"]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        obj = McpServerConfig.objects.get(user=self.staff, name="local-mcp")
        self.assertEqual(obj.transport, "stdio")
        self.assertEqual(obj.status, "active")

    def test_manager_add_server_rejects_stdio_for_normal_user(self):
        """manager 纵深：非管理员直接调用 add_server 传 stdio 返回 FORBIDDEN。"""
        result = McpToolManager().add_server(self.user, {"name": "direct", "transport": "stdio", "command": "sh"})
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "FORBIDDEN")
        self.assertFalse(McpServerConfig.objects.filter(name="direct").exists())

    def test_update_server_cannot_switch_to_stdio(self):
        """普通用户 PUT 已有 sse Server 切换为 stdio → 400，transport 保持不变。"""
        addr_info = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        with mock.patch("socket.getaddrinfo", return_value=addr_info):
            result = McpToolManager().add_server(
                self.user, {"name": "mysrv", "transport": "sse", "url": "https://mcp.example-public.com/sse"}
            )
        self.assertTrue(result["success"], result)
        resp = self.client.put(
            f"{TOOLS_URL}mcp/servers/mysrv/",
            {"transport": "stdio", "command": "sh"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.content)
        obj = McpServerConfig.objects.get(user=self.user, name="mysrv")
        self.assertEqual(obj.transport, "sse")

    def test_add_server_rejects_private_url(self):
        """普通用户 POST 内网 URL（Redis 端口）→ 400，url 字段报错。"""
        resp = self.client.post(
            f"{TOOLS_URL}mcp/servers/",
            {"name": "internal", "transport": "http", "url": "http://192.168.1.1:6379"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("内网", str(resp.json()["data"]["details"]["url"]))

    def test_manager_add_server_rejects_private_url(self):
        """manager 纵深：直接调用 add_server 传回环 URL 返回 VALIDATION。"""
        result = McpToolManager().add_server(
            self.user, {"name": "direct-url", "transport": "sse", "url": "http://127.0.0.1:6379"}
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "VALIDATION")
        self.assertIn("回环", result["message"])


class StdioLoadIsolationTests(McpSecurityTestBase, TransactionTestCase):
    """S-04 加载层：系统级 stdio（可信配置）对所有用户保留，仅过滤用户自建 stdio。"""

    def test_merged_servers_keep_system_stdio_for_normal_user(self):
        """_get_merged_mcp_servers：普通用户保留系统级 stdio（config 模块，可信）。"""
        self._create_user_servers(self.user)
        with self._patch_system_servers():
            servers = views_mcp._get_merged_mcp_servers(user=self.user)
        names = [s.get("name") for s in servers]
        self.assertIn("sys-stdio", names)
        self.assertIn("sys-http", names)
        self.assertIn("u-sse", names)

    def test_merged_servers_skip_user_stdio_for_normal_user(self):
        """_get_merged_mcp_servers：普通用户结果不含用户自建 stdio。"""
        self._create_user_servers(self.user)
        with self._patch_system_servers():
            servers = views_mcp._get_merged_mcp_servers(user=self.user)
        names = [s.get("name") for s in servers]
        self.assertNotIn("u-stdio", names)

    def test_merged_servers_keep_db_system_stdio_for_normal_user(self):
        """_get_merged_mcp_servers：DB 中 source="system" 的 stdio（管理员持有）对普通用户保留。"""
        McpServerConfig.objects.create(
            user=self.user,
            name="db-sys-stdio",
            transport="stdio",
            command="npx",
            args=["-y", "@demo/server"],
            source="system",
            tool_type="mcp",
            category=self.category,
            status="active",
        )
        with self._patch_system_servers():
            servers = views_mcp._get_merged_mcp_servers(user=self.user)
        names = [s.get("name") for s in servers]
        self.assertIn("db-sys-stdio", names)

    def test_merged_servers_include_all_stdio_for_staff(self):
        """_get_merged_mcp_servers：staff 用户结果包含全部 stdio（管理员不设限）。"""
        self._create_user_servers(self.staff)
        with self._patch_system_servers():
            servers = views_mcp._get_merged_mcp_servers(user=self.staff)
        names = [s.get("name") for s in servers]
        self.assertIn("u-stdio", names)
        self.assertIn("sys-stdio", names)

    def test_fetch_mcp_tools_loads_only_system_stdio_for_normal_user(self):
        """GET /tools/mcp 加载链：普通用户 stdio 调用仅来自系统级（用户自建 u-stdio 不触发）。"""
        self._create_user_servers(self.user)
        fake_tool = SimpleNamespace(name="tool-a", description="demo")
        with (
            self._patch_system_servers(),
            patch("Django_xm.apps.tools.mcp.is_mcp_available", return_value=True),
            patch(
                "Django_xm.apps.tools.mcp.get_mcp_tools",
                new_callable=AsyncMock,
                return_value=[fake_tool],
            ) as mock_get,
        ):
            data = run_async(views_mcp._fetch_mcp_tools(user=self.user))

        self.assertTrue(data["available"])
        stdio_calls = [c for c in mock_get.call_args_list if c.kwargs.get("transport") == "stdio"]
        # 仅系统级 sys-stdio 触发一次 stdio 调用（用户自建 u-stdio 被跳过），
        # 且 command 来自系统级可信配置
        self.assertEqual(len(stdio_calls), 1)
        self.assertEqual(stdio_calls[0].kwargs.get("server_name"), "sys-stdio")
        self.assertEqual(stdio_calls[0].kwargs.get("command"), "npx")
        transports = [c.kwargs.get("transport") for c in mock_get.call_args_list]
        self.assertIn("sse", transports)
        self.assertIn("http", transports)
        tool_servers = {t["server"] for t in data["tools"]}
        self.assertEqual(tool_servers, {"sys-stdio", "sys-http", "u-sse"})

    def test_fetch_mcp_tools_calls_stdio_for_staff(self):
        """GET /tools/mcp 加载链：staff 用户保留全部 stdio 调用（系统级 + 用户级）。"""
        self._create_user_servers(self.staff)
        fake_tool = SimpleNamespace(name="tool-a", description="demo")
        with (
            self._patch_system_servers(),
            patch("Django_xm.apps.tools.mcp.is_mcp_available", return_value=True),
            patch(
                "Django_xm.apps.tools.mcp.get_mcp_tools",
                new_callable=AsyncMock,
                return_value=[fake_tool],
            ) as mock_get,
        ):
            data = run_async(views_mcp._fetch_mcp_tools(user=self.staff))

        stdio_calls = [c for c in mock_get.call_args_list if c.kwargs.get("transport") == "stdio"]
        self.assertEqual(len(stdio_calls), 2)  # sys-stdio + u-stdio
        self.assertTrue(all(c.kwargs.get("command") for c in stdio_calls))
        self.assertTrue(data["tools"])

    def test_get_available_tools_keeps_system_stdio_for_normal_user(self):
        """Agent 工具加载链：普通用户白名单含系统级 stdio，不含用户自建 stdio。"""
        self._create_user_servers(self.user)
        with (
            self._patch_system_servers(),
            patch(
                "Django_xm.apps.tools._load_mcp_tools_async",
                new_callable=AsyncMock,
                return_value=[],
            ) as mock_load,
        ):
            tools = McpToolManager().get_available_tools(self.user)

        self.assertEqual(tools, [])
        selected = mock_load.call_args.kwargs.get("selected_servers")
        self.assertIsNotNone(selected)
        self.assertIn("sys-stdio", selected)  # 系统级 stdio 保留（可信配置）
        self.assertIn("sys-http", selected)
        self.assertIn("u-sse", selected)
        self.assertNotIn("u-stdio", selected)  # 用户自建 stdio 被过滤

    def test_get_available_tools_no_filter_for_staff(self):
        """Agent 工具加载链：staff 用户不做过滤（不注入 selected_servers）。"""
        self._create_user_servers(self.staff)
        with (
            self._patch_system_servers(),
            patch(
                "Django_xm.apps.tools._load_mcp_tools_async",
                new_callable=AsyncMock,
                return_value=[],
            ) as mock_load,
        ):
            McpToolManager().get_available_tools(self.staff)

        self.assertNotIn("selected_servers", mock_load.call_args.kwargs)
        self.assertEqual(mock_load.call_args.kwargs.get("user_id"), self.staff.id)


class McpUrlSsrfValidationTests(McpSecurityTestBase, TestCase):
    """S-07：MCP URL SSRF 校验（serializer 层 + IP 判定单元级）。"""

    @staticmethod
    def _add_serializer(url):
        """构造 McpServerAddSerializer（http 传输，不涉 stdio 权限分支）。"""
        return McpServerAddSerializer(data={"name": "srv", "transport": "http", "url": url})

    def test_loopback_rejected(self):
        serializer = self._add_serializer("http://127.0.0.1:6379")
        self.assertFalse(serializer.is_valid())
        self.assertIn("内网", str(serializer.errors))

    def test_metadata_endpoint_rejected(self):
        """169.254.169.254 云元数据地址拒绝。"""
        serializer = self._add_serializer("http://169.254.169.254/latest/meta-data/")
        self.assertFalse(serializer.is_valid())

    def test_rfc1918_rejected(self):
        for url in ("http://10.0.0.5", "http://172.16.0.9", "http://192.168.1.1"):
            with self.subTest(url=url):
                serializer = self._add_serializer(url)
                self.assertFalse(serializer.is_valid(), serializer.errors)

    def test_zero_network_rejected(self):
        serializer = self._add_serializer("http://0.0.0.0")
        self.assertFalse(serializer.is_valid())

    def test_ipv6_loopback_rejected(self):
        serializer = self._add_serializer("http://[::1]:8080/")
        self.assertFalse(serializer.is_valid())

    def test_blocked_ip_unit_cases(self):
        """_is_blocked_ip 单元级：含 IPv6-mapped IPv4 回环还原。"""
        blocked = [
            "127.0.0.1",
            "::1",
            "::ffff:127.0.0.1",
            "169.254.169.254",
            "172.16.1.1",
            "0.0.0.0",
            "10.1.2.3",
            "192.168.0.1",
        ]
        for raw in blocked:
            with self.subTest(ip=raw):
                self.assertTrue(_is_blocked_ip(ipaddress.ip_address(raw)))
        self.assertFalse(_is_blocked_ip(ipaddress.ip_address("93.184.216.34")))

    def test_unresolvable_host_rejected(self):
        """DNS 解析失败一律拒绝。"""
        with mock.patch("socket.getaddrinfo", side_effect=socket.gaierror("resolution failed")):
            serializer = self._add_serializer("http://nonexistent.invalid/mcp")
            self.assertFalse(serializer.is_valid())
        self.assertIn("无法解析", str(serializer.errors))

    def test_public_domain_resolving_public_ip_passes(self):
        """公网域名解析为公网 IP → 通过（mock getaddrinfo，不依赖真实 DNS）。"""
        addr_info = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        with mock.patch("socket.getaddrinfo", return_value=addr_info):
            serializer = self._add_serializer("https://mcp.example-public.com/mcp")
            self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_domain_resolving_internal_ip_rejected(self):
        """域名解析到内网 IP → 拒绝（防 DNS rebinding）。"""
        addr_info = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 0))]
        with mock.patch("socket.getaddrinfo", return_value=addr_info):
            serializer = self._add_serializer("https://rebind.example.com/mcp")
            self.assertFalse(serializer.is_valid())

    @override_settings(MCP_SSRF_ALLOW_PRIVATE_ADDRESSES=True)
    def test_exempt_setting_allows_private_url(self):
        """settings 豁免开关（默认关）：显式开启后内网 URL 放行。"""
        serializer = self._add_serializer("http://127.0.0.1:6379")
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_registry_url_internal_rejected(self):
        """发现接口 registry_url 同样做 SSRF 校验。"""
        serializer = McpServerDiscoverSerializer(data={"registry_url": "http://169.254.169.254/latest/"})
        self.assertFalse(serializer.is_valid())
        self.assertIn("registry_url", serializer.errors)

    def test_registry_url_public_passes(self):
        addr_info = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        with mock.patch("socket.getaddrinfo", return_value=addr_info):
            serializer = McpServerDiscoverSerializer(
                data={"registry_url": "https://registry.example-public.com/servers.json"}
            )
            self.assertTrue(serializer.is_valid(), serializer.errors)
