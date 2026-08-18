"""会话级单执行流（SessionExecutor）。

一个深度研究会话对应一个实例，单协程全生命周期运行 LangGraph：
- 审批中断时 ``asyncio.Event`` 原生挂起（不退出、不拆分恢复任务），
  收到 Redis 信令（审批决策）后校验批次完整性并注入决策唤醒恢复；
- 事件发射复用 ``realtime_sync.publish_event``（Django Channels 网关转发前端），
  执行服务对前端完全透明；
- 审批等决策以 DB 为唯一真相源，挂起时周期性扫描 DB 批次决策与过期审批自愈，
  信令丢失最终一致。

薄封装：复用 adapter.astream_research_with_interrupts 的 Command(resume=...) 语义，
on_interrupt 返回非空 decisions dict 时 adapter 自动重入执行。
"""

import asyncio
import logging
import time
from typing import Any

from asgiref.sync import sync_to_async

from Django_xm.apps.approvals.models import Approval
from Django_xm.apps.research.services.research_runner import (
    cleanup_research_sandbox,
    execute_research_async,
    finalize_research,
    self_heal_expired_approvals,
)
from Django_xm.common.approval_batch import (
    collect_batch_decisions,
    create_approvals_for_interrupts,
    finalize_batch_approvals,
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
    """深度研究会话执行器（单协程全生命周期）。"""

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
        # 子代理重试指令队列（Task 3 单独重启失败子代理）：
        # - 运行中会话：on_retry_subagent 入队，adapter astream 循环按 chunk 消费
        # - 恢复重试场景（会话已结束/服务重启）：params.retry_instruction 预注入队列
        self._retry_queue: asyncio.Queue = asyncio.Queue()
        # 后台清理任务引用（sandbox 延迟清理），防止被 GC 回收
        self._background_tasks: set[asyncio.Task] = set()
        # 业务等待挂起（spec D4）：父 Graph 等待子代理结果时退出协程（非审批 interrupt）。
        # 规则：挂起态「保留会话槽 + 不释放 checkpointer」，调度器唤醒后新建协程续跑；
        # 真正结束（成功/失败/取消）时才释放 checkpointer + 移除会话槽。
        self._agent = None
        self._suspended = False
        self._wait_interrupt_id = ""
        # 业务等待挂起（批量 fan-out/fan-in）：一次等待多个子代理，全部终态后恢复
        self._wait_subagent_thread_ids: list[str] = []
        self._pending_subagent_results: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self) -> None:
        """启动执行协程（由 SessionManager 在事件循环内调用）。"""
        self._run_task = asyncio.create_task(self.run(), name=f"research-exec-{self.thread_id}")

    def stop(self) -> None:
        """优雅停止执行协程（Task 9，替代原 cancel() 硬取消）。

        机制：
        1. 置位停止标志，执行循环在安全点（循环迭代 / 审批挂起）检查并退出；
        2. 唤醒所有审批挂起等待（asyncio.Event.set），使挂起协程立即退出等待；
        3. 宽限期（_STOP_GRACE_PERIOD）后若协程仍在运行，兜底硬取消，
           保证极端场景（LLM 长阻塞）也不会永久悬挂。

        checkpoint 由 LangGraph 在节点执行后自动持久化，已输出内容经
        finally 落库并广播，均不受优雅停止影响。
        """
        self._stop_requested = True
        for event in list(self._pending_events.values()):
            event.set()
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

    async def run(self) -> None:
        """单协程完整执行流程，持续运行至会话结束（chat / research 分派）。"""
        thread_id = self.thread_id
        try:
            if self.session_type == "chat":
                await self._run_chat()
            else:
                await self._run_research()
        except asyncio.CancelledError:
            logger.info(f"[SessionExecutor] 执行协程被终止: thread_id={thread_id}")
            raise
        except Exception as exc:
            logger.exception(f"[SessionExecutor] 执行异常: thread_id={thread_id}")
            if self.session_type != "chat":
                # chat 的失败处理在 chat_executor_core 内部完成（广播 stream_completed）
                await self._handle_failure(str(exc))
        finally:
            # 挂起态（业务等待子代理）：保留会话槽 + 不释放 checkpointer，
            # 由调度器唤醒后新建协程续跑并在此真正结束时清理（见 _resume_after_subagent_wait）。
            # 其余路径（成功/失败/取消/崩溃）统一释放 checkpointer + 移除会话槽。
            if not self._suspended:
                # 清理父工具上下文（仅本会话的 key，避免清空其他并发会话）
                try:
                    from Django_xm.apps.tools.langchain.agent_context import clear_parent_tool_context

                    clear_parent_tool_context(thread_id)
                except Exception:
                    logger.warning(
                        f"[SessionExecutor] 清理父工具上下文失败（非致命）: thread_id={thread_id}",
                        exc_info=True,
                    )
                await self._release_checkpointer()
                self.manager.remove_session(thread_id)

    async def _run_research(self) -> None:
        """深度研究执行核心（单协程，原 run 逻辑）。"""
        thread_id = self.thread_id
        await _update_task_status(thread_id, {"status": "running", "current_step": "research_started"})
        await self._publish_status_change("running", current_step="research_started")
        agent = await self._build_agent()
        # 保存 graph 引用供调度器唤醒时复用（业务等待挂起 → Command(resume) 续跑）
        self._agent = agent

        # 挂接子代理重试指令队列：adapter astream 循环按 chunk 消费并注入 graph state
        # （Task 3 单独重启失败子代理：运行中会话由 on_retry_subagent 入队；
        # 恢复重试场景由下方 params.retry_instruction 预注入）
        agent._retry_instruction_queue = self._retry_queue
        retry_instruction = self.params.get("retry_instruction")
        if retry_instruction:
            self._retry_queue.put_nowait(retry_instruction)
            logger.info(
                f"[SessionExecutor] 预注入子代理重试指令: thread_id={thread_id}, "
                f"tool_call_id={retry_instruction.get('tool_call_id')}"
            )

        # 恢复模式（服务重启/崩溃自愈）：等待首个已决断批次后从 checkpoint 恢复
        resume_command = self.params.get("resume_command")
        query = self.query
        if self.params.get("resume_mode"):
            if resume_command is None:
                decisions = await self._wait_for_initial_batch()
                if decisions:
                    from langgraph.types import Command

                    resume_command = Command(resume=decisions)
            query = None  # 恢复模式不发送新 query（checkpoint 已有上下文）
            # 恢复基线注入：新建 adapter 内存态从空开始，挂起前已由
            # _persist_research_progress 落库的子代理工具条目/图层正文从 DB 恢复，
            # 避免恢复轮 position 注入为 0、最终 writeback 丢失挂起前子代理工具卡。
            await self._inject_research_resume_baseline()

        await self._execute_and_handle(resume_command, query)

    async def _inject_research_resume_baseline(self) -> None:
        """服务重启恢复：从 ResearchTask 读取挂起前落库的子代理状态注入 adapter。

        与 chat 链路 ``_inject_chat_resume_baseline`` 同构——业务等待/审批挂起
        期间已由 ``_persist_research_progress`` 落库到 ``ResearchTask.tool_calls`` /
        ``subagent_contents``，重启后新建 adapter 从空开始，据此重建
        ``subagent_contents`` / ``_subagent_tool_entries``（key=tool_call_id），
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
        subagent_tool_entries = baseline.get("subagent_tool_entries") or {}
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
        if subagent_tool_entries and hasattr(agent, "_subagent_tool_entries"):
            merged = dict(getattr(agent, "_subagent_tool_entries", {}) or {})
            merged.update(subagent_tool_entries)
            agent._subagent_tool_entries = merged
            logger.info(
                f"[SessionExecutor] 恢复基线注入 subagent_tool_entries: thread_id={self.thread_id}, "
                f"entries={len(merged)}"
            )

    @staticmethod
    def _load_research_resume_baseline(thread_id: str) -> dict:
        """同步读取研究恢复基线（subagent_contents / 子代理工具条目）。

        子代理工具条目从 ``ResearchTask.tool_calls`` 中筛选 ``subagent_thread_id``
        非空的条目重建（key=tool_call_id），与 adapter ``_subagent_tool_entries``
        结构一致（含 subagent_thread_id/agent_name/depth/position/seq/status/result）。
        """
        from Django_xm.apps.research.models import ResearchTask

        task = ResearchTask.objects.filter(task_id=thread_id, is_deleted=False).first()
        if task is None:
            return {}

        subagent_tool_entries: dict[str, dict] = {}
        for tc in task.tool_calls or []:
            if not isinstance(tc, dict):
                continue
            if not tc.get("subagent_thread_id"):
                continue
            key = tc.get("id") or tc.get("name") or ""
            if key:
                subagent_tool_entries[key] = dict(tc)

        subagent_contents = task.subagent_contents or {}
        if not isinstance(subagent_contents, dict):
            subagent_contents = {}

        return {
            "subagent_contents": dict(subagent_contents),
            "subagent_tool_entries": subagent_tool_entries,
            "content": task.content or "",
        }

    async def _execute_and_handle(self, resume_command, query) -> None:
        """执行一轮研究并处理结果（初始执行 / 调度器唤醒续跑共用）。

        结果语义：
        - 正常完成/失败 → _handle_result 落库广播；
        - 业务等待挂起（suspended）→ 注册父 awaiter、置挂起态、退出（不 finalize）。
        """
        result = await execute_research_async(
            self._agent,
            query,
            self.thread_id,
            disable_llm_cache=True,
            user_id=self.user_id,
            chat_session_id=self.session_id,
            message_id=self.message_id,
            resume_command=resume_command,
            interrupt_handler=self._on_interrupt,
        )
        if result.suspended:
            await self._handle_suspend(result)
            return
        response_time = round(time.time() - self.start_time, 2)
        await self._handle_result(result, response_time)

    async def _persist_research_progress(
        self,
        subagent_contents: dict | None = None,
        subagent_tool_entries: dict | None = None,
        main_content: str | None = None,
    ) -> None:
        """挂起前落库研究内存态（子代理工具条目 + 图层正文 + 主代理累计正文）到 DB。

        与 chat 链路 ``_persist_chat_tool_calls`` 同构：审批中断挂起前
        （``_on_interrupt``）与业务等待挂起前（``_handle_suspend``）调用，
        保证挂起期间刷新浏览器、服务重启恢复后可还原子代理工具卡、图层正文
        与主代理累计正文（adapter 内存态随协程/实例销毁而丢失，DB 是唯一可还原源）。
        """
        if not subagent_contents and not subagent_tool_entries and not main_content:
            return
        try:
            from Django_xm.apps.research.services.writeback import persist_research_progress

            await sync_to_async(persist_research_progress)(
                self.thread_id,
                subagent_contents=subagent_contents,
                subagent_tool_entries=subagent_tool_entries,
                main_content=main_content,
            )
        except Exception:
            logger.warning(
                f"[SessionExecutor] 挂起落库研究进度失败: thread_id={self.thread_id}",
                exc_info=True,
            )

    async def _handle_suspend(self, result) -> None:
        """业务等待挂起（spec D4）：置挂起态 + 注册父 awaiter，退出协程（不 finalize）。

        固化规则：
        - 保留会话槽（不调用 manager.remove_session），保持 thread_id 幂等 + 信令可达；
        - 不释放 checkpointer（graph 复用，恢复时从 checkpoint 续跑）；
        - 不触发 sandbox 清理（研究未完成）。
        """
        self._suspended = True
        self._wait_interrupt_id = result.interrupt_id
        self._wait_subagent_thread_ids = list(result.subagent_thread_ids or [])
        # 新一轮等待开始：清空上一轮残留的子代理终态结果。
        # 否则 _register_waiter 竞态兜底（len(_pending_subagent_results) >=
        # len(_wait_subagent_thread_ids)）会被旧残留提前满足，误判"全部子代理
        # 已终态"并立即恢复父 Graph——导致 wait_for_subagent 未真正等待子代理
        # （如子代理审批中断未完成），任务提前 completed 而子代理仍在后台运行。
        self._pending_subagent_results = {}

        # 业务等待挂起前落库：adapter 内存态（子代理工具条目 + 图层正文）随协程
        # 退出而销毁，先落库到 DB，保证挂起期间刷新浏览器/服务重启恢复可还原。
        # getattr 兜底：部分执行路径（测试桩）可能无 subagent 字段。
        await self._persist_research_progress(
            getattr(result, "subagent_contents", None),
            getattr(result, "subagent_tool_entries", None),
            getattr(result, "main_content", "") or None,
        )

        await _update_task_status(
            self.thread_id, {"status": "running", "current_step": "waiting_subagent"}
        )
        await self._publish_status_change("running", current_step="waiting_subagent")
        logger.info(
            f"[SessionExecutor] 业务等待挂起: thread_id={self.thread_id}, "
            f"subagents={self._wait_subagent_thread_ids}, interrupt_id={result.interrupt_id}"
        )
        await self._register_waiter()

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

    async def _handle_chat_wait_suspend(self, wait_info: dict) -> None:
        """chat 模式业务等待挂起（批量，与 _handle_suspend 同构）。

        与 research 的 _handle_suspend 差异：chat 无 research task 状态更新
        （挂起期间会话保持流式等待态，由唤醒恢复后的最终结束统一收尾）。
        """
        interrupt_id = wait_info.get("interrupt_id", "")
        subagent_thread_ids = wait_info.get("subagent_thread_ids", []) or []
        self._suspended = True
        self._wait_interrupt_id = interrupt_id
        self._wait_subagent_thread_ids = list(subagent_thread_ids)
        # 与 _handle_suspend 一致：清空上一轮残留的子代理终态结果，防止
        # _register_waiter 竞态兜底被旧数据误判"全部已终态"提前恢复父 Graph。
        self._pending_subagent_results = {}
        logger.info(
            f"[SessionExecutor] chat 业务等待挂起: thread_id={self.thread_id}, "
            f"subagents={subagent_thread_ids}, interrupt_id={interrupt_id}"
        )
        await self._register_waiter()

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
        最终结束时释放 checkpointer + 移除会话槽 + 注销 awaiter。
        """
        thread_id = self.thread_id
        # 用户已停止：不续跑父 Graph，直接清理（释放 checkpointer + 移除会话槽）
        if self._stop_requested:
            logger.info(f"[SessionExecutor] 挂起态收到停止请求，放弃续跑: thread_id={thread_id}")
            self._suspended = False
            await self._release_checkpointer()
            self.manager.remove_session(thread_id)
            from Django_xm.apps.ai_engine.subagent_runtime.lifecycle import get_lifecycle_manager

            get_lifecycle_manager().unregister_parent_awaiter(thread_id)
            return

        try:
            self._suspended = False
            from langgraph.types import Command

            subagent_results = [
                {"subagent_thread_id": sid, "status": r["status"], "result": r["result"]}
                for sid, r in self._pending_subagent_results.items()
            ]
            resume_value = {"subagent_results": subagent_results}
            resume_command = Command(resume={self._wait_interrupt_id: resume_value})
            if self.session_type == "chat":
                # chat 恢复：以 resume_command 作为 graph_input 重跑 chat 执行链
                # （run_chat_session → run_agent_session 检测 data["resume_command"]
                # 后跳过 pending interrupt 清理，从 checkpoint 续跑）。
                self.params["resume_command"] = resume_command
                # 业务等待挂起恢复（wait_for_subagent，spec D4）：挂起时父协程退出，
                # content_state / _subagent_tool_entries / _subagent_contents 随协程销毁。
                # 恢复前从 DB 读取挂起前已持久化的消息状态作为执行基线注入 params，
                # 使恢复在基线之上继续累积（避免总结正文覆盖历史段、position 注入为 0、
                # 子代理工具图层字段 subagent_thread_id 丢失）。
                await self._inject_chat_resume_baseline()
                await self._run_chat()
            else:
                await self._execute_and_handle(resume_command, None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(f"[SessionExecutor] 恢复父 Graph 异常: thread_id={thread_id}")
            if self.session_type == "chat":
                # chat 无 research task：广播 stream_failed 终结前端等待态即可
                # （_handle_failure 是 research 专属：task 状态 + sandbox 清理）
                try:
                    from Django_xm.common.event_schema import EventSource, EventType
                    from Django_xm.common.realtime_events import publish_event

                    await publish_event(
                        EventType.STREAM_COMPLETED,
                        {
                            "source": EventSource.CHAT,
                            "source_id": thread_id,
                            "message_id": None,
                            "data": {"success": False, "error": str(exc)},
                        },
                        session_id=thread_id,
                    )
                except Exception:
                    logger.exception(f"[SessionExecutor] chat 恢复失败广播异常: thread_id={thread_id}")
            else:
                await self._handle_failure(str(exc))
        finally:
            if not self._suspended:
                # 真正结束（成功/失败/取消）：释放 checkpointer + 移除会话槽 + 注销 awaiter
                await self._release_checkpointer()
                self.manager.remove_session(thread_id)
                from Django_xm.apps.ai_engine.subagent_runtime.lifecycle import get_lifecycle_manager

                get_lifecycle_manager().unregister_parent_awaiter(thread_id)

    async def _read_subagent_result(self, subagent_thread_id: str) -> str:
        """读取子代理最终结果预览（result_preview）。"""
        from Django_xm.apps.ai_engine.subagent_runtime import get_subagent_runtime

        instance = await get_subagent_runtime().get_instance(subagent_thread_id)
        if instance is None:
            return ""
        return instance.result_preview or ""

    async def _inject_chat_resume_baseline(self) -> None:
        """读取挂起前已持久化的消息状态，注入 params 作为 chat 恢复基线。

        业务等待挂起（wait_for_subagent）时父协程退出，content_state /
        _subagent_tool_entries / _subagent_contents 随协程销毁。恢复前从 DB
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
        self.params["resume_subagent_tool_entries"] = baseline.get("resume_subagent_tool_entries") or {}

    @staticmethod
    def _load_chat_resume_baseline(session_id: str, message_id: str) -> dict:
        """同步读取 chat 恢复基线（content / subagent_contents / 子代理工具条目）。

        子代理工具条目基线从 ``tool_calls`` 中筛选 ``subagent_thread_id`` 非空的
        条目重建（key=tool_call_id），与 chat_service 恢复轮的 ``_subagent_tool_entries``
        结构一致（含 subagent_thread_id/position/seq/status/result 等图层字段）。
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

        subagent_tool_entries: dict[str, dict] = {}
        for tc in assistant_msg.tool_calls or []:
            if not isinstance(tc, dict):
                continue
            if not tc.get("subagent_thread_id"):
                continue
            key = tc.get("id") or tc.get("name") or ""
            if key:
                subagent_tool_entries[key] = dict(tc)

        subagent_contents = assistant_msg.subagent_contents or {}
        if not isinstance(subagent_contents, dict):
            subagent_contents = {}

        return {
            "resume_content": assistant_msg.content or "",
            "resume_subagent_contents": dict(subagent_contents),
            "resume_subagent_tool_entries": subagent_tool_entries,
        }

    async def _run_chat(self) -> None:
        """chat agent 执行核心（单协程，挂起 + 信令唤醒）。"""
        from Django_xm.services.fastapi_service.chat_executor_core import run_chat_session

        await run_chat_session(self, self.params)

    async def _wait_for_initial_batch(self) -> dict:
        """恢复模式：等待首个全部决断的审批批次（服务重启后任务在等审批）。

        返回该批次的 decisions（{langgraph_resume_id: {tool_call_id: bool}}）；
        若任务不存在任何审批批次，返回空 dict（agent 从 checkpoint 自然继续）。
        """
        while True:
            batch_ids = await sync_to_async(self._list_batch_ids)(self.thread_id)
            if not batch_ids:
                # 任务不存在任何审批批次：从 checkpoint 自然继续（重新触发 pending interrupt 会幂等重建）
                return {}
            for gid in batch_ids:
                decisions, all_resolved = await sync_to_async(collect_batch_decisions)(
                    Approval.SOURCE_DEEP_RESEARCH, self.thread_id, gid
                )
                if all_resolved and decisions:
                    await sync_to_async(finalize_batch_approvals)(
                        decisions, Approval.SOURCE_DEEP_RESEARCH, self.thread_id
                    )
                    logger.info(
                        f"[SessionExecutor] 恢复首个已决断批次: thread_id={self.thread_id}, "
                        f"graph_interrupt_id={gid}"
                    )
                    return decisions
            for gid in batch_ids:
                await sync_to_async(self_heal_expired_approvals)(self.thread_id, gid)
            # 所有批次均未决断：挂起等待审批信令（周期性轮询兜底）
            event = asyncio.Event()
            self._pending_events["__initial__"] = event
            try:
                await asyncio.wait_for(event.wait(), timeout=_BATCH_POLL_INTERVAL)
            except TimeoutError:
                pass
            finally:
                self._pending_events.pop("__initial__", None)

    @staticmethod
    def _list_batch_ids(thread_id: str) -> list[str]:
        from Django_xm.apps.approvals.models import Approval

        return list(
            Approval.objects.filter(
                source=Approval.SOURCE_DEEP_RESEARCH,
                source_id=thread_id,
            )
            .values_list("extra__graph_interrupt_id", flat=True)
            .distinct()
        )

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
            source=Approval.SOURCE_DEEP_RESEARCH,
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
                getattr(agent, "_subagent_tool_entries", None),
                getattr(agent, "_main_content", "") or None,
            )
        # 状态标签实时同步：进入"等待审批"
        await _update_task_status(self.thread_id, {"status": "awaiting_approval", "current_step": "awaiting_approval"})
        await self._publish_status_change("awaiting_approval", current_step="awaiting_approval")
        return await self._wait_for_batch_decision(graph_interrupt_id)

    async def _wait_for_batch_decision(self, graph_interrupt_id: str) -> dict:
        """挂起等待同批次全部审批决断，返回 Command(resume=...) 的 decisions。

        机制：
        - 优先检查 DB 批次完整性（可能已被前端提前全部确认）；
        - 否则挂起 ``asyncio.Event``，由审批信令唤醒；
        - 挂起期间周期性自愈过期审批（终态化 TIMEOUT），轮询 DB；
        - 信令唤醒/轮询超时后统一重新读取 DB 校验批次完整性，
          仅当同批次全部决断才退出恢复（避免部分决断即恢复导致重入）。
        """
        event = asyncio.Event()
        self._pending_events[graph_interrupt_id] = event
        try:
            while True:
                # 安全点（Task 9）：收到停止请求立即退出等待，由外层 finally 落库并广播
                if self._stop_requested:
                    raise asyncio.CancelledError("用户停止生成")
                resume_by_interrupt, all_resolved = await sync_to_async(collect_batch_decisions)(
                    Approval.SOURCE_DEEP_RESEARCH, self.thread_id, graph_interrupt_id
                )
                if all_resolved:
                    break
                # 批次自愈：过期 pending 终态化为 TIMEOUT
                await sync_to_async(self_heal_expired_approvals)(self.thread_id, graph_interrupt_id)
                # 等待审批信令（周期轮询兜底）。信令唤醒或轮询超时均重新读取 DB，
                # 只有全部决断才退出循环——保证 Command(resume=...) 携带完整批次决策。
                event.clear()
                try:
                    await asyncio.wait_for(event.wait(), timeout=_BATCH_POLL_INTERVAL)
                except TimeoutError:
                    pass  # 未收到信令：继续轮询校验
        finally:
            self._pending_events.pop(graph_interrupt_id, None)

        logger.info(
            f"[SessionExecutor] 批次全部决断，恢复执行: "
            f"thread_id={self.thread_id}, graph_interrupt_id={graph_interrupt_id}, "
            f"decisions={resume_by_interrupt}"
        )
        await sync_to_async(finalize_batch_approvals)(
            resume_by_interrupt, Approval.SOURCE_DEEP_RESEARCH, self.thread_id
        )
        return resume_by_interrupt

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

    def on_retry_subagent(self, payload: dict) -> bool:
        """向运行中会话注入子代理重试指令（Task 3 单独重启失败子代理）。

        指令经 ``_retry_queue`` 入队，adapter astream 循环按 chunk 消费后
        经 ``aupdate_state`` 注入 SystemMessage 到 graph state，主 agent
        以原始入参重新调用目标子代理，新执行事件归属同一 agentPath。

        Args:
            payload: 重试指令 dict（agent_path/tool_call_id/agent_name/original_args）

        Returns:
            bool：True 表示已注入队列；False 表示执行协程已结束/未运行，
            调用方（SessionManager）应转入从 checkpoint 恢复后注入的路径
        """
        if self._run_task is None or self._run_task.done():
            logger.warning(
                f"[SessionExecutor] 执行协程已结束，无法注入重试指令，转入恢复路径: "
                f"thread_id={self.thread_id}, tool_call_id={payload.get('tool_call_id')}"
            )
            return False
        self._retry_queue.put_nowait(payload)
        logger.info(
            f"[SessionExecutor] 已注入子代理重试指令: thread_id={self.thread_id}, "
            f"tool_call_id={payload.get('tool_call_id')}, "
            f"agent_path={payload.get('agent_path')}"
        )
        return True

    # ------------------------------------------------------------------
    # 结果处理
    # ------------------------------------------------------------------
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
                subagent_tool_entries=result.subagent_tool_entries,
                final_report=result.final_report,
            )
            await self._schedule_sandbox_cleanup()
            return

        await self._handle_failure(result.error_message)

    async def _handle_failure(self, error_message: str) -> None:
        thread_id = self.thread_id
        logger.warning(f"[SessionExecutor] 研究失败: thread_id={thread_id}, error={error_message}")
        try:
            await _update_task_status(thread_id, {"status": "failed", "error": error_message})
            await self._publish_status_change("failed", error=error_message)
        except Exception:
            logger.exception(f"[SessionExecutor] 更新任务失败状态异常: {thread_id}")
        await self._writeback_and_broadcast(error_message, success=False)
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
        subagent_tool_entries: dict | None = None,
        final_report: str = "",
    ) -> None:
        """回写 ChatMessage + 广播 stream_completed（聊天深度研究场景）。"""
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
                subagent_tool_entries=subagent_tool_entries,
            )
            await sync_to_async(broadcast_stream_completed)(
                self.session_id,
                self.thread_id,
                success=success,
                final_report=final_report if success else None,
                error=None if success else content,
                message_id=message_id,
                user_id=task.created_by_id,
                content=content if success else "",
            )
        except Exception as e:
            logger.warning(f"[SessionExecutor] 回写 ChatMessage 失败: {e}")

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
    # Agent 构建
    # ------------------------------------------------------------------
    async def _build_agent(self):
        """构建研究智能体（初始执行：从 start 信令参数构建）。

        缓存（model/embedding registry、SystemConfig）由 ai_engine AppConfig.ready()
        在进程启动时同步预热，此处不重复预热（异步上下文无法访问 ORM）。
        """
        params = self.params
        thread_id = self.thread_id

        from Django_xm.apps.agent_hub import AgentConfig, AgentType
        from Django_xm.apps.agent_hub import create as agent_hub_create

        # 1. 知识库检索工具
        retriever_tool = None
        if params.get("enable_doc_analysis") and params.get("knowledge_base_ids") and self.user_id:
            try:
                from Django_xm.apps.knowledge.services.multi_kb_retriever import build_retriever_tool_for_research

                retriever_tool = await sync_to_async(build_retriever_tool_for_research)(
                    knowledge_base_ids=params.get("knowledge_base_ids"),
                    user_id=self.user_id,
                )
            except Exception:
                logger.exception("[SessionExecutor] 构建知识库检索工具异常")

        # 2. 额外工具（MCP / 自定义工具）
        extra_tools = []
        if params.get("use_mcp") or params.get("selected_tools"):
            try:
                from Django_xm.apps.ai_engine.capabilities import registry

                capabilities = registry.get_default_capabilities("deep_research")
                if "tool_injection" in capabilities:
                    tool_config = {
                        "use_tools": True,
                        "use_web_search": False,
                        "use_mcp": params.get("use_mcp"),
                        "selected_tools": params.get("selected_tools"),
                        "selected_mcp_servers": params.get("selected_mcp_servers"),
                        "user_id": self.user_id,
                        "tool_tier": "extended",
                    }
                    extra_tools = await registry.build_tools_for_agent_async(
                        "deep_research", capabilities, tool_config=tool_config
                    )
                else:
                    from Django_xm.apps.tools import get_tools_for_request_async

                    extra_tools = await get_tools_for_request_async(
                        use_tools=True,
                        use_web_search=False,
                        use_mcp=params.get("use_mcp"),
                        selected_mcp_servers=params.get("selected_mcp_servers"),
                        selected_tools=params.get("selected_tools"),
                        user_id=self.user_id,
                        tool_tier="extended",
                    )
                if extra_tools:
                    logger.info(f"[SessionExecutor] 加载 {len(extra_tools)} 个额外工具")
            except Exception as e:
                logger.warning(f"[SessionExecutor] 加载额外工具失败: {e}")

        # 3. Skills
        skills = None
        selected_skill_names = [
            name.replace("skill_", "", 1)
            for name in (params.get("selected_tools") or [])
            if name.startswith("skill_")
        ]
        if selected_skill_names:
            try:
                from Django_xm.apps.tools.skills.adapter import SkillAdapter

                adapter = SkillAdapter(user_id=self.user_id)
                skill_dirs = adapter.to_deep_agent_skills(selected_skill_names=selected_skill_names)
                if skill_dirs:
                    skills = skill_dirs
            except Exception as e:
                logger.warning(f"[SessionExecutor] 加载 Skill 目录失败: {e}")

        # 4. 续研上下文（continue_task_id）
        research_context = ""
        continue_task_id = params.get("continue_task_id")
        if continue_task_id:
            from Django_xm.apps.research.services.research_runner import load_research_context

            ctx = await sync_to_async(load_research_context)(continue_task_id)
            if ctx:
                research_context += (
                    "## 先前研究成果摘要\n\n"
                    "以下是之前研究的成果，请在此基础上继续深入研究。\n"
                    "**要求**：\n"
                    "1. 不要重复已有内容，聚焦于未覆盖的方面\n"
                    "2. 对已有结论进行补充证据和深入分析\n"
                    "3. 将新的研究发现写入新的文件（不要覆盖已有文件）\n"
                    "4. 在最终报告中整合新旧研究成果\n"
                    "5. 父任务的原始文件已放在 /sandbox/inherited/ 目录下，可用 read_file 读取参考\n\n"
                    f"{ctx}"
                )
            await self._inherit_parent_files(continue_task_id, thread_id)

        system_prompt = None
        if research_context:
            from Django_xm.apps.agent_hub.builders.deep_builder import DEEP_RESEARCH_SYSTEM_PROMPT

            system_prompt = f"{DEEP_RESEARCH_SYSTEM_PROMPT}\n\n---\n\n{research_context}"

        # 5. 组装工具列表与 AgentConfig
        # 三分类组合加载（与 chat 代理模式 ToolService.get_tools 对齐）：
        #   系统自带（standard 层，visibility=core）+ 按开关（网络搜索，
        #   visibility=switch）+ 用户选择（selected_tools，visibility=selectable，
        #   由 extra_tools 加载）+ 文档检索（retriever）。
        # 三类 visibility 天然不重合；config.tools 必须传「完整组合」，否则
        # resolve_tools 的显式工具集分支（config.tools 非空即直接返回）会丢失
        # 系统自带工具——用户选择了任一工具后主 agent 只剩用户选的 + spawn/wait。
        from Django_xm.apps.tools import get_tools_for_request_async as _get_tools_for_request

        tools = await _get_tools_for_request(
            use_tools=True,
            use_web_search=params.get("enable_web_search", True),
            use_mcp=False,  # MCP 工具由 extra_tools（用户选择类）单独加载
            user_id=self.user_id,
            tool_tier="standard",
        )
        _names = {getattr(t, "name", "") for t in tools}
        if retriever_tool and getattr(retriever_tool, "name", "") not in _names:
            tools.append(retriever_tool)
            _names.add(getattr(retriever_tool, "name", ""))
        if extra_tools:
            for _t in extra_tools:
                _n = getattr(_t, "name", "")
                if _n and _n not in _names:
                    tools.append(_t)
                    _names.add(_n)

        tool_config = {
            "use_tools": True,
            "use_web_search": params.get("enable_web_search", True),
            "use_doc_analysis": params.get("enable_doc_analysis", False),
            "user_id": self.user_id,
        }
        config = AgentConfig(
            agent_type=AgentType.DEEP_RESEARCH,
            provider_id=params.get("provider_id"),
            model_name=params.get("model_name"),
            temperature=params.get("temperature"),
            max_tokens=params.get("max_tokens"),
            special_params=params.get("special_params"),
            enable_deep_thinking=params.get("enable_deep_thinking", False),
            tools=tools if tools else None,
            tool_config=tool_config,
            system_prompt=system_prompt,
            user_id=self.user_id,
            session_id=thread_id,
            skills=skills,
            debug=False,
        )

        # 6. 注入异步 Checkpointer
        from Django_xm.apps.ai_engine.services.checkpointer_factory import get_async_checkpointer

        async_cp = await get_async_checkpointer()
        if async_cp is not None:
            config.checkpointer = async_cp
            logger.info("[SessionExecutor] 使用异步 Checkpointer")

        agent = await agent_hub_create(config)

        # 设置父工具上下文：spawn_sub_agent 读取此处继承主 agent 工具集与深度思考参数
        # （深度研究此前未设置 → 子代理仅得基础工具；此处与 chat_service 对齐。
        # 按 thread_id 隔离存储，多任务并发互不清空。）
        try:
            from Django_xm.apps.tools.langchain.agent_context import set_parent_tool_context

            main_tools = getattr(agent, "original_tools", None) or (tools if tools else None) or []
            set_parent_tool_context(
                thread_id,
                main_tools,
                {
                    "use_web_search": params.get("enable_web_search", True),
                    "use_mcp": params.get("use_mcp"),
                    "user_id": self.user_id,
                    "session_id": thread_id,
                    "model_name": params.get("model_name"),
                    "store": params.get("store"),
                    "enable_deep_thinking": bool(params.get("enable_deep_thinking", False)),
                },
            )
        except Exception as e:
            logger.warning(f"[SessionExecutor] 设置父工具上下文失败: {e}")

        return agent

    @staticmethod
    async def _inherit_parent_files(continue_task_id: str, thread_id: str) -> None:
        """续研模式：继承父任务文件到 sandbox 目录（供 agent 读取）。"""
        import os
        import shutil

        try:
            from django.conf import settings as django_settings

            data_dir = str(
                getattr(django_settings, "DATA_DIR", None)
                or os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data"
                )
            )
            parent_work_dir = os.path.join(data_dir, "research", continue_task_id)
            child_work_dir = os.path.join(data_dir, "research", thread_id)
            sandbox_dir = os.path.join(child_work_dir, "sandbox")
            os.makedirs(sandbox_dir, exist_ok=True)
            if os.path.isdir(parent_work_dir):
                inherited_dir = os.path.join(sandbox_dir, "inherited")
                os.makedirs(inherited_dir, exist_ok=True)
                for subdir in ("notes", "plans", "reports"):
                    src = os.path.join(parent_work_dir, subdir)
                    if os.path.isdir(src):
                        dst = os.path.join(inherited_dir, subdir)
                        os.makedirs(dst, exist_ok=True)
                        for fname in os.listdir(src):
                            src_file = os.path.join(src, fname)
                            dst_file = os.path.join(dst, fname)
                            if os.path.isfile(src_file) and not os.path.exists(dst_file):
                                shutil.copy2(src_file, dst_file)
                logger.info(
                    f"[SessionExecutor] 已继承父任务文件到 sandbox: "
                    f"{parent_work_dir} -> {inherited_dir}"
                )
        except Exception as e:
            logger.warning(f"[SessionExecutor] 继承父任务文件失败: {e}")
