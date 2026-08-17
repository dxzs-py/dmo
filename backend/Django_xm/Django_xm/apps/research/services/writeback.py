"""深度研究结果回写与广播公共模块。

供执行服务（SessionExecutor）与聊天路径共用，
负责将深度研究结果回写到关联的 ChatMessage，并广播 stream_completed 事件。
"""

import logging
import threading
import time

from django.utils import timezone

from Django_xm.common.event_schema import EventSource, EventType, PayloadValidationError
from Django_xm.common.realtime_events import publish_event_sync

logger = logging.getLogger(__name__)

# stream_reasoning 广播节流（推理逐 chunk 累积广播）：若每个 chunk 都发布，
# 一次长推理会产生上千事件/会话（seq 5000+），拖慢流式循环并造成前端实时
# 同步风暴与间隙等待/快照校对风暴。广播内容始终为累积全文（前端 AiReasoning
# 为覆盖语义），节流仅跳过窗口内的中间广播，内容不丢失；推理结束由
# broadcast_stream_completed 触发快照同步兜底补齐尾部。
REASONING_BROADCAST_INTERVAL_SECONDS = 0.2

_reasoning_throttle_lock = threading.Lock()
# key: (session_id, task_id, message_id) -> [last_broadcast_time, latest_content]
_reasoning_throttle_state = {}


def broadcast_stream_completed(
    chat_session_id: str | None,
    task_id: str,
    success: bool,
    final_report: str = "",
    error: str = "",
    message_id: str | None = None,
    user_id: int | None = None,
    content: str = "",
):
    """广播 stream_completed 事件到 session + task 双频道。

    深度研究完成（成功/失败）时由执行服务（services/fastapi_service SessionExecutor）调用，
    作为权威完成事件。
    与聊天 SSE 结束时发布的 stream_completed（finalized=false，无 task_id）不同，
    本事件始终携带 task_id 和 finalized=true，前端据此进入深度研究回写逻辑。

    统一底层：同时广播到 session + task 双频道，确保所有模块订阅者都能收到：
    - session:{chat_session_id} 频道：聊天模块深度研究模式订阅，触发
      handleStreamCompleted 处理聊天消息回写（更新 message.streamState 等）。
    - task:{task_id} 频道：DeepResearchView 独立模式订阅，触发
      researchStore.updateTaskFromEvent 更新任务状态（status/final_report/error）。
      独立深度研究模式（无 chat_session_id）下，task 频道是唯一事件源。
    - user:{user_id} 频道：任务进入终态时发布 task_status_changed，
      深度研究模块任务列表据此自动刷新状态（completed/failed），
      避免列表停留在旧状态（awaiting_approval/running）需手动刷新。

    Args:
        chat_session_id: 关联的 chat session ID（可为 None，表示独立深度研究模式）
        task_id: 深度研究任务 ID（始终非空）
        success: 是否成功
        final_report: 最终报告内容（成功时，可能为空字符串）
        error: 错误信息（失败时）
        message_id: 关联的 ChatMessage ID（可选，用于前端精确定位消息）
    """
    payload = {
        "source": EventSource.DEEP_RESEARCH,
        "source_id": chat_session_id or task_id,
        "task_id": task_id,
        "success": success,
        "final_report": final_report,
        "finalized": True,
    }
    if error:
        payload["error"] = error
    if message_id:
        payload["message_id"] = message_id
    # 主 agent 累计正文（过程信息权威源，对齐 ChatMessage.content）：
    # 独立深度研究模块据此在完成事件中直接获得过程正文（无需额外快照请求），
    # 与 final_report（最终报告，由报告组件展示）语义分离。
    if content:
        payload["content"] = content

    try:
        # 统一底层：同时发布到 session + task 双频道
        # - chat_session_id 非空 → session:{chat_session_id} 频道（聊天模块深度研究模式）
        # - task_id 始终非空 → task:{task_id} 频道（DeepResearchView 独立模式 + 双订阅场景）
        # publish_event_sync 在 session_id 和 task_id 同时非空时，会分别向两个频道广播，
        # 确保所有模块（聊天模块深度研究模式 / DeepResearchView 独立模式 / 学习工作流）
        # 都能收到 stream_completed 事件，避免"刷新后才能看到完成信息"的问题。
        publish_event_sync(
            EventType.STREAM_COMPLETED,
            payload,
            session_id=chat_session_id,
            task_id=task_id,
        )
    except PayloadValidationError:
        logger.exception(
            f"[Writeback] STREAM_COMPLETED payload 校验失败，跳过广播: "
            f"task_id={task_id}, chat_session_id={chat_session_id}, success={success}",
        )
    except Exception as e:
        logger.warning(f"[Writeback] 广播 STREAM_COMPLETED 失败: {e}")

    # 任务进入终态：发布 task_status_changed 到 user 频道，通知深度研究模块任务列表
    # 自动刷新状态（completed/failed）。与 stream_completed（session+task 频道，业务事件）
    # 分离：task_status_changed 仅 user 频道，是用户级列表刷新通知（非业务数据）。
    # 发布失败不影响主流程（stream_completed 已广播），仅记录日志。
    if user_id:
        try:
            publish_event_sync(
                EventType.TASK_STATUS_CHANGED,
                {
                    "task_id": task_id,
                    "status": "completed" if success else "failed",
                },
                user_id=user_id,
            )
        except Exception as e:
            logger.warning(f"[Writeback] 发布 task_status_changed 失败: task_id={task_id}, error={e}")


