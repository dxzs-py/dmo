"""会话级单执行流（SessionExecutor 共享基类 + chat/research 域子类）。

一个会话（深度研究 / chat agent）对应一个实例，单协程全生命周期运行 LangGraph：
- 审批中断时 ``asyncio.Event`` 原生挂起（不退出、不拆分恢复任务），
  收到 Redis 信令（审批决策）后校验批次完整性并注入决策唤醒恢复；
- 事件发射复用 ``realtime_sync.publish_event``（Django Channels 网关转发前端），
  执行服务对前端完全透明；
- 审批等决策以 DB 为唯一真相源，挂起时周期性扫描 DB 批次决策与过期审批自愈，
  信令丢失最终一致。

薄封装：复用 adapter.astream_research_with_interrupts 的 Command(resume=...) 语义，
on_interrupt 返回非空 decisions dict 时 adapter 自动重入执行。

域拆分（E7/审计 §3.2-2）：
- ``SessionExecutor``：共享基类（生命周期 / 挂起 watchdog / 审批信令 / 子代理
  等待 fan-in / 共用清理）。保留 ``SessionExecutor`` 名字并经 ``__new__`` 工厂
  按 ``session_type`` 返回域子类实例（历史构造点 ``SessionExecutor(session_type=...)``
  与测试类级 patch 面 ``mock.patch.object(SessionExecutor, ...)`` 保持兼容）；
- ``ResearchSessionExecutor``：research 域（研究执行分派 / 取消终态化级联 /
  审批中断创建 / 业务等待挂起）；
- ``ChatSessionExecutor``：chat 域（chat 执行链分派 / 停止级联清理 / 恢复
  等待与基线注入 / 业务等待挂起）。

归属说明：部分 research 域方法（``_run_research`` / ``_execute_and_handle`` /
``_persist_research_progress`` / ``_handle_result`` / ``_handle_failure`` /
``_wait_for_initial_batch`` / ``_inject_research_resume_baseline`` /
``_load_research_resume_baseline``）与 chat 批次静态读取器
（``_list_chat_batch_ids``）因测试类级 patch / 静态调用面（
``mock.patch.object(SessionExecutor, ...)`` / ``SessionExecutor._static(...)``）
锚定在基类——子类覆盖会 shadow 掉对基类属性的 patch 注入，故不强行下移
（详见 E7 交付报告）。Agent 物料装配（原 ``_build_agent`` 主体）已下移
``apps/research/services/agent_assembly.py``，基类仅留薄委托。
"""

import asyncio
import logging
import time
from typing import Any

from asgiref.sync import sync_to_async

from Django_xm.apps.approvals.models import Approval
from Django_xm.apps.approvals.services.approval_batch import (
    abandon_session_pending_approvals,
    await_wait_for_batch_decision,
    collect_batch_decisions,
    create_approvals_for_interrupts,
    finalize_batch_approvals,
    self_heal_expired_approvals,
)
from Django_xm.apps.research.models import ResearchTaskStatus
from Django_xm.apps.research.services.research_runner import (
    cleanup_research_sandbox,
    execute_research_async,
    finalize_research,
)

logger = logging.getLogger(__name__)

# 挂起等待时的批次轮询间隔（秒）：信令唤醒走 Event，轮询仅用于自愈/兜底
_BATCH_POLL_INTERVAL = 5
# sandbox 延迟清理（秒）
_SANDBOX_CLEANUP_DELAY = 120
# 停止宽限期（秒）：用户停止后等待执行协程在安全点优雅退出，超时兜底硬取消。
# 流式 token 输出为毫秒级，安全点（循环迭代/审批挂起）通常远快于此上限。
_STOP_GRACE_PERIOD = 5


async def _update_task_status(thread_id: str, status_data: dict) -> None:
    """异步包装 update_task_status（同步 ORM 调用）。"""
    from Django_xm.apps.research.services.task_manager import update_task_status

    await sync_to_async(update_task_status)(thread_id, status_data)


