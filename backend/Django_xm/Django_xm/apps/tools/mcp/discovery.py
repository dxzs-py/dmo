from typing import Any

import httpx

from Django_xm.apps.core.logging_utils import get_logger

# Task 27.3：MCP_SERVERS 配置归属 tools 模块，不再走 Django settings 间接访问
from Django_xm.apps.tools.mcp.config import get_system_mcp_servers

logger = get_logger(__name__)

_USER_MCP_CONFIG_KEY = "user_mcp_servers"


class MCPServerDiscovery:
    def discover_from_config(self) -> list[dict[str, Any]]:
        servers = get_system_mcp_servers()
        result = []
        for srv in servers:
            entry = {
                "name": srv.get("name", "unknown"),
                "transport": srv.get("transport", "sse"),
                "description": srv.get("description", ""),
                "enabled": srv.get("enabled", True),
                "source": "system",
            }
            if srv.get("transport") == "stdio":
                entry["command"] = srv.get("command", "")
                entry["args"] = srv.get("args", [])
            else:
                entry["url"] = srv.get("url", "")
            result.append(entry)
        return result

    async def discover_from_registry(self, registry_url: str) -> list[dict[str, Any]]:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(registry_url)
                response.raise_for_status()
                data = response.json()

                servers = []
                if isinstance(data, list):
                    items = data
                elif isinstance(data, dict):
                    items = data.get("servers", data.get("data", []))
                    if not isinstance(items, list):
                        items = []
                else:
                    items = []

                for item in items:
                    if not isinstance(item, dict):
                        continue
                    if not item.get("name") or not item.get("url"):
                        continue
                    server = {
                        "name": item["name"],
                        "transport": item.get("transport", "sse"),
                        "url": item["url"],
                        "description": item.get("description", ""),
                        "enabled": item.get("enabled", True),
                        "source": "registry",
                    }
                    if item.get("headers"):
                        server["headers"] = item["headers"]
                    if item.get("auth_token"):
                        server["auth_token"] = item["auth_token"]
                    servers.append(server)

                logger.info(f"从注册中心 ({registry_url}) 发现 {len(servers)} 个 MCP Server")
                return servers

        except httpx.HTTPStatusError as e:
            logger.exception(f"注册中心请求失败 ({registry_url}): HTTP {e.response.status_code}")
            return []
        except httpx.RequestError:
            logger.exception(f"注册中心连接失败 ({registry_url})")
            return []
        except Exception:
            logger.exception(f"从注册中心发现 MCP Server 失败 ({registry_url})")
            return []

    def register_server(self, server_config: dict[str, Any], user_id: int | None = None) -> bool:
        name = server_config.get("name")
        if not name:
            logger.warning("注册 MCP Server 失败: 缺少 name")
            return False

        system_servers = get_system_mcp_servers()
        if any(s.get("name") == name for s in system_servers):
            logger.warning(f"注册 MCP Server 失败: 系统级 Server '{name}' 已存在")
            return False

        try:
            from Django_xm.apps.cache_manager.services.cache_service import CacheService

            user_servers = CacheService.get(_USER_MCP_CONFIG_KEY) or []
            if not isinstance(user_servers, list):
                user_servers = []
            if any(s.get("name") == name for s in user_servers):
                logger.warning(f"注册 MCP Server 失败: 用户级 Server '{name}' 已存在")
                return False

            new_server = {
                "name": name,
                "transport": server_config.get("transport", "sse"),
                "description": server_config.get("description", ""),
                "enabled": server_config.get("enabled", True),
            }

            transport = new_server["transport"]
            if transport == "stdio":
                command = server_config.get("command", "")
                if not command:
                    logger.warning(f"注册 MCP Server '{name}' 失败: stdio 传输缺少 command")
                    return False
                new_server["command"] = command
                new_server["args"] = server_config.get("args", [])
            else:
                url = server_config.get("url", "")
                if not url:
                    logger.warning(f"注册 MCP Server '{name}' 失败: {transport} 传输缺少 url")
                    return False
                new_server["url"] = url
                if server_config.get("headers"):
                    new_server["headers"] = server_config["headers"]
                if server_config.get("auth_token"):
                    new_server["auth_token"] = server_config["auth_token"]

            user_servers.append(new_server)
            CacheService.set(_USER_MCP_CONFIG_KEY, user_servers, ttl=None)
            logger.info(f"MCP Server 已注册: {name} (transport={transport})")
            return True

        except Exception:
            logger.exception("注册 MCP Server 失败")
            return False

    def unregister_server(self, server_name: str, user_id: int | None = None) -> bool:
        system_servers = get_system_mcp_servers()
        if any(s.get("name") == server_name for s in system_servers):
            logger.warning(f"注销 MCP Server 失败: 系统级 Server '{server_name}' 不可删除")
            return False

        try:
            from Django_xm.apps.cache_manager.services.cache_service import CacheService

            user_servers = CacheService.get(_USER_MCP_CONFIG_KEY) or []
            if not isinstance(user_servers, list):
                user_servers = []
            original_len = len(user_servers)
            user_servers = [s for s in user_servers if s.get("name") != server_name]

            if len(user_servers) == original_len:
                logger.warning(f"注销 MCP Server 失败: 未找到用户级 Server '{server_name}'")
                return False

            CacheService.set(_USER_MCP_CONFIG_KEY, user_servers, ttl=None)
            logger.info(f"MCP Server 已注销: {server_name}")
            return True

        except Exception:
            logger.exception("注销 MCP Server 失败")
            return False

    def list_available_servers(self) -> list[dict[str, Any]]:
        system_servers = get_system_mcp_servers()

        result = []
        for srv in system_servers:
            entry = {
                "name": srv.get("name", "unknown"),
                "transport": srv.get("transport", "sse"),
                "description": srv.get("description", ""),
                "enabled": srv.get("enabled", True),
                "source": "system",
            }
            if srv.get("transport") == "stdio":
                entry["command"] = srv.get("command", "")
                entry["args"] = srv.get("args", [])
            else:
                entry["url"] = srv.get("url", "")
            result.append(entry)

        try:
            from Django_xm.apps.cache_manager.services.cache_service import CacheService

            user_servers = CacheService.get(_USER_MCP_CONFIG_KEY) or []
            if not isinstance(user_servers, list):
                user_servers = []
            seen = {s.get("name") for s in result}
            for srv in user_servers:
                name = srv.get("name")
                if name and name not in seen:
                    entry = {
                        "name": name,
                        "transport": srv.get("transport", "sse"),
                        "description": srv.get("description", ""),
                        "enabled": srv.get("enabled", True),
                        "source": "user",
                    }
                    if srv.get("transport") == "stdio":
                        entry["command"] = srv.get("command", "")
                        entry["args"] = srv.get("args", [])
                    else:
                        entry["url"] = srv.get("url", "")
                    result.append(entry)
                    seen.add(name)
        except Exception as e:
            logger.warning(f"获取用户级 MCP Server 失败: {e}")

        return result


_discovery: MCPServerDiscovery | None = None


def get_mcp_discovery() -> MCPServerDiscovery:
    global _discovery  # noqa: PLW0603  # 模块级单例缓存 lazy init
    if _discovery is None:
        _discovery = MCPServerDiscovery()
    return _discovery
