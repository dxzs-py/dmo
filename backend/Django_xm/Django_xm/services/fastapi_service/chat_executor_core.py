"""chat agent 单协程执行核心（挂起 + Redis 信令唤醒 + Command(resume) 重入）。

将原 chat SSE 生成器（``_process_normal_stream_chat`` / ``run_stream_loop``）的
执行语义迁移到 FastAPI 执行服务的单协程模型中，与 deep_research 物理同构：

- 外层 while 循环驱动 ``agent.graph.astream``；
- updates 模式检测 ``__interrupt__`` → 创建审批（``request_approval_async``）+
  广播 approval 事件 → ``asyncio.Event`` 挂起等待信令；
- 信令唤醒后从 DB 收集批次决策（source=chat），``Command(resume=...)`` 重入 astream；
- 执行全程经 ``publish_stream_event`` 广播 WS（不依赖任何 HTTP 连接）；
- 完成后 finalize + ``persist_stream_result`` 落库 + 广播 stream_completed。

审批终态化在挂起等待后立即完成（按 DB 真实决策），与执行是否成功无关，
彻底消除"连接断开误判 rejected / 中断执行"的历史缺陷。
"""

import asyncio
import logging

from asgiref.sync import sync_to_async

from Django_xm.apps.chat.services.stream_broadcast import publish_stream_event
from Django_xm.apps.chat.services.stream_persistence import persist_chat_tool_calls

logger = logging.getLogger(__name__)

# 挂起等待时的批次轮询间隔（秒）：信令唤醒走 Event，轮询仅用于自愈/兜底
_CHAT_BATCH_POLL_INTERVAL = 5


# 审批批次决策/终态化/自愈/挂起等待已收敛到 approvals.services.approval_batch
# （B⑦：source 参数区分 chat/research），由 _wait_for_chat_batch_decision 薄委托。


async def _wait_for_chat_batch_decision(
    executor,
    session_id: str,
    graph_interrupt_id: str,
) -> dict:
    """挂起等待 chat 同批次全部审批决断，返回 Command(resume=...) 的 decisions。

    薄委托 approvals.services.approval_batch.await_wait_for_batch_decision
    （B⑦ 收敛：与 SessionExecutor._wait_for_batch_decision 共享唯一实现），
    仅注入 chat 链路差异参数：source=chat / source_id=session_id / 轮询间隔 /
    日志标签。chat 链路已补齐过期审批自愈语义（与 research 版一致）：
    - 优先检查 DB 批次完整性（可能已被前端提前全部确认）；
    - 否则挂起 asyncio.Event，由审批信令（on_approval_signal）唤醒；
    - 挂起期间周期性自愈过期审批（终态化 TIMEOUT）并轮询 DB（兜底）；
    - 仅当同批次全部决断才退出（避免部分决断即恢复导致重入）。
    """
    from Django_xm.apps.approvals.models import Approval
    from Django_xm.apps.approvals.services.approval_batch import await_wait_for_batch_decision

    return await await_wait_for_batch_decision(
        executor,
        source=Approval.Source.CHAT,
        source_id=session_id,
        graph_interrupt_id=graph_interrupt_id,
        poll_interval=_CHAT_BATCH_POLL_INTERVAL,
        log_label="ChatExec",
        id_label="session",
    )


