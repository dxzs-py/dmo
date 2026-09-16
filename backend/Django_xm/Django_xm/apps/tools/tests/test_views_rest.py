"""tools app REST 资源化路由与视图测试（dj-06 回归）。

覆盖：
- 旧动词路由不可达（POST add/update/delete/toggle/create/upload 等；path 参数化后
  动词名命中同名资源路由返回 405，未匹配任何路由返回 404——均代表旧端点已死）
- 四组资源（McpServer / CustomTool / Skill / SkillPackage）的
  POST/PATCH/PUT/DELETE 新路径可达，响应 {code, message, data} 结构与字段形状
  与重构前逐字段一致（manager/loader 层 mock，隔离 MCP 连接等外部依赖）
- name 字符集边界校验（创建入口拒绝 "/" 等路径不安全字符，返回 400）
- 限流类按方法挂载（GET=MetaRateThrottle / POST=SensitiveOperationRateThrottle /
  其余回退全局 DEFAULT_THROTTLE_CLASSES，与合并前各独立视图一致）

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.tools.tests.test_views_rest --noinput
"""

import os
from typing import ClassVar
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient, APIRequestFactory

from Django_xm.apps.core.throttling import (
    AnonymousRateThrottle,
    MetaRateThrottle,
    SensitiveOperationRateThrottle,
    UserRateThrottle,
)
from Django_xm.apps.tools import views_mcp
from Django_xm.apps.tools.models import CustomTool, SkillPackage, ToolCategory

TOOLS_URL = "/api/v1/tools/"

# 通过 compile 语法校验的最小 @tool 代码（@tool 未定义也无妨，compile 只查语法）
VALID_TOOL_CODE = "@tool\ndef demo_tool(query: str) -> str:\n    '''demo tool'''\n    return query\n"


class ToolsRestTestBase(TestCase):
    """公共 setUp：认证客户端 + general 分类兜底。"""

    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(username="restuser", password="pw123456")
        self.client.force_authenticate(user=self.user)
        self.category, _ = ToolCategory.objects.get_or_create(code="general", defaults={"name": "通用"})


class OldVerbRoutesRemovedTests(ToolsRestTestBase):
    """旧动词路由与旧动词视图直接删除，不留兼容层。"""

    def test_old_verb_routes_unavailable(self):
        """旧动词端点全部不可用（404=路由不存在 / 405=命中同名资源路由但方法不允许）。"""
        cases = [
            # (method, path, 期望状态码)
            ("POST", "mcp/test/", 404),  # 旧集合动作路径，不匹配任何新路由
            ("POST", "mcp/servers/add/", 405),  # 命中 mcp/servers/<str:name>/（name="add"）
            ("POST", "mcp/servers/update/", 405),
            ("POST", "mcp/servers/delete/", 405),
            ("POST", "mcp/servers/toggle/", 405),
            ("POST", "upload/", 404),  # 旧 CustomTool 创建路径
            ("POST", "custom/delete/", 405),
            ("POST", "custom/toggle/", 405),
            ("POST", "custom/update/", 405),
            ("POST", "skills/create/", 405),
            ("POST", "skills/update/", 405),
            ("POST", "skills/delete/", 405),
            ("POST", "skills/toggle/", 405),
            ("POST", "skills/upload/", 405),  # 旧 SkillPackage 上传路径
            ("POST", "skills/packages/delete/", 405),
            ("POST", "skills/packages/toggle/", 405),
        ]
        for method, path, expected in cases:
            with self.subTest(path=path):
                resp = getattr(self.client, method.lower())(f"{TOOLS_URL}{path}", {}, format="json")
                self.assertEqual(resp.status_code, expected, resp.content)

    def test_old_verb_view_classes_removed(self):
        """旧动词 View 类已删除。"""
        removed = [
            "McpServerAddView",
            "McpServerUpdateView",
            "McpServerDeleteView",
            "McpServerToggleView",
            "McpServerListView",
            "ToolUploadView",
            "CustomToolListView",
            "CustomToolDeleteView",
            "CustomToolToggleView",
            "CustomToolUpdateView",
            "SkillListView",
            "SkillCreateView",
            "SkillDeleteView",
            "SkillUpdateView",
            "SkillToggleView",
            "SkillPackageListView",
            "SkillPackageUploadView",
            "SkillPackageDeleteView",
            "SkillPackageToggleView",
        ]
        for name in removed:
            self.assertFalse(hasattr(views_mcp, name), f"旧视图类 {name} 应已删除")


