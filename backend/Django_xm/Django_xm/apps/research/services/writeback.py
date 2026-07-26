"""深度研究结果回写与广播公共模块。

供 Celery 任务（deep_research.py / research_resume_task.py）共用，
负责将深度研究结果回写到关联的 ChatMessage，并广播 stream_completed 事件。
"""

import logging
from typing import Optional

from django.utils import timezone

from Django_xm.common.realtime_events import publish_event_sync
from Django_xm.common.event_schema import EventType, EventSource, PayloadValidationError

logger = logging.getLogger(__name__)


def broadcast_stream_completed(
    chat_session_id: Optional[str],
    task_id: str,
    success: bool,
    final_report: str = '',
    error: str = '',
    message_id: Optional[str] = None,
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
        'source': EventSource.DEEP_RESEARCH,
        'source_id': chat_session_id or task_id,
        'task_id': task_id,
        'success': success,
        'final_report': final_report,
        'finalized': True,
    }
    if error:
        payload['error'] = error
    if message_id:
        payload['message_id'] = message_id

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
        logger.error(
            f"[Writeback] STREAM_COMPLETED payload 校验失败，跳过广播: "
            f"task_id={task_id}, chat_session_id={chat_session_id}, success={success}",
            exc_info=True
        )
    except Exception as e:
        logger.warning(f"[Writeback] 广播 STREAM_COMPLETED 失败: {e}")


def writeback_to_chat_message(
    task_id: str,
    content: str,
    success: bool,
    chat_session_id: Optional[str] = None,
) -> Optional[str]:
    """将深度研究结果回写到关联的 ChatMessage 并广播 WebSocket 事件。

    Args:
        task_id: 深度研究任务 ID
        content: 最终报告内容或错误信息
        success: 是否成功
        chat_session_id: 关联的聊天会话 ID（可选，若为 None 则从 ChatMessage 反查）

    Returns:
        回写的 ChatMessage ID（字符串），失败时返回 None
    """
    try:
        from Django_xm.apps.chat.models import ChatMessage
        from Django_xm.apps.research.models import ResearchTask

        # 优先通过 ResearchTask.chat_message_id 查找关联的 ChatMessage（稳定正向关联，前端无法触及）
        # 回退到 ChatMessage.research_task_id 查询（向后兼容旧数据）
        chat_msg = None
        chat_session_id_from_task = None

        research_task = None
        try:
            research_task = ResearchTask.objects.get(task_id=task_id)
            chat_session_id_from_task = research_task.session_id
            if research_task.chat_message_id:
                chat_msg = ChatMessage.objects.filter(
                    id=research_task.chat_message_id,
                    role='assistant',
                    is_deleted=False,
                ).select_related('session').first()
                if chat_msg is None:
                    logger.warning(
                        f"[Writeback] chat_message_id={research_task.chat_message_id} 存在但 ChatMessage 未找到,"
                        f"回退到 research_task_id 查询: task_id={task_id}"
                    )
        except ResearchTask.DoesNotExist:
            logger.warning(f"[Writeback] ResearchTask 不存在: task_id={task_id}")

        # 回退：通过 research_task_id 查询（向后兼容旧数据，记录 warning）
        if chat_msg is None:
            chat_msg = ChatMessage.objects.filter(
                research_task_id=task_id,
                role='assistant',
                is_deleted=False,
            ).select_related('session').order_by('-created_at').first()
            if chat_msg is not None:
                logger.info(
                    f"[Writeback] 通过 research_task_id 回退查询成功(旧数据): task_id={task_id}, "
                    f"建议迁移设置 chat_message_id"
                )

        if chat_msg is None:
            logger.warning(f"[Writeback] 未找到关联 ChatMessage: task_id={task_id}")
            return None

        # chat_session_id 优先使用传入参数，其次从 ResearchTask.session_id 获取，最后从 ChatMessage.session 获取
        if chat_session_id is None:
            chat_session_id = chat_session_id_from_task or chat_msg.session.session_id

        # 更新 ChatMessage 内容
        # 只在 new_content 比当前 content 更长时覆盖，避免用短的 final_report
        # 覆盖之前已保存的更完整的流式内容（包含中间步骤文本）
        if success:
            new_content = content
        else:
            # 失败时显示友好错误提示
            new_content = f"深度研究执行失败：{content}"

        if len(new_content) > len(chat_msg.content or ''):
            chat_msg.content = new_content

        # 计算深度研究耗时（秒，至少 1 秒；research_task 不存在时用 0 兜底）
        # created_at 是 aware datetime，用 timezone.now() 比较
        if research_task is not None:
            research_duration = max(1, int((timezone.now() - research_task.created_at).total_seconds()))
        else:
            research_duration = 0

        # 更新 reasoning 字段为完成态（与前端 AiReasoning 期望格式一致：{content, duration}）
        if success:
            chat_msg.reasoning = {"content": "深度研究已完成", "duration": research_duration}
        else:
            chat_msg.reasoning = {"content": f"深度研究执行失败：{content}", "duration": research_duration}

        chat_msg.is_streaming = False
        chat_msg.save(update_fields=['content', 'is_streaming', 'reasoning'])

        # 广播 message_updated 事件到 chat session 频道
        # 注意：writeback 只修改 content/is_streaming，不广播 tool_calls
        # 原因：writeback 加载的 chat_msg 实例可能未包含最新审批状态
        #   （sync_approval_state_to_chat_message 在 complete_approval 时更新 DB，
        #    但本函数加载 chat_msg 时刻可能早于该提交），广播陈旧 tool_calls 会
        #    覆盖前端通过 approval_* 系列事件维护的正确本地审批状态。
        #    前端应通过 approval_* / tool_call_* 系列事件维护 tool_calls 状态，
        #    与代理模式行为一致（代理模式 SSE 流的 message_updated 不携带 tool_calls）。
        try:
            publish_event_sync(
                EventType.MESSAGE_UPDATED,
                {
                    'message_id': str(chat_msg.id),
                    'session_id': chat_session_id,
                    'content': chat_msg.content,
                    'is_streaming': False,
                    'research_task_id': task_id,
                },
                session_id=chat_session_id,
            )
        except PayloadValidationError:
            logger.error(
                f"[Writeback] MESSAGE_UPDATED payload 校验失败，跳过广播（回写仍生效）: "
                f"task_id={task_id}, chat_session_id={chat_session_id}, message_id={chat_msg.id}",
                exc_info=True
            )

        logger.info(
            f"[Writeback] ChatMessage 回写成功: task_id={task_id}, "
            f"session={chat_session_id}, success={success}"
        )
        return str(chat_msg.id)

    except Exception as e:
        logger.error(f"[Writeback] 回写 ChatMessage 失败: task_id={task_id}, error={e}", exc_info=True)
        return None