async def run_chat_session(executor, params: dict) -> None:
    """chat agent 单协程执行（挂起 + resume 重入），由 SessionExecutor 调用。

    Args:
        executor: SessionExecutor 实例（提供 _pending_events / on_approval_signal）
        params: start 信令 payload（thread_id=session_id，含 message/mode/模型工具配置等）
    """
    from Django_xm.apps.chat.services.chat_service import ChatService

    session_id = params.get("thread_id", "")
    message_id = params.get("message_id", "")
    user_id = params.get("user_id")
    if not session_id:
        logger.error("[ChatExec] chat 会话缺少 thread_id，无法执行")
        return

    data = dict(params)
    data["session_id"] = session_id
    data["_assistant_message_id"] = str(message_id) if message_id else ""

    chat_service = ChatService(user_id=user_id, thread_id=session_id)

    # 业务等待恢复模式（wait_for_subagent 挂起后子代理终态唤醒）：以挂起前已持久化的
    # content 作为累积基线，恢复轮在此之上继续累积（position 注入与最终落库依赖此基线，
    # 避免 content 从空重建导致历史段被覆盖 / position 注入为 0）。
    _resume_content = params.get("resume_content") or ""
    content_state: dict = {"content": _resume_content, "last_broadcast": 0.0}
    # reasoning 节流状态容器（D2/P2-8）：时间窗合流器由 publish_stream_event
    # 惰性创建挂在本 dict 的 "throttle" 键下；与 content_state 同生命周期
    # （单次执行协程一份，业务等待恢复轮新建协程时重置窗口）。
    reasoning_state: dict = {}
    # 引用透传给 chat_service 子代理回调：子代理工具/正文事件聚合时实时落库
    # （挂起落库发生在子代理执行前，若仅靠挂起/结束落库，子代理正文与图层字段
    # 中途丢失，恢复轮从 DB 读不到基线 → 刷新后子代理卡归集失败/正文错位）。
    data["_content_state_ref"] = content_state

    async def _broadcast(event: dict) -> None:
        """将执行事件广播到 WS（触发/非触发浏览器统一消费）。

        subagent_contents：子代理图层正文累计（Agent 图层嵌套规范 Task 1），
        由 chat_service 在执行期注入 data["_subagent_contents"]，供 tool 事件
        position 采集（子代理图层正文长度依据）。
        """
        try:
            await publish_stream_event(
                event,
                session_id,
                message_id=int(message_id) if message_id else None,
                content_state=content_state,
                subagent_contents=data.get("_subagent_contents") or None,
                reasoning_state=reasoning_state,
            )
        except Exception as e:
            logger.warning(f"[ChatExec] 广播事件失败: {e}")

    async def _interrupt_handler(graph_interrupt_id: str, langgraph_resume_id: str) -> dict:
        """审批中断挂起：先落库已执行工具结果，再等待批次决策。

        修复：审批挂起期间（同批/后续工具待决断）刷新/后开浏览器时，
        已完成工具的 result 必须已持久化，否则输出结果组件缺失。
        """
        # 安全点（Task 9）：审批到达时已收到停止请求则不再创建审批等待
        if executor.check_stop_requested():
            raise asyncio.CancelledError("用户停止生成")
        await persist_chat_tool_calls(data, content_state, session_id, message_id)
        return await _wait_for_chat_batch_decision(executor, session_id, graph_interrupt_id)

    stopped = False
    is_regenerate = bool(params.get("regenerate"))
    wait_suspend = None
    # 失败终态信息（A1.3）：异常分支置位，finally 分支化广播。
    # None = 成功/停止路径（data 与历史结构一致）；dict = 失败路径，
    # 携带 {code, message}（分类错误码 + 用户可行动中文文案）。
    execution_error: dict | None = None
    try:
        wait_suspend = await chat_service.run_agent_session(
            data,
            _interrupt_handler,
            _broadcast,
            should_stop=executor.check_stop_requested,
        )
        if wait_suspend:
            # 业务等待挂起（wait_for_subagent，spec D4）：注册父 awaiter、置挂起态
            # 后退出本协程（不 finalize、不释放 checkpointer）——子代理终态时由
            # 生命周期管理器唤醒，以 Command(resume) 新建协程续跑（与 research 同构）。
            # 挂起前先落库已执行工具结果（与审批挂起 _interrupt_handler 一致），
            # 保证挂起期间刷新/后开浏览器子代理工具卡与正文完整可见。
            await persist_chat_tool_calls(data, content_state, session_id, message_id)
            await executor._handle_chat_wait_suspend(wait_suspend)
            return
    except asyncio.CancelledError:
        # 优雅停止（Task 9）：保留已输出内容 + checkpoint，标记 stopped/incomplete 广播
        logger.info(f"[ChatExec] chat 会话被用户停止: session={session_id}")
        stopped = True
    except Exception as exc:
        # A1.3（审计 F1-1）：失败必须对用户可见。分类映射为错误码 + 可行动文案
        # （原始异常仅进日志，不透出 str(exc)），finally 广播 success=False 终态。
        logger.exception(f"[ChatExec] chat 会话执行异常: session={session_id}")
        from Django_xm.apps.ai_engine.services.exceptions import build_user_facing_error

        error_code, user_message = build_user_facing_error(exc)
        execution_error = {"code": error_code, "message": user_message}
        # spec D6：重新生成启动即失败（无任何输出内容）→ 回滚归档空版本并广播
        # MESSAGE_REGENERATE_REVERTED（前端 handleMessageRegenerateReverted 镜像恢复）。
        # 仅失败路径回滚：用户停止（CancelledError，走 incomplete 广播）与已有
        # 部分输出（content 非空，保留失败版本）均不回滚。
        if is_regenerate and message_id and not (content_state.get("content") or "").strip():
            try:
                from Django_xm.apps.chat.services.stream_persistence import revert_regenerate_archive

                await revert_regenerate_archive(session_id, str(message_id))
            except Exception:
                logger.warning(
                    f"[ChatExec] 重新生成失败回滚异常: session={session_id}, message={message_id}",
                    exc_info=True,
                )
    finally:
        # D2/P2-8：reasoning 时间窗尾部强制冲刷（最终态红线）。窗口内未广播的
        # 推理快照在流收尾（成功/停止/失败/挂起）必须送达；flush 幂等，空
        # pending 为 no-op。try/except 不阻断收尾主流程。
        try:
            _throttle = reasoning_state.get("throttle")
            if _throttle is not None:
                await _throttle.flush()
        except Exception:
            logger.debug(
                f"[ChatExec] reasoning 节流窗口冲刷失败（不阻断收尾）: session={session_id}",
                exc_info=True,
            )
        # 挂起态不 finalize（会话未结束）：不落库终态、不广播 stream_completed、
        # 不释放 checkpointer（graph 恢复时从 checkpoint 续跑）
        if wait_suspend is None and not executor._suspended:
            # 落库 content + tool_calls 终态，并广播 stream_completed（前端最终化）
            # 重生成中断（regenerate 模式）广播 incomplete（禁止固化为主版本），
            # 普通发送停止广播 stopped（允许固化）——与前端 _streamKind 语义对齐；
            # 失败路径（execution_error 非空）广播 success=False + error/error_code
            # （A1.3：不再恒 success=True 假成功）
            await _finalize_chat_session(
                chat_service, data, content_state, session_id, message_id,
                stopped=stopped,
                incomplete=(stopped and is_regenerate),
                error_info=execution_error,
            )
            # B4 配套：会话执行已终态（成功/停止/失败），遗留 PENDING 审批批次
            # 无法再被本运行消费 → 终态化（TIMEOUT 语义，不触发恢复信令），
            # 防止审批恢复扫描（SessionManager._recover_chat_approvals）反复
            # 重建已死会话。挂起路径不收尾：会话仍存活，批次仍待决策/待消费。
            try:
                from Django_xm.apps.approvals.models import Approval
                from Django_xm.apps.approvals.services.approval_batch import (
                    abandon_session_pending_approvals,
                )

                await sync_to_async(abandon_session_pending_approvals)(
                    Approval.Source.CHAT, session_id
                )
            except Exception:
                logger.warning(
                    f"[ChatExec] 终态收尾审批批次失败（不阻断）: session={session_id}",
                    exc_info=True,
                )
            try:
                from Django_xm.apps.ai_engine.services.checkpointer_factory import release_async_checkpointer

                await release_async_checkpointer()
            except Exception:
                logger.debug("[ChatExec] 释放异步 Checkpointer 连接失败（可忽略）")


