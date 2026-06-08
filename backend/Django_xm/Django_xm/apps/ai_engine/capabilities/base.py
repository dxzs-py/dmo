from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Sequence

from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import BaseTool


class AgentCapability(ABC):

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def build_middleware(self, **kwargs) -> Sequence[AgentMiddleware]:
        ...

    @abstractmethod
    def build_tools(self, **kwargs) -> Sequence[BaseTool]:
        ...

    async def build_tools_async(self, **kwargs) -> Sequence[BaseTool]:
        return self.build_tools(**kwargs)

    @abstractmethod
    def build_config(self, **kwargs) -> Dict[str, Any]:
        ...

    @abstractmethod
    def is_compatible(self, agent_type: str) -> bool:
        ...
