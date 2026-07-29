"""工具管理器模块

提供公共基础工具管理类和三个子类，统一管理 LangChain 内置/自定义工具、
MCP Server 工具、Skill 技能工具的查询、删除、状态切换等操作。

类层次：
    BaseToolManager (ABC)
    ├── LangChainToolManager  — langchain 内置 + 用户自定义工具
    ├── McpToolManager        — MCP Server 工具
    └── SkillToolManager      — Skill 技能工具
"""

import logging
from abc import ABC, abstractmethod
from typing import Any

from langchain_core.tools import BaseTool

from Django_xm.async_utils import run_async

logger = logging.getLogger(__name__)

_category_cache: dict[str, dict[str, str]] = {}


def _get_category_info(category_code: str) -> dict[str, str]:
    if category_code in _category_cache:
        return _category_cache[category_code]
    from Django_xm.apps.tools.models import ToolCategory

    try:
        cat = ToolCategory.objects.get(code=category_code)
        info = {"code": cat.code, "name": cat.name}
    except ToolCategory.DoesNotExist:
        info = {"code": category_code, "name": category_code}
    _category_cache[category_code] = info
    return info


def _build_user_tool_category_info(tool_obj) -> dict[str, str]:
    if tool_obj.category:
        return {"code": tool_obj.category.code, "name": tool_obj.category.name}
    return {"code": "general", "name": "通用"}


def _invalidate_category_cache():
    _category_cache.clear()


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------


class BaseToolManager(ABC):
    """工具管理器抽象基类

    子类必须覆盖 tool_type 并实现所有抽象方法。
    通用逻辑（删除/切换状态）在基类中处理系统工具与用户工具的边界判断，
    具体操作委托给子类的 _do_delete_user_tool / _do_toggle_user_tool。
    """

    tool_type: str = ""  # 子类必须覆盖: langchain / mcp / skill

    @abstractmethod
    def get_available_tools(self, user) -> list:
        """获取用户可用的工具实例列表（BaseTool 列表）"""

    @abstractmethod
    def get_tool_info_list(self, user) -> list[dict[str, Any]]:
        """获取工具信息列表

        每项包含: name, description, category, source, deletable, status
        """

    @abstractmethod
    def is_system_tool(self, tool_name: str) -> bool:
        """判断是否为系统内置工具"""

    @abstractmethod
    def is_user_tool(self, tool_name: str, user) -> bool:
        """判断是否为用户自定义工具"""

    # ---- 通用删除逻辑 ----

    def delete_user_tool(self, tool_name: str, user) -> dict[str, Any]:
        """删除用户自定义工具，系统工具不可删除

        Returns:
            {"success": bool, "message": str}
        """
        if self.is_system_tool(tool_name):
            return {"success": False, "error_code": "FORBIDDEN", "message": f"系统内置工具 '{tool_name}' 不可删除"}
        if not self.is_user_tool(tool_name, user):
            return {
                "success": False,
                "error_code": "NOT_FOUND",
                "message": f"未找到用户自定义工具 '{tool_name}'，无权操作",
            }
        return self._do_delete_user_tool(tool_name, user)

    def _do_delete_user_tool(self, tool_name: str, user) -> dict[str, Any]:
        """子类覆盖实现具体删除逻辑"""
        raise NotImplementedError

    # ---- 通用状态切换逻辑 ----

    def toggle_user_tool(self, tool_name: str, user, status: str | None = None) -> dict[str, Any]:
        """切换用户自定义工具状态

        Args:
            tool_name: 工具名称
            user: 用户实例
            status: 目标状态 ('active' / 'disabled')，为 None 时自动切换

        Returns:
            {"success": bool, "message": str, "status": str}
        """
        if self.is_system_tool(tool_name):
            return {"success": False, "error_code": "FORBIDDEN", "message": f"系统内置工具 '{tool_name}' 不可切换状态"}
        if not self.is_user_tool(tool_name, user):
            return {"success": False, "error_code": "NOT_FOUND", "message": f"未找到用户自定义工具 '{tool_name}'"}
        return self._do_toggle_user_tool(tool_name, user, status)

    def _do_toggle_user_tool(self, tool_name: str, user, status: str | None = None) -> dict[str, Any]:
        """子类覆盖实现具体切换逻辑"""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# LangChain 工具管理器