async def _finalize_chat_session(
    chat_service,
    data,
    content_state,
    session_id,
    message_id,
    stopped: bool = False,
    incomplete: bool = False,
    error_info: dict | None = None,
) -> None:
    """执行结束后落库 content/tool_calls 并广播 stream_completed。

    复用 stream_persistence.persist_stream_result（唯一持久化入口），
    与旧 _finalize_stream_content 一致。

    Args:
        stopped: 是否用户主动停止（Task 9）。停止时广播携带 stopped 标记，
            前端据此保留已输出内容并标记 stopped 状态（普通发送停止允许固化）。
        incomplete: 是否重生成中断（Task 9）。重生成模式下用户停止产出的
            半成品版本标记 incomplete，禁止固化为本轮主消息。
        error_info: 失败终态信息（A1.3）。None = 成功/停止路径（data 与历史
            结构一致：{success: True, stopped, incomplete}）；非 None 时广播
            success=False + error（用户可行动文案）+ error_code（分类错误码），
            与 research 链 _handle_failure 的失败语义对齐（审计 F1-1/F1-3）。
            D-1 扩展：落库失败（persist_chat_tool_calls / _mark_not_streaming
            抛异常）同样视为失败终态，以 PERSIST_FAILED 错误码广播
            success=False，前端可感知"执行完成但内容未持久化"，不再假成功。
    """
    if not session_id or not message_id:
        return
    persist_error: dict | None = None
    try:
        from Django_xm.apps.chat.models import ChatMessage

        await persist_chat_tool_calls(data, content_state, session_id, message_id)

        @sync_to_async
        def _mark_not_streaming():
            msg = ChatMessage.objects.filter(id=message_id).first()
            if msg and msg.is_streaming:
                msg.is_streaming = False
                msg.save(update_fields=["is_streaming"])

        await _mark_not_streaming()
    except Exception:
        logger.warning(f"[ChatExec] 落库失败: session={session_id}", exc_info=True)
        # D-1：落库失败是异常路径，广播体必须携带失败原因（不透出裸异常原文）
        persist_error = {
            "code": "PERSIST_FAILED",
            "message": "回复已生成，但内容保存失败，刷新页面后本次回复可能丢失",
        }

    # 广播 stream_completed（触发/非触发浏览器统一最终化）；
    # 用户停止时携带 stopped/incomplete 标记（Task 9），前端据此标记对应状态；
    # 失败时携带 success=False + error/error_code（A1.3/D-1），前端标记 ERROR 终态
    try:
        from Django_xm.common.event_schema import EventSource, EventType
        from Django_xm.common.realtime_events import publish_event

        # D-1：执行异常与落库失败任一存在即失败终态；执行异常优先
        # （执行失败为主因，落库失败仅在其为唯一失败时透出）
        fail_info = error_info or persist_error
        completion_data: dict = {
            "success": fail_info is None,
            "stopped": stopped,
            "incomplete": incomplete,
        }
        if fail_info:
            completion_data["error"] = fail_info.get("message", "")
            completion_data["error_code"] = fail_info.get("code", "")

        await publish_event(
            EventType.STREAM_COMPLETED,
            {
                "source": EventSource.CHAT,
                "source_id": session_id,
                "message_id": str(message_id) if message_id else None,
                "data": completion_data,
            },
            session_id=session_id,
        )
    except Exception:
        logger.warning(f"[ChatExec] 广播 stream_completed 失败: session={session_id}", exc_info=True)

    # 跨会话记忆写入（spec D2）：persist_stream_result 完成后生成会话摘要落库；
    # 失败静默降级（debug 日志），绝不阻塞会话收尾主流程
    await _save_cross_session_memory(chat_service, data, content_state, session_id, incomplete)