class McpServerResourceTests(ToolsRestTestBase):
    """McpServer 资源：POST /mcp/servers/、PUT|DELETE /mcp/servers/{name}/、PATCH .../status/。"""

    ADD_RESULT: ClassVar[dict] = {
        "success": True,
        "message": "MCP Server 'demo' 添加成功",
        "data": {"name": "demo", "transport": "sse", "url": "https://example.com/sse"},
    }

    def test_create_server(self):
        with mock.patch("Django_xm.apps.tools.managers.McpToolManager") as mock_manager_cls:
            mock_manager_cls.return_value.add_server.return_value = self.ADD_RESULT
            resp = self.client.post(
                f"{TOOLS_URL}mcp/servers/",
                {"name": "demo", "transport": "sse", "url": "https://example.com/sse"},
                format="json",
            )

        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertIn("code", body)
        self.assertIn("message", body)
        self.assertEqual(body["data"], self.ADD_RESULT["data"])
        mock_manager_cls.return_value.add_server.assert_called_once()
        args, _ = mock_manager_cls.return_value.add_server.call_args
        self.assertEqual(args[1]["name"], "demo")

    def test_create_server_rejects_unsafe_name(self):
        with mock.patch("Django_xm.apps.tools.managers.McpToolManager") as mock_manager_cls:
            resp = self.client.post(
                f"{TOOLS_URL}mcp/servers/",
                {"name": "evil/server", "transport": "sse", "url": "https://example.com/sse"},
                format="json",
            )

        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("路径不安全字符", str(resp.json()["data"]["details"]["name"]))
        mock_manager_cls.return_value.add_server.assert_not_called()

    def test_create_server_accepts_chinese_name(self):
        with mock.patch("Django_xm.apps.tools.managers.McpToolManager") as mock_manager_cls:
            mock_manager_cls.return_value.add_server.return_value = self.ADD_RESULT
            resp = self.client.post(
                f"{TOOLS_URL}mcp/servers/",
                {"name": "中文服务", "transport": "sse", "url": "https://example.com/sse"},
                format="json",
            )

        self.assertEqual(resp.status_code, 200, resp.content)

    def test_update_server_uses_path_name(self):
        """PUT 端点 name 以 path 为准，body 不再需要携带 name。"""
        update_result = {
            "success": True,
            "message": "MCP Server 'demo' 更新成功",
            "data": {"name": "demo", "transport": "sse", "url": "https://example.com/v2"},
        }
        with mock.patch("Django_xm.apps.tools.managers.McpToolManager") as mock_manager_cls:
            mock_manager_cls.return_value.update_server.return_value = update_result
            resp = self.client.put(
                f"{TOOLS_URL}mcp/servers/demo/",
                {"transport": "sse", "url": "https://example.com/v2"},
                format="json",
            )

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["data"], update_result["data"])
        args, _ = mock_manager_cls.return_value.update_server.call_args
        self.assertEqual(args[1]["name"], "demo")

    def test_update_server_body_name_is_ignored(self):
        with mock.patch("Django_xm.apps.tools.managers.McpToolManager") as mock_manager_cls:
            mock_manager_cls.return_value.update_server.return_value = {
                "success": True,
                "message": "ok",
                "data": {"name": "demo"},
            }
            self.client.put(
                f"{TOOLS_URL}mcp/servers/demo/",
                {"name": "other", "transport": "sse", "url": "https://example.com/sse"},
                format="json",
            )

        args, _ = mock_manager_cls.return_value.update_server.call_args
        self.assertEqual(args[1]["name"], "demo", "body 携带的 name 必须被 path 覆盖")

    def test_delete_server(self):
        with mock.patch("Django_xm.apps.tools.managers.McpToolManager") as mock_manager_cls:
            mock_manager_cls.return_value.delete_user_tool.return_value = {"success": True, "message": "已删除"}
            resp = self.client.delete(f"{TOOLS_URL}mcp/servers/demo/")

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertIsNone(resp.json()["data"])
        mock_manager_cls.return_value.delete_user_tool.assert_called_once_with("demo", self.user)

    def test_toggle_server_with_status(self):
        with mock.patch("Django_xm.apps.tools.managers.McpToolManager") as mock_manager_cls:
            mock_manager_cls.return_value.toggle_user_tool.return_value = {
                "success": True,
                "message": "MCP Server 'demo' 已禁用",
                "status": "disabled",
            }
            resp = self.client.patch(
                f"{TOOLS_URL}mcp/servers/demo/status/",
                {"status": "disabled"},
                format="json",
            )

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["data"], {"name": "demo", "status": "disabled"})
        mock_manager_cls.return_value.toggle_user_tool.assert_called_once_with("demo", self.user, "disabled")

    def test_toggle_server_without_status_passes_none(self):
        """body 无 status 时传 None（manager 内自动取反语义保持）。"""
        with mock.patch("Django_xm.apps.tools.managers.McpToolManager") as mock_manager_cls:
            mock_manager_cls.return_value.toggle_user_tool.return_value = {
                "success": True,
                "message": "已切换",
                "status": "active",
            }
            resp = self.client.patch(f"{TOOLS_URL}mcp/servers/demo/status/", {}, format="json")

        self.assertEqual(resp.status_code, 200, resp.content)
        mock_manager_cls.return_value.toggle_user_tool.assert_called_once_with("demo", self.user, None)

    def test_server_test_action(self):
        """POST /mcp/servers/{name}/test/ 仅测已保存的 active server。"""
        test_data = {
            "server": "demo",
            "transport": "sse",
            "connected": True,
            "tools": [{"name": "t1", "description": ""}],
            "tool_count": 1,
        }
        with (
            mock.patch.object(
                views_mcp,
                "_get_merged_mcp_servers",
                return_value=[{"name": "demo", "transport": "sse", "url": "https://example.com/sse"}],
            ),
            mock.patch.object(views_mcp, "_test_mcp_server"),
            mock.patch.object(views_mcp, "run_async", return_value=test_data),
        ):
            resp = self.client.post(f"{TOOLS_URL}mcp/servers/demo/test/")

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["data"], test_data)

    def test_server_test_action_not_found(self):
        with mock.patch.object(views_mcp, "_get_merged_mcp_servers", return_value=[]):
            resp = self.client.post(f"{TOOLS_URL}mcp/servers/ghost/test/")

        self.assertEqual(resp.status_code, 404, resp.content)
        self.assertIn("未找到 MCP Server", resp.json()["message"])


