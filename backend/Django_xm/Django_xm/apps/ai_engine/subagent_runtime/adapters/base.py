"""BaseRuntimeAdapter 抽象契约。

适配器负责对接底层编排框架（LangGraph / DeepAgents）：
- 消费 graph 的 astream 流
- 流结束读取真实 state（``state.next``）判定「完成 / 中断暂停」
- 中断时退出协程、持久化 checkpoint、写 ``pending_interrupt_info``
- 外部 resume 时基于已有 thread_id + checkpoint 断点续跑

上层业务只导入 ``SubAgentRuntime``，禁止直接 import 适配器实现。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseRuntimeAdapter(ABC):
    """子代理底层运行时的完整契约。"""

    @abstractmethod
    async def spawn(self, instance: Any, agent_config: Any, configurable: dict | None = None) -> None:
        """启动子代理执行。

        Args:
            instance: 已持久化的 SubAgentInstance（含 thread_id/parent_thread_id）。
            agent_config: 子代理 AgentConfig（上层 spawn 工具构造）。
            configurable: 子代理 graph 的 configurable（含事件转发回调 / 嵌套层级）。

        实现约束：
        - 异步非阻塞：本方法内启动后台任务后立即返回，不等待执行结束。
        - 执行结束后读 ``state.next`` 判定状态，写回 instance。
        """

    @abstractmethod
    async def resume(self, instance: Any, resume_payload: Any) -> None:
        """恢复中断的子代理（从 checkpoint 断点续跑）。"""

    @abstractmethod
    async def get_state(self, instance: Any) -> dict:
        """获取子代理 graph 的真实 state（含 ``next`` / ``tasks``）。"""

    @abstractmethod
    async def terminate(self, instance: Any) -> None:
        """终止子代理（仅标记元数据状态，不强制 kill 正在运行的协程）。"""
