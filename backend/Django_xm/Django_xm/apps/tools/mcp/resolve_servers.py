"""MCP Server 配置合并/解析（B⑧ 收敛：唯一实现）。

系统级（``mcp.config``，管理员维护，可信）与用户级（``McpServerConfig``，
status=active）合并的单一实现，取代原先分散在
``tools/__init__._load_mcp_tools_async``（agent 运行时异步装配）与
``tools.managers.McpToolManager._get_allowed_server_names``（管理台白名单）
的两处独立维护的合并规则。

合并规则：
- 去重键为 ``name``，系统级优先（同名用户级 Server 被跳过）；
- 系统级在前、用户级追加其后（保序）。

安全语义（S-04，P0 精确保留）：
- 系统级 Server（含 stdio）为管理员维护的可信配置，对所有用户保留；
- ``exclude_user_stdio=True`` 时仅排除用户自建（source="user"）的 stdio
  Server（非管理员加载白名单场景）；DB 中 source="system" 的 stdio 为
  可信配置，保留；
- URL SSRF 校验（``validate_mcp_server_url``）发生在 Server 新增/更新入口
  （views/serializers），与本装配模块无关，不受影响。
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def collect_user_mcp_servers(user_id: int, *, exclude_user_stdio: bool = False) -> list[dict[str, Any]]:
    """查询用户级 active MCP Server 配置列表（to_config_dict 产物）。

    Args:
        user_id: 用户 ID。
        exclude_user_stdio: 排除用户自建（source="user"）的 stdio Server
            （S-04：stdio 以配置中的 command 在服务端启动子进程，等价任意
            命令执行，非管理员加载场景跳过）；source="system" 的 stdio 保留。

    Returns:
        用户级 Server 配置 dict 列表。
    """
    from Django_xm.apps.tools.models import McpServerConfig

    qs = McpServerConfig.objects.filter(user_id=user_id, status="active")
    if exclude_user_stdio:
        # 仅排除用户自建 stdio（source="user"）；source="system" 的 stdio 为可信配置，保留
        qs = qs.exclude(transport="stdio", source="user")
    return [srv.to_config_dict() for srv in qs]


def resolve_mcp_servers(
    user_id: int | None = None,
    *,
    exclude_user_stdio: bool = False,
) -> list[dict[str, Any]]:
    """系统级 + 用户级 MCP Server 配置合并（单一实现，去重键 name，系统级优先）。

    Args:
        user_id: 用户 ID；None 或空时仅返回系统级配置。
        exclude_user_stdio: 排除用户自建（source="user"）stdio Server
            （非管理员白名单场景）；系统级 stdio 恒保留。

    Returns:
        合并后的 Server 配置 dict 列表（系统级在前，保序）。
        用户级配置查询失败时降级为仅系统级（warning，不阻断装配）。
    """
    from Django_xm.apps.tools.mcp import _get_mcp_servers_config

    servers = list(_get_mcp_servers_config())
    if not user_id:
        return servers

    seen = {s.get("name") for s in servers}
    try:
        for srv in collect_user_mcp_servers(user_id, exclude_user_stdio=exclude_user_stdio):
            if srv.get("name") not in seen:
                servers.append(srv)
                seen.add(srv.get("name"))
    except Exception as e:
        # 用户级配置查询失败不阻断装配：降级为仅系统级
        logger.warning(f"加载用户 MCP Server 配置失败: {e}")
    return servers