class CustomToolResourceTests(ToolsRestTestBase):
    """CustomTool 资源：POST /custom/（原 /upload/）、PUT|DELETE /custom/{name}/、PATCH .../status/。"""

    def test_upload_tool(self):
        resp = self.client.post(
            f"{TOOLS_URL}custom/",
            {"name": "demo-tool", "code": VALID_TOOL_CODE, "description": "demo"},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()["data"]
        self.assertEqual(
            sorted(data.keys()),
            ["approval_status", "description", "id", "name", "status"],
        )
        self.assertEqual(data["name"], "demo-tool")
        self.assertEqual(data["approval_status"], "pending")
        self.assertTrue(CustomTool.objects.filter(user=self.user, name="demo-tool").exists())

    def test_upload_tool_rejects_unsafe_name(self):
        resp = self.client.post(
            f"{TOOLS_URL}custom/",
            {"name": "evil/tool", "code": VALID_TOOL_CODE},
            format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("路径不安全字符", str(resp.json()["data"]["details"]["name"]))
        self.assertFalse(CustomTool.objects.filter(user=self.user, name="evil/tool").exists())

    def test_update_tool(self):
        CustomTool.objects.create(
            user=self.user,
            name="demo-tool",
            code=VALID_TOOL_CODE,
            tool_type="langchain",
            category=self.category,
            source="user",
        )
        resp = self.client.put(
            f"{TOOLS_URL}custom/demo-tool/",
            {"description": "updated"},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(
            resp.json()["data"],
            {"name": "demo-tool", "description": "updated", "status": "active"},
        )

    def test_update_tool_not_found(self):
        resp = self.client.put(f"{TOOLS_URL}custom/ghost/", {"description": "x"}, format="json")
        self.assertEqual(resp.status_code, 404, resp.content)
        self.assertIn("未找到自定义工具", resp.json()["message"])

    def test_delete_tool(self):
        with mock.patch("Django_xm.apps.tools.managers.LangChainToolManager") as mock_manager_cls:
            mock_manager_cls.return_value.delete_user_tool.return_value = {"success": True, "message": "已删除"}
            resp = self.client.delete(f"{TOOLS_URL}custom/demo-tool/")

        self.assertEqual(resp.status_code, 200, resp.content)
        mock_manager_cls.return_value.delete_user_tool.assert_called_once_with("demo-tool", self.user)

    def test_toggle_tool(self):
        with mock.patch("Django_xm.apps.tools.managers.LangChainToolManager") as mock_manager_cls:
            mock_manager_cls.return_value.toggle_user_tool.return_value = {
                "success": True,
                "message": "已切换",
                "status": "disabled",
            }
            resp = self.client.patch(f"{TOOLS_URL}custom/demo-tool/status/", {"status": "disabled"}, format="json")

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["data"], {"name": "demo-tool", "status": "disabled"})


class SkillResourceTests(ToolsRestTestBase):
    """Skill 资源：POST /skills/、PUT|DELETE /skills/{name}/、PATCH .../status/。"""

    CREATE_RESULT: ClassVar[dict] = {
        "success": True,
        "message": "Skill 'demo' 创建成功",
        "data": {"id": 1, "name": "demo", "mode": "pipeline", "steps": [], "version": "1.0.0", "status": "active"},
    }

    def test_create_skill(self):
        with mock.patch("Django_xm.apps.tools.managers.SkillToolManager") as mock_manager_cls:
            mock_manager_cls.return_value.create_skill.return_value = self.CREATE_RESULT
            resp = self.client.post(
                f"{TOOLS_URL}skills/",
                {"name": "demo", "steps": [{"tool_name": "web_search"}]},
                format="json",
            )

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["data"], self.CREATE_RESULT["data"])

    def test_create_skill_rejects_unsafe_name(self):
        with mock.patch("Django_xm.apps.tools.managers.SkillToolManager") as mock_manager_cls:
            resp = self.client.post(
                f"{TOOLS_URL}skills/",
                {"name": "evil/skill", "steps": [{"tool_name": "web_search"}]},
                format="json",
            )

        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("路径不安全字符", str(resp.json()["data"]["details"]["name"]))
        mock_manager_cls.return_value.create_skill.assert_not_called()

    def test_update_skill_uses_path_name(self):
        update_result = {
            "success": True,
            "message": "Skill 'demo' 更新成功",
            "data": {"id": 1, "name": "demo", "steps": [], "status": "active"},
        }
        with mock.patch("Django_xm.apps.tools.managers.SkillToolManager") as mock_manager_cls:
            mock_manager_cls.return_value.update_skill.return_value = update_result
            resp = self.client.put(
                f"{TOOLS_URL}skills/demo/",
                {"steps": [{"tool_name": "web_search"}]},
                format="json",
            )

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["data"], update_result["data"])
        args, _ = mock_manager_cls.return_value.update_skill.call_args
        self.assertEqual(args[1]["name"], "demo")

    def test_delete_skill_accepts_prefixed_name(self):
        """删除端点兼容带 skill_ 前缀的 name（manager 内 strip 语义保持）。"""
        with mock.patch("Django_xm.apps.tools.managers.SkillToolManager") as mock_manager_cls:
            mock_manager_cls.return_value.delete_user_tool.return_value = {"success": True, "message": "已删除"}
            resp = self.client.delete(f"{TOOLS_URL}skills/skill_demo/")

        self.assertEqual(resp.status_code, 200, resp.content)
        mock_manager_cls.return_value.delete_user_tool.assert_called_once_with("skill_demo", self.user)

    def test_toggle_skill(self):
        with mock.patch("Django_xm.apps.tools.managers.SkillToolManager") as mock_manager_cls:
            mock_manager_cls.return_value.toggle_user_tool.return_value = {
                "success": True,
                "message": "已切换",
                "status": "disabled",
            }
            resp = self.client.patch(f"{TOOLS_URL}skills/demo/status/", {"status": "disabled"}, format="json")

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["data"], {"name": "demo", "status": "disabled"})

    def test_toggle_skill_rejects_invalid_status(self):
        """status 非法值返回 400（沿用 SkillToggleSerializer 校验语义）。"""
        with mock.patch("Django_xm.apps.tools.managers.SkillToolManager") as mock_manager_cls:
            resp = self.client.patch(f"{TOOLS_URL}skills/demo/status/", {"status": "bogus"}, format="json")

        self.assertEqual(resp.status_code, 400, resp.content)
        mock_manager_cls.return_value.toggle_user_tool.assert_not_called()


