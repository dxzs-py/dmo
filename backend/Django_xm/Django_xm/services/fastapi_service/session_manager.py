"""会话调度管理器（SessionManager）。

职责：
- 会话注册与生命周期管控（{thread_id: SessionExecutor}）
- Redis Pub/Sub 信令订阅与路由（start / approval / stop）
- 启动/周期扫描恢复未完成任务（running / awaiting_approval 恢复；
  pending 超时全新启动补偿——B5 start 信令自愈）
- 并发限流、优雅停机
"""

import asyncio
import json
import logging

from asgiref.sync import sync_to_async

from Django_xm.common.signal_bus import (
    SIGNAL_APPROVAL,
    SIGNAL_PREFIX,
    SIGNAL_RETRY_SUBAGENT,
    SIGNAL_START,
    SIGNAL_STOP,
    get_signal_redis_url,
)
from Django_xm.services.fastapi_service.session_executor import SessionExecutor

logger = logging.getLogger(__name__)

# 信令订阅模式（Redis PSUBSCRIBE）：只订阅已知信令 kind，
# 避免误收 agent:result:* 等非信令频道（研究结果/失败结果频道，供聊天订阅，
# 不属于跨进程信令，收到会导致 "未知信令类型: kind=result" 告警）
SIGNAL_PATTERNS = [
    f"{SIGNAL_PREFIX}:{kind}:*"
    for kind in (SIGNAL_START, SIGNAL_APPROVAL, SIGNAL_STOP, SIGNAL_RETRY_SUBAGENT)
]

# 默认最大并发会话数
DEFAULT_MAX_CONCURRENCY = 20

# 订阅断线重连退避参数（REL-03）：初始 1s 指数翻倍，上限 30s
SUBSCRIPTION_RECONNECT_INITIAL_DELAY = 1.0
SUBSCRIPTION_RECONNECT_MAX_DELAY = 30.0

# PENDING 超时补偿阈值（分钟，B5 start 信令自愈）。
# start 信令为 Redis Pub/Sub fire-and-forget（signal_bus.publish_signal 发布异常
# 仅记日志、订阅窗口丢失、满载丢弃、并发满被拒后无重发），丢失时任务永久 PENDING。
# PENDING 超过该阈值即视为信令丢失，补偿性全新启动。刻意小于 check_stale_tasks
# 的 60min——口径差异：此处动作是"补启动"而非"判失败"，start 信令飞行窗口为
# 毫秒级（任务创建时内联发布），且 start_session 幂等（_sessions 查重）保证
# 补偿与迟到信令不会双启动。
PENDING_START_TIMEOUT_MINUTES = 5

# 周期自愈扫描间隔（秒）：recover_unfinished 原仅服务启动/信令重连时触发，
# 订阅保持期间丢失的 start 信令无补偿入口，需周期性扫描兜底。
RECOVERY_SWEEP_INTERVAL_SECONDS = 60

# 挂起会话最大存活时长（秒，B6 挂起 watchdog）：业务等待挂起（等待子代理
# fan-in）保留会话槽 + checkpointer；子代理挂死/服务重启回调丢失时父会话
# 无限占槽（并发槽耗尽风险）。挂起超过该时长 → 超时终态化（任务失败态 +
# 广播 + 释放 checkpointer + 移除槽位 + 注销 awaiter）。
SUSPENDED_SESSION_MAX_LIFETIME_SECONDS = 7200