def broadcast_stream_reasoning(
    session_id: str,
    task_id: str,
    message_id: str,
    content: str,
):
    """广播 stream_reasoning 事件到 session + task 双频道（带节流）。

    深度研究执行过程中，每当 LLM 产生 reasoning_content（思考链），
    通过 WebSocket 实时推送到前端，供 AiReasoning 组件展示。

    与 broadcast_stream_completed 相同的双频道策略：
    - session:{session_id} 频道：聊天模块深度研究模式订阅
    - task:{task_id} 频道：DeepResearchView 独立模式订阅

    节流说明：调用方逐 chunk 传入累积全文，若全部发布将产生事件风暴
    （seq 5000+）。这里按时间窗口节流，窗口内仅记录最新内容，下个窗口
    补发累积全文；内容不丢失，前端覆盖渲染语义不受影响。

    Args:
        session_id: 关联的 chat session ID
        task_id: 深度研究任务 ID
        message_id: 关联的 ChatMessage ID（前端精确定位消息）
        content: 推理文本内容（累积全文）
    """
    now = time.monotonic()
    key = (session_id, task_id, message_id)
    with _reasoning_throttle_lock:
        last = _reasoning_throttle_state.get(key)
        if last is not None and now - last[0] < REASONING_BROADCAST_INTERVAL_SECONDS:
            last[1] = content  # 节流窗口内仅记录最新内容，下个窗口补发
            return
        _reasoning_throttle_state[key] = [now, content]
    _publish_stream_reasoning(session_id, task_id, message_id, content)


def _publish_stream_reasoning(session_id, task_id, message_id, content):
    """实际发布 stream_reasoning 事件（不节流）。"""
    payload = {
        "source": EventSource.DEEP_RESEARCH,
        "source_id": session_id,
        "message_id": message_id,
        "session_id": session_id,
        "task_id": task_id,
        "data": {"content": content},
    }

    try:
        publish_event_sync(
            EventType.STREAM_REASONING,
            payload,
            session_id=session_id,
            task_id=task_id,
        )
    except PayloadValidationError:
        logger.debug(
            f"[Writeback] STREAM_REASONING payload 校验失败，跳过广播: "
            f"task_id={task_id}, session_id={session_id}",
        )
    except Exception as e:
        logger.debug(f"[Writeback] 广播 STREAM_REASONING 失败: {e}")