# ---------------------------------------------------------------------------

_system_tool_names: set | None = None


class LangChainToolManager(BaseToolManager):
    """LangChain 内置工具 + 用户自定义工具管理"""

    tool_type: str = "langchain"

    def get_available_tools(self, user) -> list:
        """获取内置工具 + 用户自定义工具（动态实例化），合并返回"""
        from Django_xm.apps.tools import _load_custom_tools_for_user, get_all_tools

        tools = get_all_tools()
        custom_tools = _load_custom_tools_for_user(user.id)
        tools.extend(custom_tools)
        logger.debug(
            "LangChainToolManager: 内置 %d + 自定义 %d = %d 个工具",
            len(tools) - len(custom_tools),
            len(custom_tools),
            len(tools),
        )
        return tools

    def get_tool_info_list(self, user) -> list[dict[str, Any]]:
        from Django_xm.apps.tools import get_all_tools
        from Django_xm.apps.tools.models import CustomTool

        result: list[dict[str, Any]] = []

        for tool in get_all_tools():
            meta = getattr(tool, "metadata", None) or {}
            category_code = meta.get("category", "other")
            result.append(
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "tool_type": "langchain",
                    "category": _get_category_info(category_code),
                    "source": "system",
                    "deletable": False,
                    "status": "active",
                    "visibility": meta.get("visibility", "selectable"),
                    "tier": meta.get("tier", "extended"),
                }
            )

        for tool_obj in CustomTool.objects.filter(user=user).select_related("category"):
            result.append(
                {
                    "name": tool_obj.name,
                    "description": tool_obj.description or "",
                    "tool_type": tool_obj.tool_type,
                    "category": _build_user_tool_category_info(tool_obj),
                    "source": tool_obj.source,
                    "deletable": True,
                    "status": tool_obj.status,
                    "code": tool_obj.code,
                    "visibility": "selectable",
                    "tier": "extended",
                }
            )

        return result

    def is_system_tool(self, tool_name: str) -> bool:
        """检查 tool_name 是否在内置工具名列表中（带缓存）"""
        global _system_tool_names
        if _system_tool_names is None:
            from Django_xm.apps.tools import get_all_tools

            _system_tool_names = {t.name for t in get_all_tools()}
        return tool_name in _system_tool_names

    def is_user_tool(self, tool_name: str, user) -> bool:
        """检查用户是否拥有指定名称的自定义工具"""
        from Django_xm.apps.tools.models import CustomTool

        return CustomTool.objects.filter(user=user, name=tool_name).exists()

    def _do_delete_user_tool(self, tool_name: str, user) -> dict[str, Any]:
        """删除用户自定义工具"""
        from Django_xm.apps.tools.models import CustomTool

        deleted, _ = CustomTool.objects.filter(user=user, name=tool_name).delete()
        if deleted:
            logger.info("用户自定义工具 '%s' 已删除 (user=%s)", tool_name, user.id)
            return {"success": True, "message": f"工具 '{tool_name}' 已删除"}
        return {"success": False, "error_code": "UNKNOWN", "message": f"工具 '{tool_name}' 删除失败"}

    def _do_toggle_user_tool(self, tool_name: str, user, status: str | None = None) -> dict[str, Any]:
        """切换用户自定义工具状态"""
        from Django_xm.apps.tools.models import CustomTool

        tool_obj = CustomTool.objects.filter(user=user, name=tool_name).first()
        if not tool_obj:
            return {"success": False, "error_code": "NOT_FOUND", "message": f"未找到工具 '{tool_name}'"}

        if status in ("active", "disabled"):
            tool_obj.status = status
        else:
            tool_obj.status = "disabled" if tool_obj.status == "active" else "active"

        tool_obj.save(update_fields=["status", "updated_at"])
        logger.info(
            "自定义工具 '%s' 状态切换为 %s (user=%s)",
            tool_name,
            tool_obj.status,
            user.id,
        )
        return {
            "success": True,
            "message": f"工具 '{tool_name}' 已{'启用' if tool_obj.status == 'active' else '禁用'}",
            "status": tool_obj.status,
        }


