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
    error_code: str = "",
    message_id: str | None = None,
    user_id: int | None = None,
    content: str = "",
    stopped: bool = False,
):
    """广播 stream_completed 事件到 session + task 双频道。

    深度研究完成（成功/失败/取消）时由执行服务（services/fastapi_service SessionExecutor）调用，
    作为权威完成事件。
    与聊天流式连接结束时发布的 stream_completed（finalized=false，无 task_id）不同，
    本事件始终携带 task_id 和 finalized=true，前端据此进入深度研究回写逻辑。

    取消语义（stopped=true，2026-10-06 缺陷修复）：与 chat 侧用户停止生成的
    stream_completed(stopped=true)（chat_executor_core._finalize_chat_session）语义
    对齐——前端据此映射 CANCELLED 终态（区别于 success=false 的 FAILED），且不写
    "深度研究执行失败"文案。

    统一底层：同时广播到 session + task 双频道，确保所有模块订阅者都能收到：
    - session:{chat_session_id} 频道：聊天模块深度研究模式订阅，触发
      handleStreamCompleted 处理聊天消息回写（更新 message.streamState 等）。
    - task:{task_id} 频道：DeepResearchView 独立模式订阅，触发
      researchStore.updateTaskFromEvent 更新任务状态（status/final_report/error）。
      独立深度研究模式（无 chat_session_id）下，task 频道是唯一事件源。
    - user:{user_id} 频道：任务进入终态时发布 task_status_changed，
      深度研究模块任务列表据此自动刷新状态（completed/failed/cancelled），
      避免列表停留在旧状态（awaiting_approval/running）需手动刷新。

    Args:
        chat_session_id: 关联的 chat session ID（可为 None，表示独立深度研究模式）
        task_id: 深度研究任务 ID（始终非空）
        success: 是否成功
        final_report: 最终报告内容（成功时，可能为空字符串）
        error: 错误信息（失败时）
        error_code: 分类错误码（失败时与 error 同透，空字符串不透传）
        message_id: 关联的 ChatMessage ID（可选，用于前端精确定位消息）
        stopped: 是否用户取消（取消终态标记，透传前端映射 CANCELLED）
    """
    payload = {
        "source": EventSource.DEEP_RESEARCH,
        "source_id": chat_session_id or task_id,
        "task_id": task_id,
        "success": success,
        "final_report": final_report,
        "finalized": True,
    }
    if stopped:
        payload["stopped"] = True
    if error:
        payload["error"] = error
    # 分类错误码（整改 A1.4）：失败时与 error 文案同透，对齐 chat 链失败终态
    # payload 结构（error + error_code），供前端按错误码做差异化处理
    if error_code:
        payload["error_code"] = error_code
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
    # 自动刷新状态（completed/failed/cancelled）。与 stream_completed（session+task 频道，业务事件）
    # 分离：task_status_changed 仅 user 频道，是用户级列表刷新通知（非业务数据）。
    # 发布失败不影响主流程（stream_completed 已广播），仅记录日志。
    if user_id:
        try:
            if stopped:
                terminal_status = "cancelled"
            else:
                terminal_status = "completed" if success else "failed"
            publish_event_sync(
                EventType.TASK_STATUS_CHANGED,
                {
                    "task_id": task_id,
                    "status": terminal_status,
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
    """实际发布 stream_reasoning 事件（不节流）。

    persist_history=False（E9/P2-8 对齐 chat 链 D2）：中间态推理快照仅实时
    广播不入回放历史 List——payload 为累积全量（前端覆盖语义），晚订阅浏览器
    经下一条实时快照收敛；重连回放的推理终态恢复权威源为 ChatMessage.reasoning
    （完成时 writeback_to_chat_message 落库 + message_updated 广播，正常入历史）。
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
            persist_history=False,
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
    """实际发布 stream_content_update 事件（不节流）。

    persist_history=False（E9/P2-8 对齐 chat 链 D2）：正文快照仅实时广播不入
    回放历史 List——每频道历史席位（LTRIM 500 条）留给 tool_call/approval/
    status_change 等关键事件。payload 为累积全量（前端覆盖语义），晚订阅浏览器
    经下一条实时快照收敛；重连回放的正文终态恢复权威源为 stream_completed
    （payload 携带最终全量 content）与 message_updated（落库快照），均正常入历史。
    """
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
            persist_history=False,
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
    tool_entries: dict | None = None,
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
        tool_entries: 子代理工具条目会话级聚合（tool_call_id → entry，
            adapter._tool_entries 格式）。图层字段贯通
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

        # 定位关联 ChatMessage：ChatMessage.research_task_id（指向
        # ResearchTask.task_id 的 CharField）是唯一关联方向——ResearchTask 侧的
        # chat_message_id 正向字段已于迁移 0009 删除，旧正向查找未跟删导致
        # getattr 恒 None（静默失效，2026-10-06 深研冒烟修复）。
        chat_msg_data = None
        chat_session_id_from_task = None

        research_task = None
        try:
            research_task = ResearchTask.objects.get(task_id=task_id)
            chat_session_id_from_task = research_task.session_id
        except ResearchTask.DoesNotExist:
            logger.warning(f"[Writeback] ResearchTask 不存在: task_id={task_id}")

        chat_msg_data = get_chat_message_for_writeback(research_task_id=task_id)
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
            # tool_entries 聚合）合并到 ChatMessage.tool_calls 顶层，
            # 确保工具执行的 status/result/error 对快照 API 可见（不在仅 versions 内）。
            # 乱序根源修复：审批重建路径落库丢失图层字段且无审批 SAFE 工具
            # 完全丢失，经 merge_tool_calls_incremental（字段级演进 + 图层字段
            # 补齐 subagent_thread_id/agent_name/depth）合并后刷新可完整归集子代理工具卡。
            merge_source: list[dict] = []
            if research_task is not None and research_task.tool_calls:
                merge_source.extend(tc for tc in research_task.tool_calls if isinstance(tc, dict))
            if isinstance(tool_entries, dict):
                merge_source.extend(
                    dict(entry) for entry in tool_entries.values() if isinstance(entry, dict)
                )
            if merge_source:
                from Django_xm.apps.chat.services.stream_persistence import merge_tool_calls_incremental

                existing_tc = msg.tool_calls
                merged_tc = merge_tool_calls_incremental(existing_tc, merge_source)
                if merged_tc != existing_tc:
                    msg.tool_calls = merged_tc
                    msg_save_fields.append("tool_calls")
                    logger.info(
                        f"[Writeback] 已合并 tool_calls: task_id={task_id}, "
                        f"existing={len(existing_tc)}, merged={len(merged_tc)}, "
                        f"subagent_entries="
                        f"{len(tool_entries) if isinstance(tool_entries, dict) else 0}"
                    )
                # 同步 ResearchTask.tool_calls（迁移 0010 设计意图：tool_calls 记录
                # 全部工具调用的生命周期状态，作为独立深度研究/后续校验的持久化源）
                if research_task is not None:
                    current_rt_tc = research_task.tool_calls or []
                    if merged_tc != current_rt_tc:
                        research_task.tool_calls = merged_tc
                        research_task.save(update_fields=["tool_calls", "updated_at"])

            # B5: 同步 versions[0].content（最终报告内容对 API 快照可见）
            versions = msg.versions
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
                existing_sub = msg.subagent_contents
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
            tool_calls = ChatMessage.objects.only("tool_calls").get(id=chat_msg_id).tool_calls
        except ChatMessage.DoesNotExist:
            tool_calls = []

        try:
            payload_extra: dict = {}
            # 阶段 2（权威恢复源闭合）：message_updated 携带子代理图层正文终态，
            # 供前端 mergeMessageFromBackend 覆盖恢复（断线期间错过的快照帧，
            # 经此入历史权威事件收敛）；为空 dict 时不携带（前端按 undefined 跳过）。
            if isinstance(msg.subagent_contents, dict) and msg.subagent_contents:
                payload_extra["subagent_contents"] = msg.subagent_contents
            publish_event_sync(
                EventType.MESSAGE_UPDATED,
                {
                    "message_id": chat_msg_id,
                    "session_id": chat_session_id,
                    "content": final_content,
                    "is_streaming": False,
                    "research_task_id": task_id,
                    "tool_calls": tool_calls,
                    **payload_extra,
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


def finalize_chat_message_cancelled(
    task_id: str,
    error_message: str = "用户已取消该研究任务",
) -> str | None:
    """研究取消的聊天侧收尾（2026-10-06 缺陷修复公共单源）。

    背景：执行器取消级联（_finalize_research_cancelled）此前零 chat 侧收尾——
    无 ChatMessage 落库、无会话频道广播，聊天模块对取消毫无反应；刷新后聊天
    消息卡片也因缺少取消语义分支误显"深度研究已完成"。本函数与完成路径
    writeback_to_chat_message + broadcast_stream_completed 对称，被两条取消路径
    共用（session_executor 级联 / session_manager 无执行器 DB 兜底）。

    职责（与既有设施的关系）：
    - ChatMessage 终止语义落库：is_streaming=False；content 为空时补
      「深度研究已取消。」（对齐失败分支「深度研究执行失败：{...}」文案风格），
      非空保留已抢救过程正文（取消=部分成果保留展示，persist_research_progress
      已在级联前置步骤抢救增量，本函数不覆盖）；补写时同步 versions[0].content
      （B5 语义对齐）。
    - MESSAGE_UPDATED 广播（session + task 双频道，携带 research_task_id）：
      通知所有浏览器拉取终态快照。
    - broadcast_stream_completed(stopped=True) 广播：前端据此把聊天消息流状态
      收尾 + taskInfo 映射 CANCELLED（区别于 success=false 的 FAILED），user 频道
      任务列表同步刷新为 cancelled。

    Args:
        task_id: 深度研究任务 ID（= ChatMessage.research_task_id 关联值）
        error_message: 取消说明文案（透传 stream_completed.error）

    Returns:
        收尾的 ChatMessage ID（字符串）；ResearchTask 或关联 ChatMessage 不存在
        （独立深度研究场景，status_change 已覆盖 task 频道）时返回 None，静默跳过。
    """
    try:
        from django.apps import apps

        from Django_xm.apps.research.models import ResearchTask

        try:
            research_task = ResearchTask.objects.get(task_id=task_id)
        except ResearchTask.DoesNotExist:
            logger.warning(f"[Writeback] 取消收尾 ResearchTask 不存在: task_id={task_id}")
            return None
        chat_session_id = research_task.session_id
        user_id = research_task.created_by_id

        # 唯一关联方向 = ChatMessage.research_task_id（迁移 0009 后，同 writeback_to_chat_message）
        ChatMessage = apps.get_model("chat", "ChatMessage")
        msg = ChatMessage.objects.filter(research_task_id=task_id).first()
        if msg is None:
            logger.info(f"[Writeback] 取消收尾无关联 ChatMessage（独立深研场景）: task_id={task_id}")
            return None

        # 终止语义落库（幂等：已收尾则跳过 save，广播照发——前端可能错过先前终态事件）
        save_fields: list[str] = []
        final_content = msg.content or ""
        if not final_content.strip():
            final_content = "深度研究已取消。"
            msg.content = final_content
            save_fields.append("content")
            versions = msg.versions
            if versions:
                ver0 = versions[0] if isinstance(versions[0], dict) else {}
                if ver0.get("content") != final_content:
                    ver0["content"] = final_content
                    versions[0] = ver0
                    msg.versions = versions
                    save_fields.append("versions")
        if msg.is_streaming:
            msg.is_streaming = False
            save_fields.append("is_streaming")
        if save_fields:
            msg.save(update_fields=[*save_fields, "updated_at"])
        chat_msg_id = str(msg.id)

        # MESSAGE_UPDATED 广播（session + task 双频道）：对齐 persist_research_progress
        # 的广播结构（content/is_streaming/research_task_id/tool_calls）
        try:
            try:
                tool_calls = ChatMessage.objects.only("tool_calls").get(id=msg.id).tool_calls
            except ChatMessage.DoesNotExist:
                tool_calls = []
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
                task_id=task_id,
            )
        except PayloadValidationError:
            logger.exception(
                f"[Writeback] 取消收尾 MESSAGE_UPDATED payload 校验失败，跳过广播（落库仍生效）: "
                f"task_id={task_id}, message_id={chat_msg_id}",
            )
        except Exception as e:
            logger.warning(f"[Writeback] 取消收尾 MESSAGE_UPDATED 广播失败: task_id={task_id}, err={e}")

        # 权威取消终态事件（session + task + user 频道）：前端映射 CANCELLED
        broadcast_stream_completed(
            chat_session_id,
            task_id,
            success=False,
            error=error_message,
            message_id=chat_msg_id,
            user_id=user_id,
            stopped=True,
        )

        logger.info(
            f"[Writeback] 取消收尾完成: task_id={task_id}, session={chat_session_id}, "
            f"message_id={chat_msg_id}, content_backfilled={'content' in save_fields}"
        )
        return chat_msg_id
    except Exception:
        logger.exception(f"[Writeback] 取消收尾 ChatMessage 失败: task_id={task_id}")
        return None


def persist_research_subagent_contents(
    thread_id: str,
    subagent_thread_id: str,
    content: str,
    reasoning_content: str,
) -> None:
    """运行期落库单条子代理图层正文快照（阶段 2，2.1 权威源补缺）。

    背景：research 链挂起前落库（persist_research_progress）与完成回写
    （writeback_to_chat_message）之间存在运行期落库空窗——独立深研运行中
    刷新浏览器时 getStatus 返回的 ResearchTask.subagent_contents 陈旧/为空，
    且快照帧（persist_history=False）不进回放历史，"断线期间该子代理已完成 +
    任务未终态"场景正文空洞到任务完成。本函数由 STREAM_SUBAGENT_CONTENT
    节流广播器调用（与广播同频合流，≤2 次/s per subagent），补齐运行期权威源。

    合并语义：按 subagent_thread_id 单键合并（其余线程条目保留），相同则跳过；
    仅写 subagent_contents 字段，**不广播 MESSAGE_UPDATED**（挂起/完成落库点
    专用——运行期高频广播会引发前端快照校对风暴）；tool_calls 恢复仍走回放
    历史（persist_history=True），本函数不合并工具条目。

    Args:
        thread_id: 深度研究任务 ID。
        subagent_thread_id: 子代理 thread_id。
        content: 该子代理累积全量正文（快照）。
        reasoning_content: 该子代理累积全量中间思考（快照）。
    """
    if not thread_id or not subagent_thread_id:
        return
    incoming = {"content": content or "", "reasoning_content": reasoning_content or ""}
    research_task = None
    try:
        from Django_xm.apps.research.models import ResearchTask

        research_task = ResearchTask.objects.filter(task_id=thread_id, is_deleted=False).first()
    except Exception:
        logger.warning(
            f"[Writeback] 运行期落库子代理正文查询 ResearchTask 失败: task_id={thread_id}",
            exc_info=True,
        )

    if research_task is not None:
        try:
            existing_sub = research_task.subagent_contents or {}
            if not isinstance(existing_sub, dict):
                existing_sub = {}
            merged = {**existing_sub, subagent_thread_id: incoming}
            if merged.get(subagent_thread_id) != existing_sub.get(subagent_thread_id):
                research_task.subagent_contents = merged
                research_task.save(update_fields=["subagent_contents", "updated_at"])
        except Exception:
            logger.warning(
                f"[Writeback] 运行期落库子代理正文失败: task_id={thread_id}, "
                f"subagent={subagent_thread_id}",
                exc_info=True,
            )

    # 关联 ChatMessage 同步（chat 深度研究模式运行中刷新可见）。
    # 唯一关联方向 = ChatMessage.research_task_id（指向 ResearchTask.task_id 的
    # CharField）；ResearchTask 侧的 chat_message_id 正向字段已于迁移 0009 删除，
    # 旧正向查找代码未跟删 → FieldDoesNotExist 被 except 吞成 warning、本段同步
    # 从不生效（2026-10-06 深研冒烟实测修复）。
    try:
        from django.apps import apps

        ChatMessage = apps.get_model("chat", "ChatMessage")
        msg = ChatMessage.objects.filter(research_task_id=thread_id).first()
        if msg is None:
            return
        existing_msg_sub = msg.subagent_contents or {}
        if not isinstance(existing_msg_sub, dict):
            existing_msg_sub = {}
        merged_msg = {**existing_msg_sub, subagent_thread_id: incoming}
        if merged_msg.get(subagent_thread_id) != existing_msg_sub.get(subagent_thread_id):
            msg.subagent_contents = merged_msg
            msg.save(update_fields=["subagent_contents", "updated_at"])
    except Exception:
        logger.warning(
            f"[Writeback] 运行期落库子代理正文（关联 ChatMessage）失败: task_id={thread_id}",
            exc_info=True,
        )


def persist_research_progress(
    thread_id: str,
    subagent_contents: dict | None = None,
    tool_entries: dict | None = None,
    main_content: str | None = None,
) -> None:
    """挂起期间将深度研究内存态子代理数据落库（服务重启恢复 / 挂起中刷新的还原依据）。

    覆盖场景（与 chat 链路 ``persist_chat_tool_calls`` 同构）：
    - 审批中断挂起前（SessionExecutor._on_interrupt）与业务等待挂起前
      （SessionExecutor._handle_suspend）调用，将 adapter 内存态
      ``tool_entries`` / ``subagent_contents`` 落库到
      ``ResearchTask.tool_calls`` / ``subagent_contents`` 及关联
      ``ChatMessage.tool_calls`` / ``subagent_contents``，保证：
        1. 挂起期间刷新浏览器：前端从 DB 快照可读到子代理工具卡与图层正文；
        2. 服务重启恢复：``_inject_research_resume_baseline`` 从 DB 重建
           adapter 初始态，恢复轮不丢失挂起前的子代理工具条目。
    - 合并语义（``merge_tool_calls_incremental``）：字段级演进，既有条目
      保留并演进 status/result/图层字段，重复调用幂等（不覆盖历史）。

    Args:
        thread_id: 深度研究任务 ID
        subagent_contents: 子代理图层正文累计（adapter.subagent_contents 格式）
        tool_entries: 子代理工具条目聚合（tool_call_id → entry，
            adapter._tool_entries 格式，含 subagent_thread_id/position/seq）
    """
    from Django_xm.apps.chat.services.stream_persistence import merge_tool_calls_incremental

    merge_source: list[dict] = []
    if isinstance(tool_entries, dict):
        merge_source.extend(
            dict(entry) for entry in tool_entries.values() if isinstance(entry, dict)
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
            merged_tc = merge_tool_calls_incremental(existing_tc, merge_source)
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
                existing_tc = msg.tool_calls
                merged_tc = merge_tool_calls_incremental(existing_tc, merge_source)
                if merged_tc != existing_tc:
                    msg.tool_calls = merged_tc
                    msg_save_fields.append("tool_calls")
            if isinstance(subagent_contents, dict) and subagent_contents:
                existing_sub = msg.subagent_contents
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
                    ChatMessage.objects.only("tool_calls").get(id=chat_msg_id).tool_calls
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