# stream_content 广播节流（主 agent 正文逐 chunk 累积广播，对齐 chat 链路
# STREAM_CONTENT_UPDATE 500ms 节流语义）：若每个 chunk 都发布会造成事件风暴。
# 广播内容始终为累积全文（前端覆盖语义），节流仅跳过窗口内中间广播，内容不丢失；
# 完成时由 broadcast_stream_completed 携带 content 兜底补齐尾部。
CONTENT_BROADCAST_INTERVAL_SECONDS = 0.5

_content_throttle_lock = threading.Lock()
# key: (session_id, task_id, message_id) -> [last_broadcast_time, latest_content]
_content_throttle_state = {}


def broadcast_stream_content(
    session_id: str | None,
    task_id: str,
    message_id: str,
    content: str,
):
    """广播主 agent 累计正文（STREAM_CONTENT_UPDATE）到 session + task 双频道（带节流）。

    深度研究执行过程中主 agent 逐 chunk 输出过程正文，实时推送到前端，使
    前端 content 非空、``splitContentByToolPositions`` 可按 position 内联工具卡
    （position 语义依赖 content 非空，否则工具卡堆叠末尾乱序——乱序根因）。

    与 broadcast_stream_reasoning 相同的双频道策略与节流机制：
    - session:{session_id} 频道：聊天模块深度研究模式订阅（非空时）
    - task:{task_id} 频道：DeepResearchView 独立模式订阅（始终）

    Args:
        session_id: 关联的 chat session ID（独立深度研究场景为 None/空）
        task_id: 深度研究任务 ID（始终非空）
        message_id: 关联的 ChatMessage ID（独立深度研究场景可为空）
        content: 主 agent 累计正文全文
    """
    now = time.monotonic()
    key = (session_id or "", task_id, message_id or "")
    with _content_throttle_lock:
        last = _content_throttle_state.get(key)
        if last is not None and now - last[0] < CONTENT_BROADCAST_INTERVAL_SECONDS:
            last[1] = content  # 节流窗口内仅记录最新内容，下个窗口补发
            return
        _content_throttle_state[key] = [now, content]
    _publish_stream_content(session_id, task_id, message_id, content)


def _publish_stream_content(session_id, task_id, message_id, content):
    """实际发布 stream_content_update 事件（不节流）。"""
    payload = {
        "source": EventSource.DEEP_RESEARCH,
        "source_id": session_id or task_id,
        "message_id": message_id or None,
        "session_id": session_id,
        "task_id": task_id,
        "data": {"content": content},
    }

    try:
        publish_event_sync(
            EventType.STREAM_CONTENT_UPDATE,
            payload,
            session_id=session_id or None,
            task_id=task_id,
        )
    except PayloadValidationError:
        logger.debug(
            f"[Writeback] STREAM_CONTENT_UPDATE payload 校验失败，跳过广播: "
            f"task_id={task_id}, session_id={session_id}",
        )
    except Exception as e:
        logger.debug(f"[Writeback] 广播 STREAM_CONTENT_UPDATE 失败: {e}")


