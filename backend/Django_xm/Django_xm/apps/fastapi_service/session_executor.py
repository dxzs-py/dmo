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

from Django_xm.apps.research.services.research_runner import (
    cleanup_research_sandbox,
    collect_batch_decisions,
    create_approvals_for_interrupts,
    execute_research_async,
    finalize_batch_approvals,
    finalize_research,
    publish_research_failure,
    self_heal_expired_approvals,
)

logger = logging.getLogger(__name__)

# 挂起等待时的批次轮询间隔（秒）：信令唤醒走 Event，轮询仅用于自愈/兜底
_BATCH_POLL_INTERVAL = 5
# sandbox 延迟清理（秒）
_SANDBOX_CLEANUP_DELAY = 120


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
        publish_to_redis: bool = False,
        params: dict[str, Any] | None = None,
        session_type: str = "research",
    ):
        self.manager = manager
        self.thread_id = thread_id
        self.query = query
        self.user_id = user_id
        self.session_id = session_id
        self.message_id = message_id
        self.publish_to_redis = publish_to_redis
        self.session_type = session_type
        # Agent 构建参数（初始执行时由 start 信令透传；恢复时由 task 组装）
        self.params = params or {}
        self.start_time = time.time()
        # 挂起等待表：graph_interrupt_id -> asyncio.Event
        self._pending_events: dict[str, asyncio.Event] = {}
        self._run_task: asyncio.Task | None = None
        # 子代理重试指令队列（Task 3 单独重启失败子代理）：
        # - 运行中会话：on_retry_subagent 入队，adapter astream 循环按 chunk 消费
        # - 恢复重试场景（会话已结束/服务重启）：params.retry_instruction 预注入队列
        self._retry_queue: asyncio.Queue = asyncio.Queue()
        # 后台清理任务引用（sandbox 延迟清理），防止被 GC 回收
        self._background_tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self) -> None:
        """启动执行协程（由 SessionManager 在事件循环内调用）。"""
        self._run_task = asyncio.create_task(self.run(), name=f"research-exec-{self.thread_id}")

    def stop(self) -> None:
        """终止执行协程（优雅停机/用户终止）。"""
        if self._run_task and not self._run_task.done():
            self._run_task.cancel()

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
            # 统一释放 checkpointer 连接 + 移除会话槽位（崩溃/取消/完成均不泄漏）
            await self._release_checkpointer()
            self.manager.remove_session(thread_id)

    async def _run_research(self) -> None:
        """深度研究执行核心（单协程，原 run 逻辑）。"""
        thread_id = self.thread_id
        await _update_task_status(thread_id, {"status": "running", "current_step": "research_started"})
        await self._publish_status_change("running", current_step="research_started")
        agent = await self._build_agent()

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

        result = await execute_research_async(
            agent,
            query,
            thread_id,
            disable_llm_cache=True,
            user_id=self.user_id,
            chat_session_id=self.session_id,
            message_id=self.message_id,
            resume_command=resume_command,
            interrupt_handler=self._on_interrupt,
        )
        response_time = round(time.time() - self.start_time, 2)
        await self._handle_result(result, response_time)

    async def _run_chat(self) -> None:
        """chat agent 执行核心（单协程，挂起 + 信令唤醒）。"""
        from Django_xm.apps.fastapi_service.chat_executor_core import run_chat_session

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
                decisions, all_resolved = await sync_to_async(collect_batch_decisions)(self.thread_id, gid)
                if all_resolved and decisions:
                    await sync_to_async(finalize_batch_approvals)(decisions, self.thread_id)
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
            thread_id=self.thread_id,
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
                resume_by_interrupt, all_resolved = await sync_to_async(collect_batch_decisions)(
                    self.thread_id, graph_interrupt_id
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
        await sync_to_async(finalize_batch_approvals)(resume_by_interrupt, self.thread_id)
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
                publish_to_redis=self.publish_to_redis,
            )
            await self._publish_status_change("completed", final_report=result.final_report)
            await self._writeback_and_broadcast(result.final_report, success=True, reasoning_content=result.reasoning)
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
        if self.publish_to_redis:
            publish_research_failure(thread_id, error_message)
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

    async def _writeback_and_broadcast(self, content: str, *, success: bool, reasoning_content: str = "") -> None:
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
            )
            await sync_to_async(broadcast_stream_completed)(
                self.session_id,
                self.thread_id,
                success=success,
                final_report=content if success else None,
                error=None if success else content,
                message_id=message_id,
                user_id=task.created_by_id,
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
        tools = []
        if retriever_tool:
            tools.append(retriever_tool)
        if extra_tools:
            tools.extend(extra_tools)

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