class SkillPackageResourceTests(ToolsRestTestBase):
    """SkillPackage 资源：POST /skills/packages/、GET|DELETE .../{name}/、PATCH .../status/。"""

    def _zip_file(self, filename="pkg.zip"):
        return SimpleUploadedFile(filename, b"fake-zip-content", content_type="application/zip")

    def _patch_loader(self, validate_ret, install_ret=None):
        """patch SkillLoader 类：validate 返回 (is_valid, msg, frontmatter)。"""
        patcher = mock.patch("Django_xm.apps.tools.skills.loader.SkillLoader")
        mock_loader_cls = patcher.start()
        mock_loader_cls.return_value.validate_skill_package.return_value = validate_ret
        mock_loader_cls.return_value.install_skill_package.return_value = install_ret
        self.addCleanup(patcher.stop)
        return mock_loader_cls

    def test_upload_package(self):
        metadata = {"name": "my-pkg", "description": "demo", "version": "1.0.0"}
        mock_loader_cls = self._patch_loader(
            validate_ret=(True, "", {"name": "my-pkg", "description": "demo"}),
            install_ret=(True, "Skill 包安装成功", metadata),
        )
        resp = self.client.post(f"{TOOLS_URL}skills/packages/", {"file": self._zip_file()}, format="multipart")

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["data"], metadata)
        mock_loader_cls.return_value.install_skill_package.assert_called_once()

    def test_upload_package_rejects_unsafe_name(self):
        """ZIP frontmatter 的 name 含 "/" 时拒绝安装（400，不落库不落盘）。"""
        mock_loader_cls = self._patch_loader(
            validate_ret=(True, "", {"name": "evil/pkg", "description": "demo"}),
            install_ret=(True, "安装成功", {"name": "evil/pkg"}),
        )
        resp = self.client.post(f"{TOOLS_URL}skills/packages/", {"file": self._zip_file()}, format="multipart")

        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("路径不安全字符", resp.json()["message"])
        mock_loader_cls.return_value.install_skill_package.assert_not_called()

    def test_delete_package(self):
        with mock.patch("Django_xm.apps.tools.skills.loader.SkillLoader") as mock_loader_cls:
            mock_loader_cls.return_value.uninstall_skill_package.return_value = (True, "Skill 包已卸载")
            resp = self.client.delete(f"{TOOLS_URL}skills/packages/my-pkg/")

        self.assertEqual(resp.status_code, 200, resp.content)
        mock_loader_cls.return_value.uninstall_skill_package.assert_called_once_with("my-pkg", self.user)

    def test_delete_system_package_returns_403(self):
        with mock.patch("Django_xm.apps.tools.skills.loader.SkillLoader") as mock_loader_cls:
            mock_loader_cls.return_value.uninstall_skill_package.return_value = (False, "系统级 Skill 'x' 不可删除")
            resp = self.client.delete(f"{TOOLS_URL}skills/packages/x/")

        self.assertEqual(resp.status_code, 403, resp.content)

    def _create_package(self, name="my-pkg", status="active"):
        return SkillPackage.objects.create(
            user=self.user,
            name=name,
            description="demo",
            tool_type="skill",
            category=self.category,
            source="user",
            status=status,
            skill_dir="/tmp/skills/my-pkg",
        )

    def test_toggle_package_with_status(self):
        self._create_package(status="active")
        resp = self.client.patch(
            f"{TOOLS_URL}skills/packages/my-pkg/status/",
            {"status": "disabled"},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["data"], {"name": "my-pkg", "status": "disabled"})

    def test_toggle_package_auto_reverse_without_status(self):
        self._create_package(status="active")
        resp = self.client.patch(f"{TOOLS_URL}skills/packages/my-pkg/status/", {}, format="json")

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["data"], {"name": "my-pkg", "status": "disabled"}, "缺省 status 应自动取反")

    def test_toggle_package_not_found(self):
        resp = self.client.patch(f"{TOOLS_URL}skills/packages/ghost/status/", {}, format="json")
        self.assertEqual(resp.status_code, 404, resp.content)
        self.assertIn("未找到 Skill 包", resp.json()["message"])

    def test_package_detail(self):
        with mock.patch("Django_xm.apps.tools.skills.loader.SkillLoader") as mock_loader_cls:
            mock_loader_cls.return_value.activate_skill.return_value = "# 指令内容"
            resp = self.client.get(f"{TOOLS_URL}skills/packages/my-pkg/")

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["data"], {"name": "my-pkg", "instructions": "# 指令内容"})
        mock_loader_cls.return_value.activate_skill.assert_called_once_with("my-pkg", user_id=self.user.id)