def writeback_to_chat_message(
    task_id: str,
    content: str,
    success: bool,
    chat_session_id: str | None = None,
    reasoning_content: str | None = None,
    subagent_contents: dict | None = None,
    subagent_tool_entries: dict | None = None,
) -> str | None:
    """将深度研究结果回写到关联的 ChatMessage 并广播 WebSocket 事件。

    Args:
        task_id: 深度研究任务 ID
        content: 最终报告内容或错误信息
        success: 是否成功
        chat_session_id: 关联的聊天会话 ID（可选，若为 None 则从 ResearchTask/ChatMessage 反查）
        reasoning_content: 深度研究过程中 LLM 累积的推理内容（深度思考功能）。
            仅承载模型推理，与"深度研究任务完成态"（由前端研究卡片表达）概念分离；
            为空/None 时不写 reasoning 字段。
        subagent_contents: 子代理图层正文/中间思考累计（spec MODIFIED：按
            subagent_thread_id 键累计，adapter.subagent_contents 格式：
            {subagent_thread_id: {content, reasoning_content}}）。
            为空/None 时不写 subagent_contents 字段。
        subagent_tool_entries: 子代理工具条目会话级聚合（tool_call_id → entry，
            adapter._subagent_tool_entries 格式）。图层字段贯通
            （subagent_thread_id/agent_name/depth + seq/position），合并进
            ChatMessage.tool_calls（乱序根源修复，与 chat 链路同构）。
            为空/None 时不合并。

    Returns:
        回写的 ChatMessage ID（字符串），失败时返回 None
    """
    try:
        # 通过 chat cross_app 门面访问 ChatMessage，消除 research → chat.models 直接依赖
        from Django_xm.apps.chat.services.cross_app import (
            get_chat_message_for_writeback,
            update_chat_message_fields,
        )
        from Django_xm.apps.research.models import ResearchTask

        # 优先通过 ResearchTask.chat_message_id 查找关联的 ChatMessage（稳定正向关联，前端无法触及）
        # 回退到 ChatMessage.research_task_id 查询（向后兼容旧数据）
        chat_msg_data = None
        chat_session_id_from_task = None

        research_task = None
        try:
            research_task = ResearchTask.objects.get(task_id=task_id)
            chat_session_id_from_task = research_task.session_id
            chat_message_id = getattr(research_task, "chat_message_id", None)
            if chat_message_id:
                chat_msg_data = get_chat_message_for_writeback(message_id=chat_message_id)
                if chat_msg_data is None:
                    logger.warning(
                        f"[Writeback] chat_message_id={chat_message_id} 存在但 ChatMessage 未找到,"
                        f"回退到 research_task_id 查询: task_id={task_id}"
                    )
        except ResearchTask.DoesNotExist:
            logger.warning(f"[Writeback] ResearchTask 不存在: task_id={task_id}")

        # 回退：通过 research_task_id 查询（向后兼容旧数据，记录 warning）
        if chat_msg_data is None:
            chat_msg_data = get_chat_message_for_writeback(research_task_id=task_id)
            if chat_msg_data is not None:
                logger.info(
                    f"[Writeback] 通过 research_task_id 回退查询成功(旧数据): task_id={task_id}, "
                    f"建议迁移设置 chat_message_id"
                )

        if chat_msg_data is None:
            logger.warning(f"[Writeback] 未找到关联 ChatMessage: task_id={task_id}")
            return None

        chat_msg_id = chat_msg_data["id"]
        current_content = chat_msg_data["content"]

        # chat_session_id 优先使用传入参数，其次从 ResearchTask.session_id 获取，最后从 ChatMessage.session 获取
        if chat_session_id is None:
            chat_session_id = chat_session_id_from_task or chat_msg_data["session_id"]

        # 更新 ChatMessage 内容
        # 只在 new_content 比当前 content 更长时覆盖，避免用短的 final_report
        # 覆盖之前已保存的更完整的流式内容（包含中间步骤文本）
        if success:
            new_content = content
        else:
            # 失败时显示友好错误提示
            new_content = f"深度研究执行失败：{content}"

        updated_content = None
        if len(new_content) > len(current_content or ""):
            updated_content = new_content
            final_content = new_content
        else:
            final_content = current_content

        # 计算深度研究耗时（秒，至少 1 秒；research_task 不存在时用 0 兜底）
        # created_at 是 aware datetime，用 timezone.now() 比较
        if research_task is not None:
            research_duration = max(1, int((timezone.now() - research_task.created_at).total_seconds()))
        else:
            research_duration = 0

        # 更新 reasoning 字段为完成态（与前端 AiReasoning 期望格式一致：{content, duration}）
        # 概念边界：reasoning 只承载模型推理内容（深度思考功能），
        # "深度研究任务完成态"由前端研究卡片（正在进行深度研究/深度研究已完成）表达，
        # 不再将任务状态伪装成模型推理写入 reasoning。
        # 无推理内容时置 None（cross_app 门面仅在 reasoning 非 None 时更新，保留原值）。
        if success and reasoning_content and reasoning_content.strip():
            reasoning = {"content": reasoning_content, "duration": research_duration}
        else:
            reasoning = None

        # 通过门面更新 ChatMessage 字段（content 仅在变更时更新）
        update_chat_message_fields(
            chat_msg_id,
            content=updated_content,
            is_streaming=False,
            reasoning=reasoning,
        )

        # B3: 将 ResearchTask.tool_calls 合并到 ChatMessage.tool_calls 顶层，
        # 确保工具执行的 status/result/error 对快照 API 可见（不在仅 versions 内）。
        # B5: 同步 versions[0].content 到最终报告内容。
        from django.apps import apps

        ChatMessage = apps.get_model("chat", "ChatMessage")
        try:
            msg = ChatMessage.objects.get(id=chat_msg_id)
        except ChatMessage.DoesNotExist:
            msg = None

        msg_save_fields = []
        if msg is not None:
            # B3: 将 tool_calls（ResearchTask.tool_calls 历史字段 + 本次运行
            # subagent_tool_entries 聚合）合并到 ChatMessage.tool_calls 顶层，
            # 确保工具执行的 status/result/error 对快照 API 可见（不在仅 versions 内）。
            # 乱序根源修复：审批重建路径落库丢失图层字段且无审批 SAFE 工具
            # 完全丢失，经 _merge_tool_calls_incremental（字段级演进 + 图层字段
            # 补齐 subagent_thread_id/agent_name/depth）合并后刷新可完整归集子代理工具卡。
            merge_source: list[dict] = []
            if research_task is not None and research_task.tool_calls:
                merge_source.extend(tc for tc in research_task.tool_calls if isinstance(tc, dict))
            if isinstance(subagent_tool_entries, dict):
                merge_source.extend(
                    dict(entry) for entry in subagent_tool_entries.values() if isinstance(entry, dict)
                )
            if merge_source:
                from Django_xm.apps.chat.services.stream_persistence import _merge_tool_calls_incremental

                existing_tc = msg.tool_calls or []
                merged_tc = _merge_tool_calls_incremental(existing_tc, merge_source)
                if merged_tc != existing_tc:
                    msg.tool_calls = merged_tc
                    msg_save_fields.append("tool_calls")
                    logger.info(
                        f"[Writeback] 已合并 tool_calls: task_id={task_id}, "
                        f"existing={len(existing_tc)}, merged={len(merged_tc)}, "
                        f"subagent_entries={len(subagent_tool_entries) if isinstance(subagent_tool_entries, dict) else 0}"
                    )
                # 同步 ResearchTask.tool_calls（迁移 0010 设计意图：tool_calls 记录
                # 全部工具调用的生命周期状态，作为独立深度研究/后续校验的持久化源）
                if research_task is not None:
                    current_rt_tc = research_task.tool_calls or []
                    if merged_tc != current_rt_tc:
                        research_task.tool_calls = merged_tc
                        research_task.save(update_fields=["tool_calls", "updated_at"])

            # B5: 同步 versions[0].content（最终报告内容对 API 快照可见）
            versions = msg.versions or []
            if final_content and versions:
                ver0 = versions[0] if isinstance(versions[0], dict) else {}
                if ver0.get("content") != final_content:
                    ver0["content"] = final_content
                    versions[0] = ver0
                    msg.versions = versions
                    if "versions" not in msg_save_fields:
                        msg_save_fields.append("versions")

            # Agent 图层嵌套：子代理图层正文/思考落库（仅传入非空且与已有值不同时覆盖）
            if isinstance(subagent_contents, dict) and subagent_contents:
                existing_sub = msg.subagent_contents or {}
                if not isinstance(existing_sub, dict):
                    existing_sub = {}
                if subagent_contents != existing_sub:
                    msg.subagent_contents = subagent_contents
                    if "subagent_contents" not in msg_save_fields:
                        msg_save_fields.append("subagent_contents")

            # 同步 ResearchTask.subagent_contents（独立深研刷新还原的唯一权威来源）
            if isinstance(subagent_contents, dict) and subagent_contents and research_task is not None:
                rt_sub = research_task.subagent_contents or {}
                if not isinstance(rt_sub, dict):
                    rt_sub = {}
                if subagent_contents != rt_sub:
                    research_task.subagent_contents = subagent_contents
                    research_task.save(update_fields=["subagent_contents", "updated_at"])

            if msg_save_fields:
                msg.save(update_fields=[*msg_save_fields, "updated_at"])

        # 重新加载 tool_calls 用于广播（确保拿到合并后的最新数据）
        try:
            tool_calls = ChatMessage.objects.only("tool_calls").get(id=chat_msg_id).tool_calls or []
        except ChatMessage.DoesNotExist:
            tool_calls = []

        try:
            publish_event_sync(
                EventType.MESSAGE_UPDATED,
                {
                    "message_id": chat_msg_id,
                    "session_id": chat_session_id,
                    "content": final_content,
                    "is_streaming": False,
                    "research_task_id": task_id,
                    "tool_calls": tool_calls,
                },
                session_id=chat_session_id,
            )
        except PayloadValidationError:
            logger.exception(
                f"[Writeback] MESSAGE_UPDATED payload 校验失败，跳过广播（回写仍生效）: "
                f"task_id={task_id}, chat_session_id={chat_session_id}, message_id={chat_msg_id}",
            )

        logger.info(
            f"[Writeback] ChatMessage 回写成功: task_id={task_id}, session={chat_session_id}, success={success}"
        )
        return chat_msg_id

    except Exception:
        logger.exception(f"[Writeback] 回写 ChatMessage 失败: task_id={task_id}")
        return None


