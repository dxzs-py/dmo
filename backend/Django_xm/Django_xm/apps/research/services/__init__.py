"""Research 服务层 - 提供深度研究的服务接口

包含：
- 官方深度研究智能体（OfficialDeepAgentAdapter）- 基于 deepagents 官方包 create_deep_agent
  * 由 agent_hub.builders.deep_builder.DeepAgentBuilder.build() 直接构造并返回
  * 注入 original_tools / original_config / model，韧性降级 _rebuild_with_degraded_tools 可用
- 任务管理器（TaskManager）

注意：
  原 ``create_research_agent`` 函数已删除（死代码，无调用方）。
  废弃的 ``DeepResearchAgent``（deep_agent.py）与 ``SafeDeepResearchAgent``（safe_deep_agent.py）
  已删除（死代码，无调用方）。
  废弃的研究工作流（research_workflow.py）与子智能体提示（subagents.py）已删除（死代码，无调用方）。
  深度研究智能体的创建统一通过 ``agent_hub.create(AgentConfig)`` 入口，
  由 ``DeepAgentBuilder.build()`` 返回 ``OfficialDeepAgentAdapter``。
  执行统一走执行服务（services.fastapi_service.session_executor）。
"""

from .adapter import OfficialDeepAgentAdapter
from .task_manager import TaskManager, get_task_manager, update_task_status

__all__ = [
    "OfficialDeepAgentAdapter",
    "TaskManager",
    "get_task_manager",
    "update_task_status",
]
