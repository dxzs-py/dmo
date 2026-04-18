"""
工具权限控制系统 + DRF 权限类
参考 claw-code-main 的 permissions.rs 实现
支持三级权限模式和工具级权限覆盖
"""

import json
import logging
from typing import Dict, Any, Optional, List, Set
from enum import Enum
from dataclasses import dataclass, field

from django.core.cache import cache
from rest_framework.permissions import BasePermission

from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)

CACHE_PREFIX = "tool_perms:"
CACHE_TIMEOUT = 3600


class QueryParamTokenAuthentication:
    pass


class IsAuthenticatedOrQueryParam(BasePermission):
    def has_permission(self, request, view):
        if request.user and request.user.is_authenticated:
            return True
        token = request.query_params.get('token')
        if token:
            return True
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if auth_header.startswith('Bearer '):
            return True
        return False


class PermissionMode(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    PROMPT = "prompt"


class SessionPermissionMode(str, Enum):
    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    DANGER_FULL_ACCESS = "danger-full-access"


READ_ONLY_TOOLS: Set[str] = {
    "get_current_time",
    "get_current_date",
    "calculator",
    "translate_text",
    "detect_language",
    "web_search",
    "duckduckgo_search",
    "weather_query",
    "fs_read_file",
    "fs_list_files",
    "fs_search_files",
    "file_reader",
    "attachment_reader",
    "web_fetch",
    "todo_read",
}

WRITE_TOOLS: Set[str] = {
    "fs_write_file",
    "todo_write",
}

DANGEROUS_TOOLS: Set[str] = {
    "bash_execute",
    "repl_execute",
    "notebook_edit",
}

SESSION_MODE_ALLOWED_TOOLS: Dict[SessionPermissionMode, Set[str]] = {
    SessionPermissionMode.READ_ONLY: READ_ONLY_TOOLS,
    SessionPermissionMode.WORKSPACE_WRITE: READ_ONLY_TOOLS | WRITE_TOOLS,
    SessionPermissionMode.DANGER_FULL_ACCESS: READ_ONLY_TOOLS | WRITE_TOOLS | DANGEROUS_TOOLS,
}


@dataclass
class PermissionPolicy:
    default_mode: PermissionMode = PermissionMode.ALLOW
    tool_modes: Dict[str, PermissionMode] = field(default_factory=dict)
    session_mode: SessionPermissionMode = SessionPermissionMode.WORKSPACE_WRITE

    def get_tool_permission(self, tool_name: str) -> PermissionMode:
        if tool_name in self.tool_modes:
            return self.tool_modes[tool_name]
        if not self._is_tool_allowed_by_session_mode(tool_name):
            return PermissionMode.DENY
        return self.default_mode

    def _is_tool_allowed_by_session_mode(self, tool_name: str) -> bool:
        allowed = SESSION_MODE_ALLOWED_TOOLS.get(self.session_mode, set())
        return tool_name in allowed

    def is_tool_allowed(self, tool_name: str) -> bool:
        permission = self.get_tool_permission(tool_name)
        return permission != PermissionMode.DENY

    def needs_prompt(self, tool_name: str) -> bool:
        permission = self.get_tool_permission(tool_name)
        return permission == PermissionMode.PROMPT

    def to_dict(self) -> Dict[str, Any]:
        return {
            "defaultMode": self.default_mode.value,
            "sessionMode": self.session_mode.value,
            "toolModes": {k: v.value for k, v in self.tool_modes.items()},
            "allowedTools": list(SESSION_MODE_ALLOWED_TOOLS.get(self.session_mode, set())),
        }


class PermissionService:
    """权限管理服务 - 管理用户会话的工具权限"""

    @staticmethod
    def get_policy(user_id: int, session_id: Optional[str] = None) -> PermissionPolicy:
        cache_key = f"{CACHE_PREFIX}{user_id}"
        if session_id:
            cache_key += f":{session_id}"

        cached = cache.get(cache_key)
        if cached:
            try:
                data = json.loads(cached)
                return PermissionPolicy(
                    default_mode=PermissionMode(data.get("defaultMode", "allow")),
                    session_mode=SessionPermissionMode(data.get("sessionMode", "workspace-write")),
                    tool_modes={
                        k: PermissionMode(v)
                        for k, v in data.get("toolModes", {}).items()
                    },
                )
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(f"解析缓存的权限策略失败: {e}")

        return PermissionPolicy()

    @staticmethod
    def save_policy(user_id: int, policy: PermissionPolicy, session_id: Optional[str] = None):
        cache_key = f"{CACHE_PREFIX}{user_id}"
        if session_id:
            cache_key += f":{session_id}"

        data = {
            "defaultMode": policy.default_mode.value,
            "sessionMode": policy.session_mode.value,
            "toolModes": {k: v.value for k, v in policy.tool_modes.items()},
        }
        cache.set(cache_key, json.dumps(data), CACHE_TIMEOUT)

    @staticmethod
    def update_session_mode(
        user_id: int,
        session_mode: str,
        session_id: Optional[str] = None,
    ) -> PermissionPolicy:
        try:
            mode = SessionPermissionMode(session_mode)
        except ValueError:
            valid_modes = [m.value for m in SessionPermissionMode]
            raise ValueError(f"无效的权限模式: {session_mode}, 可选: {valid_modes}")

        policy = PermissionService.get_policy(user_id, session_id)
        policy.session_mode = mode
        PermissionService.save_policy(user_id, policy, session_id)

        logger.info(f"用户 {user_id} 权限模式更新为: {mode.value}")
        return policy

    @staticmethod
    def set_tool_permission(
        user_id: int,
        tool_name: str,
        permission: str,
        session_id: Optional[str] = None,
    ) -> PermissionPolicy:
        try:
            perm = PermissionMode(permission)
        except ValueError:
            valid_perms = [p.value for p in PermissionMode]
            raise ValueError(f"无效的权限: {permission}, 可选: {valid_perms}")

        policy = PermissionService.get_policy(user_id, session_id)
        policy.tool_modes[tool_name] = perm
        PermissionService.save_policy(user_id, policy, session_id)

        logger.info(f"用户 {user_id} 工具 {tool_name} 权限更新为: {perm.value}")
        return policy

    @staticmethod
    def filter_tools_by_permission(
        user_id: int,
        tools: List,
        session_id: Optional[str] = None,
    ) -> List:
        policy = PermissionService.get_policy(user_id, session_id)
        allowed_tools = []
        denied_tools = []

        for tool in tools:
            tool_name = getattr(tool, 'name', str(tool))
            if policy.is_tool_allowed(tool_name):
                allowed_tools.append(tool)
            else:
                denied_tools.append(tool_name)

        if denied_tools:
            logger.info(
                f"用户 {user_id} 被拒绝的工具: {', '.join(denied_tools)}"
            )

        return allowed_tools

    @staticmethod
    def get_permission_info(user_id: int, session_id: Optional[str] = None) -> Dict[str, Any]:
        policy = PermissionService.get_policy(user_id, session_id)
        all_tools = READ_ONLY_TOOLS | WRITE_TOOLS | DANGEROUS_TOOLS
        tool_permissions = {}
        for tool_name in sorted(all_tools):
            tool_permissions[tool_name] = {
                "allowed": policy.is_tool_allowed(tool_name),
                "needsPrompt": policy.needs_prompt(tool_name),
                "permission": policy.get_tool_permission(tool_name).value,
            }

        return {
            "policy": policy.to_dict(),
            "tools": tool_permissions,
            "sessionModes": {
                m.value: {
                    "description": {
                        SessionPermissionMode.READ_ONLY: "只读模式 - 仅允许查询和搜索工具",
                        SessionPermissionMode.WORKSPACE_WRITE: "工作区模式 - 允许文件读写和搜索",
                        SessionPermissionMode.DANGER_FULL_ACCESS: "完全访问 - 允许所有工具包括代码执行",
                    }.get(m, ""),
                    "allowedToolCount": len(SESSION_MODE_ALLOWED_TOOLS.get(m, set())),
                }
                for m in SessionPermissionMode
            },
        }
