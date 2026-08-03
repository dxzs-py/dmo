"""重新生成 — 审批恢复流式生成器（Task 15.5 拆分）。

从 regenerate_service.py 抽离的 stream_regenerate_resume 异步生成器，
负责重新生成场景下审批中断后的 SSE 流式恢复输出。

与 stream_chat_resume_generator 的区别：
1. 使用 regen_{session_id}_{message_id} 作为 thread_id（而非 session_id）
2. 使用 approval.extra 中保存的 regenerate 配置重建 agent
3. 流结束后调用 _save_regenerated_message 保存到 versions
4. 不依赖 ChatService 的正常会话 checkpointer 恢复（regenerate 有独立状态）

⚠️ 已知问题：该函数当前未被 approval_gateway 调用（gateway 对 SOURCE_CHAT 统一路由
到 stream_chat_resume_generator）。需在 gateway 中增加 regenerate 分支或将本函数
合并到 chat resume 路径中。详见 ontology issue_round4_regen_resume_not_wired。

依赖方向：
- 引用 regenerate_context（_make_regen_thread_id）
- 引用 regenerate_persist（_request_approval + _save_* + _cleanup_*）
"""

import asyncio
import json
import logging
import time
from typing import Any

from asgiref.sync import sync_to_async

from Django_xm.apps.approvals.models import Approval
from Django_xm.apps.approvals.services import approval_service
from Django_xm.apps.chat.models import ChatMessage
from Django_xm.apps.chat.services.chat_service import ChatService
from Django_xm.apps.chat.services.stream_helpers import (
    _publish_tool_lifecycle_event,
    build_context_info,
    finalize_tool_calls,
    process_stream_chunk,
    update_usage_and_tokens,
)
from Django_xm.apps.chat.utils import _lcp_len
from Django_xm.common.event_schema import EventSource, EventType, PayloadValidationError
from Django_xm.common.realtime_events import publish_event
from Django_xm.common.sse_utils import sse_error_event

from .regenerate_context import _make_regen_thread_id
from .regenerate_persist import (
    _cleanup_streaming_flag,
    _request_approval,
    _save_partial_regen_content,
    _save_regenerated_message,
)

logger = logging.getLogger(__name__)


