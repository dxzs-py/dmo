import logging
import threading
from collections.abc import Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import BaseTool

from .base import AgentCapability

logger = logging.getLogger(__name__)


class CapabilityRegistry:

    def __init__(self) -> None:
        self._capabilities: dict[str, AgentCapability] = {}
        self._lock = threading.Lock()

    def register(self, name: str, capability: AgentCapability) -> None:
        with self._lock:
            if name in self._capabilities:
                logger.debug("Capability '%s' already registered, skipping", name)
                return
            self._capabilities[name] = capability
            logger.info("Registered capability: %s", name)

    def unregister(self, name: str) -> None:
        with self._lock:
            if name in self._capabilities:
                del self._capabilities[name]
                logger.info("Unregistered capability: %s", name)
            else:
                logger.warning("Capability '%s' not found, cannot unregister", name)

    def get(self, name: str) -> AgentCapability | None:
        with self._lock:
            return self._capabilities.get(name)

    def list_capabilities(self) -> list[str]:
        with self._lock:
            return list(self._capabilities.keys())

    def build_middleware_for_agent(
        self,
        agent_type: str,
        capabilities: Sequence[str],
        **kwargs,
    ) -> list[AgentMiddleware]:
        result: list[AgentMiddleware] = []
        for cap_name in capabilities:
            cap = self.get(cap_name)
            if cap is None:
                logger.warning("Capability '%s' not found, skipping", cap_name)
                continue
            if not cap.is_compatible(agent_type):
                logger.warning(
                    "Capability '%s' not compatible with agent_type '%s', skipping",
                    cap_name,
                    agent_type,
                )
                continue
            try:
                middleware = cap.build_middleware(**kwargs)
                result.extend(middleware)
            except Exception as e:
                logger.error(
                    "Failed to build middleware for capability '%s': %s",
                    cap_name,
                    e,
                )
        return result

    async def build_tools_for_agent_async(
        self,
        agent_type: str,
        capabilities: Sequence[str],
        tool_config: dict | None = None,
        **kwargs,
    ) -> list[BaseTool]:
        result: list[BaseTool] = []
        for cap_name in capabilities:
            cap = self.get(cap_name)
            if cap is None:
                logger.warning("Capability '%s' not found, skipping", cap_name)
                continue
            if not cap.is_compatible(agent_type):
                logger.warning(
                    "Capability '%s' not compatible with agent_type '%s', skipping",
                    cap_name,
                    agent_type,
                )
                continue
            try:
                merged_kwargs = dict(kwargs)
                if tool_config is not None:
                    merged_kwargs["tool_config"] = tool_config
                tools = await cap.build_tools_async(**merged_kwargs)
                result.extend(tools)
            except Exception as e:
                logger.error(
                    "Failed to build tools for capability '%s': %s",
                    cap_name,
                    e,
                )
        seen = set()
        deduped: list[BaseTool] = []
        for tool in result:
            if tool.name not in seen:
                seen.add(tool.name)
                deduped.append(tool)
        return deduped

    def build_config_for_agent(
        self,
        agent_type: str,
        capabilities: Sequence[str],
        **kwargs,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for cap_name in capabilities:
            cap = self.get(cap_name)
            if cap is None:
                logger.warning("Capability '%s' not found, skipping", cap_name)
                continue
            if not cap.is_compatible(agent_type):
                logger.warning(
                    "Capability '%s' not compatible with agent_type '%s', skipping",
                    cap_name,
                    agent_type,
                )
                continue
            try:
                config = cap.build_config(**kwargs)
                result[cap_name] = config
            except Exception as e:
                logger.error(
                    "Failed to build config for capability '%s': %s",
                    cap_name,
                    e,
                )
        return result

    def get_default_capabilities(self, agent_type: str) -> list[str]:
        try:
            from Django_xm.apps.ai_engine.config import settings as ai_settings
            defaults: dict[str, list[str]] = getattr(
                ai_settings, "AGENT_CAPABILITIES_DEFAULT", {}
            )
            return list(defaults.get(agent_type, []))
        except Exception:
            return []


registry = CapabilityRegistry()
