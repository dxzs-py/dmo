"""深度研究结果回写与广播公共模块。

供 Celery 任务（deep_research.py / research_resume_task.py）共用，
负责将深度研究结果回写到关联的 ChatMessage，并广播 stream_completed 事件。
"""

import logging

from django.utils import timezone

from Django_xm.common.event_schema import EventSource, EventType, PayloadValidationError
from Django_xm.common.realtime_events import publish_event_sync

logger = logging.getLogger(__name__)


def broadcast_stream_completed(
    chat_session_id: str | None,
    task_id: str,
    success: bool,
    final_report: str = "",
    error: str = "",
    message_id: str | None = None,
):
    """广播 stream_completed 事件到 session + task 双频道。

    深度研究完成（成功/失败）时由 Celery worker 调用，作为权威完成事件。
    与聊天 SSE 结束时发布的 stream_completed（finalized=false，无 task_id）不同，
    本事件始终携带 task_id 和 finalized=true，前端据此进入深度研究回写逻辑。

    统一底层：同时广播到 session + task 双频道，确保所有模块订阅者都能收到：
    - session:{chat_session_id} 频道：聊天模块深度研究模式订阅，触发
      handleStreamCompleted 处理聊天消息回写（更新 message.streamState 等）。
    - task:{task_id} 频道：DeepResearchView 独立模式订阅，触发
      researchStore.updateTaskFromEvent 更新任务状态（status/final_report/error）。
      独立深度研究模式（无 chat_session_id）下，task 频道是唯一事件源。

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


def broadcast_stream_reasoning(
    session_id: str,
    task_id: str,
    message_id: str,
    content: str,
):
    """广播 stream_reasoning 事件到 session + task 双频道。

    深度研究执行过程中，每当 LLM 产生 reasoning_content（思考链），
    通过 WebSocket 实时推送到前端，供 AiReasoning 组件展示。

    与 broadcast_stream_completed 相同的双频道策略：
    - session:{session_id} 频道：聊天模块深度研究模式订阅
    - task:{task_id} 频道：DeepResearchView 独立模式订阅

    Args:
        session_id: 关联的 chat session ID
        task_id: 深度研究任务 ID
        message_id: 关联的 ChatMessage ID（前端精确定位消息）
        content: 推理文本内容
    """
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


def writeback_to_chat_message(
    task_id: str,
    content: str,
    success: bool,
    chat_session_id: str | None = None,
) -> str | None:
    """将深度研究结果回写到关联的 ChatMessage 并广播 WebSocket 事件。

    Args:
        task_id: 深度研究任务 ID
        content: 最终报告内容或错误信息
        success: 是否成功
        chat_session_id: 关联的聊天会话 ID（可选，若为 None 则从 ResearchTask/ChatMessage 反查）

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
        if success:
            reasoning = {"content": "深度研究已完成", "duration": research_duration}
        else:
            reasoning = {"content": f"深度研究执行失败：{content}", "duration": research_duration}

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
            # B3: 合并 ResearchTask tool_calls
            if research_task is not None and research_task.tool_calls:
                from Django_xm.apps.chat.services.stream_persistence import _merge_tool_calls_incremental

                existing_tc = msg.tool_calls or []
                merged_tc = _merge_tool_calls_incremental(existing_tc, research_task.tool_calls)
                if merged_tc != existing_tc:
                    msg.tool_calls = merged_tc
                    msg_save_fields.append("tool_calls")
                    logger.info(
                        f"[Writeback] 已合并 tool_calls: task_id={task_id}, "
                        f"existing={len(existing_tc)}, merged={len(merged_tc)}"
                    )

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

            if msg_save_fields:
                msg.save(update_fields=msg_save_fields + ["updated_at"])

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