class MethodThrottleTests(TestCase):
    """合并视图后限流类按方法挂载，与合并前各独立视图的限流一致。"""

    factory = APIRequestFactory()

    def _throttles(self, view_cls, method):
        request = getattr(self.factory, method.lower())("/api/v1/tools/x/")
        view = view_cls()
        view.request = request
        return view.get_throttles()

    def test_mcp_server_view_throttles(self):
        """GET=MetaRateThrottle（原列表视图）；POST=全局默认（原 add 视图无显式限流）。"""
        get_throttles = self._throttles(views_mcp.McpServerView, "GET")
        self.assertEqual(len(get_throttles), 1)
        self.assertIsInstance(get_throttles[0], MetaRateThrottle)

        post_throttles = self._throttles(views_mcp.McpServerView, "POST")
        self.assertEqual(
            [type(t) for t in post_throttles],
            [AnonymousRateThrottle, UserRateThrottle],
        )

    def test_custom_tool_view_throttles(self):
        """POST=SensitiveOperationRateThrottle（原上传视图防刷）；GET=全局默认（原列表视图）。"""
        post_throttles = self._throttles(views_mcp.CustomToolView, "POST")
        self.assertEqual(len(post_throttles), 1)
        self.assertIsInstance(post_throttles[0], SensitiveOperationRateThrottle)

        get_throttles = self._throttles(views_mcp.CustomToolView, "GET")
        self.assertEqual(
            [type(t) for t in get_throttles],
            [AnonymousRateThrottle, UserRateThrottle],
        )

    def test_skill_and_package_view_throttles(self):
        """Skill / SkillPackage 集合 GET=MetaRateThrottle，POST=全局默认。"""
        for view_cls in (views_mcp.SkillView, views_mcp.SkillPackageView):
            with self.subTest(view=view_cls.__name__):
                get_throttles = self._throttles(view_cls, "GET")
                self.assertEqual(len(get_throttles), 1)
                self.assertIsInstance(get_throttles[0], MetaRateThrottle)

                post_throttles = self._throttles(view_cls, "POST")
                self.assertEqual(
                    [type(t) for t in post_throttles],
                    [AnonymousRateThrottle, UserRateThrottle],
                )