class SessionManager:
    """统一会话调度管理器（FastAPI 执行服务进程内单例，chat/research 共用）。"""

    def __init__(self, max_concurrency: int = DEFAULT_MAX_CONCURRENCY):
        self.max_concurrency = max_concurrency
        self._sessions: dict[str, SessionExecutor] = {}
        self._redis = None
        self._pubsub = None
        self._subscription_task: asyncio.Task | None = None
        # 周期自愈扫描任务（B5：运行期 start 信令丢失的 PENDING 补偿）
        self._recovery_sweep_task: asyncio.Task | None = None
        # 后台任务引用集合（信令触发的恢复协程），防止被 GC 回收
        self._background_tasks: set[asyncio.Task] = set()
        # 停机标记（REL-03）：置位后订阅循环不再重连，直接退出
        self._shutting_down: bool = False
        # 重连退避参数（实例属性便于测试注入更小值）
        self._reconnect_initial_delay = SUBSCRIPTION_RECONNECT_INITIAL_DELAY
        self._reconnect_max_delay = SUBSCRIPTION_RECONNECT_MAX_DELAY

    @property
    def active_session_count(self) -> int:
        """当前活跃会话数（只读）。

        供 `/health` 等外部观测端点使用，避免调用方直接访问 `_sessions`
        私有属性（跨模块访问私有成员在重构时会静默破坏）。
        """
        return len(self._sessions)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """启动：连接 Redis + 订阅信令 + 扫描恢复未完成任务。"""
        import redis.asyncio as redis_async

        self._shutting_down = False

        # 注册常驻主事件循环：主 loop 上的异步 checkpointer 连接池多会话共享，
        # 不随单个会话释放（避免一个会话结束关闭其他挂起会话的连接，见问题L）。
        from Django_xm.apps.ai_engine.services.checkpointer_factory import register_main_loop

        register_main_loop()

        self._redis = redis_async.Redis.from_url(get_signal_redis_url())
        self._pubsub = self._redis.pubsub()
        await self._pubsub.psubscribe(*SIGNAL_PATTERNS)
        self._subscription_task = asyncio.create_task(
            self._subscription_loop(), name="signal-subscription"
        )
        logger.info(f"[SessionManager] 信令订阅启动: patterns={SIGNAL_PATTERNS}")
        await self.recover_unfinished()
        self._recovery_sweep_task = asyncio.create_task(
            self._recovery_sweep_loop(), name="recovery-sweep"
        )

    async def shutdown(self) -> None:
        """优雅停机：终止订阅 + 停止全部执行协程（checkpoint 已持久化，重启自动恢复）。"""
        # 先置停机标记再取消任务：订阅循环在异常/退避路径检测到标记后退出，
        # 不再发起重连（REL-03）
        self._shutting_down = True
        if self._subscription_task and not self._subscription_task.done():
            self._subscription_task.cancel()
        if self._recovery_sweep_task and not self._recovery_sweep_task.done():
            self._recovery_sweep_task.cancel()
        for thread_id, executor in list(self._sessions.items()):
            executor.stop()
            logger.info(f"[SessionManager] 已停止会话: thread_id={thread_id}")
        self._sessions.clear()
        if self._pubsub:
            try:
                await self._pubsub.close()
            except Exception:
                logger.debug("关闭 PubSub 失败（可忽略）")
        if self._redis:
            try:
                await self._redis.aclose()
            except Exception:
                logger.debug("关闭 Redis 连接失败（可忽略）")
        logger.info("[SessionManager] 已优雅停机")

    # ------------------------------------------------------------------
    # 会话管理
    # ------------------------------------------------------------------
    async def start_session(self, payload: dict) -> None:
        """从 start 信令创建会话执行器并启动（幂等：已存在则忽略）。"""
        thread_id = payload.get("thread_id")
        if not thread_id:
            logger.warning("[SessionManager] start 信令缺少 thread_id")
            return
        if thread_id in self._sessions:
            logger.info(f"[SessionManager] 会话已存在，忽略重复启动: thread_id={thread_id}")
            return
        if len(self._sessions) >= self.max_concurrency:
            logger.warning(
                f"[SessionManager] 超过最大并发会话数({self.max_concurrency})，拒绝启动: thread_id={thread_id}"
            )
            return

        executor = SessionExecutor(
            self,
            thread_id=thread_id,
            query=payload.get("query", ""),
            user_id=payload.get("user_id"),
            session_id=payload.get("session_id"),
            message_id=payload.get("message_id", ""),
            params=payload,
            session_type=payload.get("session_type", "research"),
        )
        self._sessions[thread_id] = executor
        executor.start()
        logger.info(
            f"[SessionManager] 会话已启动: thread_id={thread_id}, "
            f"session_type={payload.get('session_type', 'research')}, "
            f"session_id={payload.get('session_id')}, active={len(self._sessions)}"
        )

    def stop_session(self, thread_id: str) -> None:
        """终止指定会话。"""
        executor = self._sessions.get(thread_id)
        if executor:
            executor.stop()
            logger.info(f"[SessionManager] 已终止会话: thread_id={thread_id}")

    def remove_session(self, thread_id: str) -> None:
        """执行协程结束时移除会话槽位（幂等，成功/失败/取消均调用）。"""
        removed = self._sessions.pop(thread_id, None)
        if removed:
            logger.info(
                f"[SessionManager] 会话已移除: thread_id={thread_id}, active={len(self._sessions)}"
            )

    def on_approval_signal(self, thread_id: str, payload: dict) -> None:
        """审批信令路由：子代理审批 → runtime.resume；主 agent 审批 → 唤醒挂起协程。

        子代理与主代理审批共用统一审批端点（/approvals/{interrupt_id}/resume/），
        仅信令 payload 携带 subagent_thread_id 区分恢复目标（对齐 Trae solo：
        审批是全局机制，与 agent 层级无关，主/子代理仅展示位置不同）。
        """
        subagent_thread_id = payload.get("subagent_thread_id") or ""
        if subagent_thread_id:
            task = asyncio.create_task(
                self._resume_subagent_from_signal(payload, subagent_thread_id),
                name=f"subagent-resume-signal-{subagent_thread_id}",
            )
            # 保存引用防止被 GC 回收；完成后自动从集合移除
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
            return

        executor = self._sessions.get(thread_id)
        if executor:
            executor.on_approval_signal(payload)
        else:
            # 会话未在内存（可能刚启动/信令先于执行器创建到达）：
            # 决策已落库，执行器挂起时轮询 DB 最终一致，无需补发。
            logger.info(
                f"[SessionManager] 审批信令到达但会话未激活（DB 最终一致兜底）: "
                f"thread_id={thread_id}"
            )

    async def _resume_subagent_from_signal(self, payload: dict, subagent_thread_id: str) -> None:
        """子代理审批信令：终态化批次审批 + 断点恢复子代理独立 thread。

        与主 agent 恢复同构（_wait_for_batch_decision 唤醒后 finalize + resume）：
        1. resume_value 已由 views._check_batch_and_route 聚合为批量决策
           {tool_call_id: bool}（子代理 middleware 批量 interrupt 契约）；
        2. 终态化批次审批（approved/rejected，按 session_type 走 research/chat
           版 finalize，middleware 恢复时据此幂等跳过）；
        3. runtime.resume 重建子代理 graph 并 Command(resume=decisions) 断点续跑，
           子代理完成后经生命周期回调唤醒父 graph（spec D4）。
        """
        from Django_xm.apps.ai_engine.subagent_runtime import get_subagent_runtime
        from Django_xm.common.signal_bus import (
            SESSION_TYPE_CHAT,
            SESSION_TYPE_RESEARCH,
        )

        resume_value = payload.get("resume_value")
        decisions = resume_value if isinstance(resume_value, dict) else {}
        parent_thread_id = payload.get("thread_id", "")
        session_type = payload.get("session_type", "")
        graph_interrupt_id = payload.get("graph_interrupt_id", "") or ""

        from Django_xm.apps.approvals.models import Approval
        from Django_xm.apps.approvals.services.approval_batch import (
            collect_batch_decisions,
            finalize_batch_approvals,
        )

        source = (
            Approval.Source.CHAT
            if session_type == SESSION_TYPE_CHAT
            else Approval.Source.DEEP_RESEARCH
        )

        # 非 dict 信令（审批超时 resume_value=TIMEOUT_DECISION="_timeout" 字符串）：
        # 信令只携带单条审批的超时标记，无法直接作为 Command(resume) 决策。
        # 与主代理审批等待（_wait_for_chat_batch_decision）一致，回退到 DB 批次
        # 决策收集：超时审批 state=TIMEOUT → {tool_call_id: TIMEOUT_DECISION}，
        # 全部决断后恢复子代理（Agent 收到超时 ToolMessage 调整策略继续，而非
        # 永远挂起导致 wait_for_subagent 卡死）。
        if not decisions and graph_interrupt_id:
            # 轮询等待批次全部决断（含并发超时场景）：子代理 interrupt 后协程
            # 退出，没有主 agent 挂起循环的 DB 轮询兜底；多个审批并发超时
            # 时信令可能先于同批次其它审批终态化到达（竞态），若未决断直接
            # 放弃则子代理永不恢复 → wait_for_subagent 卡死。故重试直至全部
            # 决断（与主 agent 挂起循环"轮询 DB 最终一致"语义对齐）。
            decisions = {}
            _collect_attempts = 0
            _max_collect_attempts = 30  # 30s 内等待批次终态化（自愈/超时处理均已触发）
            while _collect_attempts < _max_collect_attempts:
                _collected, _all_resolved = await sync_to_async(collect_batch_decisions)(
                    source, parent_thread_id, graph_interrupt_id
                )
                # collect_batch_decisions 返回嵌套 {langgraph_resume_id: {tool_call_id: decision}}，
                # 子代理 resume 期望扁平 {tool_call_id: decision}（与 _check_batch_and_route
                # 构建的 batch_resume_value 同构，runtime.resume → Command(resume={interrupt_id: decisions})）
                decisions = {}
                for _nested in _collected.values():
                    if isinstance(_nested, dict):
                        decisions.update(_nested)
                if _all_resolved and decisions:
                    break
                _collect_attempts += 1
                if _collect_attempts >= _max_collect_attempts:
                    logger.warning(
                        f"[SessionManager] 子代理审批批次等待超时仍未全部决断，放弃恢复: "
                        f"subagent={subagent_thread_id}, graph_interrupt_id={graph_interrupt_id}, "
                        f"collected={_collected}"
                    )
                    return
                await asyncio.sleep(1.0)

        if not decisions:
            logger.warning(
                f"[SessionManager] 子代理审批信令缺少批量决策，跳过恢复: "
                f"subagent={subagent_thread_id}, resume_value={resume_value!r}"
            )
            return

        try:
            await sync_to_async(finalize_batch_approvals)(decisions, source, parent_thread_id)
            await get_subagent_runtime().resume(subagent_thread_id, decisions)
            logger.info(
                f"[SessionManager] 子代理审批信令已恢复子代理: "
                f"subagent={subagent_thread_id}, parent={parent_thread_id}, "
                f"session_type={session_type or SESSION_TYPE_RESEARCH}, decisions={decisions}"
            )
        except Exception:
            logger.exception(
                f"[SessionManager] 子代理审批恢复失败: "
                f"subagent={subagent_thread_id}, parent={parent_thread_id}"
            )

    async def on_retry_subagent_signal(self, thread_id: str, payload: dict) -> None:
        """重试子代理信令路由：运行中会话直接注入；已结束/恢复态从 checkpoint 恢复后注入。

        运行中会话由 SessionExecutor.on_retry_subagent 入队（adapter astream
        循环按 chunk 消费并注入 graph state）；执行协程已结束或会话不在内存
        （任务失败后/服务重启）时，从 checkpoint 恢复会话并预置重试指令。
        """
        executor = self._sessions.get(thread_id)
        if executor is not None:
            if executor.on_retry_subagent(payload):
                logger.info(
                    f"[SessionManager] 已向运行中会话注入子代理重试指令: thread_id={thread_id}"
                )
                return
            logger.info(
                f"[SessionManager] 运行中会话无法注入（协程已结束），转入恢复路径: thread_id={thread_id}"
            )
        await self._start_retry_recovery(thread_id, payload)

    async def _start_retry_recovery(self, thread_id: str, payload: dict) -> None:
        """从 checkpoint 恢复会话并预置子代理重试指令（会话已结束/服务重启恢复态）。

        与 ``recover_unfinished`` 使用同一恢复启动参数组装逻辑：
        - 任务不存在 / 已完成时放弃恢复（completed 无法重试）；
        - 恢复会话以 resume_mode=True 启动，retry_instruction 由
          SessionExecutor 在首个 astream 前预注入重试队列。
        """
        from Django_xm.apps.research.models import ResearchTask, ResearchTaskStatus

        # 等待旧会话槽位释放（执行协程结束清理与重试信令到达的竞态窗口）
        for _ in range(10):
            if thread_id not in self._sessions:
                break
            await asyncio.sleep(0.05)
        if thread_id in self._sessions:
            logger.warning(
                f"[SessionManager] 会话槽位未释放，放弃重试恢复: thread_id={thread_id}"
            )
            return

        try:
            task = await sync_to_async(
                lambda: ResearchTask.objects.filter(task_id=thread_id, is_deleted=False).first()
            )()
        except Exception:
            logger.exception(f"[SessionManager] 查询任务失败，重试恢复中止: thread_id={thread_id}")
            return
        if task is None:
            logger.warning(f"[SessionManager] 任务不存在，重试恢复中止: thread_id={thread_id}")
            return
        if task.status == ResearchTaskStatus.COMPLETED:
            logger.warning(
                f"[SessionManager] 任务已完成，无法重试子代理: thread_id={thread_id}"
            )
            return

        start_payload = {
            "thread_id": task.task_id,
            "query": task.query,
            "user_id": task.created_by_id,
            "session_id": task.session_id,
            "message_id": "",
            "enable_web_search": task.enable_web_search,
            "enable_doc_analysis": task.enable_doc_analysis,
            "enable_sandbox": task.enable_sandbox,
            "knowledge_base_ids": task.knowledge_base_ids,
            "use_mcp": task.use_mcp,
            "selected_mcp_servers": task.selected_mcp_servers,
            "selected_tools": task.selected_tools,
            "model_name": task.model,
            "resume_mode": True,
            "retry_instruction": payload,
        }
        await self.start_session(start_payload)
        logger.info(
            f"[SessionManager] 已恢复会话并预置子代理重试指令: thread_id={thread_id}"
        )

    # ------------------------------------------------------------------
    # 信令订阅循环
    # ------------------------------------------------------------------
    async def _subscription_loop(self) -> None:
        """Redis 模式订阅循环：解析信令并路由（断线自动重连，REL-03）。

        Redis 抖动/重启导致 pubsub.listen() 抛 ConnectionError 时，循环不再
        退出（原实现 except 后函数返回 → 订阅永久失效，新任务无法启动），而是
        指数退避后重建 PubSub 连接并重新订阅原频道，同时补跑 recover_unfinished
        （重连窗口内丢失的 start 信令经 DB 扫描兜底恢复）。
        停机（shutdown 置位 _shutting_down）后不再重连，循环干净退出。
        """
        if self._pubsub is None:
            logger.error("[SessionManager] 信令订阅循环启动但 pubsub 未初始化（start 未完成）")
            return
        delay = self._reconnect_initial_delay
        while True:
            try:
                async for message in self._pubsub.listen():
                    if message.get("type") != "pmessage":
                        continue
                    channel = message.get("channel", b"")
                    if isinstance(channel, bytes):
                        channel = channel.decode("utf-8")
                    data = message.get("data", b"")
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")
                    await self._dispatch_signal(channel, data)
            except asyncio.CancelledError:
                raise
            except Exception:
                if self._shutting_down:
                    logger.info("[SessionManager] 停机中，信令订阅循环退出（不再重连）")
                    return
                logger.exception("[SessionManager] 信令订阅连接中断")
            if self._shutting_down:
                return
            # 指数退避等待重连（sleep 中被 cancel 属停机路径，直接向上传播）
            await asyncio.sleep(delay)
            if self._shutting_down:
                return
            delay = min(delay * 2, self._reconnect_max_delay)
            try:
                await self._reconnect_subscription()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[SessionManager] 信令订阅重连失败，继续退避重试")
                continue
            # 重连成功，重置退避
            delay = self._reconnect_initial_delay

    async def _reconnect_subscription(self) -> None:
        """重建 PubSub 连接并重新订阅信令频道 + 补跑未完成任务恢复（重连路径）。

        仅在新 PubSub psubscribe 成功后才替换 ``self._pubsub``（失败时保留旧
        对象，由外层循环继续退避重试）；旧 PubSub 关闭失败可忽略。
        """
        pubsub = self._redis.pubsub()
        await pubsub.psubscribe(*SIGNAL_PATTERNS)
        old_pubsub = self._pubsub
        self._pubsub = pubsub
        if old_pubsub is not None:
            try:
                await old_pubsub.close()
            except Exception:
                logger.debug("关闭旧 PubSub 失败（可忽略）")
        logger.info(f"[SessionManager] 信令订阅已重连: patterns={SIGNAL_PATTERNS}")
        # 重连窗口内的 start 信令可能丢失：补跑未完成任务扫描兜底
        # （幂等：已激活会话由 recover/start_session 跳过）
        await self.recover_unfinished()

    async def _dispatch_signal(self, channel: str, data: str) -> None:
        """解析频道（agent:{kind}:{thread_id}）并路由。"""
        prefix = f"{SIGNAL_PREFIX}:"
        if not channel.startswith(prefix):
            return
        rest = channel[len(prefix):]
        if ":" not in rest:
            return
        kind, thread_id = rest.split(":", 1)
        try:
            payload = json.loads(data) if data else {}
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"[SessionManager] 信令解析失败: channel={channel}")
            return

        logger.info(f"[SessionManager] 收到信令: kind={kind}, thread_id={thread_id}")
        if kind == SIGNAL_START:
            await self.start_session(payload)
        elif kind == SIGNAL_APPROVAL:
            self.on_approval_signal(thread_id, payload)
        elif kind == SIGNAL_STOP:
            self.stop_session(thread_id)
        elif kind == SIGNAL_RETRY_SUBAGENT:
            await self.on_retry_subagent_signal(thread_id, payload)
        else:
            logger.warning(f"[SessionManager] 未知信令类型: kind={kind}")

    # ------------------------------------------------------------------
    # 启动恢复
    # ------------------------------------------------------------------
    async def recover_unfinished(
        self, *, pending_timeout_minutes: int = PENDING_START_TIMEOUT_MINUTES
    ) -> None:
        """启动/周期扫描恢复未完成任务。

        - running / awaiting_approval：信令已成功启动过的会话（有 checkpoint），
          以 resume_mode=True 从 checkpoint 恢复（原语义不变）。
        - pending 超时补偿（B5 start 信令自愈）：start 信令为 fire-and-forget，
          丢失（发布异常被吞/订阅窗口/满载丢弃/并发满被拒）时任务永久 PENDING。
          超过 pending_timeout_minutes 仍为 PENDING 的任务以**全新启动**补偿：
          从未执行的任务无 checkpoint，不能走 resume_mode（executor 的恢复路径
          会等待初始决断批次且不发送 query）。start_session 幂等（_sessions 查重），
          补偿与迟到信令不会双启动。

        Args:
            pending_timeout_minutes: PENDING 视为信令丢失的阈值（分钟）。
        """
        from datetime import timedelta

        from django.utils import timezone

        try:
            from Django_xm.apps.research.models import ResearchTask, ResearchTaskStatus

            def _scan():
                active = list(
                    ResearchTask.objects.filter(
                        status__in=[
                            ResearchTaskStatus.RUNNING,
                            ResearchTaskStatus.AWAITING_APPROVAL,
                        ],
                        is_deleted=False,
                    )
                )
                pending_cutoff = timezone.now() - timedelta(minutes=pending_timeout_minutes)
                stale_pending = list(
                    ResearchTask.objects.filter(
                        status=ResearchTaskStatus.PENDING,
                        is_deleted=False,
                        created_at__lt=pending_cutoff,
                    )
                )
                return active, stale_pending

            active_tasks, stale_pending = await sync_to_async(_scan)()
        except Exception:
            logger.exception("[SessionManager] 扫描未完成任务失败")
            return

        recovered = 0
        seen: set[str] = set()
        for task, is_resume in [
            *((t, True) for t in active_tasks),
            *((t, False) for t in stale_pending),
        ]:
            if task.task_id in self._sessions or task.task_id in seen:
                continue
            seen.add(task.task_id)
            payload = {
                "thread_id": task.task_id,
                "query": task.query,
                "user_id": task.created_by_id,
                "session_id": task.session_id,
                "message_id": "",
                "enable_web_search": task.enable_web_search,
                "enable_doc_analysis": task.enable_doc_analysis,
                "enable_sandbox": task.enable_sandbox,
                "knowledge_base_ids": task.knowledge_base_ids,
                "use_mcp": task.use_mcp,
                "selected_mcp_servers": task.selected_mcp_servers,
                "selected_tools": task.selected_tools,
                "model_name": task.model,
            }
            if is_resume:
                payload["resume_mode"] = True
            await self.start_session(payload)
            recovered += 1
        if recovered:
            logger.info(f"[SessionManager] 已恢复 {recovered} 个未完成任务")

    async def _recovery_sweep_loop(self) -> None:
        """周期自愈扫描（B5）：运行期 start 信令丢失的 PENDING 任务补偿。

        recover_unfinished 原仅服务启动/信令重连时触发；订阅保持期间丢失的
        start 信令（发布异常被吞/满载丢弃/并发满被拒后无重发）此前无任何
        补偿入口，任务永久 PENDING。每 RECOVERY_SWEEP_INTERVAL_SECONDS 扫描一次。
        """
        while True:
            await asyncio.sleep(RECOVERY_SWEEP_INTERVAL_SECONDS)
            try:
                await self.recover_unfinished()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[SessionManager] 周期自愈扫描失败")
            self._enforce_suspended_watchdog()

    def _enforce_suspended_watchdog(self) -> None:
        """挂起会话 watchdog（B6）：业务等待挂起超过最大存活时长 → 超时终态化。

        挂起态（业务等待子代理 fan-in）保留会话槽 + checkpointer；子代理挂死/
        服务重启回调丢失时父会话无限占槽（并发槽耗尽风险）。周期扫描逐会话
        检查，超时终态化由 SessionExecutor 异步执行。
        """
        for executor in list(self._sessions.values()):
            try:
                if executor.enforce_suspended_timeout(SUSPENDED_SESSION_MAX_LIFETIME_SECONDS):
                    logger.warning(
                        f"[SessionManager] 挂起 watchdog 已触发终态化: thread_id={executor.thread_id}"
                    )
            except Exception:
                logger.exception(
                    f"[SessionManager] 挂起 watchdog 检查异常: "
                    f"thread_id={getattr(executor, 'thread_id', '?')}"
                )
