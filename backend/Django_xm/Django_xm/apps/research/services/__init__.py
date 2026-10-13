"""Research 服务层 - 提供深度研究的服务接口

包含：
- 官方深度研究智能体（OfficialDeepAgentAdapter）- 基于 deepagents 官方包 create_deep_agent
  * 由 agent_assembly.build_research_agent 组装（spec 任务 4.3 环 3：包装上移 research——
    deep_builder 返回 AgentWrapper 元数据载体，本域消费 wrapper 组装 adapter）
  * 注入 original_tools / original_config / model，韧性降级 _rebuild_with_degraded_tools 可用
- 任务管理器（TaskManager）

注意：
  原 ``create_research_agent`` 函数已删除（死代码，无调用方）。
  废弃的 ``DeepResearchAgent``（deep_agent.py）与 ``SafeDeepResearchAgent``（safe_deep_agent.py）
  已删除（死代码，无调用方）。
  废弃的研究工作流（research_workflow.py）与子智能体提示（subagents.py）已删除（死代码，无调用方）。
  深度研究智能体的创建统一通过 ``agent_hub.create(AgentConfig)`` 入口，
  由 ``agent_assembly`` 消费 ``AgentWrapper`` 返回 ``OfficialDeepAgentAdapter``。
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