def persist_research_progress(
    thread_id: str,
    subagent_contents: dict | None = None,
    subagent_tool_entries: dict | None = None,
    main_content: str | None = None,
) -> None:
    """挂起期间将深度研究内存态子代理数据落库（服务重启恢复 / 挂起中刷新的还原依据）。

    覆盖场景（与 chat 链路 ``_persist_chat_tool_calls`` 同构）：
    - 审批中断挂起前（SessionExecutor._on_interrupt）与业务等待挂起前
      （SessionExecutor._handle_suspend）调用，将 adapter 内存态
      ``subagent_tool_entries`` / ``subagent_contents`` 落库到
      ``ResearchTask.tool_calls`` / ``subagent_contents`` 及关联
      ``ChatMessage.tool_calls`` / ``subagent_contents``，保证：
        1. 挂起期间刷新浏览器：前端从 DB 快照可读到子代理工具卡与图层正文；
        2. 服务重启恢复：``_inject_research_resume_baseline`` 从 DB 重建
           adapter 初始态，恢复轮不丢失挂起前的子代理工具条目。
    - 合并语义（``_merge_tool_calls_incremental``）：字段级演进，既有条目
      保留并演进 status/result/图层字段，重复调用幂等（不覆盖历史）。

    Args:
        thread_id: 深度研究任务 ID
        subagent_contents: 子代理图层正文累计（adapter.subagent_contents 格式）
        subagent_tool_entries: 子代理工具条目聚合（tool_call_id → entry，
            adapter._subagent_tool_entries 格式，含 subagent_thread_id/position/seq）
    """
    from Django_xm.apps.chat.services.stream_persistence import _merge_tool_calls_incremental

    merge_source: list[dict] = []
    if isinstance(subagent_tool_entries, dict):
        merge_source.extend(
            dict(entry) for entry in subagent_tool_entries.values() if isinstance(entry, dict)
        )

    save_fields_research: list[str] = []
    research_task = None
    try:
        from Django_xm.apps.research.models import ResearchTask

        research_task = ResearchTask.objects.filter(task_id=thread_id, is_deleted=False).first()
    except Exception:
        logger.warning(f"[Writeback] persist_research_progress 查询 ResearchTask 失败: task_id={thread_id}")

    if research_task is not None:
        # tool_calls 合并（演进不覆盖）
        if merge_source:
            existing_tc = research_task.tool_calls or []
            merged_tc = _merge_tool_calls_incremental(existing_tc, merge_source)
            if merged_tc != existing_tc:
                research_task.tool_calls = merged_tc
                save_fields_research.append("tool_calls")
                logger.info(
                    f"[Writeback] 挂起落库 ResearchTask.tool_calls: task_id={thread_id}, "
                    f"existing={len(existing_tc)}, merged={len(merged_tc)}"
                )
        # subagent_contents 覆盖（非空且不同）
        if isinstance(subagent_contents, dict) and subagent_contents:
            existing_sub = research_task.subagent_contents or {}
            if not isinstance(existing_sub, dict):
                existing_sub = {}
            if subagent_contents != existing_sub:
                research_task.subagent_contents = dict(subagent_contents)
                save_fields_research.append("subagent_contents")
        # 主代理累计正文覆盖（非空且更长，避免回退覆盖更完整正文）
        if main_content and len(main_content) > len(research_task.content or ""):
            research_task.content = main_content
            save_fields_research.append("content")
        if save_fields_research:
            research_task.save(update_fields=[*save_fields_research, "updated_at"])

    # 关联 ChatMessage 同步（chat 深度研究模式挂起中刷新可见）
    chat_session_id = None
    chat_msg_id = None
    try:
        from django.apps import apps

        ChatMessage = apps.get_model("chat", "ChatMessage")
        msg = None
        if research_task is not None and getattr(research_task, "chat_message_id", None):
            msg = ChatMessage.objects.filter(
                id=research_task.chat_message_id, role="assistant"
            ).first()
        if msg is None:
            msg = ChatMessage.objects.filter(research_task_id=thread_id).first()
        if msg is not None:
            chat_session_id = getattr(msg.session, "session_id", None)
            chat_msg_id = str(msg.id)
            msg_save_fields: list[str] = []
            if merge_source:
                existing_tc = msg.tool_calls or []
                merged_tc = _merge_tool_calls_incremental(existing_tc, merge_source)
                if merged_tc != existing_tc:
                    msg.tool_calls = merged_tc
                    msg_save_fields.append("tool_calls")
            if isinstance(subagent_contents, dict) and subagent_contents:
                existing_sub = msg.subagent_contents or {}
                if not isinstance(existing_sub, dict):
                    existing_sub = {}
                if subagent_contents != existing_sub:
                    msg.subagent_contents = dict(subagent_contents)
                    msg_save_fields.append("subagent_contents")
            # 主代理累计正文覆盖（非空且更长，chat 深度研究模式挂起中刷新可见）
            if main_content and len(main_content) > len(msg.content or ""):
                msg.content = main_content
                msg_save_fields.append("content")
            if msg_save_fields:
                msg.save(update_fields=[*msg_save_fields, "updated_at"])
    except Exception:
        logger.warning(f"[Writeback] 挂起落库 ChatMessage 失败: task_id={thread_id}", exc_info=True)

    # 广播 MESSAGE_UPDATED（session + task 双频道），通知其他浏览器拉取快照
    if chat_session_id and chat_msg_id:
        try:
            from django.apps import apps

            ChatMessage = apps.get_model("chat", "ChatMessage")
            try:
                tool_calls = (
                    ChatMessage.objects.only("tool_calls").get(id=chat_msg_id).tool_calls or []
                )
            except ChatMessage.DoesNotExist:
                tool_calls = []
            publish_event_sync(
                EventType.MESSAGE_UPDATED,
                {
                    "message_id": chat_msg_id,
                    "session_id": chat_session_id,
                    "research_task_id": thread_id,
                    "tool_calls": tool_calls,
                },
                session_id=chat_session_id,
                task_id=thread_id,
            )
        except PayloadValidationError:
            logger.exception(
                f"[Writeback] 挂起落库 MESSAGE_UPDATED payload 校验失败，跳过广播: "
                f"task_id={thread_id}, message_id={chat_msg_id}",
            )
        except Exception as e:
            logger.warning(f"[Writeback] 挂起落库 MESSAGE_UPDATED 广播失败: task_id={thread_id}, err={e}")
