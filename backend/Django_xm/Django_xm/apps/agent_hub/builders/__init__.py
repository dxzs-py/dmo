"""Builder 模块自动注册

导入所有 builder 模块以触发 @register_builder 装饰器执行，
将 AgentType -> Builder 类映射注册到全局注册表。

注意：deep_builder 不在本模块级导入，改为在 AgentFactory._get_builders()
中延迟导入（保留 deepagents 等三方依赖缺失时的降级语义；原与
research.services.adapter 的循环依赖已于 spec 任务 4.3 环 3 破除）。
"""

from .base_builder import BaseAgentBuilder  # noqa: F401 — @register_builder(AgentType.BASE, RAG, SAFE_RAG)
