"""子代理生命周期管理器（事件驱动核心）。

子代理到达终态（``completed`` / ``failed``）后，适配器触发
``on_subagent_finished``；管理器按 ``parent_thread_id`` 定位等待中的父 Graph，
调度唤醒（父 Graph 从自身 checkpoint 断点续跑）。

父 Graph 等待挂起语义（见 spec D4）：
- 父 Graph 执行业务等待挂起逻辑（持久化 checkpoint、退出协程、释放资源），
  并非面向用户审批的 interrupt，仅为流程暂停，不产出审批 UI。
- 父 Graph 挂起前通过 ``register_parent_awaiter`` 注册唤醒回调。
- 子代理终态回调唤醒父 Graph，父 Graph 新建协程续跑（非原协程驻留）。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

# 父 Graph 唤醒回调签名：async (subagent_thread_id, status) -> None
ParentAwaiter = Callable[[str, str], Awaitable[None]]


class SubAgentLifecycleManager:
    """子代理终态回调 + 父 Graph 唤醒调度。"""

    def __init__(self):
        # parent_thread_id -> 唤醒回调（父 Graph 业务等待挂起时注册）
        self._parent_awaiters: dict[str, ParentAwaiter] = {}

    def register_parent_awaiter(self, parent_thread_id: str, awaiter: ParentAwaiter) -> None:
        """注册父 Graph 唤醒回调。

        父 Graph 业务等待挂起时调用；子代理终态时据此唤醒父 Graph。
        """
        if not parent_thread_id:
            return
        self._parent_awaiters[parent_thread_id] = awaiter
        logger.info(f"注册父 Graph 唤醒回调: parent={parent_thread_id}")

    def unregister_parent_awaiter(self, parent_thread_id: str) -> None:
        """注销父 Graph 唤醒回调（父 Graph 恢复完成或不再等待时调用）。"""
        self._parent_awaiters.pop(parent_thread_id, None)

    def has_awaiter(self, parent_thread_id: str) -> bool:
        """是否存在等待该父线程的唤醒回调。"""
        return parent_thread_id in self._parent_awaiters

    async def on_subagent_finished(self, subagent_thread_id: str, parent_thread_id: str, status: str) -> None:
        """子代理终态回调：定位父 awaiter 并唤醒。

        Args:
            subagent_thread_id: 已完成/失败的子代理 thread_id。
            parent_thread_id: 父线程 thread_id。
            status: 子代理终态（completed / failed）。
        """
        awaiter = self._parent_awaiters.get(parent_thread_id)
        if awaiter is None:
            logger.info(f"无父 Graph 等待子代理（已收尾或未注册）: {subagent_thread_id}")
            return
        try:
            await awaiter(subagent_thread_id, status)
        except Exception:
            logger.exception(
                f"唤醒父 Graph 失败: parent={parent_thread_id}, sub={subagent_thread_id}"
            )


# 进程级单例
_lifecycle_manager: SubAgentLifecycleManager | None = None


def get_lifecycle_manager() -> SubAgentLifecycleManager:
    """获取进程级 SubAgentLifecycleManager 单例。"""
    global _lifecycle_manager  # noqa: PLW0603 - 模块级单例惰性初始化
    if _lifecycle_manager is None:
        _lifecycle_manager = SubAgentLifecycleManager()
    return _lifecycle_manager
