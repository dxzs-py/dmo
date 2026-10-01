from typing import Any

import httpx

from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)


class MCPServerDiscovery:
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


_discovery: MCPServerDiscovery | None = None


def get_mcp_discovery() -> MCPServerDiscovery:
    global _discovery  # noqa: PLW0603  # 模块级单例缓存 lazy init
    if _discovery is None:
        _discovery = MCPServerDiscovery()
    return _discovery
