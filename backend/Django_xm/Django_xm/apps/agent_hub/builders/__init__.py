"""Builder 模块自动注册

导入所有 builder 模块以触发 @register_builder 装饰器执行，
将 AgentType -> Builder 类映射注册到全局注册表。

注意：deep_builder 与 research.services.adapter 存在循环依赖
（deep_builder → adapter → subagent_patch → builders 包），
因此不在本模块级导入，改为在 AgentFactory._get_builders() 中延迟导入。
"""

from .base_builder import BaseAgentBuilder  # noqa: F401 — @register_builder(AgentType.BASE, RAG, SAFE_RAG)
from .custom_builder import CustomWorkflowBuilder  # noqa: F401 — @register_builder(AgentType.DEEP_RESEARCH_CUSTOM)
from .subagent_builder import SubAgentBuilder  # noqa: F401 — @register_builder(AgentType.WEB_RESEARCHER, DOC_ANALYST, REPORT_WRITER)