# ---------------------------------------------------------------------------
# MCP 工具管理器
# ---------------------------------------------------------------------------


class McpToolManager(BaseToolManager):
    """MCP Server 工具管理"""

    tool_type: str = "mcp"

    def get_available_tools(self, user) -> list:
        """同步获取系统级 + 用户级 MCP Server 的工具

        内部使用 _run_async 辅助函数运行异步加载逻辑。
        """
        from Django_xm.apps.tools import _load_mcp_tools_async

        tools: list[BaseTool] = run_async(_load_mcp_tools_async(user_id=user.id))
        logger.debug("McpToolManager: 获取到 %d 个 MCP 工具 (user=%s)", len(tools), user.id)
        return tools

    def get_tool_info_list(self, user) -> list[dict[str, Any]]:
        from Django_xm.apps.tools.mcp import _get_mcp_servers_config
        from Django_xm.apps.tools.models import McpServerConfig

        result: list[dict[str, Any]] = []
        general_cat = _get_category_info("general")

        for srv in _get_mcp_servers_config():
            entry: dict[str, Any] = {
                "name": srv.get("name", ""),
                "description": srv.get("description", ""),
                "tool_type": "mcp",
                "category": general_cat,
                "source": "system",
                "deletable": False,
                "status": "active" if srv.get("enabled", True) else "disabled",
                "transport": srv.get("transport", "sse"),
                "visibility": "selectable",
                "tier": "extended",
            }
            if srv.get("transport") == "stdio":
                entry["command"] = srv.get("command", "")
            else:
                entry["url"] = srv.get("url", "")
            result.append(entry)

        for srv_obj in McpServerConfig.objects.filter(user=user).select_related("category"):
            entry = {
                "name": srv_obj.name,
                "description": srv_obj.description or "",
                "tool_type": srv_obj.tool_type,
                "category": _build_user_tool_category_info(srv_obj),
                "source": srv_obj.source,
                "deletable": True,
                "status": srv_obj.status,
                "transport": srv_obj.transport,
                "visibility": "selectable",
                "tier": "extended",
            }
            if srv_obj.transport == "stdio":
                entry["command"] = srv_obj.command
                entry["args"] = srv_obj.args or []
                if srv_obj.env:
                    entry["env"] = srv_obj.env
            else:
                entry["url"] = srv_obj.url
                if srv_obj.headers:
                    entry["headers"] = srv_obj.headers
                if srv_obj.auth_token:
                    entry["auth_token"] = srv_obj.auth_token
            result.append(entry)

        return result

    def is_system_tool(self, tool_name: str) -> bool:
        """检查是否在 settings.MCP_SERVERS 配置中"""
        from django.conf import settings as django_settings

        system_servers = getattr(django_settings, "MCP_SERVERS", [])
        return any(s.get("name") == tool_name for s in system_servers)

    def is_user_tool(self, tool_name: str, user) -> bool:
        """检查用户是否拥有指定名称的 MCP Server 配置"""
        from Django_xm.apps.tools.models import McpServerConfig

        return McpServerConfig.objects.filter(user=user, name=tool_name).exists()

    def _do_delete_user_tool(self, tool_name: str, user) -> dict[str, Any]:
        """删除用户自定义 MCP Server 配置"""
        from Django_xm.apps.tools.models import McpServerConfig

        deleted, _ = McpServerConfig.objects.filter(user=user, name=tool_name).delete()
        if deleted:
            logger.info("用户 MCP Server '%s' 已删除 (user=%s)", tool_name, user.id)
            return {"success": True, "message": f"MCP Server '{tool_name}' 已删除"}
        return {"success": False, "error_code": "UNKNOWN", "message": f"MCP Server '{tool_name}' 删除失败"}

    def _do_toggle_user_tool(self, tool_name: str, user, status: str | None = None) -> dict[str, Any]:
        """切换用户 MCP Server 状态"""
        from Django_xm.apps.tools.models import McpServerConfig

        srv = McpServerConfig.objects.filter(user=user, name=tool_name).first()
        if not srv:
            return {"success": False, "error_code": "NOT_FOUND", "message": f"未找到 MCP Server '{tool_name}'"}

        if status in ("active", "disabled"):
            srv.status = status
        else:
            srv.status = "disabled" if srv.status == "active" else "active"

        srv.save(update_fields=["status", "updated_at"])
        logger.info(
            "MCP Server '%s' 状态切换为 %s (user=%s)",
            tool_name,
            srv.status,
            user.id,
        )
        return {
            "success": True,
            "message": f"MCP Server '{tool_name}' 已{'启用' if srv.status == 'active' else '禁用'}",
            "status": srv.status,
        }

    # ---- MCP 专属方法 ----

    def add_server(self, user, config_dict: dict[str, Any]) -> dict[str, Any]:
        from Django_xm.apps.tools.models import McpServerConfig, ToolCategory

        name = config_dict.get("name", "")
        transport = config_dict.get("transport", "sse")

        if not name:
            return {"success": False, "error_code": "VALIDATION", "message": "MCP Server 名称不能为空"}

        if self.is_system_server(name):
            return {
                "success": False,
                "error_code": "FORBIDDEN",
                "message": f"系统内置 MCP Server '{name}' 已存在，不可重复添加",
            }

        if McpServerConfig.objects.filter(user=user, name=name).exists():
            return {"success": False, "error_code": "VALIDATION", "message": f"MCP Server '{name}' 已存在"}

        category_code = config_dict.get("category", "general")
        try:
            category = ToolCategory.objects.get(code=category_code)
        except ToolCategory.DoesNotExist:
            category = ToolCategory.objects.get(code="general")

        try:
            srv = McpServerConfig.objects.create(
                user=user,
                name=name,
                transport=transport,
                url=config_dict.get("url", ""),
                command=config_dict.get("command", ""),
                args=config_dict.get("args", []),
                env=config_dict.get("env") or {},
                headers=config_dict.get("headers") or {},
                auth_token=config_dict.get("auth_token", ""),
                description=config_dict.get("description", ""),
                tool_type="mcp",
                category=category,
                source="user",
                status="active",
            )
            logger.info("用户添加 MCP Server: %s (transport=%s, user=%s)", name, transport, user.id)
            return {
                "success": True,
                "message": f"MCP Server '{name}' 添加成功",
                "data": srv.to_config_dict(),
            }
        except Exception as e:
            logger.exception("MCP Server 添加失败")
            return {"success": False, "error_code": "UNKNOWN", "message": f"添加失败: {e}"}

    def update_server(self, user, config_dict: dict[str, Any]) -> dict[str, Any]:
        from Django_xm.apps.tools.models import McpServerConfig, ToolCategory

        name = config_dict.get("name", "")
        if not name:
            return {"success": False, "error_code": "VALIDATION", "message": "MCP Server 名称不能为空"}

        try:
            srv = McpServerConfig.objects.get(user=user, name=name)
        except McpServerConfig.DoesNotExist:
            return {"success": False, "error_code": "NOT_FOUND", "message": f"MCP Server '{name}' 不存在"}

        transport = config_dict.get("transport", srv.transport)
        if transport in ("sse", "http", "websocket") and not config_dict.get("url", srv.url):
            return {"success": False, "error_code": "VALIDATION", "message": f"{transport} 传输协议必须提供 url"}
        if transport == "stdio" and not config_dict.get("command", srv.command):
            return {"success": False, "error_code": "VALIDATION", "message": "stdio 传输协议必须提供 command"}

        try:
            srv.transport = transport
            srv.url = config_dict.get("url", srv.url)
            srv.command = config_dict.get("command", srv.command)
            if "args" in config_dict:
                srv.args = config_dict["args"]
            if "env" in config_dict:
                srv.env = config_dict["env"]
            if "headers" in config_dict:
                srv.headers = config_dict["headers"]
            if "auth_token" in config_dict:
                srv.auth_token = config_dict["auth_token"]
            if "description" in config_dict:
                srv.description = config_dict["description"]
            if "category" in config_dict:
                try:
                    srv.category = ToolCategory.objects.get(code=config_dict["category"])
                except ToolCategory.DoesNotExist:
                    pass
            srv.save()
            logger.info("用户更新 MCP Server: %s (transport=%s, user=%s)", name, transport, user.id)
            return {
                "success": True,
                "message": f"MCP Server '{name}' 更新成功",
                "data": srv.to_config_dict(),
            }
        except Exception as e:
            logger.exception("MCP Server 更新失败")
            return {"success": False, "error_code": "UNKNOWN", "message": f"更新失败: {e}"}

    def test_server(self, user, server_name: str) -> dict[str, Any]:
        """测试 MCP Server 连接

        Args:
            user: 用户实例
            server_name: MCP Server 名称

        Returns:
            {"success": bool, "message": str, "data": dict}
        """
        from Django_xm.apps.tools.mcp import is_mcp_available

        if not is_mcp_available():
            return {"success": False, "error_code": "UNKNOWN", "message": "langchain-mcp-adapters 未安装"}

        # 合并查找目标 Server 配置
        target = self._find_server_config(server_name, user)
        if not target:
            return {"success": False, "error_code": "NOT_FOUND", "message": f"未找到 MCP Server: {server_name}"}

        try:
            data: dict[str, Any] = run_async(self._test_mcp_server_async(server_name, target))
            return {"success": True, "message": "连接测试成功", "data": data}
        except Exception as e:
            logger.exception("MCP Server 连接测试失败 (%s)", server_name)
            return {"success": False, "error_code": "UNKNOWN", "message": f"连接失败: {e}"}

    # ---- MCP 内部辅助 ----

    @staticmethod
    def is_system_server(name: str) -> bool:
        """判断是否为系统级 MCP Server"""
        from django.conf import settings as django_settings

        system_servers = getattr(django_settings, "MCP_SERVERS", [])
        return any(s.get("name") == name for s in system_servers)

    def _find_server_config(self, server_name: str, user) -> dict[str, Any] | None:
        """在系统级 + 用户级配置中查找指定名称的 Server"""
        from Django_xm.apps.tools.mcp import _get_mcp_servers_config
        from Django_xm.apps.tools.models import McpServerConfig

        # 系统级
        for srv in _get_mcp_servers_config():
            if srv.get("name") == server_name:
                return srv

        # 用户级
        srv_obj = McpServerConfig.objects.filter(user=user, name=server_name).first()
        if srv_obj:
            return srv_obj.to_config_dict()

        return None

    @staticmethod
    async def _test_mcp_server_async(server_name: str, target: dict[str, Any]) -> dict[str, Any]:
        """异步测试 MCP Server 连接"""
        from Django_xm.apps.tools.mcp import get_mcp_tools

        transport = target.get("transport", "sse")
        if transport == "stdio":
            tools = await get_mcp_tools(
                server_name=server_name,
                transport="stdio",
                command=target.get("command"),
                args=target.get("args"),
                env=target.get("env"),
            )
        else:
            url = target.get("url")
            if not url:
                raise ValueError(f"MCP Server '{server_name}' 缺少 url 配置")
            tools = await get_mcp_tools(
                server_url=url,
                transport=transport,
                headers=target.get("headers"),
                auth_token=target.get("auth_token"),
            )

        tool_list = [{"name": t.name, "description": t.description or ""} for t in tools]
        return {
            "server": server_name,
            "transport": transport,
            "connected": True,
            "tools": tool_list,
            "tool_count": len(tools),
        }


