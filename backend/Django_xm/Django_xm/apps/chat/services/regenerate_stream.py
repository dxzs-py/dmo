"""重新生成 — 流式生成器（Task 15.5 拆分）。

从 regenerate_service.py 抽离的 stream_regenerate 异步生成器，
负责重新生成场景的 SSE 流式输出：

流程：
1. 标记 message.is_streaming=True
2. 解析原始模型配置，构建 ChatService + agent
3. 使用 regen_{session_id}_{message_id} 作为 thread_id（独立 checkpointer 状态）
4. astream 迭代：process_stream_chunk 解析 chunk + __interrupt__ 审批中断处理
5. finally：保存/回滚/清理 + 广播 STREAM_COMPLETED

依赖方向：
- 引用 regenerate_context（thread_id + 无）
- 引用 regenerate_persist（_request_approval + _save_* + _cleanup_* + _rollback_*）
"""

import asyncio
import json
import logging
import time
from typing import Any

from asgiref.sync import sync_to_async

from Django_xm.apps.chat.models import ChatMessage, MessageRole
from Django_xm.apps.chat.services.chat_service import ChatService
from Django_xm.apps.chat.services.stream_helpers import (
    build_context_info,
    finalize_tool_calls,
    process_stream_chunk,
    update_usage_and_tokens,
)
from Django_xm.apps.chat.utils import _lcp_len, convert_chat_history
from Django_xm.common.event_schema import EventSource, EventType, PayloadValidationError
from Django_xm.common.realtime_events import publish_event
from Django_xm.common.sse_utils import sse_error_event

from .regenerate_context import _make_regen_thread_id
from .regenerate_persist import (
    _cleanup_streaming_flag,
    _request_approval,
    _rollback_regenerated_message,
    _save_partial_regen_content,
    _save_regenerated_message,
)

logger = logging.getLogger(__name__)


