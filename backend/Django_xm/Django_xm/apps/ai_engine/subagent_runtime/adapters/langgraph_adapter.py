"""LangGraph 适配器：对接独立 LangGraph graph 的子代理执行。

核心（对齐 Trae Solo / spec D3）：
- ``astream`` 迭代结束 ≠ 任务完成；流消费完毕必须读 ``state.next`` 判定。
- interrupt 时协程退出释放资源，状态置 ``interrupted_pending_user_input``。
- 子代理在独立线程 + 独立事件循环执行（创建 agent / astream / 释放连接同 loop）。
- 终态（completed/failed）触发生命周期回调（spec D4，唤醒父 Graph）。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from Django_xm.apps.ai_engine.models import SubAgentStatus
from Django_xm.apps.ai_engine.subagent_runtime.adapters.base import BaseRuntimeAdapter
from Django_xm.apps.ai_engine.subagent_runtime.concurrency import (
    get_subagent_concurrency_acquire_timeout,
    get_subagent_concurrency_limit,
    get_subagent_concurrency_semaphore,
)
from Django_xm.apps.ai_engine.subagent_runtime.runtime import MAX_SUBAGENT_ROUNDS
from Django_xm.common.messages import content_to_str

logger = logging.getLogger(__name__)


# 子代理 thread_id → 父传入 configurable 引用（含 _on_tool_event / _on_subagent_content
# 等回调，及 chat_session_id / assistant_message_id）。spawn 时保存、resume 时恢复：
# resume 重建 graph 后若回调丢失，子代理后续工具事件（PENDING/COMPLETED）将不再转发，
# 导致前端"运行结果不展示、新审批落入主工具流"。终态（completed/failed）清理。
# 说明：回调为函数对象不可序列化，故用进程内 registry 而非 DB 字段保存；
# 服务重启后 registry 为空，resume 回落 instance.metadata 重建（极端场景，主流程不受影响）。
_configurable_registry: dict[str, dict] = {}

# 子代理 thread_id → 协作式取消标志（C14）。terminate 置位后，执行线程在
# astream 下一个事件到达时检测并停止消费；执行线程退出（_execute_in_thread
# finally）时清理。进程内对象，服务重启后为空（仅影响已失联实例的取消，
# 状态标记由 runtime.terminate 落 DB 兜底）。
_cancel_flags: dict[str, threading.Event] = {}


# 子代理 graph 工厂（依赖倒置）：ai_engine 禁止直接 import agent_hub（反向依赖），
# 由 agent_hub AppConfig.ready() 调用 set_default_graph_factory 注册 create；
# 测试/定制运行时可经构造参数 graph_factory 注入实例级工厂。
_default_graph_factory = None
# AgentConfig 工厂（resume 重建配置用）：同样由 agent_hub 侧注册，
# agent_type 由工厂实现侧固定（BASE）。
_default_config_factory = None


def set_default_graph_factory(factory):
    """注册进程级默认子代理 graph 工厂（agent_hub AppConfig.ready() 调用）。"""
    global _default_graph_factory  # noqa: PLW0603 - 进程级注册点
    _default_graph_factory = factory


def set_default_config_factory(factory):
    """注册进程级默认 AgentConfig 工厂（agent_hub AppConfig.ready() 调用）。"""
    global _default_config_factory  # noqa: PLW0603 - 进程级注册点
    _default_config_factory = factory


class LangGraphAdapter(BaseRuntimeAdapter):
    """对接 LangGraph CompiledStateGraph（BASE agent 工具链 + DeepAgents 独立 graph）。"""

    def __init__(self, runtime: Any, graph_factory=None, config_factory=None):
        self.runtime = runtime
        self._graph_factory = graph_factory
        self._config_factory = config_factory

    # ── 对外契约 ────────────────────────────────────────────────────────

    async def spawn(self, instance: Any, agent_config: Any, configurable: dict | None = None) -> None:
        """启动子代理执行（独立线程，非阻塞）。"""
        thread = threading.Thread(
            target=self._run_in_thread,
            args=(instance, agent_config, configurable, None),
            daemon=True,
        )
        thread.start()

    async def resume(self, instance: Any, resume_payload: Any) -> None:
        """恢复中断的子代理（独立线程，用 Command(resume) 断点续跑）。"""
        # 恢复需要重新创建 graph（graph 对象不能跨事件循环复用）
        try:
            agent_config = await self._rebuild_agent_config(instance)
        except Exception:
            logger.exception(f"子代理 resume 重建 AgentConfig 失败: {instance.thread_id}")
            agent_config = None
        if agent_config is None:
            # 重建失败：置终态并触发生命周期回调（对齐 _execute_in_thread 异常路径），
            # 否则父会话的 fan-in 等待者永不唤醒，挂到 watchdog 超时。
            await self.runtime._update_status(instance.thread_id, SubAgentStatus.FAILED)
            await self._notify_finished(instance, "failed")
            return
        thread = threading.Thread(
            target=self._run_in_thread,
            args=(instance, agent_config, None, resume_payload),
            daemon=True,
        )
        thread.start()

    async def get_state(self, instance: Any) -> dict:
        """获取子代理 graph 的真实 state。"""
        return {"thread_id": instance.thread_id}

    async def terminate(self, instance: Any) -> None:
        """终止子代理（C14 协作式取消：置取消标志，执行线程在下一个流事件处停止）。

        边界（LangGraph 图执行不可从外部强制 kill，仅能协作式取消）：
        - 置位进程内取消标志后，正在消费 astream 的执行线程在下一次事件到达时
          停止（stream_mode 含 messages，token 级事件流量下响应为秒级）；
          长时间工具调用期间无事件流出，需等该步骤结束后才响应；
        - 已挂起（审批/业务等待）或无运行线程的实例仅置标志（无消费循环可停），
          状态由 runtime.terminate 统一更新为 FAILED；resume 的状态校验会拒绝
          已终态实例续跑，业务等待恢复路径同样被 runtime.resume 拒绝；
        - 父线程被终止后再 spawn 孙代理由 runtime.spawn 的父状态校验拒绝。
        """
        thread_id = instance.thread_id
        cancel_event = _cancel_flags.get(thread_id)
        if cancel_event is None:
            logger.info(f"子代理终止：无运行中执行线程（已完成/挂起/服务重启），仅标记状态: {thread_id}")
            return
        cancel_event.set()
        logger.info(f"子代理终止：已置协作式取消标志: {thread_id}")

    # ── 后台执行 ────────────────────────────────────────────────────────

    def _run_in_thread(self, instance: Any, agent_config: Any, configurable: dict | None, resume_payload: Any) -> None:
        """子代理执行线程唯一入口（D3/P2-9 并发槽收敛点）。

        spawn 与 resume（审批恢复 / 等待恢复 / 重启恢复）全部经此启动唯一
        执行线程：进入即获取进程级并发槽（threading.BoundedSemaphore，容量
        settings.AGENT_SUBAGENT_CONCURRENCY，默认 8），超限排队等待；排队
        超时（settings.AGENT_SUBAGENT_ACQUIRE_TIMEOUT，默认 30s，round3 2.5）
        置 FAILED + _notify_finished（父 wait/fan-in 等待者立即收到失败，
        而非悬挂到 watchdog 7200s），终态/异常/interrupt 全路径 finally 释放。

        收敛点选择依据：
        - 覆盖面：全部子代理图执行唯一经此入口（spawn/resume 两处线程创建
          的共同 target），单点获取/释放，无多入口漏覆盖、无重复计数；
        - 跨事件循环安全：子代理在独立线程 + 独立事件循环执行，asyncio
          原语绑定单一 loop，跨 loop await 会抛 "bound to a different
          event loop"；threading 信号量的等待发生在建 loop 之前的纯线程
          上下文，天然跨 loop；
        - 不占工具超时：spawn 工具立即返回（runtime.spawn → adapter.spawn
          非阻塞），排队发生在子代理自身线程内、其图尚未启动，B6
          ToolExecutionTimeoutMiddleware（包裹父图单次工具调用）与 Agent
          soft/hard 超时均不覆盖此等待；正常排队（秒级获得调度）≪ 挂起
          会话 watchdog 7200s，等待父会话不会被误终态。
        """
        semaphore = get_subagent_concurrency_semaphore()
        acquired = semaphore.acquire(blocking=False)
        if not acquired:
            acquire_timeout = get_subagent_concurrency_acquire_timeout()
            if acquire_timeout is None:
                logger.info(
                    f"[SubAgentConcurrency] 子代理并发已达上限"
                    f"（{get_subagent_concurrency_limit()}），排队等待（无超时）: {instance.thread_id}"
                )
                semaphore.acquire()
                acquired = True
            else:
                logger.info(
                    f"[SubAgentConcurrency] 子代理并发已达上限"
                    f"（{get_subagent_concurrency_limit()}），排队等待（最多 {acquire_timeout:g}s）: "
                    f"{instance.thread_id}"
                )
                acquired = semaphore.acquire(timeout=acquire_timeout)
                if not acquired:
                    logger.warning(
                        f"[SubAgentConcurrency] 并发槽排队超时（{acquire_timeout:g}s），"
                        f"置 FAILED 并唤醒父等待者: {instance.thread_id}"
                    )
                    self._fail_before_execution(instance, configurable, acquire_timeout)
                    return
        try:
            self._execute_in_thread(instance, agent_config, configurable, resume_payload)
        finally:
            if acquired:
                semaphore.release()

    def _fail_before_execution(self, instance: Any, configurable: dict | None, waited_seconds: float) -> None:
        """并发槽排队超时失败终态（round3 2.5）：置 FAILED + 状态事件 + 唤醒父等待者。

        在独立一次性事件循环中执行异步收尾（本方法运行于执行线程、loop 尚未
        创建），语义对齐 _execute_in_thread 的异常终态路径：DB 状态 FAILED →
        前端状态事件 → lifecycle 回调（父 wait/fan-in 等待者立即收到失败，
        不挂 watchdog）。任何一步失败仅日志留痕，不阻塞线程退出。
        """
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._mark_failed_before_execution(instance, configurable, waited_seconds))
        except Exception:
            logger.exception(f"子代理排队超时失败终态处理异常: {instance.thread_id}")
        finally:
            try:
                loop.close()
            except Exception:
                logger.warning("关闭排队超时失败路径事件循环失败（非致命）", exc_info=True)

    async def _mark_failed_before_execution(
        self, instance: Any, configurable: dict | None, waited_seconds: float
    ) -> None:
        try:
            await self.runtime._update_status(instance.thread_id, SubAgentStatus.FAILED)
        except Exception:
            logger.exception(f"子代理排队超时置 FAILED 失败: {instance.thread_id}")
        await self._publish_status_event(instance, configurable, SubAgentStatus.FAILED)
        await self._notify_finished(instance, "failed")

    def _execute_in_thread(self, instance: Any, agent_config: Any, configurable: dict | None, resume_payload: Any) -> None:
        """持有并发槽执行子代理图（原 _run_in_thread 执行体，独立成方法）。"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._execute(instance, agent_config, configurable, resume_payload))
        except Exception:
            logger.exception(f"子代理执行异常: {instance.thread_id}")
            _configurable_registry.pop(instance.thread_id, None)
            loop.run_until_complete(
                self.runtime._update_status(instance.thread_id, SubAgentStatus.FAILED)
            )
            loop.run_until_complete(self._notify_finished(instance, "failed"))
        finally:
            # 三种退出路径（正常完成 / interrupt / 异常）统一释放异步 checkpointer 连接
            try:
                from Django_xm.apps.ai_engine.services.checkpointer_factory import release_async_checkpointer

                loop.run_until_complete(release_async_checkpointer())
            except Exception:
                logger.exception("释放子代理 checkpointer 连接失败（非致命）")
            try:
                loop.close()
            except Exception:
                logger.warning("关闭子代理事件循环失败（非致命）", exc_info=True)
            asyncio.set_event_loop(None)
            # 协作式取消标志随执行线程退出清理（防 thread_id 复用泄漏/误触）
            _cancel_flags.pop(instance.thread_id, None)

    async def _execute(self, instance: Any, agent_config: Any, configurable: dict | None, resume_payload: Any) -> None:
        from langchain_core.messages import HumanMessage
        from langgraph.types import Command as LgCommand

        thread_id = instance.thread_id
        # 协作式取消（C14）：执行线程入口注册取消标志，terminate 置位后停止流消费
        cancel_event = threading.Event()
        _cancel_flags[thread_id] = cancel_event
        if resume_payload is None:
            # 首次 spawn：保存父 configurable（含事件回调引用），resume 时复用
            if configurable:
                _configurable_registry[thread_id] = configurable
        elif configurable is None:
            # resume：从 registry 恢复父 configurable（graph 重建后回调不可丢）
            configurable = _configurable_registry.get(thread_id)
        run_config = {
            "configurable": self._build_configurable(instance, configurable, agent_config),
            "recursion_limit": MAX_SUBAGENT_ROUNDS,
        }
        # 本子代理权威路由配置（session_id=根标识、chat_session_id/assistant_message_id
        # 经 _build_configurable 兜底后齐全）：供审批创建与状态事件消费，
        # 替代 raw configurable（重启恢复时为内存 registry 兜底值，可能为 None）。
        routing_config = run_config["configurable"]

        # 终止竞态兜底：terminate 可能在执行线程尚未注册取消标志时到达
        # （spawn 后立即终止，thread 调度晚于 DB 终态写入），复核 DB 状态，
        # 已终态则不再启动图执行（否则终止的任务会以 completed 终态覆盖 FAILED）
        fresh = await self.runtime.get_instance(thread_id)
        if fresh is not None and fresh.status == SubAgentStatus.FAILED:
            logger.info(f"子代理已在执行启动前被终止，跳过图执行: {thread_id}")
            _configurable_registry.pop(thread_id, None)
            await self._notify_finished(instance, "failed")
            return

        # 创建子代理 graph（独立 thread_id + 独立 checkpoint）：
        # 经注入工厂创建（默认由 agent_hub AppConfig.ready() 注册），
        # 本模块禁止直接 import agent_hub（依赖倒置，避免 ai_engine 反向依赖）。
        factory = self._graph_factory or _default_graph_factory
        if factory is None:
            raise RuntimeError(
                "子代理 graph 工厂未注册：需在 agent_hub AppConfig.ready() 中调用 "
                "set_default_graph_factory 注册"
            )
        agent = await factory(agent_config)

        if resume_payload is None:
            # 首次 spawn：HumanMessage 输入（task 为任务描述，来自 metadata）
            task = (instance.metadata or {}).get("task", "")
            graph_input = {"messages": [HumanMessage(content=task)]}
        else:
            # resume：Command(resume={interrupt_id: decisions})
            interrupt_id = (instance.pending_interrupt_info or {}).get("interrupt_id", "")
            graph_input = LgCommand(resume={interrupt_id: resume_payload})

        # 消费 astream（事件转发由中间件钩子完成，无需手动消费 chunk）；
        # C14 协作式取消：每个事件检查点检测终止标志，命中则停止消费
        cancelled = False
        async for _chunk in agent.graph.astream(
            graph_input,
            config=run_config,
            stream_mode=["messages", "updates"],
        ):
            if cancel_event.is_set():
                cancelled = True
                break

        if cancelled:
            logger.info(f"子代理协作式取消生效，停止流消费: {thread_id}")
            _configurable_registry.pop(thread_id, None)
            await self.runtime._update_status(thread_id, SubAgentStatus.FAILED)
            # 子代理状态事件：前端实时刷新子代理卡为终态（否则停留"执行中"）
            await self._publish_status_event(instance, routing_config, SubAgentStatus.FAILED)
            # 唤醒父会话 fan-in 等待者（与异常终态路径一致）
            await self._notify_finished(instance, "failed")
            return

        # 关键：astream 结束 ≠ 完成，读真实 state 判定
        state = await agent.graph.aget_state(run_config)
        if state is not None and getattr(state, "next", None):
            # 业务等待中断（wait_for_subagent，等待孙代理终态，非用户审批）：
            # 保持 RUNNING + 注册父 awaiter，孙代理终态时由 lifecycle_manager 唤醒恢复。
            wait_info = self._detect_subagent_wait_interrupt(state)
            if wait_info:
                await self._handle_subagent_wait_suspend(instance, wait_info)
                return
            # 审批中断：写 pending_interrupt_info，退出协程等外部 resume
            pending = self._extract_interrupt_info(state)
            await self.runtime._update_status(
                thread_id,
                SubAgentStatus.INTERRUPTED_PENDING_USER_INPUT,
                pending_interrupt_info=pending,
            )
            # 子代理状态事件：前端实时刷新子代理卡状态（否则"等待你的确认"滞后）
            await self._publish_status_event(instance, routing_config, SubAgentStatus.INTERRUPTED_PENDING_USER_INPUT)
            # 审批中断（非业务等待）：创建审批 DB 记录 + 发布 approval_pending 事件，
            # 供前端子代理卡片内工具卡渲染"确认执行/拒绝"控件（spec D9/D10）。
            # 统一审批链路：子代理与主代理审批共用 /approvals/{interrupt_id}/resume/
            # 端点，仅 source（chat/deep_research）与 extra.subagent_thread_id 不同；
            # 前端审批 → gateway 信令（带 subagent_thread_id）→ SessionManager
            # 子代理分支 → 终态化 + runtime.resume 断点续跑。
            try:
                await self._create_approvals_from_interrupts(instance, routing_config, state)
            except Exception:
                logger.exception(
                    f"子代理审批创建失败（非致命，状态已挂起）: {thread_id}"
                )
            logger.info(f"子代理 interrupt 挂起: {thread_id}, interrupt_id={pending.get('interrupt_id')}")
        else:
            result_preview = self._extract_result(state)
            await self.runtime._update_status(
                thread_id,
                SubAgentStatus.COMPLETED,
                result_preview=result_preview,
            )
            # 子代理状态事件：前端实时刷新子代理卡状态为"已完成"（缺此事件会导致
            # 子代理卡实时停留"执行中"，仅刷新页面才正常）
            await self._publish_status_event(instance, routing_config, SubAgentStatus.COMPLETED)
            logger.info(f"子代理执行完成: {thread_id}, result_len={len(result_preview)}")
            _configurable_registry.pop(thread_id, None)
            await self._notify_finished(instance, "completed")

    # ── 辅助 ────────────────────────────────────────────────────────────

    async def _publish_status_event(self, instance: Any, configurable: dict | None, status: str) -> None:
        """发布子代理状态变更事件（chat 场景，前端据此实时刷新子代理元数据）。

        子代理状态仅更新 DB 不够：前端实时状态靠事件驱动 scheduleSubagentsRefresh
        拉取 /subagents，缺事件会停留旧值（如已完成仍显示"执行中"，刷新才正常）。
        事件发布到父线程频道（chat 场景 session:{chat_session_id}），payload 携带
        status，前端按顶层 subagent_thread_id 路由刷新该子代理卡。

        仅在 chat 场景（configurable 携带 chat_session_id）发布；research 场景
        有 task 状态轮询/status_change 通道覆盖，避免事件链路误路由。
        """
        try:
            from Django_xm.common.event_schema import EventSource, EventType
            from Django_xm.common.realtime_events import publish_event

            cfg = configurable or {}
            chat_session_id = cfg.get("chat_session_id") or ""
            if not chat_session_id:
                return
            await publish_event(
                EventType.SUBAGENT_STATUS_CHANGE,
                {
                    "source": EventSource.CHAT,
                    # source_id = 子代理权威父线程 id（instance.parent_thread_id）：
                    # 代理模式 = chat_session_id；chat 关联深研 = research task_id。
                    # 前端 scheduleSubagentsRefresh 以此为 parent_thread_id 拉取元数据，
                    # 用 chat_session_id 在关联深研模式会查空（子代理挂在 research task 下）。
                    "source_id": instance.parent_thread_id or chat_session_id,
                    "message_id": cfg.get("assistant_message_id") or None,
                    "data": {
                        "status": status,
                        "agent_name": (instance.metadata or {}).get("agent_name") or "",
                    },
                },
                session_id=chat_session_id,
                subagent_thread_id=instance.thread_id,
            )
            logger.info(
                f"子代理状态事件已发布: sub={instance.thread_id}, status={status}, "
                f"session={chat_session_id}"
            )
        except Exception:
            logger.warning(
                f"子代理状态事件发布失败（非致命）: sub={instance.thread_id}, status={status}",
                exc_info=True,
            )

    async def _notify_finished(self, instance: Any, status: str) -> None:
        """子代理终态（completed/failed）触发生命周期回调，唤醒父 Graph（spec D4）。"""
        try:
            from Django_xm.apps.ai_engine.subagent_runtime.lifecycle import get_lifecycle_manager

            await get_lifecycle_manager().on_subagent_finished(
                subagent_thread_id=instance.thread_id,
                parent_thread_id=instance.parent_thread_id,
                status=status,
            )
        except Exception:
            logger.exception(f"子代理生命周期回调失败（非致命）: {instance.thread_id}")

    # ── 业务等待挂起（wait_for_subagent，嵌套子代理） ─────────────────────
    #
    # 子代理内部可再次 spawn 孙代理并 wait_for_subagent。wait 中断（_subagent_wait）
    # 与审批中断（_approval）不同：无需用户确认，孙代理终态后自动恢复。
    # 实现与主 agent（SessionExecutor._register_waiter）同构：
    # - 保持 RUNNING（子代理确实未完成，前端"执行中"语义准确），
    #   pending_interrupt_info 记录 wait 中断 id + _subagent_wait 标记（runtime.resume 校验放行）；
    # - 注册父 awaiter（subagent_thread_id → 回调），孙代理终态时由
    #   lifecycle_manager.on_subagent_finished 按 parent_thread_id 唤醒；
    # - 全部孙代理终态 → runtime.resume(thread_id, {"subagent_results": [...]})
    #   → adapter 以 Command(resume={wait_interrupt_id: payload}) 断点续跑。

    def _detect_subagent_wait_interrupt(self, state: Any) -> dict | None:
        """检测业务等待中断（wait_for_subagent）。

        Returns:
            {"subagent_thread_ids": [...], "interrupt_id": LangGraph interrupt id}
            未命中返回 None。
        """
        from Django_xm.apps.tools.base import is_subagent_wait_interrupt

        for task in getattr(state, "tasks", []) or []:
            for intr in getattr(task, "interrupts", []) or []:
                value = getattr(intr, "value", None)
                if is_subagent_wait_interrupt(value):
                    subagent_thread_ids = value.get("subagent_thread_ids", []) or []
                    return {
                        "subagent_thread_ids": list(subagent_thread_ids),
                        "interrupt_id": getattr(intr, "id", "") or "",
                    }
        return None

    async def _handle_subagent_wait_suspend(self, instance: Any, wait_info: dict) -> None:
        """子代理业务等待挂起：保持 RUNNING + 注册父 awaiter（孙代理终态唤醒）。"""
        thread_id = instance.thread_id
        subagent_thread_ids = wait_info["subagent_thread_ids"]
        interrupt_id = wait_info["interrupt_id"]

        # 挂起信息：RUNNING + _subagent_wait 标记（runtime.resume 校验放行，
        # Command(resume={interrupt_id: ...}) 使用 interrupt_id 断点续跑）
        await self.runtime._update_status(
            thread_id,
            SubAgentStatus.RUNNING,
            pending_interrupt_info={
                "interrupt_id": interrupt_id,
                "_subagent_wait": True,
            },
        )
        logger.info(
            f"子代理业务等待挂起: {thread_id}, subagents={subagent_thread_ids}, "
            f"interrupt_id={interrupt_id}"
        )

        # TOCTOU 修复（round3 2.6）：先注册 awaiter，再复核 DB 终态。
        # 旧实现「先查终态 → 再注册」在两步之间存在竞态窗口：孙代理在
        # 查询之后、注册之前终态时，其终态回调（on_subagent_finished）
        # 找不到 awaiter 被丢弃 → 父会话永久等待（挂到 watchdog）。
        # 注册后复核把窗口收敛到「注册点之后」——此后回调必然送达；
        # 复核命中的已终态孙代理经 awaiter 补投（与 lifecycle 回调同一路径，
        # 与回调的重复投递由 _make_subagent_wait_awaiter 的 resume-once
        # 守卫幂等吸收）。
        from Django_xm.apps.ai_engine.subagent_runtime.lifecycle import get_lifecycle_manager

        awaiter = self._make_subagent_wait_awaiter(instance, subagent_thread_ids)
        get_lifecycle_manager().register_parent_awaiter(thread_id, awaiter)

        for sid in subagent_thread_ids:
            inst = await self.runtime.get_instance(sid)
            if inst is not None and inst.status in (SubAgentStatus.COMPLETED, SubAgentStatus.FAILED):
                await awaiter(sid, inst.status)

    def _make_subagent_wait_awaiter(self, instance: Any, subagent_thread_ids: list[str]):
        """构造父 awaiter（孙代理终态回调）：全部终态后恢复子代理 graph。

        孙代理终态在其独立线程/事件循环触发（_notify_finished），awaiter 在该
        loop 中被 lifecycle_manager await。恢复（runtime.resume → adapter.resume）
        自行新建线程执行子代理 graph，不依赖本 loop 存活（子代理挂起协程的 loop
        在 _run_in_thread 结束时已 close），故直接 await 而非调度回原 loop。

        并发安全（round3 2.6）：多个孙代理终态回调在不同线程并发到达，且
        注册后复核（_handle_subagent_wait_suspend）可能与 lifecycle 回调对
        同一 sid 重复投递——resume-once 守卫（threading.Event + 锁保护临界区，
        锁不跨 await：DB 读取在锁外、判定与置位在锁内）保证 runtime.resume
        恰好触发一次。
        """
        collected: dict[str, dict] = {}
        resume_lock = threading.Lock()
        resumed = threading.Event()

        async def _awaiter(subagent_thread_id: str, status: str) -> None:
            if subagent_thread_id not in subagent_thread_ids or resumed.is_set():
                return
            inst = await self.runtime.get_instance(subagent_thread_id)
            with resume_lock:
                if resumed.is_set():
                    return
                collected[subagent_thread_id] = {
                    "status": status,
                    "result": (inst.result_preview if inst is not None else "") or "",
                }
                if len(collected) < len(subagent_thread_ids):
                    return
                resumed.set()
            await self._resume_subagent_after_wait(instance, subagent_thread_ids, collected)

        return _awaiter

    async def _resume_subagent_after_wait(
        self, instance: Any, subagent_thread_ids: list[str], results: dict[str, dict]
    ) -> None:
        """全部孙代理终态后恢复子代理 graph（runtime.resume → Command(resume) 续跑）。"""
        thread_id = instance.thread_id
        from Django_xm.apps.ai_engine.subagent_runtime.lifecycle import get_lifecycle_manager

        get_lifecycle_manager().unregister_parent_awaiter(thread_id)
        subagent_results = [
            {
                "subagent_thread_id": sid,
                "status": results[sid]["status"],
                "result": results[sid]["result"],
            }
            for sid in subagent_thread_ids
            if sid in results
        ]
        logger.info(f"子代理业务等待恢复: {thread_id}, subagents={subagent_thread_ids}")
        try:
            await self.runtime.resume(thread_id, {"subagent_results": subagent_results})
        except Exception:
            logger.exception(f"子代理业务等待恢复失败: {thread_id}")

    def _build_configurable(self, instance: Any, configurable: dict | None, agent_config: Any = None) -> dict:
        """构造子代理 configurable。

        - ``thread_id``：子代理独立 id（独立 checkpoint）。
        - ``subagent_thread_id``：SSE 定向推送路由标识符（协议字段，snake_case）。
        - 事件转发回调（_on_tool_event / _on_subagent_content）从父 configurable 继承。
        - ``agent_path``：从父 configurable 继承后追加当前 agent_name（父无则以
          ``["main"]`` 起始），仅服务 state.subagent_path 展示元数据（嵌套层级），
          不参与事件路由（路由唯一依据为 subagent_thread_id，spec D1/MODIFIED）。
        - ``risk_ceiling``：子代理角色风险上限（审批中间件读取）。
        - ``tool_names`` / 运行配置（user_id/session_id/model_name/store/enable_deep_thinking）：
          工具集与配置经 configurable 显式逐层传递（替代历史 get_parent_tool_context
          全局旁路）。子代理以自身 AgentConfig.tools 覆盖父 tool_names，故孙代理
          spawn 时从本 configurable 读取到的即父（=本子代理）的实际工具集。
        """
        meta = instance.metadata or {}
        depth = meta.get("depth", 0)
        agent_name = meta.get("agent_name", "general-purpose")
        parent_path = (configurable or {}).get("agent_path")
        if not isinstance(parent_path, list) or not parent_path:
            parent_path = ["main"]
        cfg: dict = {
            "thread_id": instance.thread_id,
            "subagent_thread_id": instance.thread_id,
            "depth": depth,
            "agent_name": agent_name,
            "agent_path": [*parent_path, agent_name],
            "risk_ceiling": meta.get("risk_ceiling"),
        }
        if configurable:
            for _key in ("_on_tool_event", "_on_subagent_content", "chat_session_id", "assistant_message_id"):
                if _key in configurable and configurable[_key] is not None:
                    cfg[_key] = configurable[_key]
        # 重启恢复兜底：父 configurable 来自内存 registry（_configurable_registry），
        # FastAPI 重启后为空 → chat_session_id / assistant_message_id 从持久化
        # metadata 兜底，保证 graph 内消费者与孙代理 spawn 继承不断链。
        if not cfg.get("chat_session_id") and meta.get("chat_session_id"):
            cfg["chat_session_id"] = meta["chat_session_id"]
        if not cfg.get("assistant_message_id") and meta.get("assistant_message_id"):
            cfg["assistant_message_id"] = meta["assistant_message_id"]
        if agent_config is not None:
            cfg["tool_names"] = [
                getattr(t, "name", "") for t in (getattr(agent_config, "tools", None) or [])
            ]
            cfg["user_id"] = getattr(agent_config, "user_id", None)
            cfg["session_id"] = getattr(agent_config, "session_id", None)
            cfg["model_name"] = getattr(agent_config, "model_name", None)
            cfg["provider_id"] = getattr(agent_config, "provider_id", None)
            cfg["store"] = getattr(agent_config, "store", None)
            cfg["enable_deep_thinking"] = bool(
                getattr(agent_config, "enable_deep_thinking", False)
            )
            # 任务级沙箱开关继承：主 agent（research_runner/chat_service 写入
            # configurable）开启时，子代理 HIGH 级命令同样进容器隔离，避免泄漏本机。
            cfg["enable_sandbox"] = bool((configurable or {}).get("enable_sandbox", False))
            cfg["use_web_search"] = (configurable or {}).get("use_web_search", True)
            cfg["use_mcp"] = (configurable or {}).get("use_mcp")
        return cfg

    async def _create_approvals_from_interrupts(
        self, instance: Any, configurable: dict | None, state: Any
    ) -> None:
        """子代理审批中断：创建审批 DB 记录 + 发布 approval_pending 事件。

        与主 agent（SessionExecutor._on_interrupt → create_approvals_for_interrupts）
        使用同一链路（parse_approval_interrupt → request_approval_async），
        保证审批持久化与实时事件（spec D9/D10 事件单通道发布）一致：
        - 审批归集到父线程（source_id=parent_thread_id：研究任务或 chat 会话），
          事件随 task/session 频道推送；
        - approval.extra 透传 subagent_thread_id，gateway 据此将统一审批端点的
          恢复信令路由到子代理（而非主会话挂起协程）。
        """
        from Django_xm.apps.approvals.models import Approval
        from Django_xm.apps.approvals.services.approval_batch import create_approvals_for_interrupts
        from Django_xm.common.approval_parser import parse_approval_interrupt

        interrupt_value = None
        graph_interrupt_id = ""
        langgraph_resume_id = ""
        for task in getattr(state, "tasks", []) or []:
            for intr in getattr(task, "interrupts", []) or []:
                value = getattr(intr, "value", None)
                if not isinstance(value, dict) or not value.get("_approval"):
                    continue
                interrupt_value = value
                langgraph_resume_id = getattr(intr, "id", "") or ""
                graph_interrupt_id = (value.get("_meta") or {}).get("graph_interrupt_id", "") or langgraph_resume_id
                break
            if interrupt_value is not None:
                break
        if interrupt_value is None:
            logger.warning(f"子代理无审批中断可创建: {instance.thread_id}")
            return

        approval_data_list = parse_approval_interrupt(
            interrupt_value,
            graph_interrupt_id=graph_interrupt_id,
            langgraph_resume_id=langgraph_resume_id,
        )
        if not approval_data_list:
            logger.warning(f"子代理审批解析为空: {instance.thread_id}")
            return

        meta = instance.metadata or {}
        # 审批归属（source/source_id）用「根线程标识」而非直接父线程：
        # 嵌套子代理（depth≥2）的直接父是 subagent_xxx，若按其推断会把深研嵌套
        # 审批误判为 chat（独立深研无 chat_session_id 时事件发到 subagent 频道，
        # 前端收不到）。configurable.session_id 由各模块源头写入根标识
        # （chat=会话 id，深研=research task id）并随 spawn 逐层继承，是权威归属。
        # 兜底链：configurable（内存，重启丢失）→ metadata（持久化根标识）→ 直接父线程。
        root_session_id = (
            (configurable or {}).get("session_id")
            or meta.get("session_id")
            or instance.parent_thread_id
        )
        source = (
            Approval.Source.DEEP_RESEARCH
            if str(root_session_id).startswith("research_")
            else Approval.Source.CHAT
        )
        source_id = root_session_id
        # chat_session_id / assistant_message_id 由各模块源头写入 configurable 统一契约：
        # - chat 代理模式：chat_service.py 写入（data.session_id / assistant message id）
        # - 深度研究模块 + chat 深度研究模式：research_runner.execute_research_async 写入
        # - 独立深度研究（chat_session_id=None）：source=deep_research，无强制校验
        # 新模块接入子代理时必须遵循此契约写入 chat_session_id，否则 source=chat 场景
        # request_approval_async 会显式报错拒绝创建（防呆，不做静默兜底）。
        # 重启恢复场景 configurable 丢失时从 metadata 兜底（spawn 时已持久化）。
        await create_approvals_for_interrupts(
            approval_data_list,
            source=source,
            source_id=source_id,  # 根线程标识（会话/研究任务），非直接父线程
            user_id=meta.get("user_id"),
            chat_session_id=(configurable or {}).get("chat_session_id") or meta.get("chat_session_id"),
            message_id=(
                (configurable or {}).get("assistant_message_id", "")
                or meta.get("assistant_message_id", "")
                or ""
            ),
            data=None,
        )
        # 审批自治（对齐 7.md）：子代理审批只与子代理自身状态相关，与父任务状态
        # 完全解耦。父任务保持 running（waiting_subagent），不切 awaiting_approval；
        # 前端 SubAgentCard 按子代理自身 interrupted_pending_user_input 判断审批可用性。
        logger.info(
            f"子代理审批已创建: subagent={instance.thread_id}, parent={instance.parent_thread_id}, "
            f"source={source}, count={len(approval_data_list)}, graph_interrupt_id={graph_interrupt_id}"
        )

    @staticmethod
    def _extract_interrupt_info(state: Any) -> dict:
        """从 state.tasks 提取 interrupt 信息（ApprovalMiddleware 批量审批 payload）。

        严格对齐 spec pending_interrupt_info 批量结构契约
        （interrupt_type / interrupt_id / requests:[{tool_call_id/tool_name/args/risk_level/reason}]），
        框架私有字段（_approval / graph_interrupt_id / session_id / danger_level 等）不透出上层。
        """
        for task in getattr(state, "tasks", []) or []:
            for intr in getattr(task, "interrupts", []) or []:
                value = getattr(intr, "value", None)
                if not isinstance(value, dict) or not value.get("_approval"):
                    continue
                raw_requests = value.get("requests", []) or []
                requests = []
                for req in raw_requests:
                    if not isinstance(req, dict):
                        continue
                    requests.append(
                        {
                            "tool_call_id": req.get("tool_call_id", ""),
                            "tool_name": req.get("tool_name", ""),
                            "args": req.get("args", {}) or {},
                            "risk_level": req.get("risk_level", ""),
                            "reason": req.get("description") or req.get("title") or "",
                        }
                    )
                return {
                    "interrupt_type": "approval",
                    "interrupt_id": getattr(intr, "id", ""),
                    "requests": requests,
                }
        return {"interrupt_type": "approval", "interrupt_id": "", "requests": []}

    @staticmethod
    def _extract_result(state: Any) -> str:
        """从 state.values 提取最终 AI 回复文本（结果预览）。"""
        try:
            values = getattr(state, "values", None) or {}
            messages = values.get("messages", [])
            for msg in reversed(messages):
                content = getattr(msg, "content", None)
                if content:
                    return content_to_str(content)[:5000]
        except Exception:
            logger.exception("提取子代理结果失败（非致命）")
        return ""

    async def _rebuild_agent_config(self, instance: Any) -> Any:
        """resume 时从 instance.metadata 重建 AgentConfig。

        graph 对象不能跨事件循环复用，resume 必须重新创建 agent。
        工具集按 metadata["tool_names"] 恢复（与 spawn 时一致），
        system_prompt 按 metadata["system_prompt"] 恢复（角色提示）。
        AgentConfig 经注入工厂构造（agent_type 由工厂实现侧固定 BASE），
        本模块禁止直接 import agent_hub（依赖倒置）。
        """
        from Django_xm.apps.tools import get_all_basic_tools, get_tools_for_request_async

        meta = instance.metadata or {}
        tool_names = meta.get("tool_names") or []
        user_id = meta.get("user_id")

        if tool_names:
            try:
                tools = await get_tools_for_request_async(
                    use_tools=True,
                    selected_tools=list(tool_names),
                    user_id=user_id,
                )
            except Exception as e:
                logger.warning(f"resume 重建工具集失败，回退基础工具集: {e}")
                tools = get_all_basic_tools()
        else:
            tools = get_all_basic_tools()

        config_factory = self._config_factory or _default_config_factory
        if config_factory is None:
            raise RuntimeError(
                "子代理 AgentConfig 工厂未注册：需在 agent_hub AppConfig.ready() 中调用 "
                "set_default_config_factory 注册"
            )
        return config_factory(
            name=meta.get("agent_name", "general-purpose"),
            tools=tools,
            system_prompt=meta.get("system_prompt", ""),
            user_id=user_id,
            session_id=meta.get("session_id"),
            model_name=meta.get("model_name"),
            provider_id=meta.get("provider_id"),
        )
