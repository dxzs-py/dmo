import json
from typing import Any

from langchain_core.messages import SystemMessage

from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)


class MCPResourceInjector:
    async def get_resources_for_context(
        self,
        server_names: list[str] | None = None,
        max_resources: int = 5,
        max_content_length: int = 2000,
    ) -> list[dict[str, Any]]:
        from Django_xm.apps.tools.mcp import (
            get_all_mcp_resources,
            get_mcp_resources,
            is_mcp_available,
        )

        if not is_mcp_available():
            return []

        if server_names:
            all_resources: list[dict[str, Any]] = []
            for name in server_names:
                try:
                    resources = await get_mcp_resources(server_name=name)
                    for r in resources:
                        r_dict = self._normalize_resource(r, name)
                        if r_dict:
                            all_resources.append(r_dict)
                except Exception as e:
                    logger.warning(f"获取 MCP Server '{name}' Resources 失败: {e}")
        else:
            try:
                raw_resources = await get_all_mcp_resources()
                all_resources = []
                for r in raw_resources:
                    r_dict = self._normalize_resource(r, "unknown")
                    if r_dict:
                        all_resources.append(r_dict)
            except Exception as e:
                logger.warning(f"获取全部 MCP Resources 失败: {e}")
                return []

        filtered = all_resources[:max_resources]
        for item in filtered:
            content = item.get("content", "")
            if isinstance(content, str) and len(content) > max_content_length:
                item["content"] = content[:max_content_length] + "...[truncated]"
            elif not isinstance(content, str):
                item["content"] = json.dumps(content, ensure_ascii=False)
                if len(item["content"]) > max_content_length:
                    item["content"] = item["content"][:max_content_length] + "...[truncated]"

        return filtered

    def _normalize_resource(self, resource: Any, server_name: str) -> dict[str, Any] | None:
        if isinstance(resource, dict):
            return {
                "uri": resource.get("uri", ""),
                "name": resource.get("name", ""),
                "description": resource.get("description", ""),
                "mime_type": resource.get("mimeType", resource.get("mime_type", "")),
                "content": resource.get("content", resource.get("text", "")),
                "server": server_name,
            }
        if hasattr(resource, "uri"):
            return {
                "uri": getattr(resource, "uri", str(resource)),
                "name": getattr(resource, "name", ""),
                "description": getattr(resource, "description", ""),
                "mime_type": getattr(resource, "mimeType", getattr(resource, "mime_type", "")),
                "content": getattr(resource, "content", getattr(resource, "text", "")),
                "server": server_name,
            }
        try:
            return {
                "uri": str(resource),
                "name": "",
                "description": "",
                "mime_type": "",
                "content": str(resource),
                "server": server_name,
            }
        except Exception:
            return None

    def format_resources_as_context(self, resources: list[dict[str, Any]]) -> str:
        if not resources:
            return ""

        parts: list[str] = ["<mcp-resources>"]
        for res in resources:
            uri = res.get("uri", "")
            name = res.get("name", "")
            desc = res.get("description", "")
            content = res.get("content", "")
            server = res.get("server", "")
            mime = res.get("mime_type", "")

            entry = f'  <resource uri="{uri}"'
            if name:
                entry += f' name="{name}"'
            if server:
                entry += f' server="{server}"'
            if mime:
                entry += f' mime-type="{mime}"'
            entry += ">"

            if desc:
                entry += f"\n    <description>{desc}</description>"
            if content:
                entry += f"\n    <content>{content}</content>"

            entry += "\n  </resource>"
            parts.append(entry)

        parts.append("</mcp-resources>")
        return "\n".join(parts)

    async def inject_resources_to_messages(
        self,
        messages: list[Any],
        server_names: list[str] | None = None,
        max_resources: int = 5,
        max_content_length: int = 2000,
    ) -> list[Any]:
        resources = await self.get_resources_for_context(
            server_names=server_names,
            max_resources=max_resources,
            max_content_length=max_content_length,
        )

        if not resources:
            return messages

        context_str = self.format_resources_as_context(resources)
        if not context_str:
            return messages

        result = list(messages)

        system_idx = None
        for i, msg in enumerate(result):
            if isinstance(msg, SystemMessage):
                system_idx = i
                break

        if system_idx is not None:
            existing = result[system_idx].content
            enhanced = existing + "\n\n" + context_str
            result[system_idx] = SystemMessage(content=enhanced)
        else:
            result.insert(0, SystemMessage(content=context_str))

        return result


_resource_injector: MCPResourceInjector | None = None


def get_resource_injector() -> MCPResourceInjector:
    global _resource_injector
    if _resource_injector is None:
        _resource_injector = MCPResourceInjector()
    return _resource_injector