# ---------------------------------------------------------------------------
# Skill 工具管理器
# ---------------------------------------------------------------------------


class SkillToolManager(BaseToolManager):
    """Skill 技能工具管理"""

    tool_type: str = "skill"

    # SkillConfig 模型可能尚未迁移，延迟导入
    _skill_config_model = None

    @classmethod
    def _get_skill_config_model(cls):
        """延迟获取 SkillConfig 模型，导入失败返回 None"""
        if cls._skill_config_model is not None:
            return cls._skill_config_model
        try:
            from Django_xm.apps.tools.models import SkillConfig

            cls._skill_config_model = SkillConfig
            return SkillConfig
        except ImportError:
            logger.warning("SkillConfig 模型导入失败，用户自定义 Skill 功能不可用")
            return None

    @staticmethod
    def _strip_skill_prefix(tool_name: str) -> str:
        """去掉 'skill_' 前缀"""
        return tool_name.removeprefix("skill_")

    def get_available_tools(self, user) -> list:
        """获取预置 Skill + 用户自定义 Skill 的 SkillBaseTool 实例"""
        from Django_xm.apps.tools.skills import SkillBaseTool, SkillRegistryService

        tools: list = []

        # 预置 Skill + 用户自定义 Skill（SkillRegistryService.get_skills 合并两者）
        all_builtin_tools = self._get_builtin_tools_for_skill()
        for spec in SkillRegistryService.get_skills(user.id):
            tools.append(SkillBaseTool(spec=spec, available_tools=all_builtin_tools))

        logger.debug("SkillToolManager: 预置 + 自定义 = %d 个 Skill (user=%s)", len(tools), user.id)
        return tools

    def get_tool_info_list(self, user) -> list[dict[str, Any]]:
        from Django_xm.apps.tools.skills import SkillRegistryService

        result: list[dict[str, Any]] = []
        general_cat = _get_category_info("general")

        for spec in SkillRegistryService.get_presets():
            steps_desc = " → ".join(s.tool_name for s in spec.steps)
            result.append(
                {
                    "name": f"skill_{spec.name}",
                    "description": spec.description,
                    "tool_type": "skill",
                    "category": general_cat,
                    "source": "system",
                    "deletable": False,
                    "status": "active",
                    "mode": spec.mode,
                    "steps": [s.model_dump() for s in spec.steps],
                    "steps_desc": steps_desc,
                    "visibility": "selectable",
                    "tier": "extended",
                }
            )

        SkillConfig = self._get_skill_config_model()
        if SkillConfig is not None:
            for skill_config in SkillConfig.objects.filter(user=user).select_related("category"):
                steps_desc = " → ".join(
                    s.get("tool_name", "") if isinstance(s, dict) else getattr(s, "tool_name", "")
                    for s in (skill_config.steps or [])
                )
                result.append(
                    {
                        "name": f"skill_{skill_config.name}",
                        "description": skill_config.description or "",
                        "tool_type": skill_config.tool_type,
                        "category": _build_user_tool_category_info(skill_config),
                        "source": skill_config.source,
                        "deletable": True,
                        "status": skill_config.status,
                        "mode": getattr(skill_config, "mode", "pipeline"),
                        "steps": skill_config.steps or [],
                        "steps_desc": steps_desc,
                        "visibility": "selectable",
                        "tier": "extended",
                    }
                )

        return result

    def is_system_tool(self, tool_name: str) -> bool:
        """检查去掉 'skill_' 前缀后是否在 SkillRegistryService 预置技能中"""
        from Django_xm.apps.tools.skills import SkillRegistryService

        raw_name = self._strip_skill_prefix(tool_name)
        return SkillRegistryService.get(raw_name) is not None

    def is_user_tool(self, tool_name: str, user) -> bool:
        """检查用户是否拥有指定名称的自定义 Skill"""
        SkillConfig = self._get_skill_config_model()
        if SkillConfig is None:
            return False
        raw_name = self._strip_skill_prefix(tool_name)
        return SkillConfig.objects.filter(user=user, name=raw_name).exists()

    def _do_delete_user_tool(self, tool_name: str, user) -> dict[str, Any]:
        """删除用户自定义 Skill"""
        SkillConfig = self._get_skill_config_model()
        if SkillConfig is None:
            return {"success": False, "error_code": "UNKNOWN", "message": "SkillConfig 模型不可用"}

        raw_name = self._strip_skill_prefix(tool_name)
        deleted, _ = SkillConfig.objects.filter(user=user, name=raw_name).delete()
        if deleted:
            logger.info("用户 Skill '%s' 已删除 (user=%s)", raw_name, user.id)
            return {"success": True, "message": f"Skill '{raw_name}' 已删除"}
        return {"success": False, "error_code": "UNKNOWN", "message": f"Skill '{raw_name}' 删除失败"}

    def _do_toggle_user_tool(self, tool_name: str, user, status: str | None = None) -> dict[str, Any]:
        """切换用户自定义 Skill 状态"""
        SkillConfig = self._get_skill_config_model()
        if SkillConfig is None:
            return {"success": False, "error_code": "UNKNOWN", "message": "SkillConfig 模型不可用"}

        raw_name = self._strip_skill_prefix(tool_name)
        skill_config = SkillConfig.objects.filter(user=user, name=raw_name).first()
        if not skill_config:
            return {"success": False, "error_code": "NOT_FOUND", "message": f"未找到 Skill '{raw_name}'"}

        if status in ("active", "disabled"):
            skill_config.status = status
        else:
            skill_config.status = "disabled" if skill_config.status == "active" else "active"

        skill_config.save(update_fields=["status", "updated_at"])
        logger.info(
            "Skill '%s' 状态切换为 %s (user=%s)",
            raw_name,
            skill_config.status,
            user.id,
        )
        return {
            "success": True,
            "message": f"Skill '{raw_name}' 已{'启用' if skill_config.status == 'active' else '禁用'}",
            "status": skill_config.status,
        }

    # ---- Skill 专属方法 ----

    def update_skill(self, user, skill_data: dict[str, Any]) -> dict[str, Any]:
        """更新用户自定义 Skill

        Args:
            user: 用户实例
            skill_data: 包含 name 及需要更新的字段 (description, steps, mode, version 等)

        Returns:
            {"success": bool, "message": str, "data": dict}
        """
        SkillConfig = self._get_skill_config_model()
        if SkillConfig is None:
            return {"success": False, "error_code": "UNKNOWN", "message": "SkillConfig 模型不可用"}

        name = skill_data.get("name", "")
        if not name:
            return {"success": False, "error_code": "VALIDATION", "message": "Skill 名称不能为空"}

        raw_name = self._strip_skill_prefix(name)
        try:
            skill_config = SkillConfig.objects.get(user=user, name=raw_name)
        except SkillConfig.DoesNotExist:
            return {"success": False, "error_code": "NOT_FOUND", "message": f"Skill '{raw_name}' 不存在"}

        # 验证步骤（如果提供了 steps）
        steps = skill_data.get("steps")
        if steps is not None:
            if not steps:
                return {"success": False, "error_code": "VALIDATION", "message": "Skill 至少需要一个步骤"}
            validation = self._validate_skill_steps(steps)
            if not validation["valid"]:
                return {"success": False, "error_code": "VALIDATION", "message": validation["message"]}

        try:
            if "description" in skill_data:
                skill_config.description = skill_data["description"]
            if "mode" in skill_data:
                skill_config.mode = skill_data["mode"]
            if steps is not None:
                skill_config.steps = steps
            if "version" in skill_data:
                skill_config.version = skill_data["version"]
            skill_config.save()
            logger.info("用户更新 Skill: %s (user=%s)", raw_name, user.id)
            return {
                "success": True,
                "message": f"Skill '{raw_name}' 更新成功",
                "data": {
                    "id": skill_config.id,
                    "name": skill_config.name,
                    "description": skill_config.description,
                    "mode": skill_config.mode,
                    "steps": skill_config.steps,
                    "version": skill_config.version,
                    "status": skill_config.status,
                },
            }
        except Exception as e:
            logger.exception("Skill 更新失败")
            return {"success": False, "error_code": "UNKNOWN", "message": f"更新失败: {e}"}

    def create_skill(self, user, skill_data: dict[str, Any]) -> dict[str, Any]:
        from Django_xm.apps.tools.models import ToolCategory

        SkillConfig = self._get_skill_config_model()
        if SkillConfig is None:
            return {"success": False, "error_code": "UNKNOWN", "message": "SkillConfig 模型不可用"}

        name = skill_data.get("name", "")
        if not name:
            return {"success": False, "error_code": "VALIDATION", "message": "Skill 名称不能为空"}

        if self.is_system_tool(name):
            return {
                "success": False,
                "error_code": "FORBIDDEN",
                "message": f"系统内置 Skill '{name}' 已存在，不可重复添加",
            }

        if SkillConfig.objects.filter(user=user, name=name).exists():
            return {"success": False, "error_code": "VALIDATION", "message": f"Skill '{name}' 已存在"}

        steps = skill_data.get("steps", [])
        if not steps:
            return {"success": False, "error_code": "VALIDATION", "message": "Skill 至少需要一个步骤"}

        validation = self._validate_skill_steps(steps)
        if not validation["valid"]:
            return {"success": False, "error_code": "VALIDATION", "message": validation["message"]}

        category_code = skill_data.get("category", "general")
        try:
            category = ToolCategory.objects.get(code=category_code)
        except ToolCategory.DoesNotExist:
            category = ToolCategory.objects.get(code="general")

        try:
            skill_config = SkillConfig.objects.create(
                user=user,
                name=name,
                description=skill_data.get("description", ""),
                mode=skill_data.get("mode", "pipeline"),
                steps=steps,
                version=skill_data.get("version", "1.0.0"),
                tool_type="skill",
                category=category,
                source="user",
                status="active",
            )
            logger.info("用户创建 Skill: %s (user=%s, mode=%s)", name, user.id, skill_config.mode)
            return {
                "success": True,
                "message": f"Skill '{name}' 创建成功",
                "data": {
                    "id": skill_config.id,
                    "name": skill_config.name,
                    "description": skill_config.description,
                    "mode": skill_config.mode,
                    "steps": skill_config.steps,
                    "version": skill_config.version,
                    "status": skill_config.status,
                },
            }
        except Exception as e:
            logger.exception("Skill 创建失败")
            return {"success": False, "error_code": "UNKNOWN", "message": f"创建失败: {e}"}

    # ---- Skill 内部辅助 ----

    @staticmethod
    def _get_builtin_tools_for_skill() -> list:
        """获取内置工具列表，供 SkillBaseTool 执行时查找子工具"""
        from Django_xm.apps.tools import get_all_tools

        return get_all_tools()

    def _validate_skill_steps(self, steps: list) -> dict[str, Any]:
        """验证 Skill 步骤中引用的工具名是否合法

        Returns:
            {"valid": bool, "message": str}
        """
        from Django_xm.apps.tools import get_all_tools
        from Django_xm.apps.tools.skills import SkillRegistryService

        builtin_names = {t.name for t in get_all_tools()}
        skill_names = {f"skill_{s.name}" for s in SkillRegistryService.get_presets()}
        known_names = builtin_names | skill_names

        for idx, step in enumerate(steps):
            if not isinstance(step, dict):
                return {"valid": False, "message": f"步骤 {idx} 格式错误，必须是字典"}
            tool_name = step.get("tool_name", "")
            if not tool_name:
                return {"valid": False, "message": f"步骤 {idx} 缺少 tool_name"}
            if tool_name not in known_names:
                return {
                    "valid": False,
                    "message": f"步骤 {idx} 引用的工具 '{tool_name}' 不存在",
                }

        return {"valid": True, "message": "验证通过"}