class SessionExecutor:
    """会话执行器共享基类（单协程全生命周期）。

    直接构造 ``SessionExecutor(...)`` 时经 ``__new__`` 工厂按 ``session_type``
    返回域子类实例（chat → ``ChatSessionExecutor``，其余 →
    ``ResearchSessionExecutor``，语义与拆分前 run()/stop() 的 ``!= "chat"``
    分派一致），``__init__`` 签名不变。
    """

    def __new__(cls, *args, **kwargs):
        if cls is SessionExecutor:
            # 构造兼容工厂（E7）：按 session_type 分派域子类。非 chat 一律
            # research（与拆分前各处 `session_type != "chat"` 分派语义一致）。
            session_type = kwargs.get("session_type", "research")
            target = ChatSessionExecutor if session_type == "chat" else ResearchSessionExecutor
            return super().__new__(target)
        return super().__new__(cls)

    def __init__(
        self,
        manager,
        *,
        thread_id: str,
        query: str = "",
        user_id: int | None = None,
        session_id: str | None = None,
        message_id: str = "",
        params: dict[str, Any] | None = None,
        session_type: str = "research",
    ):
        self.manager = manager
        self.thread_id = thread_id
        self.query = query
        self.user_id = user_id
        self.session_id = session_id
        self.message_id = message_id
        self.session_type = session_type
        # Agent 构建参数（初始执行时由 start 信令透传；恢复时由 task 组装）
        self.params = params or {}
        self.start_time = time.time()
        # 挂起等待表：graph_interrupt_id -> asyncio.Event
        self._pending_events: dict[str, asyncio.Event] = {}
        self._run_task: asyncio.Task | None = None
        # 优雅停止标志（Task 9）：用户停止生成时置位，执行循环在安全点检查并退出，
        # 保留 LangGraph checkpoint 与已输出内容（区别于 cancel() 硬取消中断执行中途）
        self._stop_requested = False
        # 后台清理任务引用（sandbox 延迟清理），防止被 GC 回收
        self._background_tasks: set[asyncio.Task] = set()
        # 业务等待挂起（spec D4）：父 Graph 等待子代理结果时退出协程（非审批 interrupt）。
        # 规则：挂起态「保留会话槽 + 不释放 checkpointer」，调度器唤醒后新建协程续跑；
        # 真正结束（成功/失败/取消）时才释放 checkpointer + 移除会话槽。
        self._agent = None
        self._suspended = False
        # 挂起起始时刻（monotonic 秒，B6 挂起 watchdog 判定依据；非挂起态为 None）
        self._suspended_since: float | None = None
        # watchdog 终态化已触发标志（防周期扫描重复触发）
        self._finalized_by_watchdog = False
        self._wait_interrupt_id = ""
        # 业务等待挂起（批量 fan-out/fan-in）：一次等待多个子代理，全部终态后恢复
        self._wait_subagent_thread_ids: list[str] = []
        self._pending_subagent_results: dict[str, dict] = {}
        # chat 停止级联清理已调度标志（C2/P2-6）：防重复 stop() 重复调度级联任务
        # （research 侧由 cancel_task_if_active 条件终态化天然自护，chat 无任务表需显式防重）
        self._chat_stop_cleanup_scheduled = False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self) -> None:
        """启动执行协程（由 SessionManager 在事件循环内调用）。"""
        self._run_task = asyncio.create_task(self.run(), name=f"research-exec-{self.thread_id}")

    def stop(self, *, finalize_cancel: bool = True) -> None:
        """终止执行协程（共用机制 + 域终态化经 ``_schedule_stop_finalization`` 多态分派）。

        research 会话（C1/P2-1，``ResearchSessionExecutor._schedule_stop_finalization``）：
        取消=终态不可恢复——调度取消终态化任务（终止执行协程 + 级联清理 +
        状态落库 CANCELLED 广播），checkpoint 可清理。终态化经
        cancel_task_if_active 条件写入，与完成/失败终态竞争时（取消瞬间任务
        恰好终态）自动退让。

        chat 会话（Task 9 / C2/P2-6，``ChatSessionExecutor._schedule_stop_finalization``）：
        优雅停止——置位停止标志，执行循环在安全点检查并退出，保留 checkpoint
        与已输出内容（可恢复）；stopped=True 广播 / 审批批次收尾 / checkpointer
        释放 / 会话槽移除由执行协程退出路径完成，对外语义不变。额外调度资源
        级联清理（终止运行中子代理 + 沙箱容器清理 + 挂起态停止补清理），见
        ``_finalize_chat_stop_cleanup``。

        机制（共用）：
        1. 置位停止标志，执行循环在安全点（循环迭代 / 审批挂起）检查并退出；
        2. 唤醒所有审批挂起等待（asyncio.Event.set），使挂起协程立即退出等待；
        3. 宽限期（_STOP_GRACE_PERIOD）后若协程仍在运行，兜底硬取消，
           保证极端场景（LLM 长阻塞）也不会永久悬挂。

        Args:
            finalize_cancel: 取消终态化/级联清理开关。服务停机路径传 False——
              停机不是用户取消，会话保持可恢复态（checkpoint 已持久化，重启后
              由 recover_unfinished 恢复续跑），research 不得终态化为 CANCELLED，
              chat 亦不终止子代理/清理沙箱（重启后子代理经信令恢复续跑）。

        checkpoint 由 LangGraph 在节点执行后自动持久化，已输出内容经
        finally 落库并广播，均不受优雅停止影响。
        """
        self._stop_requested = True
        for event in list(self._pending_events.values()):
            event.set()
        if finalize_cancel:
            self._schedule_stop_finalization()
        if self._run_task and not self._run_task.done():
            try:
                loop = self._run_task.get_loop()
            except Exception:
                loop = None
            if loop is not None:
                loop.call_later(_STOP_GRACE_PERIOD, self._force_cancel_if_still_running)
            logger.info(
                f"[SessionExecutor] 优雅停止已请求，等待安全点退出（宽限期 {_STOP_GRACE_PERIOD}s）: "
                f"thread_id={self.thread_id}"
            )

    def _schedule_stop_finalization(self) -> None:
        """stop(finalize_cancel=True) 的域终态化调度钩子（research/chat 子类多态分派）。"""
        raise NotImplementedError("域子类必须实现 _schedule_stop_finalization")

    def _force_cancel_if_still_running(self) -> None:
        """宽限期到后协程仍未退出时的兜底硬取消。"""
        if self._stop_requested and self._run_task and not self._run_task.done():
            self._run_task.cancel()
            logger.warning(
                f"[SessionExecutor] 停止宽限期已到，强制终止执行协程: thread_id={self.thread_id}"
            )

    def check_stop_requested(self) -> bool:
        """安全点检查：是否收到停止请求。执行循环在迭代边界调用。"""
        return self._stop_requested

    # ------------------------------------------------------------------
    # 挂起 watchdog（B6）
    # ------------------------------------------------------------------
    @property
    def suspended_since(self) -> float | None:
        """业务等待挂起起始时刻（monotonic 秒；非挂起态为 None）。

        公开只读：供 SessionManager 挂起 watchdog 判定，避免跨对象访问
        _suspended 私有标志。
        """
        return self._suspended_since

    def enforce_suspended_timeout(self, max_lifetime_seconds: float) -> bool:
        """挂起 watchdog 检查（B6）：挂起超过最大存活时长则触发超时终态化。

        由 SessionManager 周期扫描调用（事件循环内）。终态化在新任务中异步
        执行（任务失败态 + 广播 + 释放 checkpointer + 移除会话槽 + 注销
        awaiter），本方法立即返回。

        Args:
            max_lifetime_seconds: 挂起态最大存活时长（秒）。

        Returns:
            True 表示本次触发了终态化（含防重标志置位）。
        """
        if self._suspended_since is None or self._finalized_by_watchdog:
            return False
        if time.monotonic() - self._suspended_since < max_lifetime_seconds:
            return False
        self._finalized_by_watchdog = True
        self._suspended = False
        self._suspended_since = None
        task = asyncio.create_task(
            self._finalize_suspended_timeout(),
            name=f"suspended-watchdog-{self.thread_id}",
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return True

    async def _finalize_suspended_timeout(self) -> None:
        """挂起超时终态化（B6）公共骨架：日志 + 域终态化 + 统一清理。

        与 _resume_after_subagent_wait 的竞态窗口（终态化瞬间子代理恰好全部
        终态）：恢复路径的清理（释放 checkpointer/移除槽位/注销 awaiter）均
        幂等，重复调用无害；_finalized_by_watchdog 防止 watchdog 重复触发。
        域终态化差异（research 任务失败态 / chat 失败广播）由子类
        ``_finalize_suspended_failure`` 承担。
        """
        thread_id = self.thread_id
        logger.warning(
            f"[SessionExecutor] 挂起超时终态化（子代理挂死/回调丢失兜底）: "
            f"thread_id={thread_id}, session_type={self.session_type}"
        )
        try:
            await self._finalize_suspended_failure()
        finally:
            await self._release_checkpointer()
            self.manager.remove_session(thread_id)
            from Django_xm.apps.ai_engine.subagent_runtime.lifecycle import get_lifecycle_manager

            get_lifecycle_manager().unregister_parent_awaiter(thread_id)

    async def _finalize_suspended_failure(self) -> None:
        """挂起超时的域终态化钩子（research 任务失败态 / chat 失败广播）。"""
        raise NotImplementedError("域子类必须实现 _finalize_suspended_failure")

    # ------------------------------------------------------------------
    # run 编排（域执行经 _run_domain 多态分派）
    # ------------------------------------------------------------------
    async def run(self) -> None:
        """单协程完整执行流程，持续运行至会话结束（域执行由子类分派）。"""
        thread_id = self.thread_id
        try:
            await self._run_domain()
        except asyncio.CancelledError:
            logger.info(f"[SessionExecutor] 执行协程被终止: thread_id={thread_id}")
            raise
        except Exception as exc:
            logger.exception(f"[SessionExecutor] 执行异常: thread_id={thread_id}")
            await self._handle_run_exception(exc)
        finally:
            # 挂起态（业务等待子代理）：保留会话槽 + 不释放 checkpointer，
            # 由调度器唤醒后新建协程续跑并在此真正结束时清理（见 _resume_after_subagent_wait）。
            # 其余路径（成功/失败/取消/崩溃）统一释放 checkpointer + 移除会话槽。
            if not self._suspended:
                await self._release_checkpointer()
                self.manager.remove_session(thread_id)

    async def _run_domain(self) -> None:
        """域执行入口钩子（research → _run_research / chat → _run_chat）。"""
        raise NotImplementedError("域子类必须实现 _run_domain")

    async def _handle_run_exception(self, exc: Exception) -> None:
        """域运行异常终态化钩子（chat 无需处理 / research 分类文案失败终态）。"""
        raise NotImplementedError("域子类必须实现 _handle_run_exception")

    # ------------------------------------------------------------------
    # 子代理等待 fan-in（research/chat 共享）
    # ------------------------------------------------------------------
    def _begin_suspended_wait(self, interrupt_id: str, subagent_thread_ids) -> None:
        """业务等待挂起公共骨架（spec D4，research/chat 同构）：置挂起态 + 记录等待集。

        固化规则：
        - 保留会话槽（不调用 manager.remove_session），保持 thread_id 幂等 + 信令可达；
        - 不释放 checkpointer（graph 复用，恢复时从 checkpoint 续跑）；
        - 不触发 sandbox 清理（任务未完成）。
        """
        self._suspended = True
        self._suspended_since = time.monotonic()
        self._wait_interrupt_id = interrupt_id
        self._wait_subagent_thread_ids = list(subagent_thread_ids or [])
        # 新一轮等待开始：清空上一轮残留的子代理终态结果。
        # 否则 _register_waiter 竞态兜底（len(_pending_subagent_results) >=
        # len(_wait_subagent_thread_ids)）会被旧残留提前满足，误判"全部子代理
        # 已终态"并立即恢复父 Graph——导致 wait_for_subagent 未真正等待子代理
        # （如子代理审批中断未完成），任务提前 completed 而子代理仍在后台运行。
        self._pending_subagent_results = {}

    async def _register_waiter(self) -> None:
        """批量等待：竞态兜底（检查全部子代理）+ 注册父 awaiter。"""
        from Django_xm.apps.ai_engine.models import SubAgentStatus
        from Django_xm.apps.ai_engine.subagent_runtime import get_subagent_runtime
        from Django_xm.apps.ai_engine.subagent_runtime.lifecycle import get_lifecycle_manager

        runtime = get_subagent_runtime()
        # 竞态兜底：子代理可能已在「tool 检查 → interrupt 挂起」窗口内终态，
        # 此时终态回调已错过、awaiter 永不被触发。这里二次检查并累积已终态结果。
        for sid in self._wait_subagent_thread_ids:
            instance = await runtime.get_instance(sid)
            if instance is not None and instance.status in (SubAgentStatus.COMPLETED, SubAgentStatus.FAILED):
                self._pending_subagent_results[sid] = {
                    "status": instance.status,
                    "result": instance.result_preview or "",
                }

        # 全部子代理已终态：立即恢复（不再注册 awaiter）
        if len(self._pending_subagent_results) >= len(self._wait_subagent_thread_ids):
            logger.info(
                f"[SessionExecutor] 全部子代理已终态，立即恢复: thread_id={self.thread_id}"
            )
            task = asyncio.create_task(
                self._resume_after_subagent_wait(),
                name=f"subagent-resume-{self.thread_id}",
            )
            # 保存引用防止被 GC 回收；完成后自动从集合移除
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
            return

        get_lifecycle_manager().register_parent_awaiter(
            self.thread_id, self._make_parent_awaiter()
        )

    def _make_parent_awaiter(self):
        """构造父 awaiter（子代理终态回调）。

        子代理终态在其独立线程/事件循环触发（LangGraphAdapter._notify_finished），
        本 awaiter 用 run_coroutine_threadsafe 将恢复调度回主事件循环（graph/checkpointer
        所属 loop），不阻塞子代理线程。
        """
        loop = asyncio.get_running_loop()

        def _log_future_error(fut) -> None:
            try:
                exc = fut.exception()
            except Exception:
                exc = None
            if exc is not None:
                logger.error(f"[SessionExecutor] 父 Graph 恢复任务异常: {exc}", exc_info=exc)

        async def _awaiter(subagent_thread_id: str, status: str) -> None:
            fut = asyncio.run_coroutine_threadsafe(
                self._on_subagent_finished(subagent_thread_id, status),
                loop,
            )
            fut.add_done_callback(_log_future_error)

        return _awaiter

    async def _on_subagent_finished(self, subagent_thread_id: str, status: str) -> None:
        """单个子代理终态回调：累积结果，全部终态后恢复父 Graph（批量 fan-in）。"""
        if subagent_thread_id not in self._wait_subagent_thread_ids:
            logger.info(
                f"[SessionExecutor] 忽略非等待子代理终态: thread_id={self.thread_id}, "
                f"subagent={subagent_thread_id}, waiting={self._wait_subagent_thread_ids}"
            )
            return

        result_text = await self._read_subagent_result(subagent_thread_id)
        self._pending_subagent_results[subagent_thread_id] = {
            "status": status,
            "result": result_text,
        }
        # 部分终态不恢复：等待全部子代理终态
        if len(self._pending_subagent_results) < len(self._wait_subagent_thread_ids):
            return
        await self._resume_after_subagent_wait()

    async def _resume_after_subagent_wait(self) -> None:
        """全部子代理终态后恢复父 Graph（批量 fan-in，主事件循环新建协程）。

        以 Command(resume={interrupt_id: {"subagent_results": [...]}}) 恢复父 Graph，
        最终结束时释放 checkpointer + 移除会话槽 + 注销 awaiter。域差异
        （恢复执行方式 / 失败处理）由子类 ``_resume_execution`` /
        ``_handle_resume_failure`` 承担。
        """
        thread_id = self.thread_id
        # 用户已停止：不续跑父 Graph，直接清理（释放 checkpointer + 移除会话槽）
        if self._stop_requested:
            logger.info(f"[SessionExecutor] 挂起态收到停止请求，放弃续跑: thread_id={thread_id}")
            self._suspended = False
            self._suspended_since = None
            await self._release_checkpointer()
            self.manager.remove_session(thread_id)
            from Django_xm.apps.ai_engine.subagent_runtime.lifecycle import get_lifecycle_manager

            get_lifecycle_manager().unregister_parent_awaiter(thread_id)
            return

        try:
            self._suspended = False
            self._suspended_since = None
            from langgraph.types import Command

            subagent_results = [
                {"subagent_thread_id": sid, "status": r["status"], "result": r["result"]}
                for sid, r in self._pending_subagent_results.items()
            ]
            resume_value = {"subagent_results": subagent_results}
            resume_command = Command(resume={self._wait_interrupt_id: resume_value})
            await self._resume_execution(resume_command)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(f"[SessionExecutor] 恢复父 Graph 异常: thread_id={thread_id}")
            await self._handle_resume_failure(exc)
        finally:
            if not self._suspended:
                # 真正结束（成功/失败/取消）：释放 checkpointer + 移除会话槽 + 注销 awaiter
                await self._release_checkpointer()
                self.manager.remove_session(thread_id)
                from Django_xm.apps.ai_engine.subagent_runtime.lifecycle import get_lifecycle_manager

                get_lifecycle_manager().unregister_parent_awaiter(thread_id)

    async def _resume_execution(self, resume_command) -> None:
        """域恢复执行钩子（research 重入研究链 / chat 重跑 chat 执行链）。"""
        raise NotImplementedError("域子类必须实现 _resume_execution")

    async def _handle_resume_failure(self, exc: Exception) -> None:
        """域恢复失败终态化钩子（research 失败终态 / chat 失败广播）。"""
        raise NotImplementedError("域子类必须实现 _handle_resume_failure")

    async def _read_subagent_result(self, subagent_thread_id: str) -> str:
        """读取子代理最终结果预览（result_preview）。"""
        from Django_xm.apps.ai_engine.subagent_runtime import get_subagent_runtime

        instance = await get_subagent_runtime().get_instance(subagent_thread_id)
        if instance is None:
            return ""
        return instance.result_preview or ""

    # ------------------------------------------------------------------
    # 审批信令（research/chat 共享：唤醒对应批次的挂起协程）
    # ------------------------------------------------------------------
    def on_approval_signal(self, payload: dict) -> None:
        """审批信令到达：唤醒对应批次的挂起协程（若存在）。"""
        graph_interrupt_id = payload.get("graph_interrupt_id") or ""
        if not graph_interrupt_id:
            return
        event = self._pending_events.get(graph_interrupt_id)
        if event:
            event.set()
            logger.info(
                f"[SessionExecutor] 审批信令唤醒批次: "
                f"thread_id={self.thread_id}, graph_interrupt_id={graph_interrupt_id}"
            )

    # ------------------------------------------------------------------
    # research 恢复等待与结果处理（域归属 research；因测试类级 patch /
    # 静态调用面锚定基类，不强行下移——子类覆盖会 shadow 掉对基类属性的
    # mock.patch.object(SessionExecutor, ...) 注入，详见 E7 交付报告）
    # ------------------------------------------------------------------
    async def _wait_for_initial_batch(self) -> dict:
        """恢复模式：等待首个全部决断的审批批次（服务重启后任务在等审批）。

        返回该批次的 decisions（{langgraph_resume_id: {tool_call_id: bool}}）；
        若任务不存在任何审批批次，返回空 dict——调用方（_run_research）按安全
        终态处理（B5/F3：不得以空 HumanMessage 重入取代 checkpoint 的 pending
        interrupt，也不得对悬空 tool_calls 的 checkpoint 伪造续跑）。
        """
        while True:
            batch_ids = await sync_to_async(self._list_batch_ids)(self.thread_id)
            if not batch_ids:
                # 任务不存在任何审批批次：返回空 dict，由调用方安全终态化（B5/F3）
                return {}
            for gid in batch_ids:
                decisions, all_resolved = await sync_to_async(collect_batch_decisions)(
                    Approval.Source.DEEP_RESEARCH, self.thread_id, gid
                )
                if all_resolved and decisions:
                    await sync_to_async(finalize_batch_approvals)(
                        decisions, Approval.Source.DEEP_RESEARCH, self.thread_id
                    )
                    logger.info(
                        f"[SessionExecutor] 恢复首个已决断批次: thread_id={self.thread_id}, "
                        f"graph_interrupt_id={gid}"
                    )
                    return decisions
            for gid in batch_ids:
                await sync_to_async(self_heal_expired_approvals)(
                    Approval.Source.DEEP_RESEARCH, self.thread_id, gid
                )
            # 所有批次均未决断：挂起等待审批信令（周期性轮询兜底）
            event = asyncio.Event()
            self._pending_events["__initial__"] = event
            try:
                await asyncio.wait_for(event.wait(), timeout=_BATCH_POLL_INTERVAL)
            except TimeoutError:
                pass
            finally:
                self._pending_events.pop("__initial__", None)

    async def _handle_result(self, result, response_time: float) -> None:
        thread_id = self.thread_id
        if result.success:
            logger.info(f"[SessionExecutor] 研究成功: thread_id={thread_id}")
            await sync_to_async(finalize_research)(
                thread_id,
                result,
                response_time,
                sync_files=True,
            )
            await self._publish_status_change("completed", final_report=result.final_report)
            await self._writeback_and_broadcast(
                result.main_content or result.final_report,
                success=True,
                reasoning_content=result.reasoning,
                subagent_contents=result.subagent_contents,
                tool_entries=result.tool_entries,
                final_report=result.final_report,
            )
            await self._schedule_sandbox_cleanup()
            return

        await self._handle_failure(result.error_message)

    async def _handle_failure(self, error_message: str, error_code: str = "") -> None:
        """研究失败终态（A1.4）：error_message 必须是用户可行动分类文案。

        Args:
            error_message: 用户面错误文案（调用方经 build_user_facing_error 分类，
                或 runner 内部语义化文案；禁止传裸 str(exc)）。
            error_code: 分类错误码（如 MODEL_CALL_ERROR），空字符串表示
                内部语义化文案（无异常对象可分类），不透传到事件 payload。
        """
        thread_id = self.thread_id
        logger.warning(f"[SessionExecutor] 研究失败: thread_id={thread_id}, error={error_message}")
        try:
            await _update_task_status(
                thread_id, {"status": ResearchTaskStatus.FAILED, "error": error_message}
            )
            if error_code:
                await self._publish_status_change(
                    ResearchTaskStatus.FAILED, error=error_message, error_code=error_code
                )
            else:
                await self._publish_status_change(ResearchTaskStatus.FAILED, error=error_message)
        except Exception:
            logger.exception(f"[SessionExecutor] 更新任务失败状态异常: {thread_id}")
        await self._writeback_and_broadcast(
            error_message, success=False, error_code=error_code
        )
        await self._schedule_sandbox_cleanup()

    async def _publish_status_change(self, status: str, **kw) -> None:
        """发布 status_change 实时事件（任务状态标签跨浏览器实时一致）。"""
        try:
            from Django_xm.common.realtime_sync import publish_task_status

            await publish_task_status(
                task_id=self.thread_id,
                status=status,
                cross_module_id=self.session_id or None,
                **kw,
            )
        except Exception as e:
            logger.debug(f"[SessionExecutor] 发布状态变更事件失败（非致命）: {e}")

    async def _writeback_and_broadcast(
        self,
        content: str,
        *,
        success: bool,
        reasoning_content: str = "",
        subagent_contents: dict | None = None,
        tool_entries: dict | None = None,
        final_report: str = "",
        error_code: str = "",
    ) -> None:
        """回写 ChatMessage + 广播 stream_completed（聊天深度研究场景）。

        error_code（A1.4）：失败时的分类错误码，透传到 stream_completed payload
        （对齐 chat 链失败终态结构：error 文案 + error_code），空字符串不透传。
        """
        if not self.session_id:
            return
        try:
            from Django_xm.apps.research.models import ResearchTask
            from Django_xm.apps.research.services.writeback import (
                broadcast_stream_completed,
                writeback_to_chat_message,
            )

            task = await sync_to_async(
                lambda: ResearchTask.objects.filter(task_id=self.thread_id, is_deleted=False).first()
            )()
            if task is None:
                return
            message_id = await sync_to_async(writeback_to_chat_message)(
                self.thread_id,
                content,
                success=success,
                chat_session_id=self.session_id,
                reasoning_content=reasoning_content,
                subagent_contents=subagent_contents,
                tool_entries=tool_entries,
            )
            await sync_to_async(broadcast_stream_completed)(
                self.session_id,
                self.thread_id,
                success=success,
                final_report=final_report if success else None,
                error=None if success else content,
                error_code=error_code,
                message_id=message_id,
                user_id=task.created_by_id,
                content=content if success else "",
            )
        except Exception as e:
            logger.warning(f"[SessionExecutor] 回写 ChatMessage 失败: {e}")

    async def _run_research(self) -> None:
        """深度研究执行核心（单协程，原 run 逻辑）。"""
        thread_id = self.thread_id
        await _update_task_status(
            thread_id,
            {"status": ResearchTaskStatus.RUNNING, "current_step": "research_started"},
        )
        await self._publish_status_change(ResearchTaskStatus.RUNNING, current_step="research_started")
        agent = await self._build_agent()
        # 保存 graph 引用供调度器唤醒时复用（业务等待挂起 → Command(resume) 续跑）
        self._agent = agent

        # 恢复模式（服务重启/崩溃自愈）：等待首个已决断批次后从 checkpoint 恢复
        resume_command = self.params.get("resume_command")
        query = self.query
        if self.params.get("resume_mode"):
            if resume_command is None:
                decisions = await self._wait_for_initial_batch()
                if decisions:
                    from langgraph.types import Command

                    resume_command = Command(resume=decisions)
                else:
                    # B5（P2-12/F3 ①成立）：无审批批次时禁止重入。空 HumanMessage
                    # 新输入会取代 checkpoint 的 pending interrupt（wait_for_subagent
                    # 挂起场景下子代理结果投递路径随之丢失），且悬空 tool_calls 的
                    # checkpoint 重入会触发模型 API 校验错误 → 降级假答案。
                    # 按安全终态处理（与链3 失败语义一致），不再伪造"自然继续"。
                    await self._handle_failure(
                        "研究任务在服务重启后无法安全恢复（中断点不在审批等待状态），"
                        "任务已终止，请重新发起研究"
                    )
                    return
            query = None  # 恢复模式不发送新 query（checkpoint 已有上下文）
            # 恢复基线注入：新建 adapter 内存态从空开始，挂起前已由
            # _persist_research_progress 落库的子代理工具条目/图层正文从 DB 恢复，
            # 避免恢复轮 position 注入为 0、最终 writeback 丢失挂起前子代理工具卡。
            await self._inject_research_resume_baseline()

        await self._execute_and_handle(resume_command, query)

    async def _execute_and_handle(self, resume_command, query) -> None:
        """执行一轮研究并处理结果（初始执行 / 调度器唤醒续跑共用）。

        结果语义：
        - 正常完成/失败 → _handle_result 落库广播；
        - 业务等待挂起（suspended）→ 注册父 awaiter、置挂起态、退出（不 finalize）。
        """
        agent = self._agent
        # 主 agent 工具集与运行配置显式透传（spawn_sub_agent 继承 / filesystem 落盘）。
        # 替代历史 get_parent_tool_context 全局旁路：经 research_runner 注入
        # configurable，子代理再经 langgraph_adapter 逐层传递。
        original_tools = getattr(agent, "original_tools", None)
        parent_tool_names = (
            [getattr(t, "name", "") for t in original_tools]
            if isinstance(original_tools, (list, tuple))
            else []
        )
        parent_config = {
            "use_web_search": bool(self.params.get("enable_web_search", True)),
            "use_mcp": self.params.get("use_mcp"),
            "user_id": self.user_id,
            "session_id": self.thread_id,
            # 模型选择双字段透传：缺 provider_id 时子代理 resolve_model 会回退
            # SystemConfig 默认模型（用户 UI 选择失效的经典根因）
            "provider_id": self.params.get("provider_id"),
            "model_name": self.params.get("model_name"),
            "store": self.params.get("store"),
            "enable_deep_thinking": bool(self.params.get("enable_deep_thinking", False)),
            # 任务级沙箱开关：写入 configurable 供 shell 工具路由（与 thread_id 同源）
            "enable_sandbox": bool(self.params.get("enable_sandbox", False)),
        }
        result = await execute_research_async(
            agent,
            query,
            self.thread_id,
            disable_llm_cache=True,
            user_id=self.user_id,
            chat_session_id=self.session_id,
            message_id=self.message_id,
            resume_command=resume_command,
            interrupt_handler=self._on_interrupt,
            parent_tool_names=parent_tool_names,
            parent_config=parent_config,
            enable_sandbox=bool(self.params.get("enable_sandbox", False)),
        )
        if result.suspended:
            await self._handle_suspend(result)
            return
        response_time = round(time.time() - self.start_time, 2)
        await self._handle_result(result, response_time)

    async def _persist_research_progress(
        self,
        subagent_contents: dict | None = None,
        tool_entries: dict | None = None,
        main_content: str | None = None,
    ) -> None:
        """挂起前落库研究内存态（子代理工具条目 + 图层正文 + 主代理累计正文）到 DB。

        与 chat 链路 ``persist_chat_tool_calls`` 同构：审批中断挂起前
        （``_on_interrupt``）与业务等待挂起前（``_handle_suspend``）调用，
        保证挂起期间刷新浏览器、服务重启恢复后可还原子代理工具卡、图层正文
        与主代理累计正文（adapter 内存态随协程/实例销毁而丢失，DB 是唯一可还原源）。
        """
        if not subagent_contents and not tool_entries and not main_content:
            return
        try:
            from Django_xm.apps.research.services.writeback import persist_research_progress

            await sync_to_async(persist_research_progress)(
                self.thread_id,
                subagent_contents=subagent_contents,
                tool_entries=tool_entries,
                main_content=main_content,
            )
        except Exception:
            logger.warning(
                f"[SessionExecutor] 挂起落库研究进度失败: thread_id={self.thread_id}",
                exc_info=True,
            )

    async def _inject_research_resume_baseline(self) -> None:
        """服务重启恢复：从 ResearchTask 读取挂起前落库的子代理状态注入 adapter。

        与 chat 链路 ``_inject_chat_resume_baseline`` 同构——业务等待/审批挂起
        期间已由 ``_persist_research_progress`` 落库到 ``ResearchTask.tool_calls`` /
        ``subagent_contents``，重启后新建 adapter 从空开始，据此重建
        ``subagent_contents`` / ``_tool_entries``（key=tool_call_id），
        保证恢复轮 position 注入（子代理图层正文长度依据）与最终 writeback
        （merge_source 聚合）不丢失挂起前的子代理工具条目。
        """
        agent = getattr(self, "_agent", None)
        if agent is None:
            return
        try:
            baseline = await sync_to_async(self._load_research_resume_baseline)(self.thread_id)
        except Exception:
            logger.exception(
                f"[SessionExecutor] 读取研究恢复基线失败: thread_id={self.thread_id}"
            )
            return
        subagent_contents = baseline.get("subagent_contents") or {}
        tool_entries = baseline.get("tool_entries") or {}
        main_content = baseline.get("content") or ""
        if main_content and hasattr(agent, "_main_content"):
            # 主代理累计正文恢复：position 注入依据（len(_main_content)）+ 最终写回权威源。
            # 服务重启/审批恢复后新建 adapter 从空开始，若不恢复，恢复轮 position 回退为 0，
            # 导致工具卡内联位置错乱（工具卡插入正文流头部而非原位置）。
            current = getattr(agent, "_main_content", "") or ""
            if len(main_content) > len(current):
                agent._main_content = main_content
                logger.info(
                    f"[SessionExecutor] 恢复基线注入 main_content: thread_id={self.thread_id}, "
                    f"len={len(main_content)}"
                )
        if subagent_contents and hasattr(agent, "subagent_contents"):
            merged = dict(getattr(agent, "subagent_contents", {}) or {})
            merged.update(subagent_contents)
            agent.subagent_contents = merged
            logger.info(
                f"[SessionExecutor] 恢复基线注入 subagent_contents: thread_id={self.thread_id}, "
                f"entries={len(merged)}"
            )
        if tool_entries and hasattr(agent, "_tool_entries"):
            merged = dict(getattr(agent, "_tool_entries", {}) or {})
            merged.update(tool_entries)
            agent._tool_entries = merged
            logger.info(
                f"[SessionExecutor] 恢复基线注入 tool_entries: thread_id={self.thread_id}, "
                f"entries={len(merged)}"
            )

    @staticmethod
    def _load_research_resume_baseline(thread_id: str) -> dict:
        """同步读取研究恢复基线（subagent_contents / 子代理工具条目）。

        子代理工具条目从 ``ResearchTask.tool_calls`` 中筛选 ``subagent_thread_id``
        非空的条目重建（key=tool_call_id），与 adapter ``_tool_entries``
        结构一致（含 subagent_thread_id/agent_name/depth/position/seq/status/result）。
        """
        from Django_xm.apps.research.models import ResearchTask

        task = ResearchTask.objects.filter(task_id=thread_id, is_deleted=False).first()
        if task is None:
            return {}

        tool_entries: dict[str, dict] = {}
        for tc in task.tool_calls or []:
            if not isinstance(tc, dict):
                continue
            if not tc.get("subagent_thread_id"):
                continue
            key = tc.get("id") or tc.get("name") or ""
            if key:
                tool_entries[key] = dict(tc)

        subagent_contents = task.subagent_contents or {}
        if not isinstance(subagent_contents, dict):
            subagent_contents = {}

        return {
            "subagent_contents": dict(subagent_contents),
            "tool_entries": tool_entries,
            "content": task.content or "",
        }

    # ------------------------------------------------------------------
    # 共用清理（C1/C2 级联设施 + 会话终态清理）
    # ------------------------------------------------------------------
    async def _terminate_running_subagents(self) -> None:
        """级联终止本会话的全部非终态子代理（C1/C2 共用清理设施）。

        先注销父 awaiter（防终止回调触发恢复竞态），再逐个终止 running /
        interrupted_pending_user_input 实例——覆盖 running 与 suspended
        （业务等待 fan-out，协程已退出但子代理仍在跑）两种场景。单实例
        终止失败不阻断其余实例，整体失败不阻断级联后续步骤（非致命）。
        """
        thread_id = self.thread_id
        try:
            from Django_xm.apps.ai_engine.models import SubAgentStatus
            from Django_xm.apps.ai_engine.subagent_runtime import get_subagent_runtime
            from Django_xm.apps.ai_engine.subagent_runtime.lifecycle import get_lifecycle_manager

            get_lifecycle_manager().unregister_parent_awaiter(thread_id)
            runtime = get_subagent_runtime()
            instances = await runtime.list_instances(thread_id)
            for inst in instances:
                if inst.status not in (
                    SubAgentStatus.RUNNING,
                    SubAgentStatus.INTERRUPTED_PENDING_USER_INPUT,
                ):
                    continue
                try:
                    await runtime.terminate(inst.thread_id)
                except Exception:
                    logger.warning(
                        f"[SessionExecutor] 级联终止子代理失败（非致命）: "
                        f"thread={inst.thread_id}, task={thread_id}"
                    )
        except Exception:
            logger.warning(
                f"[SessionExecutor] 级联子代理终止失败（非致命）: thread_id={thread_id}",
                exc_info=True,
            )

    async def _schedule_sandbox_cleanup(self) -> None:
        async def _delayed_cleanup():
            await asyncio.sleep(_SANDBOX_CLEANUP_DELAY)
            try:
                await sync_to_async(cleanup_research_sandbox)(self.thread_id)
            except Exception:
                logger.warning(f"[SessionExecutor] 清理 sandbox 失败: {self.thread_id}")

        task = asyncio.create_task(_delayed_cleanup(), name=f"sandbox-cleanup-{self.thread_id}")
        # 保存引用防止被 GC 回收；完成后自动从集合移除
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    @staticmethod
    async def _release_checkpointer() -> None:
        try:
            from Django_xm.apps.ai_engine.services.checkpointer_factory import release_async_checkpointer

            await release_async_checkpointer()
        except Exception:
            logger.debug("释放异步 Checkpointer 连接失败（可忽略）")

    # ------------------------------------------------------------------
    # chat 批次静态读取器（域归属 chat；因测试静态调用面
    # SessionExecutor._list_chat_batch_ids 锚定基类，不强行下移）
    # ------------------------------------------------------------------
    @staticmethod
    def _list_chat_batch_ids(session_id: str) -> list[str]:
        """列出 chat 会话未收尾审批批次（主代理批次，B4 恢复锚点）。

        排除子代理审批行（extra.subagent_thread_id 非空）：子代理审批经独立
        子代理运行时恢复路由（SessionManager._resume_subagent_from_signal），
        其父会话停在 wait_for_subagent 挂起（无 DB 任务记录不可扫描），以
        审批决策 resume wait_for_subagent interrupt 属非法 resume 值
        （B5/F3 同源风险）。
        含 PROCESSING/WAITING：决策已落库但执行器未消费（重启窗口）同样是
        F2 待恢复态。newest-first：批次乱序重复时保留最新（checkpoint 的
        pending interrupt 恒为最新批次），并过滤空批次 ID 行。
        """
        from Django_xm.apps.approvals.models import Approval

        rows = (
            Approval.objects.filter(
                source=Approval.Source.CHAT,
                source_id=session_id,
                state__in=[
                    Approval.State.PENDING,
                    Approval.State.PROCESSING,
                    Approval.State.WAITING,
                ],
            )
            .exclude(extra__subagent_thread_id__isnull=False)
            .order_by("-created_at")
            .values_list("extra__graph_interrupt_id", flat=True)
        )
        batch_ids: list[str] = []
        for gid in rows:
            if gid and gid not in batch_ids:
                batch_ids.append(gid)
        return batch_ids

    # ------------------------------------------------------------------
    # research Agent 物料装配（薄委托：装配主体已下移
    # apps/research/services/agent_assembly.py，审计 §3.2-1 / 批E E7。
    # 方法保留于基类 = 测试类级 patch 面锚定，详见 E7 交付报告）
    # ------------------------------------------------------------------
    async def _build_agent(self):
        """构建研究智能体（薄委托 build_research_agent，行为等价下移）。"""
        from Django_xm.apps.research.services.agent_assembly import build_research_agent

        return await build_research_agent(self.params, thread_id=self.thread_id, user_id=self.user_id)

    def _load_skill_dirs(self, selected_skill_names: list[str]) -> list[str]:
        """加载选中 Skill 包目录（薄委托 load_skill_dirs；test_skill_dirs_loading 调用面）。"""
        from Django_xm.apps.research.services.agent_assembly import load_skill_dirs

        return load_skill_dirs(selected_skill_names, user_id=self.user_id)


class ResearchSessionExecutor(SessionExecutor):
    """research 会话执行器（深度研究域：执行分派 / 取消终态化 / 审批中断 / 业务等待挂起）。"""

    # ------------------------------------------------------------------
    # 域钩子实现
    # ------------------------------------------------------------------
    async def _run_domain(self) -> None:
        await self._run_research()

    async def _handle_run_exception(self, exc: Exception) -> None:
        # research 失败终态走分类文案（A1.4）：原始异常仅进日志，
        # 用户面只见错误码 + 可行动中文文案（不透出裸 str(exc)）
        from Django_xm.apps.ai_engine.services.exceptions import build_user_facing_error

        error_code, user_message = build_user_facing_error(exc)
        await self._handle_failure(user_message, error_code=error_code)

    def _schedule_stop_finalization(self) -> None:
        """research 取消终态化（C1/P2-1）：调度取消级联后台任务。"""
        self._schedule_research_cancel_finalization()

    async def _finalize_suspended_failure(self) -> None:
        await self._handle_failure("研究任务挂起超时：等待的子代理长时间未返回，任务被系统终止")

    async def _resume_execution(self, resume_command) -> None:
        await self._execute_and_handle(resume_command, None)

    async def _handle_resume_failure(self, exc: Exception) -> None:
        from Django_xm.apps.ai_engine.services.exceptions import build_user_facing_error

        error_code, user_message = build_user_facing_error(exc)
        await self._handle_failure(user_message, error_code=error_code)

    # ------------------------------------------------------------------
    # 取消终态化（C1/P2-1）
    # ------------------------------------------------------------------
    def _schedule_research_cancel_finalization(self) -> None:
        """调度研究任务取消终态化（后台任务，stop() 同步入口内调用）。"""
        task = asyncio.create_task(
            self._finalize_research_cancelled(),
            name=f"research-cancel-{self.thread_id}",
        )
        # 保存引用防止被 GC 回收；完成后自动从集合移除
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _finalize_research_cancelled(self) -> None:
        """研究任务取消终态化（C1/P2-1）：终止协程 + 级联清理 + 状态落库广播。

        取消语义边界：研究取消=终态不可恢复（区别于 chat 优雅停止保留 checkpoint
        可恢复），checkpoint 随会话结束释放。

        级联清理项（与 chat 取消级联 C2 共用的设施以注释标注）：
        - 终止执行协程：astream 取消（CancelledError 在下一 await 点送达，部分
          成果以 _persist_research_progress 已落库内容为准，取消时再抢救内存增量）；
        - 挂起审批批次收尾：复用 B4 设施 abandon_session_pending_approvals
          （source=DEEP_RESEARCH，全部遗留 PENDING → TIMEOUT，不触发恢复信令）；
        - 子代理终止：subagent_runtime 生命周期（复用任务删除的回收路径）；
        - 沙箱清理：复用 _schedule_sandbox_cleanup（120s 延迟，避让子代理收尾）；
        - 状态落库 CANCELLED + _publish_status_change 广播（所有浏览器实时可见）。

        覆盖三种到达时机：running（astream 执行中）/ awaiting_approval（审批挂起，
        经 stop() 唤醒事件快速退出）/ suspended（业务等待挂起、协程已退出、子代理
        fan-out 仍在跑——cancel 落在 awaiter 注销前，子代理被逐个终止）。
        """
        thread_id = self.thread_id
        logger.info(f"[SessionExecutor] 研究任务取消终态化开始: thread_id={thread_id}")
        # 1. 终止执行协程（已退出/挂起态跳过），等待 finally 清理完成后继续级联
        run_task = self._run_task
        if run_task is not None and not run_task.done():
            run_task.cancel()
            await asyncio.gather(run_task, return_exceptions=True)
        # 2. 条件终态化（原子）：任务已终态（取消瞬间恰好完成/失败）则退让，跳过级联
        from Django_xm.apps.research.services.task_manager import cancel_task_if_active

        try:
            cancelled = await sync_to_async(cancel_task_if_active)(thread_id)
        except Exception:
            logger.exception(f"[SessionExecutor] 取消终态化写库失败: thread_id={thread_id}")
            return
        if not cancelled:
            logger.info(
                f"[SessionExecutor] 任务已终态，取消退让（不覆盖既有终态）: thread_id={thread_id}"
            )
            return
        # 清理挂起标记：防挂起 watchdog（B6）在取消后对同一会话再次触发超时
        # 终态化（FAILED 覆盖 CANCELLED）。与 _resume_after_subagent_wait 的
        # 标记清理同构（挂起态由此真正结束，不再等待唤醒）。
        self._suspended = False
        self._suspended_since = None
        # 3. 状态广播（跨浏览器实时同步为已取消；经既有 realtime 通道消费）
        await self._publish_status_change(
            ResearchTaskStatus.CANCELLED,
            current_step="cancelled",
            error="用户已取消该研究任务",
        )
        # 4. 部分成果保留：协程已终止，adapter 内存态不再变化，抢救距上次挂起
        #    落库的增量（merge_tool_calls_incremental 字段级演进，重复落库幂等）
        agent = getattr(self, "_agent", None)
        if agent is not None:
            await self._persist_research_progress(
                getattr(agent, "subagent_contents", None),
                getattr(agent, "_tool_entries", None),
                getattr(agent, "_main_content", "") or None,
            )
        # 4.5 chat 侧收尾（2026-10-06 缺陷修复）：取消即时同步聊天模块——回写关联
        #     ChatMessage 终止语义 + 广播 stream_completed(stopped=true) 到
        #     session+task 频道（对齐完成路径 _writeback_and_broadcast 的 chat 收尾；
        #     此前取消路径零 chat 广播 → 聊天侧"毫无反应"）。与 session_manager
        #     兜底路径共用 finalize_chat_message_cancelled 单源；失败不中断级联。
        try:
            from Django_xm.apps.research.services.writeback import finalize_chat_message_cancelled

            await sync_to_async(finalize_chat_message_cancelled)(thread_id)
        except Exception:
            logger.warning(
                f"[SessionExecutor] 取消 chat 收尾失败（非致命）: thread_id={thread_id}",
                exc_info=True,
            )
        # 5. 挂起审批批次收尾（B4 设施复用：source=DEEP_RESEARCH 传参即可）
        try:
            await sync_to_async(abandon_session_pending_approvals)(
                Approval.Source.DEEP_RESEARCH, thread_id
            )
        except Exception:
            logger.warning(
                f"[SessionExecutor] 取消收尾审批批次失败（非致命）: thread_id={thread_id}",
                exc_info=True,
            )
        # 6. 子代理级联终止（C1/C2 共用设施：注销父 awaiter 防恢复竞态 +
        #    逐个终止非终态实例，覆盖 running 与 suspended(fan-out) 两种场景）
        await self._terminate_running_subagents()
        # 7. 释放 checkpointer + 移除会话槽（幂等；挂起场景协程 finally 未清理，此处补齐）
        await self._release_checkpointer()
        self.manager.remove_session(thread_id)
        # 8. 沙箱清理（复用延迟清理：容器释放 + 临时目录删除，不触碰研究产出目录）
        await self._schedule_sandbox_cleanup()
        logger.info(f"[SessionExecutor] 研究任务取消终态化完成: thread_id={thread_id}")

    # ------------------------------------------------------------------
    # 业务等待挂起（spec D4，research 版）
    # ------------------------------------------------------------------
    async def _handle_suspend(self, result) -> None:
        """业务等待挂起（spec D4）：置挂起态 + 注册父 awaiter，退出协程（不 finalize）。

        挂起态固化规则（保留会话槽 / 不释放 checkpointer / 不触发 sandbox
        清理）见共享骨架 ``_begin_suspended_wait``。
        """
        self._begin_suspended_wait(result.interrupt_id, result.subagent_thread_ids)

        # 业务等待挂起前落库：adapter 内存态（子代理工具条目 + 图层正文）随协程
        # 退出而销毁，先落库到 DB，保证挂起期间刷新浏览器/服务重启恢复可还原。
        # getattr 兜底：部分执行路径（测试桩）可能无 subagent 字段。
        await self._persist_research_progress(
            getattr(result, "subagent_contents", None),
            getattr(result, "tool_entries", None),
            getattr(result, "main_content", "") or None,
        )

        await _update_task_status(
            self.thread_id, {"status": ResearchTaskStatus.RUNNING, "current_step": "waiting_subagent"}
        )
        await self._publish_status_change(ResearchTaskStatus.RUNNING, current_step="waiting_subagent")
        logger.info(
            f"[SessionExecutor] 业务等待挂起: thread_id={self.thread_id}, "
            f"subagents={self._wait_subagent_thread_ids}, interrupt_id={result.interrupt_id}"
        )
        await self._register_waiter()

    # ------------------------------------------------------------------
    # 审批中断：创建审批 + 挂起等待批次决策
    # ------------------------------------------------------------------
    async def _on_interrupt(self, interrupts_data) -> dict:
        """on_interrupt 回调：创建审批 + 挂起等待批次决策，返回 decisions。

        返回非空 decisions dict 时，adapter 自动以 Command(resume=decisions)
        重入 astream，单协程持续执行。
        """
        graph_interrupt_id = await create_approvals_for_interrupts(
            interrupts_data,
            source=Approval.Source.DEEP_RESEARCH,
            source_id=self.thread_id,
            user_id=self.user_id,
            chat_session_id=self.session_id,
            message_id=self.message_id,
            data=None,
        )
        if not graph_interrupt_id:
            # 审批创建失败（无批次可等待）：直接抛错终止任务，避免卡在"等待审批"假状态
            raise RuntimeError("审批创建失败：未生成审批批次，任务终止")
        logger.info(
            f"[SessionExecutor] 审批中断，挂起等待批次决策: "
            f"thread_id={self.thread_id}, graph_interrupt_id={graph_interrupt_id}"
        )
        # 审批中断挂起前落库：已完成工具结果 + 子代理工具条目/图层正文落库到 DB，
        # 保证挂起期间刷新浏览器、服务重启恢复时可还原（adapter 内存态非持久化源）。
        agent = getattr(self, "_agent", None)
        if agent is not None:
            await self._persist_research_progress(
                getattr(agent, "subagent_contents", None),
                getattr(agent, "_tool_entries", None),
                getattr(agent, "_main_content", "") or None,
            )
        # 状态标签实时同步：进入"等待审批"
        await _update_task_status(
            self.thread_id,
            {
                "status": ResearchTaskStatus.AWAITING_APPROVAL,
                "current_step": ResearchTaskStatus.AWAITING_APPROVAL,
            },
        )
        await self._publish_status_change(
            ResearchTaskStatus.AWAITING_APPROVAL, current_step=ResearchTaskStatus.AWAITING_APPROVAL
        )
        return await self._wait_for_batch_decision(graph_interrupt_id)

    async def _wait_for_batch_decision(self, graph_interrupt_id: str) -> dict:
        """挂起等待同批次全部审批决断，返回 Command(resume=...) 的 decisions。

        薄委托 approvals.services.approval_batch.await_wait_for_batch_decision
        （B⑦ 收敛：与 chat 链路共享唯一实现，含过期审批自愈），仅注入
        research 链路差异参数：source=deep_research / source_id=thread_id /
        轮询间隔 / 日志标签。
        """
        return await await_wait_for_batch_decision(
            self,
            source=Approval.Source.DEEP_RESEARCH,
            source_id=self.thread_id,
            graph_interrupt_id=graph_interrupt_id,
            poll_interval=_BATCH_POLL_INTERVAL,
            log_label="SessionExecutor",
            id_label="thread_id",
        )

    @staticmethod
    def _list_batch_ids(thread_id: str) -> list[str]:
        from Django_xm.apps.approvals.models import Approval

        return list(
            Approval.objects.filter(
                source=Approval.Source.DEEP_RESEARCH,
                source_id=thread_id,
            )
            .values_list("extra__graph_interrupt_id", flat=True)
            .distinct()
        )


class ChatSessionExecutor(SessionExecutor):
    """chat 会话执行器（chat agent 域：执行分派 / 优雅停止级联 / 恢复等待与基线注入）。"""

    # ------------------------------------------------------------------
    # 域钩子实现
    # ------------------------------------------------------------------
    async def _run_domain(self) -> None:
        await self._run_chat()

    async def _handle_run_exception(self, exc: Exception) -> None:
        # chat 的失败处理在 chat_executor_core 内部完成（广播 stream_completed），
        # run() 异常路径无需额外终态化
        return

    def _schedule_stop_finalization(self) -> None:
        """chat 停止资源级联清理调度（C2/P2-6）。"""
        self._schedule_chat_stop_cleanup()

    async def _finalize_suspended_failure(self) -> None:
        # chat 无 research task：广播 STREAM_COMPLETED(success=False) 终结
        # 前端等待态即可（与 _resume_after_subagent_wait 的 chat 异常分支同构）。
        # 注：event_schema 无 STREAM_FAILED 类型（审计 F7 澄清：行为正确，
        # 失败信号即 STREAM_COMPLETED + data.success=False，勿按注释名误寻）。
        try:
            from Django_xm.common.event_schema import EventSource, EventType
            from Django_xm.common.realtime_events import publish_event

            await publish_event(
                EventType.STREAM_COMPLETED,
                {
                    "source": EventSource.CHAT,
                    "source_id": self.thread_id,
                    "message_id": None,
                    "data": {"success": False, "error": "会话挂起超时，已被系统终止"},
                },
                session_id=self.thread_id,
            )
        except Exception:
            logger.exception(
                f"[SessionExecutor] chat 挂起超时广播异常: thread_id={self.thread_id}"
            )

    async def _resume_execution(self, resume_command) -> None:
        # chat 恢复：以 resume_command 作为 graph_input 重跑 chat 执行链
        # （run_chat_session → run_agent_session 检测 data["resume_command"]
        # 后跳过 pending interrupt 清理，从 checkpoint 续跑）。
        self.params["resume_command"] = resume_command
        # 业务等待挂起恢复（wait_for_subagent，spec D4）：挂起时父协程退出，
        # content_state / _tool_entries / _subagent_contents 随协程销毁。
        # 恢复前从 DB 读取挂起前已持久化的消息状态作为执行基线注入 params，
        # 使恢复在基线之上继续累积（避免总结正文覆盖历史段、position 注入为 0、
        # 子代理工具图层字段 subagent_thread_id 丢失）。
        await self._inject_chat_resume_baseline()
        await self._run_chat()

    async def _handle_resume_failure(self, exc: Exception) -> None:
        # chat 无 research task：广播 STREAM_COMPLETED(success=False) 终结
        # 前端等待态即可（_handle_failure 是 research 专属：task 状态 +
        # sandbox 清理）。event_schema 无 STREAM_FAILED 类型（审计 F7）。
        # A1.4：用户面给分类文案，原始异常仅进日志
        from Django_xm.apps.ai_engine.services.exceptions import build_user_facing_error

        error_code, user_message = build_user_facing_error(exc)
        try:
            from Django_xm.common.event_schema import EventSource, EventType
            from Django_xm.common.realtime_events import publish_event

            await publish_event(
                EventType.STREAM_COMPLETED,
                {
                    "source": EventSource.CHAT,
                    "source_id": self.thread_id,
                    "message_id": None,
                    "data": {
                        "success": False,
                        "error": user_message,
                        "error_code": error_code,
                    },
                },
                session_id=self.thread_id,
            )
        except Exception:
            logger.exception(f"[SessionExecutor] chat 恢复失败广播异常: thread_id={self.thread_id}")

    # ------------------------------------------------------------------
    # 停止级联清理（C2/P2-6）
    # ------------------------------------------------------------------
    def _schedule_chat_stop_cleanup(self) -> None:
        """调度 chat 停止资源级联清理（C2/P2-6，后台任务，stop() 同步入口内调用）。

        防重：chat 无任务表（research 侧由 cancel_task_if_active 条件终态化
        天然自护），重复 stop() 需显式防止重复调度级联任务。
        """
        if self._chat_stop_cleanup_scheduled:
            return
        self._chat_stop_cleanup_scheduled = True
        task = asyncio.create_task(
            self._finalize_chat_stop_cleanup(),
            name=f"chat-stop-cleanup-{self.thread_id}",
        )
        # 保存引用防止被 GC 回收；完成后自动从集合移除
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _finalize_chat_stop_cleanup(self) -> None:
        """chat 停止资源级联清理（C2/P2-6）：终止子代理 + 沙箱清理 + 挂起态补清理。

        语义红线（区别于 research 取消终态化）：chat 停止=优雅停止，对外语义
        不变——stopped=True 广播、审批批次收尾（非挂起路径 B4 已在
        chat_executor_core finally 覆盖）、checkpointer 释放与会话槽移除
        （run finally）均由执行协程退出路径完成。本方法不广播、不新增终态、
        不主动取消执行协程，仅补齐资源级联（审计 P2-6：stop 原先只终止父协程，
        不 terminate 运行中子代理，沙箱容器无主动清理）。

        覆盖两种到达时机：
        - running：等待执行协程优雅退出（安全点检查退出 / 宽限期兜底硬取消
          已在 stop() 调度），协程死后再终止子代理（终止快照不再增长）；
        - suspended（业务等待子代理、协程已退出）：协程 finally 因挂起被跳过，
          补齐审批批次收尾 + 清挂起标记（防挂起 watchdog 在用户停止后再次
          触发超时终态化广播）+ 释放 checkpointer + 移除会话槽。

        沙箱清理复用 _schedule_sandbox_cleanup（120s 延迟：容器释放 + 临时
        目录删除，不触碰 checkpoint 与消息数据——保留可重新生成/继续语义）。
        """
        thread_id = self.thread_id
        logger.info(f"[SessionExecutor] chat 停止级联清理开始: thread_id={thread_id}")
        # 1. 等待执行协程优雅退出（挂起态/未启动时立即返回）。宽限期兜底硬取消
        #    已在 stop() 经 loop.call_later 调度，此处超时上限取宽限期+缓冲；
        #    到点协程仍未退出则显式硬取消（与 _force_cancel_if_still_running
        #    同效），保证子代理终止快照在协程死后取得（不再有新 spawn），
        #    挂起标记亦为最终值（覆盖停止与挂起注册的窄竞态窗口）。
        run_task = self._run_task
        if run_task is not None and not run_task.done():
            _done, pending = await asyncio.wait(
                {run_task}, timeout=_STOP_GRACE_PERIOD + 5
            )
            if pending:
                logger.warning(
                    f"[SessionExecutor] chat 停止等待协程退出超时，兜底硬取消: "
                    f"thread_id={thread_id}"
                )
                run_task.cancel()
                await asyncio.gather(run_task, return_exceptions=True)
        # 2. 挂起态补清理（业务等待挂起停止：协程 finally 因挂起未执行）
        if self._suspended:
            self._suspended = False
            self._suspended_since = None
            # 挂起态停止遗留 PENDING 审批批次收尾（非挂起路径由
            # chat_executor_core finally 的 B4 收尾覆盖，此处不重复）
            try:
                await sync_to_async(abandon_session_pending_approvals)(
                    Approval.Source.CHAT, thread_id
                )
            except Exception:
                logger.warning(
                    f"[SessionExecutor] chat 停止收尾审批批次失败（非致命）: "
                    f"thread_id={thread_id}",
                    exc_info=True,
                )
            await self._release_checkpointer()
            self.manager.remove_session(thread_id)
        # 3. 子代理级联终止（C1/C2 共用设施：注销 awaiter + 终止非终态实例）
        await self._terminate_running_subagents()
        # 4. 沙箱清理（复用延迟清理入口）
        await self._schedule_sandbox_cleanup()
        logger.info(f"[SessionExecutor] chat 停止级联清理完成: thread_id={thread_id}")

    # ------------------------------------------------------------------
    # 业务等待挂起（spec D4，chat 版）
    # ------------------------------------------------------------------
    async def _handle_chat_wait_suspend(self, wait_info: dict) -> None:
        """chat 模式业务等待挂起（批量，与 research ``_handle_suspend`` 同构）。

        与 research 的 _handle_suspend 差异：chat 无 research task 状态更新
        （挂起期间会话保持流式等待态，由唤醒恢复后的最终结束统一收尾）。
        挂起态固化规则见共享骨架 ``_begin_suspended_wait``。
        """
        interrupt_id = wait_info.get("interrupt_id", "")
        subagent_thread_ids = wait_info.get("subagent_thread_ids", []) or []
        self._begin_suspended_wait(interrupt_id, subagent_thread_ids)
        logger.info(
            f"[SessionExecutor] chat 业务等待挂起: thread_id={self.thread_id}, "
            f"subagents={subagent_thread_ids}, interrupt_id={interrupt_id}"
        )
        await self._register_waiter()

    # ------------------------------------------------------------------
    # chat 执行链编排
    # ------------------------------------------------------------------
    async def _run_chat(self) -> None:
        """chat agent 执行核心（单协程，挂起 + 信令唤醒）。"""
        from Django_xm.services.fastapi_service.chat_executor_core import run_chat_session

        if self.params.get("resume_mode"):
            # B4（P2-11）：chat 审批挂起跨服务重启恢复。chat 无任务表，
            # SessionManager._recover_chat_approvals 以审批行为锚重建 executor，
            # 此处等待首个已决断批次后 Command(resume) 重入（与 _run_research
            # 恢复分支同构）。无批次=扫描后行间消失（竞态/已被自愈收尾），
            # 广播失败终态退出，前端消息不悬挂。
            decisions = await self._wait_for_chat_initial_batch()
            if decisions is None:
                # E10：恢复等待窗口内用户主动停止（优雅停止语义，C2）——广播
                # stopped 终态退出，不得落入下方空 dict 的失败终态广播（"恢复
                # 失败"文案与用户主动停止语义冲突）。广播形态对齐
                # chat_executor_core._finalize_chat_session 停止分支
                # （STREAM_COMPLETED + success=True + stopped=True；本路径未进入
                # run_chat_session，无 content_state/data 可落库，仅广播）。
                # 遗留 PENDING 审批批次收尾（TIMEOUT 语义，与 run_chat_session
                # finally 的 B4 配套同源）：会话已终态、批次无法再被消费，防止
                # _recover_chat_approvals 下次重启复活已停止会话。checkpointer
                # 释放与会话槽移除由 run() finally 承担（本路径 _suspended=False）。
                from Django_xm.common.event_schema import EventSource, EventType
                from Django_xm.common.realtime_events import publish_event

                try:
                    await publish_event(
                        EventType.STREAM_COMPLETED,
                        {
                            "source": EventSource.CHAT,
                            "source_id": self.thread_id,
                            "message_id": self.message_id or None,
                            "data": {"success": True, "stopped": True, "incomplete": False},
                        },
                        session_id=self.thread_id,
                    )
                except Exception:
                    logger.exception(
                        f"[SessionExecutor] chat 恢复等待停止广播异常: thread_id={self.thread_id}"
                    )
                try:
                    await sync_to_async(abandon_session_pending_approvals)(
                        Approval.Source.CHAT, self.thread_id
                    )
                except Exception:
                    logger.warning(
                        f"[SessionExecutor] chat 恢复等待停止收尾审批批次失败（非致命）: "
                        f"thread_id={self.thread_id}",
                        exc_info=True,
                    )
                return
            if not decisions:
                from Django_xm.common.event_schema import EventSource, EventType
                from Django_xm.common.realtime_events import publish_event

                try:
                    await publish_event(
                        EventType.STREAM_COMPLETED,
                        {
                            "source": EventSource.CHAT,
                            "source_id": self.thread_id,
                            "message_id": self.message_id or None,
                            "data": {
                                "success": False,
                                "error": "会话恢复失败：审批批次已收尾，请重新发送消息",
                                "error_code": "",
                            },
                        },
                        session_id=self.thread_id,
                    )
                except Exception:
                    logger.exception(
                        f"[SessionExecutor] chat 恢复中止广播异常: thread_id={self.thread_id}"
                    )
                return
            from langgraph.types import Command

            self.params["resume_command"] = Command(resume=decisions)
            # 恢复基线注入：与业务等待挂起恢复同构（挂起前已落库的 content/
            # 子代理工具条目从 DB 重建，恢复轮在基线之上累积）。
            await self._inject_chat_resume_baseline()
        await run_chat_session(self, self.params)

    async def _wait_for_chat_initial_batch(self) -> dict | None:
        """恢复模式（chat）：等待首个全部决断的审批批次（服务重启后会话在等审批）。

        与 research 版 ``_wait_for_initial_batch`` 同构，差异：
        - source=chat / source_id=session_id（chat 无任务表，审批行即恢复锚）；
        - 批次 newest-first（``_list_chat_batch_ids``）：checkpoint 的 pending
          interrupt 对应最新批次，旧遗留批次不得抢占 resume（旧批次经自愈
          TIMEOUT 后由 run_chat_session 终态收尾统一收编）；
        - 停止安全点（E10/C2 遗留）：循环顶部与 Event 等待前检查停止标志——
          恢复等待窗口内用户停止时提前退出，不再等审批过期自愈（最长 300s）
          才收敛。等待中已注册 Event 的场景由 stop() 批量 set 唤醒，下一轮
          循环顶部检查退出；等待前检查覆盖 stop() 与 Event 注册间的窄竞态窗口。

        Returns:
            decisions（{langgraph_resume_id: {tool_call_id: bool|TIMEOUT_DECISION}}）；
            无任何审批批次返回空 dict，由调用方按失败终态广播退出；
            等待窗口内收到停止请求返回 None，由调用方按 stopped 终态广播退出
            （E10：None 与空 dict 必须区分——空 dict 走失败终态广播，其文案
            与用户主动停止语义冲突）。
        """
        while True:
            if self.check_stop_requested():
                return None
            batch_ids = await sync_to_async(self._list_chat_batch_ids)(self.thread_id)
            if not batch_ids:
                return {}
            for gid in batch_ids:
                decisions, all_resolved = await sync_to_async(collect_batch_decisions)(
                    Approval.Source.CHAT, self.thread_id, gid
                )
                if all_resolved and decisions:
                    await sync_to_async(finalize_batch_approvals)(
                        decisions, Approval.Source.CHAT, self.thread_id
                    )
                    logger.info(
                        f"[SessionExecutor] chat 恢复首个已决断批次: thread_id={self.thread_id}, "
                        f"graph_interrupt_id={gid}"
                    )
                    return decisions
            for gid in batch_ids:
                await sync_to_async(self_heal_expired_approvals)(
                    Approval.Source.CHAT, self.thread_id, gid
                )
            # 停止安全点（E10）：Event 等待前复查——覆盖 DB 扫描/自愈期间到达的
            # 停止请求（此时尚未注册 Event，stop() 的批量 set 唤醒不到本协程）
            if self.check_stop_requested():
                return None
            # 所有批次均未决断：挂起等待审批信令（周期性轮询兜底）
            event = asyncio.Event()
            self._pending_events["__initial_chat__"] = event
            try:
                await asyncio.wait_for(event.wait(), timeout=_BATCH_POLL_INTERVAL)
            except TimeoutError:
                pass
            finally:
                self._pending_events.pop("__initial_chat__", None)

    async def _inject_chat_resume_baseline(self) -> None:
        """读取挂起前已持久化的消息状态，注入 params 作为 chat 恢复基线。

        业务等待挂起（wait_for_subagent）时父协程退出，content_state /
        _tool_entries / _subagent_contents 随协程销毁。恢复前从 DB
        读取挂起前已落库的 content / tool_calls / subagent_contents，注入
        params，使恢复轮在此基线之上继续累积（而非从空重建）。
        """
        try:
            baseline = await sync_to_async(self._load_chat_resume_baseline)(
                self.thread_id, self.message_id
            )
        except Exception:
            logger.exception(
                f"[SessionExecutor] 读取 chat 恢复基线失败: thread_id={self.thread_id}"
            )
            return
        self.params["resume_content"] = baseline.get("resume_content") or ""
        self.params["resume_subagent_contents"] = baseline.get("resume_subagent_contents") or {}
        self.params["resume_tool_entries"] = baseline.get("resume_tool_entries") or {}

    @staticmethod
    def _load_chat_resume_baseline(session_id: str, message_id: str) -> dict:
        """同步读取 chat 恢复基线（content / subagent_contents / 子代理工具条目）。

        子代理工具条目基线从 ``tool_calls`` 中筛选 ``subagent_thread_id`` 非空的
        条目重建（key=tool_call_id），与 chat 恢复轮的工具条目容器结构一致
        （含 subagent_thread_id/position/seq/status/result 等图层字段）。
        """
        from Django_xm.apps.chat.models import ChatMessage, ChatSession

        session = ChatSession.objects.filter(session_id=session_id).first()
        if session is None:
            return {}

        assistant_msg = None
        if message_id:
            try:
                assistant_msg = ChatMessage.objects.get(
                    id=int(message_id), session=session, role="assistant"
                )
            except (ChatMessage.DoesNotExist, ValueError, TypeError):
                assistant_msg = None
        if assistant_msg is None:
            assistant_msg = (
                ChatMessage.objects.filter(session=session, role="assistant")
                .order_by("-created_at")
                .first()
            )
        if assistant_msg is None:
            return {}

        tool_entries: dict[str, dict] = {}
        for tc in assistant_msg.tool_calls:
            if not isinstance(tc, dict):
                continue
            if not tc.get("subagent_thread_id"):
                continue
            key = tc.get("id") or tc.get("name") or ""
            if key:
                tool_entries[key] = dict(tc)

        subagent_contents = assistant_msg.subagent_contents
        if not isinstance(subagent_contents, dict):
            subagent_contents = {}

        return {
            "resume_content": assistant_msg.content or "",
            "resume_subagent_contents": dict(subagent_contents),
            "resume_tool_entries": tool_entries,
        }