async def stream_regenerate_resume(
    request, approval, resume_value, session_id, request_data=None, graph_interrupt_id=None, langgraph_resume_id=None
):
    """重新生成场景下的审批恢复流式生成器。

    Args:
        request: HTTP 请求对象
        approval: Approval 模型实例
        resume_value: 恢复值（True/False/user_input，或批量场景的 {tool_call_id: bool} 字典）
        session_id: 原始会话 ID（用于事件广播）
        request_data: 预提取的 request.data（避免异步生成器中访问 request 的问题）
        graph_interrupt_id: 批次 UUID（从 _meta.graph_interrupt_id 读取，用于 DB 查询与前端 grouping）；
                           若为 None 则回退到 approval.interrupt_id
        langgraph_resume_id: LangGraph 恢复 ID（= intr.id，作为 Command(resume=...) 的 KEY）；
                             若为 None 则回退到 approval.interrupt_id
    """
    logger.info(
        f"[RegenerateResume] 开始: approval={approval.interrupt_id if approval else None}, session={session_id}"
    )

    if request_data is None:
        try:
            request_data = dict(request.data) if hasattr(request, "data") else {}
        except Exception as req_err:
            logger.warning(f"[RegenerateResume] 读取 request.data 失败: {req_err}")
            request_data = {}

    interrupt_id = approval.interrupt_id
    regen_extra = (approval.extra or {}).get("regenerate", {})
    if not isinstance(regen_extra, dict):
        regen_extra = {}

    regen_thread_id = regen_extra.get("thread_id") or (approval.extra or {}).get("regenerate_thread_id")
    regen_message_id = regen_extra.get("message_id")
    if not regen_thread_id and regen_message_id:
        regen_thread_id = _make_regen_thread_id(session_id, regen_message_id)
    if not regen_thread_id:
        logger.error("[RegenerateResume] 缺少 regen_thread_id，无法恢复")
        yield sse_error_event("approval_error", "无法恢复重新生成状态：缺少线程标识")
        return

    if not regen_message_id:
        logger.error("[RegenerateResume] 缺少 regen_message_id，无法恢复")
        yield sse_error_event("approval_error", "无法恢复重新生成状态：缺少消息标识")
        return

    persisted_tool_config = (approval.extra or {}).get("tool_config", {})
    persisted_model_config = (approval.extra or {}).get("model_config", {})

    provider_id = request_data.get("provider_id") or persisted_model_config.get("provider_id")
    model_name = request_data.get("model_name") or persisted_model_config.get("model_name")
    use_deep_thinking = request_data.get("use_deep_thinking", persisted_model_config.get("use_deep_thinking", False))
    special_params = request_data.get("special_params") or persisted_model_config.get("special_params")
    temperature = request_data.get("temperature") or persisted_model_config.get("temperature")
    max_tokens = request_data.get("max_tokens") or persisted_model_config.get("max_tokens")

    use_tools = request_data.get("use_tools", persisted_tool_config.get("use_tools", True))
    use_web_search = request_data.get("use_web_search", persisted_tool_config.get("use_web_search", False))
    use_mcp = request_data.get("use_mcp", persisted_tool_config.get("use_mcp", False))
    selected_mcp_servers = request_data.get("selected_mcp_servers") or persisted_tool_config.get("selected_mcp_servers")
    selected_tools = request_data.get("selected_tools") or persisted_tool_config.get("selected_tools")
    use_knowledge_base = request_data.get("use_knowledge_base", persisted_tool_config.get("use_knowledge_base", False))
    selected_knowledge_bases = request_data.get("selected_knowledge_bases") or persisted_tool_config.get(
        "selected_knowledge_bases", []
    )
    tool_tier = persisted_tool_config.get("tool_tier", "standard")

    logger.info(
        f"[RegenerateResume] 配置: thread={regen_thread_id}, model={model_name}, "
        f"tool_tier={tool_tier}, tools={selected_tools}"
    )

    ended_by_interrupt = False
    new_interrupt_ids = set()
    current_message_content = ""
    # BUG N 修复：parse_approval_interrupt 需要用来跟踪已匹配的 tool_call_id
    used_tool_call_ids: set = set()
    try:

        @sync_to_async
        def _mark_streaming_resume():
            ChatMessage.objects.filter(id=regen_message_id).update(is_streaming=True)

        await _mark_streaming_resume()
    except Exception as e:
        logger.warning(f"[RegenerateResume] 标记 is_streaming 失败: {e}")

    try:
        from langgraph.types import Command

        data: dict[str, Any] = {
            "session_id": regen_thread_id,
            "mode": "agent",
            "use_tools": use_tools,
            "use_web_search": use_web_search,
            "use_mcp": use_mcp,
            "selected_mcp_servers": selected_mcp_servers,
            "selected_tools": selected_tools,
            "use_knowledge_base": use_knowledge_base,
            "selected_knowledge_bases": selected_knowledge_bases,
            "tool_tier": tool_tier,
            "provider_id": provider_id,
            "model_name": model_name,
            "use_deep_thinking": use_deep_thinking,
            "special_params": special_params,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        enable_deep_thinking = use_deep_thinking and provider_id and model_name
        if enable_deep_thinking:
            from Django_xm.apps.ai_engine.services.llm_factory import model_supports_capability

            enable_deep_thinking = model_supports_capability(provider_id, model_name, "deep_thinking")
        if enable_deep_thinking:
            data["_enable_deep_thinking"] = True

        # Command(resume=...) 的 KEY 必须是 LangGraph 真正的 interrupt_id（intr.id）
        effective_resume_key = langgraph_resume_id or interrupt_id
        command = Command(resume={effective_resume_key: resume_value})

        logger.info(
            f"[RegenerateResume] 创建 ChatService 并恢复 Agent: "
            f"effective_resume_key={effective_resume_key}, "
            f"interrupt_id={interrupt_id}, graph_interrupt_id={graph_interrupt_id}, "
            f"langgraph_resume_id={langgraph_resume_id}, "
            f"resume_value_type={type(resume_value).__name__}"
        )
        chat_service = ChatService(user_id=request.user.id, thread_id=regen_thread_id)
        model_instance = ChatService._resolve_model_instance(data)
        tools = await chat_service._get_tools(data)
        tool_config_for_agent = chat_service._build_tool_config(data)

        agent, thread_config, use_checkpointer = await chat_service._create_agent_with_memory(
            data,
            prompt_mode="agent",
            model_instance=model_instance,
            tool_config=tool_config_for_agent,
            tools=tools,
        )

        if not use_checkpointer or not thread_config:
            logger.error("[RegenerateResume] checkpointer 不可用")
            yield sse_error_event("approval_error", "无法恢复重新生成状态：checkpointer 不可用")
            return

        logger.info("[RegenerateResume] Agent 创建成功，开始流式恢复...")

        tool_calls_map: dict[str, dict] = {}
        used_tool_call_ids = set()
        tool_call_count: dict[str, int] = {}
        accumulated_reasoning: dict[str, str] = {}
        tool_args_accumulator: dict[str, str] = {}
        all_messages: list = []

        yield f"data: {json.dumps({'type': 'start', 'message': '审批恢复，继续生成...'}, ensure_ascii=False)}\n\n"

        stream_config = thread_config
        from langchain_core.messages import AIMessage as LCAIMessage

        from Django_xm.apps.ai_engine.config import settings as ai_settings
        from Django_xm.apps.ai_engine.services.cost_tracker import create_token_detail_tracker
        from Django_xm.apps.ai_engine.services.token_counter import TokenUsageCallbackHandler
        from Django_xm.apps.ai_engine.services.usage_tracker import create_usage_tracker

        usage_tracker = create_usage_tracker(model_id=model_name or ai_settings.openai_model)
        token_detail_tracker = create_token_detail_tracker()

        with TokenUsageCallbackHandler() as cb:
            async for chunk in agent.graph.astream(
                command,
                config=stream_config,
                stream_mode=["messages", "updates"],
            ):
                if isinstance(chunk, tuple) and len(chunk) == 2:
                    mode_name, mode_data = chunk
                else:
                    mode_name, mode_data = "messages", chunk

                if mode_name == "updates":
                    if isinstance(mode_data, dict):
                        if "__interrupt__" in mode_data:
                            from Django_xm.apps.chat.services.stream_helpers import (
                                extract_interrupt_ids,
                                parse_approval_interrupt,
                            )
                            from Django_xm.apps.tools.base import is_approval_interrupt

                            interrupts = mode_data["__interrupt__"]
                            if interrupts:
                                batch_approval_data = []
                                for intr in interrupts:
                                    interrupt_value, batch_id, resume_id = extract_interrupt_ids(intr)
                                    graph_intr_id = resume_id

                                    if not is_approval_interrupt(interrupt_value):
                                        continue

                                    parsed_list = parse_approval_interrupt(
                                        interrupt_value,
                                        graph_interrupt_id=batch_id,
                                        langgraph_resume_id=resume_id,
                                        tool_calls_map=tool_calls_map,
                                        tool_args_accumulator=tool_args_accumulator,
                                        used_tool_call_ids=used_tool_call_ids,
                                    )
                                    for approval_data in parsed_list:
                                        batch_approval_data.append((graph_intr_id, approval_data))

                                for graph_intr_id, approval_data in batch_approval_data:
                                    tool_name = approval_data.get("tool_name", "unknown")
                                    tool_call_id = approval_data.get("tool_call_id", "") or graph_intr_id
                                    new_langgraph_resume_id = approval_data.get("langgraph_resume_id", graph_intr_id)
                                    new_graph_interrupt_id = approval_data.get("graph_interrupt_id", graph_intr_id)
                                    approval_data["message_id"] = regen_message_id
                                    extra_dict = approval_data.get("extra") or {}
                                    if not isinstance(extra_dict, dict):
                                        extra_dict = {}
                                    extra_dict["graph_interrupt_id"] = new_graph_interrupt_id
                                    extra_dict["langgraph_resume_id"] = new_langgraph_resume_id
                                    extra_dict["tool_call_id"] = tool_call_id
                                    extra_dict["message_id"] = regen_message_id
                                    extra_dict["regenerate"] = {
                                        "thread_id": regen_thread_id,
                                        "message_id": regen_message_id,
                                    }
                                    approval_data["extra"] = extra_dict
                                    ended_by_interrupt = True
                                    if graph_intr_id:
                                        new_interrupt_ids.add(graph_intr_id)
                                    logger.info(
                                        f"[RegenerateResume] 审批恢复流中检测到新审批: "
                                        f"tool={tool_name}, interrupt_id={tool_call_id}, "
                                        f"graph_interrupt_id={new_graph_interrupt_id}, "
                                        f"langgraph_resume_id={new_langgraph_resume_id}"
                                    )
                                    yield f"data: {json.dumps({'type': 'approval', 'data': approval_data}, ensure_ascii=False)}\n\n"  # noqa: E501

                                    if tool_call_id and session_id:
                                        await _request_approval(
                                            tool_call_id,
                                            session_id,
                                            regen_message_id,
                                            approval_data,
                                            data,
                                            regen_thread_id,
                                        )
                        for node_name, node_output in mode_data.items():
                            if node_name == "__interrupt__":
                                continue
                            if isinstance(node_output, dict):
                                node_messages = node_output.get("messages", [])
                                if isinstance(node_messages, list):
                                    for msg in node_messages:
                                        tool_content = None
                                        tool_call_id = None
                                        tool_name_from_msg = None
                                        if hasattr(msg, "content"):
                                            tool_content = msg.content
                                            tool_call_id = getattr(msg, "tool_call_id", None)
                                            tool_name_from_msg = getattr(msg, "name", None) or node_name
                                        elif isinstance(msg, dict):
                                            tool_content = msg.get("content")
                                            tool_call_id = msg.get("tool_call_id")
                                            tool_name_from_msg = msg.get("name") or node_name

                                        if tool_content is not None and tool_call_id:
                                            tool_name = tool_name_from_msg
                                            tc_info = tool_calls_map.get(tool_call_id)
                                            if tc_info:
                                                tool_name = tc_info.get("name", tool_name)
                                                tc_info["status"] = "completed"
                                                tc_info["result"] = tool_content
                                            _publish_tool_lifecycle_event(
                                                EventType.TOOL_CALL_COMPLETED,
                                                {
                                                    "id": tool_call_id,
                                                    "name": tool_name,
                                                    "result": tool_content,
                                                },
                                                session_id,
                                                str(regen_message_id) if regen_message_id else None,
                                            )
                    continue

                all_messages.append(mode_data if not isinstance(mode_data, tuple) else mode_data[0])

                if isinstance(mode_data, tuple) and len(mode_data) == 2:
                    msg_obj = mode_data[0]
                else:
                    msg_obj = mode_data

                if isinstance(msg_obj, LCAIMessage) and (
                    getattr(msg_obj, "tool_calls", None) or getattr(msg_obj, "tool_call_chunks", None)
                ):
                    msg_tool_calls = getattr(msg_obj, "tool_calls", None) or []
                    new_tool_calls = []
                    for tc in msg_tool_calls:
                        tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                        if tc_id and tc_id not in tool_calls_map:
                            new_tool_calls.append(tc)
                    if new_tool_calls:
                        for tc in new_tool_calls:
                            tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                            tc_name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
                            tc_args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {})
                            if tc_id:
                                tool_calls_map[tc_id] = {
                                    "id": tc_id,
                                    "name": tc_name or "unknown",
                                    "parameters": tc_args or {},
                                    "state": "input-available",
                                    "status": "running",
                                }
                                if tc_args and tc_args != {}:
                                    tool_args_accumulator[tc_id] = json.dumps(tc_args, ensure_ascii=False)
                                _publish_tool_lifecycle_event(
                                    EventType.TOOL_CALL_INPUT_READY,
                                    tool_calls_map[tc_id],
                                    session_id,
                                    str(regen_message_id) if regen_message_id else None,
                                )

                try:
                    for event in process_stream_chunk(
                        mode_data,
                        tool_calls_map,
                        current_message_content,
                        tool_call_count=tool_call_count,
                        lcp_func=_lcp_len,
                        accumulated_reasoning=accumulated_reasoning,
                        tool_args_accumulator=tool_args_accumulator,
                        mode="agent",
                        enable_deep_thinking=enable_deep_thinking,
                        session_id=session_id,
                        message_id=regen_message_id,
                        module_id=str(session_id or ""),
                    ):
                        if event.get("type") == "chunk":
                            current_message_content += event.get("content", "")
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except Exception as chunk_err:
                    logger.warning(f"[RegenerateResume] 处理流式 chunk 失败: {chunk_err}")
                    continue

        update_usage_and_tokens(cb, usage_tracker, token_detail_tracker)

        finalize_tool_calls(
            all_messages,
            tool_calls_map,
            tool_args_accumulator,
        )

        if ended_by_interrupt:
            yield f"data: {json.dumps({'type': 'interrupted', 'data': {'reason': 'approval_required'}}, ensure_ascii=False)}\n\n"  # noqa: E501
        else:
            context_info = build_context_info(usage_tracker, token_detail_tracker, time.time())
            yield f"data: {json.dumps({'type': 'context', 'data': context_info}, ensure_ascii=False)}\n\n"

        yield f"data: {json.dumps({'type': 'end', 'message': '生成完成'}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    except asyncio.CancelledError:
        logger.info(f"[RegenerateResume] 客户端断开: session={session_id}, interrupt={interrupt_id}")
        try:
            if current_message_content:
                await asyncio.shield(
                    _save_regenerated_message(
                        regen_message_id,
                        current_message_content,
                        tool_calls_map,
                        accumulated_reasoning,
                        None,
                    )
                )
        except Exception:
            logger.exception("[RegenerateResume] shield 保存失败")
        try:
            await asyncio.shield(_cleanup_streaming_flag(regen_message_id))
        except Exception:  # noqa: S110
            pass
        try:
            # BUG L 变体修复：批量场景 resume_value 是字典，需按 interrupt_id 查找实际审批值
            if isinstance(resume_value, dict):
                _shield_state = (
                    Approval.STATE_APPROVED if resume_value.get(interrupt_id) is True else Approval.STATE_REJECTED
                )
            else:
                _shield_state = Approval.STATE_APPROVED if resume_value is not False else Approval.STATE_REJECTED
            await asyncio.shield(
                approval_service.complete_approval_async(interrupt_id, _shield_state),
            )
        except Exception:
            logger.exception("[RegenerateResume] shield complete_approval 失败")
        raise
    except Exception as e:
        logger.exception("[RegenerateResume] 审批恢复执行失败")
        yield sse_error_event("approval_error", f"审批恢复执行失败: {e!s}")
        yield "data: [DONE]\n\n"
    finally:
        try:
            from Django_xm.apps.ai_engine.services.checkpointer_factory import release_async_checkpointer

            await release_async_checkpointer()
        except Exception:  # noqa: S110
            pass

        # BUG L 变体修复：批量场景 resume_value 是 {tool_call_id: bool} 字典
        if isinstance(resume_value, dict):
            tool_approved = resume_value.get(interrupt_id)
            final_state = Approval.STATE_APPROVED if tool_approved is True else Approval.STATE_REJECTED
        elif resume_value is False:
            final_state = Approval.STATE_REJECTED
        else:
            final_state = Approval.STATE_APPROVED

        if ended_by_interrupt:
            pending_content = current_message_content
            if pending_content:
                try:
                    await _save_partial_regen_content(
                        regen_message_id,
                        pending_content,
                        tool_calls_map,
                        accumulated_reasoning,
                    )
                except Exception:
                    logger.exception("[RegenerateResume] 保存中断内容失败")
            try:
                await _cleanup_streaming_flag(regen_message_id)
            except Exception:  # noqa: S110
                pass
            try:
                await approval_service.complete_approval_async(interrupt_id, final_state)
            except Exception:
                logger.exception("[RegenerateResume] complete_approval 失败")
        else:
            has_content = bool(current_message_content and current_message_content.strip())
            has_tc = bool(tool_calls_map)
            if has_content or has_tc:
                try:
                    await _save_regenerated_message(
                        regen_message_id,
                        current_message_content,
                        tool_calls_map,
                        accumulated_reasoning,
                        usage_tracker if "usage_tracker" in dir() else None,
                    )
                except Exception:
                    logger.exception("[RegenerateResume] 保存恢复内容失败")

            try:
                await _cleanup_streaming_flag(regen_message_id)
            except Exception:  # noqa: S110
                pass

            try:
                await approval_service.complete_approval_async(interrupt_id, final_state)
            except Exception:
                logger.exception("[RegenerateResume] complete_approval 失败")

            if session_id:
                try:
                    await asyncio.sleep(0.5)

                    @sync_to_async
                    def _get_last():
                        m = ChatMessage.objects.filter(id=regen_message_id).first()
                        return m.id if m else None

                    mid = await _get_last()
                    await publish_event(
                        EventType.STREAM_COMPLETED,
                        {
                            "source": EventSource.CHAT,
                            "source_id": session_id,
                            "session_id": session_id,
                            "message_id": str(mid) if mid else str(regen_message_id),
                            "is_streaming": False,
                            "finalized": False,
                        },
                        session_id=session_id,
                    )
                except PayloadValidationError:
                    logger.exception(
                        f"[RegenerateResume] STREAM_COMPLETED payload 校验失败，跳过广播: "
                        f"session_id={session_id}, regen_message_id={regen_message_id}",
                    )
                except Exception:
                    logger.exception("[RegenerateResume] 广播 stream_completed 失败")