"""
工具权限控制系统 + DRF 权限类
参考 claw-code-main 的 permissions.rs 实现
支持三级权限模式和工具级权限覆盖
权限策略持久化到数据库，Redis 作为缓存层
"""

import json
import logging
from typing import Dict, Any, Optional, List, Set
from enum import Enum
from dataclasses import dataclass, field

from Django_xm.apps.ai_engine.config import get_logger
from rest_framework.permissions import BasePermission

logger = get_logger(__name__)

CACHE_PREFIX = "tool_perms:"
CACHE_TIMEOUT = 3600


def get_cache():
    """延迟获取 cache 对象"""
    from django.core.cache import cache
    return cache


def get_base_permission():
    """延迟获取 BasePermission 对象"""
    from rest_framework.permissions import BasePermission
    return BasePermission


class QueryParamTokenAuthentication:
    def authenticate(self, request):
        token = request.GET.get('token')
        if not token or len(token) >= 2048:
            return None

        try:
            from rest_framework_simplejwt.authentication import JWTAuthentication
            from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

            auth = JWTAuthentication()

            class MockRequest:
                def __init__(self, token_str):
                    self.META = {'HTTP_AUTHORIZATION': f'Bearer {token_str}'}

            mock_request = MockRequest(token)
            raw_token = auth.get_raw_token(mock_request)
            if raw_token:
                validated_token = auth.get_validated_token(raw_token)
                user = auth.get_user(validated_token)
                if user and user.is_authenticated:
                    return (user, validated_token)
        except (InvalidToken, TokenError) as e:
            logger.warning(f"查询参数token认证失败: {e}")
        except Exception as e:
            logger.warning(f"查询参数token验证异常: {e}")

        return None

    def authenticate_header(self, request):
        return 'Bearer'


class IsAuthenticatedOrQueryParam(BasePermission):
    """
    支持标准认证或查询参数 token 认证的权限类
    用于 SSE、文件下载等无法设置 Authorization header 的场景
    """
    def has_permission(self, request, view):
        if request.user and request.user.is_authenticated:
            return True

        token = request.GET.get('token')
        if token and len(token) < 2048:
            try:
                from rest_framework_simplejwt.authentication import JWTAuthentication
                from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

                auth = JWTAuthentication()

                class MockRequest:
                    def __init__(self, token_str):
                        self.META = {'HTTP_AUTHORIZATION': f'Bearer {token_str}'}

                mock_request = MockRequest(token)
                raw_token = auth.get_raw_token(mock_request)
                if raw_token:
                    validated_token = auth.get_validated_token(raw_token)
                    user = auth.get_user(validated_token)
                    if user and user.is_authenticated:
                        request.user = user
                        return True
            except Exception as e:
                logger.warning(f"查询参数token验证失败: {e}")
                pass

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


def _get_cache_key(user_id: int, session_id: Optional[str] = None) -> str:
    key = f"{CACHE_PREFIX}{user_id}"
    if session_id:
        key += f":{session_id}"
    return key


def _policy_to_cache_data(policy: PermissionPolicy) -> str:
    return json.dumps({
        "defaultMode": policy.default_mode.value,
        "sessionMode": policy.session_mode.value,
        "toolModes": {k: v.value for k, v in policy.tool_modes.items()},
    })


def _cache_data_to_policy(data: str) -> Optional[PermissionPolicy]:
    try:
        parsed = json.loads(data)
        return PermissionPolicy(
            default_mode=PermissionMode(parsed.get("defaultMode", "allow")),
            session_mode=SessionPermissionMode(parsed.get("sessionMode", "workspace-write")),
            tool_modes={
                k: PermissionMode(v)
                for k, v in parsed.get("toolModes", {}).items()
            },
        )
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"解析缓存的权限策略失败: {e}")
        return None


def _policy_to_db_fields(policy: PermissionPolicy) -> Dict[str, Any]:
    return {
        "default_mode": policy.default_mode.value,
        "session_mode": policy.session_mode.value,
        "tool_modes": {k: v.value for k, v in policy.tool_modes.items()},
    }


def _db_record_to_policy(record) -> PermissionPolicy:
    tool_modes = {}
    if record.tool_modes and isinstance(record.tool_modes, dict):
        tool_modes = {
            k: PermissionMode(v)
            for k, v in record.tool_modes.items()
            if v in [m.value for m in PermissionMode]
        }
    return PermissionPolicy(
        default_mode=PermissionMode(record.default_mode),
        session_mode=SessionPermissionMode(record.session_mode),
        tool_modes=tool_modes,
    )


class PermissionService:
    """权限管理服务 - 管理用户会话的工具权限，数据库持久化 + Redis 缓存"""

    @staticmethod
    def get_policy(user_id: int, session_id: Optional[str] = None) -> PermissionPolicy:
        cache_key = _get_cache_key(user_id, session_id)
        cached = get_cache().get(cache_key)
        if cached:
            policy = _cache_data_to_policy(cached)
            if policy:
                return policy

        try:
            from Django_xm.apps.core.permission_models import UserPermissionPolicy
            record = UserPermissionPolicy.objects.filter(
                user_id=user_id,
                session_id=session_id or None,
            ).first()
            if record:
                policy = _db_record_to_policy(record)
                cache = get_cache()
                cache.set(cache_key, _policy_to_cache_data(policy), CACHE_TIMEOUT)
                return policy
        except Exception as e:
            logger.warning(f"从数据库加载权限策略失败: {e}")

        return PermissionPolicy()

    @staticmethod
    def save_policy(user_id: int, policy: PermissionPolicy, session_id: Optional[str] = None):
        cache_key = _get_cache_key(user_id, session_id)
        get_cache().set(cache_key, _policy_to_cache_data(policy), CACHE_TIMEOUT)

        try:
            from Django_xm.apps.core.permission_models import UserPermissionPolicy
            defaults = _policy_to_db_fields(policy)
            UserPermissionPolicy.objects.update_or_create(
                user_id=user_id,
                session_id=session_id or None,
                defaults=defaults,
            )
        except Exception as e:
            logger.warning(f"持久化权限策略到数据库失败: {e}")

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