async def _save_cross_session_memory(
    chat_service,
    data: dict,
    content_state: dict | None,
    session_id: str,
    incomplete: bool,
) -> None:
    """会话终态跨会话记忆写入（spec D2）：投递 Celery 后台任务（fire-and-forget）。

    跳过条件：重生成中断（incomplete 半成品不污染记忆）/ 用户消息或最终回答为空 /
    无 user_id。cross_session_enabled 与容量淘汰语义由 ContextManager 负责。

    后台化（本轮修复）：摘要合并/KG 实体抽取走本地辅助模型，实测耗时可达
    分钟级——原同步实现（sync_to_async 等待写完）会把会话执行器占住到记忆
    写完，期间该会话新消息的 start 信令被 SessionManager 幂等忽略，用户端
    表现为"发消息一直等待"。改为投递 chat.save_cross_session_memory 后
    finalize 立即返回，会话在主回复完成时即释放；任务内契约不变
    （prepare 锁外 LLM + commit 行锁写回 + KG 抽取，失败静默降级）。
    Celery 不可用时降级为原同步路径（to_thread），不丢记忆写入。
    """
    if incomplete:
        return
    user_message = str(data.get("message") or "")
    assistant_content = str((content_state or {}).get("content") or "")
    user_id = getattr(chat_service, "user_id", None)
    if not user_message.strip() or not assistant_content.strip() or not user_id:
        return
    try:
        from Django_xm.tasks.chat_tasks import save_cross_session_memory

        save_cross_session_memory.delay(
            user_id=user_id,
            session_id=str(session_id),
            user_message=user_message,
            assistant_content=assistant_content,
        )
        logger.debug(f"[ChatExec] 跨会话记忆已投递后台任务: session={session_id}")
    except Exception:
        # Celery 不可用（broker 掉线等）：降级为线程池同步写入，宁可慢不可丢
        logger.warning("[ChatExec] 记忆任务投递失败，降级为同步写入", exc_info=True)
        try:
            await _save_cross_session_memory_sync(user_id, str(session_id), user_message, assistant_content)
        except Exception:
            logger.debug(f"[ChatExec] 跨会话记忆写入失败（静默降级）: session={session_id}", exc_info=True)


async def _save_cross_session_memory_sync(user_id, session_id: str, user_message: str, assistant_content: str) -> None:
    """记忆写入同步兜底路径（Celery 不可用时；契约与 Celery 任务体一致）。"""

    def _write() -> bool:
        from django.db import transaction

        from Django_xm.apps.chat.models import ChatSession
        from Django_xm.apps.context_manager.services.manager import create_context_manager

        manager = create_context_manager(user_id=user_id)
        prepared = manager.prepare_turn_summary(
            session_id=session_id,
            user_message=user_message,
            assistant_content=assistant_content,
        )
        if prepared is None:
            return False
        saved = False
        with transaction.atomic():
            ChatSession.objects.select_for_update().filter(session_id=session_id).first()
            saved = manager.commit_turn_summary(session_id, prepared)
            manager.process_conversation(
                user_id=user_id,
                messages=[
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": assistant_content},
                ],
            )
        return saved

    saved = await sync_to_async(_write)()
    if saved:
        logger.debug(f"[ChatExec] 跨会话记忆已写入（同步兜底）: session={session_id}")