async def stream_regenerate(
    session, message, context, tool_config, preloaded_attachment_content=None, preloaded_attachment_type=None
):
    """流式重新生成，复用 ChatService 的 agent 构建和 SSE 协议。

    使用独立的 checkpointer thread_id（regen_{session_id}_{message_id}），
    这样当 agent 因审批中断时，LangGraph 状态被保存到 checkpointer，
    后续 stream_regenerate_resume 可通过 Command(resume=...) 恢复执行。

    附件预加载参数与 ChatStreamView.post 的 _preloaded_attachment_content /
    _preloaded_attachment_type 语义完全一致，确保重新生成时 agent 看到的
    附件内容结构与原始对话相同。
    """

    session_id = session.session_id
    user_id = session.user_id
    regen_thread_id = _make_regen_thread_id(session_id, message.id)

    user_content = ""
    history: list[dict[str, Any]] = list(context or [])
    if history:
        last = history[-1]
        if last.get("role") == MessageRole.USER:
            user_content = last.get("content", "")
            history = history[:-1]
        else:
            for item in reversed(history):
                if item.get("role") == MessageRole.USER:
                    user_content = item.get("content", "")
                    break

    current_message_content = ""
    tool_calls_map: dict[str, dict] = {}
    tool_args_accumulator: dict[str, str] = {}
    accumulated_reasoning: dict[str, str] = {}
    all_messages: list = []
    interrupt_info: dict[str, Any] | None = None
    # BUG N 修复：parse_approval_interrupt 需要用来跟踪已匹配的 tool_call_id
    used_tool_call_ids: set = set()
    usage_tracker = None
    token_detail_tracker = None
    cb = None
    stream_start_time = time.time()

    try:

        @sync_to_async
        def _mark_streaming():
            ChatMessage.objects.filter(id=message.id).update(is_streaming=True)

        await _mark_streaming()
    except Exception as e:
        logger.warning(f"[Regenerate] 标记 is_streaming 失败: {e}")

    mode = tool_config.get("mode") or tool_config.get("agent_type") or "agent"

    original_model = getattr(message, "model", None) or ""
    provider_id = None
    model_name = None
    if original_model and "/" in original_model:
        provider_id, model_name = original_model.split("/", 1)
    elif original_model:
        model_name = original_model

    data: dict[str, Any] = {
        "session_id": regen_thread_id,
        "message": user_content,
        "mode": mode,
        "use_tools": tool_config.get("use_tools", True),
        "use_web_search": tool_config.get("use_web_search", False),
        "use_mcp": tool_config.get("use_mcp", False),
        "selected_tools": tool_config.get("selected_tools"),
        "selected_mcp_servers": tool_config.get("selected_mcp_servers"),
        "tool_tier": tool_config.get("tool_tier", "standard"),
        "use_knowledge_base": tool_config.get("use_knowledge_base", False),
        "selected_knowledge_bases": tool_config.get("selected_knowledge_bases", []),
        "chat_history": history,
        "provider_id": provider_id,
        "model_name": model_name,
    }

    use_deep_thinking = tool_config.get("use_deep_thinking", False)
    enable_deep_thinking = False
    if use_deep_thinking and provider_id and model_name:
        from Django_xm.apps.ai_engine.services.llm_factory import model_supports_capability
        enable_deep_thinking = model_supports_capability(provider_id, model_name, "deep_thinking")
    if enable_deep_thinking:
        data["_enable_deep_thinking"] = True

    # 附件内容预加载（与 ChatStreamView.post 的 _preloaded_attachment_content /
    # _preloaded_attachment_type 语义完全一致），确保重新生成时 agent 能识别原始对话的附件
    if preloaded_attachment_content is not None:
        data["_preloaded_attachment_content"] = preloaded_attachment_content
        data["_preloaded_attachment_type"] = preloaded_attachment_type or "multimodal"

    logger.info(
        f"[Regenerate] 启动: session={session_id}, message={message.id}, "
        f"regen_thread={regen_thread_id}, model={original_model}, tool_tier={data['tool_tier']}"
    )

    yield f"data: {json.dumps({'type': 'start', 'message': '重新生成中...'}, ensure_ascii=False)}\n\n"

    try:
        from langchain_core.messages import HumanMessage

        from Django_xm.apps.ai_engine.config import settings as ai_settings
        from Django_xm.apps.ai_engine.services.cost_tracker import create_token_detail_tracker
        from Django_xm.apps.ai_engine.services.token_counter import TokenUsageCallbackHandler
        from Django_xm.apps.ai_engine.services.usage_tracker import create_usage_tracker

        tracker_model_id = data.get("model_name") or ai_settings.openai_model
        usage_tracker = create_usage_tracker(model_id=tracker_model_id)
        token_detail_tracker = create_token_detail_tracker()

        chat_service = ChatService(user_id=user_id, thread_id=regen_thread_id)

        tools = await chat_service._get_tools(data)
        model_instance = ChatService._resolve_model_instance(data)
        tool_config_for_agent = chat_service._build_tool_config(data)

        agent, thread_config, _use_checkpointer = await chat_service._create_agent_with_memory(
            data,
            prompt_mode=mode,
            model_instance=model_instance,
            tool_config=tool_config_for_agent,
            tools=tools,
        )

        langchain_history = convert_chat_history(history)
        messages = list(langchain_history) if langchain_history else []
        if preloaded_attachment_type == "multimodal" and preloaded_attachment_content is not None:
            human_msg = HumanMessage(content=preloaded_attachment_content)
        else:
            human_msg = HumanMessage(content=user_content)
        messages.append(human_msg)
        graph_input = {"messages": messages}

        stream_config: dict[str, Any] = {"recursion_limit": 500}
        if thread_config:
            stream_config = {**stream_config, **thread_config}

        tool_call_count: dict[str, int] = {}

        with TokenUsageCallbackHandler() as cb:
            stream_config["callbacks"] = [cb]
            async for chunk in agent.graph.astream(
                graph_input,
                config=stream_config,
                stream_mode=["messages", "updates"],
            ):
                if isinstance(chunk, tuple) and len(chunk) == 2:
                    mode_name, mode_data = chunk
                else:
                    mode_name, mode_data = "messages", chunk

                if mode_name == "updates":
                    if isinstance(mode_data, dict) and "__interrupt__" in mode_data:
                        from Django_xm.apps.chat.services.stream_helpers import parse_approval_interrupt
                        from Django_xm.apps.tools.base import is_approval_interrupt

                        interrupts = mode_data["__interrupt__"]
                        if interrupts:
                            from Django_xm.apps.chat.services.stream_helpers import extract_interrupt_ids

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
                                approval_data["message_id"] = message.id
                                extra_dict = approval_data.get("extra") or {}
                                if not isinstance(extra_dict, dict):
                                    extra_dict = {}
                                extra_dict["graph_interrupt_id"] = new_graph_interrupt_id
                                extra_dict["langgraph_resume_id"] = new_langgraph_resume_id
                                extra_dict["tool_call_id"] = tool_call_id
                                extra_dict["message_id"] = message.id
                                extra_dict["regenerate"] = {
                                    "thread_id": regen_thread_id,
                                    "message_id": message.id,
                                }
                                approval_data["extra"] = extra_dict
                                if interrupt_info is None:
                                    interrupt_info = {
                                        "tool_name": tool_name,
                                        "interrupt_id": graph_intr_id,
                                        "graph_interrupt_id": new_graph_interrupt_id,
                                        "langgraph_resume_id": new_langgraph_resume_id,
                                    }
                                logger.info(
                                    f"[Regenerate] 审批中断: tool={tool_name}, "
                                    f"interrupt_id={tool_call_id}, "
                                    f"graph_interrupt_id={new_graph_interrupt_id}, "
                                    f"langgraph_resume_id={new_langgraph_resume_id}"
                                )
                                yield f"data: {json.dumps({'type': 'approval', 'data': approval_data}, ensure_ascii=False)}\n\n"  # noqa: E501

                                if tool_call_id and session_id:
                                    await _request_approval(
                                        tool_call_id,
                                        session_id,
                                        message.id,
                                        approval_data,
                                        data,
                                        regen_thread_id,
                                    )
                    continue

                all_messages.append(mode_data if not isinstance(mode_data, tuple) else mode_data[0])

                try:
                    for event in process_stream_chunk(
                        mode_data,
                        tool_calls_map,
                        current_message_content,
                        tool_call_count=tool_call_count,
                        lcp_func=_lcp_len,
                        accumulated_reasoning=accumulated_reasoning,
                        tool_args_accumulator=tool_args_accumulator,
                        mode=mode,
                        enable_deep_thinking=enable_deep_thinking,
                        session_id=session_id,
                        message_id=message.id,
                        module_id=str(session_id or ""),
                    ):
                        if event.get("type") == "chunk":
                            current_message_content += event.get("content", "")
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except Exception as chunk_err:
                    logger.warning(f"[Regenerate] 处理流式 chunk 失败: {chunk_err}")
                    continue

                await asyncio.sleep(0.01)

        update_usage_and_tokens(cb, usage_tracker, token_detail_tracker)

        finalize_tool_calls(
            all_messages,
            tool_calls_map,
            tool_args_accumulator,
            session_id=session_id,
            message_id=message.id,
        )

        if interrupt_info is not None:
            logger.info(f"[Regenerate] 审批中断: tool={interrupt_info.get('tool_name')}")
            yield f"data: {json.dumps({'type': 'interrupted', 'data': {'interrupt_id': interrupt_info.get('interrupt_id'), 'tool_name': interrupt_info.get('tool_name'), 'reason': 'approval_required'}}, ensure_ascii=False)}\n\n"  # noqa: E501
        else:
            context_info = build_context_info(usage_tracker, token_detail_tracker, stream_start_time)
            yield f"data: {json.dumps({'type': 'context', 'data': context_info}, ensure_ascii=False)}\n\n"

        yield f"data: {json.dumps({'type': 'end', 'message': '重新生成完成'}, ensure_ascii=False)}\n\n"

    except asyncio.CancelledError:
        logger.info(f"[Regenerate] 客户端断开: session_id={session_id}, message_id={message.id}")
        try:
            await asyncio.shield(
                _save_regenerated_message(
                    message.id,
                    current_message_content,
                    tool_calls_map,
                    accumulated_reasoning,
                    usage_tracker,
                )
            )
        except asyncio.CancelledError:
            logger.warning(f"[Regenerate] shield 保存被二次取消: message_id={message.id}")
        except Exception:
            logger.exception("[Regenerate] shield 保存失败")
        try:
            await asyncio.shield(_cleanup_streaming_flag(message.id))
        except Exception:  # noqa: S110
            pass
        raise
    except Exception as e:
        logger.exception("[Regenerate] 重新生成失败")
        yield sse_error_event("regenerate_error", f"重新生成失败: {e!s}")
    finally:
        has_content = bool(current_message_content and current_message_content.strip())
        has_tool_calls = bool(tool_calls_map)
        is_interrupted = interrupt_info is not None

        if is_interrupted:
            try:
                await _save_partial_regen_content(
                    message.id,
                    current_message_content,
                    tool_calls_map,
                    accumulated_reasoning,
                )
            except Exception as save_err:
                logger.warning(f"[Regenerate] 审批中断保存部分内容失败: {save_err}")
            try:
                await _cleanup_streaming_flag(message.id)
            except Exception:  # noqa: S110
                pass
        elif has_content or has_tool_calls:
            try:
                await _save_regenerated_message(
                    message.id,
                    current_message_content,
                    tool_calls_map,
                    accumulated_reasoning,
                    usage_tracker,
                )
            except Exception:
                logger.exception("[Regenerate] 保存重新生成内容失败")
        else:
            try:
                await _rollback_regenerated_message(message.id)
            except Exception:
                logger.exception("[Regenerate] 回滚版本失败")

        if not is_interrupted:
            try:
                await _cleanup_streaming_flag(message.id)
            except Exception:  # noqa: S110
                pass

        try:
            from Django_xm.apps.ai_engine.services.checkpointer_factory import release_async_checkpointer

            await release_async_checkpointer()
        except Exception:  # noqa: S110
            pass

        if session_id and not is_interrupted:
            try:
                await publish_event(
                    EventType.STREAM_COMPLETED,
                    {
                        "source": EventSource.CHAT,
                        "source_id": session_id,
                        "session_id": session_id,
                        "message_id": str(message.id),
                        "is_streaming": False,
                        "finalized": False,
                        "regenerate_failed": not (has_content or has_tool_calls),
                    },
                    session_id=session_id,
                )
            except PayloadValidationError:
                logger.exception(
                    f"[Regenerate] STREAM_COMPLETED payload 校验失败，跳过广播: "
                    f"session_id={session_id}, message_id={message.id}",
                )
            except Exception as pub_err:
                logger.warning(f"[Regenerate] 广播 stream_completed 失败: {pub_err}")

        yield "data: [DONE]\n\n"