"""重新生成服务层

封装"重新生成"功能的核心逻辑：
- prepare_context: 构建重新生成所需的对话上下文
- resolve_tool_config: 解析工具配置（优先 session，回退原消息 tool_calls）
- archive_current_version: 归档当前版本到 versions 数组，并创建新空版本
- stream_regenerate: 复用 ChatService 的 agent 构建和 SSE 流式输出
- stream_regenerate_resume: 重新生成场景下的审批恢复流式输出

设计原则：
- 不修改 chat_service.py，通过 import 复用其逻辑
- SSE 事件格式与原 chat_resume_service.stream_chat_resume_generator 保持一致（Phase 1 已移除该模块，后续 Phase 将重新实现）
- 异步兼容，不阻塞请求；环境变量不硬编码
- 重新生成使用独立的 checkpointer thread_id（格式: regen_{session_id}_{message_id}），
  以支持审批中断后的 LangGraph 状态恢复
"""

import json
import time
import asyncio
import logging
from typing import Any, Dict, List, Optional

from asgiref.sync import sync_to_async
from django.utils import timezone

from Django_xm.apps.chat.models import ChatMessage, MessageRole
from Django_xm.apps.chat.services.chat_service import ChatService
from Django_xm.apps.chat.services.stream_helpers import (
    process_stream_chunk,
    build_context_info,
    update_usage_and_tokens,
    finalize_tool_calls,
)
from Django_xm.apps.chat.utils import convert_chat_history, _lcp_len
from Django_xm.apps.approvals.models import Approval
from Django_xm.apps.approvals.services import approval_service
from Django_xm.common.realtime_events import publish_event
from Django_xm.common.event_schema import EventType, EventSource, PayloadValidationError
from Django_xm.common.sse_utils import sse_error_event

logger = logging.getLogger(__name__)

_REGEN_THREAD_PREFIX = "regen_"


def _make_regen_thread_id(session_id: str, message_id: int) -> str:
    return f"{_REGEN_THREAD_PREFIX}{session_id}_{message_id}"


def is_regen_thread_id(thread_id: Optional[str]) -> bool:
    return bool(thread_id and thread_id.startswith(_REGEN_THREAD_PREFIX))


def prepare_context(session, message_id: int) -> Dict[str, Any]:
    """构建重新生成的对话上下文。

    流程：
    1. 取 message_id 对应的 assistant 消息
    2. 找到该 assistant 消息的前一条 user 消息（按 created_at 排序）
    3. 查询前一条 user 消息关联的附件，重新构建含附件全文的 user_content
    4. 构建对话上下文：前一条 user 消息（含附件内容）+ 之前的历史消息

    返回：
        {
            'context': List[Dict[str, Any]],  # 对话历史消息列表
            'preloaded_attachment_content': Optional[Any],  # 附件内容（str 或 list）
            'preloaded_attachment_type': Optional[str],  # 'text' 或 'multimodal'
        }
        若未找到消息，返回空字典 {}。

    附件处理与 ChatStreamView.post 的预处理逻辑完全对齐：
        - text + RAG: 构造复合 content 作为 multimodal 传递
        - text 无 RAG: 使用 content 作为消息内容
        - multimodal: 使用原始 content（列表，保留图片信息）
    """
    assistant_msg = ChatMessage.objects.filter(
        id=message_id,
        session=session,
        role=MessageRole.ASSISTANT,
        is_deleted=False,
    ).first()
    if not assistant_msg:
        logger.warning(f"[Regenerate] 未找到 assistant 消息: message_id={message_id}")
        return {}

    prev_user_msg = ChatMessage.objects.filter(
        session=session,
        role=MessageRole.USER,
        is_deleted=False,
        created_at__lt=assistant_msg.created_at,
    ).order_by('-created_at').first()
    if not prev_user_msg:
        logger.warning(f"[Regenerate] 未找到 assistant 消息前的 user 消息: message_id={message_id}")
        return {}

    # 查询前一条 user 消息关联的附件，重新构建含附件全文的 user_content
    # 与 ChatStreamView.post 的附件预处理逻辑（views_chat.py:370-387）完全对齐
    user_content = prev_user_msg.content or ''
    preloaded_attachment_content: Optional[Any] = None
    preloaded_attachment_type: Optional[str] = None
    try:
        attachment_ids = list(prev_user_msg.attachments.values_list('id', flat=True))
        if attachment_ids:
            from Django_xm.apps.attachments.services.attachment_content_service import AttachmentService
            att_svc = AttachmentService()
            built_content = att_svc.build_user_content(user_content, attachment_ids)
            if built_content["type"] == "text":
                rag_context = built_content.get("_rag_context")
                if rag_context:
                    # RAG 上下文存在：构造复合 content 作为 multimodal 传递，避免 preloaded text 路径丢弃 RAG
                    preloaded_attachment_content = [
                        {"type": "text", "text": built_content["content"]},
                        {"type": "text", "text": f"\n\n---\n{rag_context}\n---\n"},
                    ]
                    preloaded_attachment_type = 'multimodal'
                else:
                    # 无 RAG 上下文：使用 content 作为消息内容
                    user_content = built_content["content"]
                    preloaded_attachment_type = 'text'
            else:
                # multimodal 类型：使用原始 content（列表，保留图片信息）
                preloaded_attachment_content = built_content["content"]
                preloaded_attachment_type = 'multimodal'
            logger.info(f"[Regenerate] 恢复附件内容: msg={prev_user_msg.id}, attachment_ids={attachment_ids}, type={preloaded_attachment_type}")
    except Exception as e:
        logger.warning(f"[Regenerate] 恢复附件内容失败: {e}", exc_info=True)

    history_msgs = ChatMessage.objects.filter(
        session=session,
        is_deleted=False,
        created_at__lt=prev_user_msg.created_at,
        role__in=[MessageRole.USER, MessageRole.ASSISTANT],
    ).order_by('created_at')

    context: List[Dict[str, Any]] = []
    for msg in history_msgs:
        context.append({
            'role': msg.role,
            'content': msg.content or '',
        })
    context.append({
        'role': MessageRole.USER,
        'content': user_content,
    })
    return {
        'context': context,
        'preloaded_attachment_content': preloaded_attachment_content,
        'preloaded_attachment_type': preloaded_attachment_type,
    }


def resolve_tool_config(session, original_message) -> Dict[str, Any]:
    """解析重新生成所需的工具配置。

    配置来源（与 ChatStreamView.post 持久化的字段一致）：
    - selected_tools: 从 session.selected_tools 读取（用户在 UI 选择的工具列表）
    - selected_knowledge_bases: 从 session.selected_knowledge_bases 读取
    - tool_tier: 当 selected_tools 为空时决定返回的工具集范围

    设计说明：
    - 不从原消息 tool_calls 反推 selected_tools 或 tool_tier，因为 tool_calls 只记录
      实际被调用过的工具，不是原始可用工具集，反推会缩窄工具范围
    - 当 selected_tools 为空时（历史会话或用户未显式选择），使用 extended tier
      返回完整的工具集，避免工具集异常缩减影响 agent 能力
    - original_message 参数保留为了未来可能的扩展（如从消息恢复模型配置），
      当前仅用于日志记录
    """
    from Django_xm.apps.tools import TOOL_TIER_EXTENDED

    use_tools = True
    use_web_search = False
    use_mcp = False
    selected_mcp_servers: Optional[List[str]] = None
    agent_type = getattr(session, 'mode', None) or 'agent'

    # 直接从 session 字段读取（ChatSession 无 metadata 字段）
    selected_tools = list(getattr(session, 'selected_tools', None) or []) or None
    selected_knowledge_bases = list(getattr(session, 'selected_knowledge_bases', None) or [])
    use_knowledge_base = bool(selected_knowledge_bases)

    # 当 selected_tools 为空时，使用 extended tier 返回完整的工具集
    # 避免历史会话（session.selected_tools 为空）工具集异常缩减为 4 个
    tool_tier = TOOL_TIER_EXTENDED

    if original_message is not None:
        logger.debug(
            f"[Regenerate] 工具配置: selected_tools={'自定义' if selected_tools else '全部'}, "
            f"knowledge_bases={selected_knowledge_bases}, tool_tier={tool_tier}"
        )

    return {
        'selected_tools': selected_tools,
        'selected_mcp_servers': selected_mcp_servers,
        'use_tools': use_tools,
        'use_web_search': use_web_search,
        'use_mcp': use_mcp,
        'use_knowledge_base': use_knowledge_base,
        'selected_knowledge_bases': selected_knowledge_bases,
        'agent_type': agent_type,
        'mode': agent_type,
        'tool_tier': tool_tier,
    }


def archive_current_version(message) -> None:
    """将当前版本归档到 versions 数组，并创建新空版本。"""
    if message.versions is None:
        message.versions = []

    archived = {
        'content': message.content or '',
        'tool_calls': list(message.tool_calls or []),
        'sources': list(message.sources or []),
        'reasoning': message.reasoning if message.reasoning else {},
        'created_at': message.created_at.isoformat() if message.created_at else '',
        'model': message.model or '',
    }
    message.versions.append(archived)

    message.versions.append({
        'content': '',
        'tool_calls': [],
        'sources': [],
        'reasoning': '',
        'created_at': timezone.now().isoformat(),
        'model': '',
    })

    message.current_version = len(message.versions) - 1

    message.content = ''
    message.tool_calls = []
    message.sources = []
    message.reasoning = {}
    message.approval = {}

    message.save()


async def stream_regenerate(session, message, context, tool_config,
                            preloaded_attachment_content=None,
                            preloaded_attachment_type=None):
    """流式重新生成，复用 ChatService 的 agent 构建和 SSE 协议。

    使用独立的 checkpointer thread_id（regen_{session_id}_{message_id}），
    这样当 agent 因审批中断时，LangGraph 状态被保存到 checkpointer，
    后续 stream_regenerate_resume 可通过 Command(resume=...) 恢复执行。

    附件预加载参数与 ChatStreamView.post 的 _preloaded_attachment_content /
    _preloaded_attachment_type 语义完全一致，确保重新生成时 agent 看到的
    附件内容结构与原始对话相同。
    """
    from Django_xm.apps.chat.services.stream_helpers import _publish_tool_lifecycle_event

    session_id = session.session_id
    user_id = session.user_id
    regen_thread_id = _make_regen_thread_id(session_id, message.id)

    user_content = ''
    history: List[Dict[str, Any]] = list(context or [])
    if history:
        last = history[-1]
        if last.get('role') == MessageRole.USER:
            user_content = last.get('content', '')
            history = history[:-1]
        else:
            for item in reversed(history):
                if item.get('role') == MessageRole.USER:
                    user_content = item.get('content', '')
                    break

    current_message_content = ""
    tool_calls_map: Dict[str, Dict] = {}
    tool_args_accumulator: Dict[str, str] = {}
    accumulated_reasoning: Dict[str, str] = {}
    all_messages: List = []
    interrupt_info: Optional[Dict[str, Any]] = None
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

    mode = tool_config.get('mode') or tool_config.get('agent_type') or 'agent'

    original_model = getattr(message, 'model', None) or ''
    provider_id = None
    model_name = None
    if original_model and '/' in original_model:
        provider_id, model_name = original_model.split('/', 1)
    elif original_model:
        model_name = original_model

    data: Dict[str, Any] = {
        'session_id': regen_thread_id,
        'message': user_content,
        'mode': mode,
        'use_tools': tool_config.get('use_tools', True),
        'use_web_search': tool_config.get('use_web_search', False),
        'use_mcp': tool_config.get('use_mcp', False),
        'selected_tools': tool_config.get('selected_tools'),
        'selected_mcp_servers': tool_config.get('selected_mcp_servers'),
        'tool_tier': tool_config.get('tool_tier', 'standard'),
        'use_knowledge_base': tool_config.get('use_knowledge_base', False),
        'selected_knowledge_bases': tool_config.get('selected_knowledge_bases', []),
        'chat_history': history,
        'provider_id': provider_id,
        'model_name': model_name,
    }

    # 附件内容预加载（与 ChatStreamView.post 的 _preloaded_attachment_content /
    # _preloaded_attachment_type 语义完全一致），确保重新生成时 agent 能识别原始对话的附件
    if preloaded_attachment_content is not None:
        data['_preloaded_attachment_content'] = preloaded_attachment_content
        data['_preloaded_attachment_type'] = preloaded_attachment_type or 'multimodal'

    logger.info(f"[Regenerate] 启动: session={session_id}, message={message.id}, regen_thread={regen_thread_id}, model={original_model}, tool_tier={data['tool_tier']}")

    yield f"data: {json.dumps({'type': 'start', 'message': '重新生成中...'}, ensure_ascii=False)}\n\n"

    try:
        from Django_xm.apps.ai_engine.services.token_counter import TokenUsageCallbackHandler
        from Django_xm.apps.ai_engine.services.usage_tracker import create_usage_tracker
        from Django_xm.apps.ai_engine.services.cost_tracker import create_token_detail_tracker
        from Django_xm.apps.ai_engine.config import settings as ai_settings
        from langchain_core.messages import HumanMessage

        tracker_model_id = data.get('model_name') or ai_settings.openai_model
        usage_tracker = create_usage_tracker(model_id=tracker_model_id)
        token_detail_tracker = create_token_detail_tracker()

        chat_service = ChatService(user_id=user_id, thread_id=regen_thread_id)

        tools = await chat_service._get_tools(data)
        model_instance = ChatService._resolve_model_instance(data)
        tool_config_for_agent = chat_service._build_tool_config(data)

        agent, thread_config, use_checkpointer = await chat_service._create_agent_with_memory(
            data, prompt_mode=mode, model_instance=model_instance,
            tool_config=tool_config_for_agent, tools=tools,
        )

        langchain_history = convert_chat_history(history)
        messages = list(langchain_history) if langchain_history else []
        if preloaded_attachment_type == 'multimodal' and preloaded_attachment_content is not None:
            human_msg = HumanMessage(content=preloaded_attachment_content)
        else:
            human_msg = HumanMessage(content=user_content)
        messages.append(human_msg)
        graph_input = {"messages": messages}

        stream_config: Dict[str, Any] = {"recursion_limit": 500}
        if thread_config:
            stream_config = {**stream_config, **thread_config}

        tool_call_count: Dict[str, int] = {}

        with TokenUsageCallbackHandler() as cb:
            stream_config["callbacks"] = [cb]
            async for chunk in agent.graph.astream(
                graph_input, config=stream_config, stream_mode=["messages", "updates"],
            ):
                if isinstance(chunk, tuple) and len(chunk) == 2:
                    mode_name, mode_data = chunk
                else:
                    mode_name, mode_data = "messages", chunk

                if mode_name == "updates":
                    if isinstance(mode_data, dict) and "__interrupt__" in mode_data:
                        from langgraph.types import Interrupt
                        from Django_xm.apps.tools.base import is_approval_interrupt
                        from Django_xm.apps.chat.services.stream_helpers import parse_approval_interrupt
                        interrupts = mode_data["__interrupt__"]
                        if interrupts:
                            # BUG N 修复：使用 parse_approval_interrupt 解析批量 interrupt
                            # 原实现直接 interrupt_value.get("tool_name", "unknown")，批量格式下
                            # 顶层无 tool_name 字段（在 requests 数组内），导致 tool=unknown
                            from Django_xm.apps.chat.services.stream_helpers import extract_interrupt_ids
                            batch_approval_data = []
                            for intr in interrupts:
                                # 统一提取 interrupt_value + 批次 ID + LangGraph 恢复 ID
                                interrupt_value, batch_id, resume_id = extract_interrupt_ids(intr)
                                graph_intr_id = resume_id  # 兼容变量名（= intr.id）

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
                                tool_name = approval_data.get('tool_name', 'unknown')
                                tool_call_id = approval_data.get('tool_call_id', '') or graph_intr_id
                                new_langgraph_resume_id = approval_data.get('langgraph_resume_id', graph_intr_id)
                                new_graph_interrupt_id = approval_data.get('graph_interrupt_id', graph_intr_id)
                                # 注入 message_id 与 regenerate 上下文，前端可据此精确定位消息
                                # （重新生成场景下审批不应跑到最后一条消息上）
                                approval_data['message_id'] = message.id
                                extra_dict = approval_data.get('extra') or {}
                                if not isinstance(extra_dict, dict):
                                    extra_dict = {}
                                extra_dict['graph_interrupt_id'] = new_graph_interrupt_id
                                extra_dict['langgraph_resume_id'] = new_langgraph_resume_id
                                extra_dict['tool_call_id'] = tool_call_id
                                extra_dict['message_id'] = message.id
                                extra_dict['regenerate'] = {
                                    'thread_id': regen_thread_id,
                                    'message_id': message.id,
                                }
                                approval_data['extra'] = extra_dict
                                # 记录第一条审批的 tool_name，供后续日志使用
                                if interrupt_info is None:
                                    interrupt_info = {
                                        "tool_name": tool_name,
                                        "interrupt_id": graph_intr_id,
                                        "graph_interrupt_id": new_graph_interrupt_id,
                                        "langgraph_resume_id": new_langgraph_resume_id,
                                    }
                                logger.info(
                                    f"[Regenerate] 审批中断: tool={tool_name}, "
                                    f"interrupt_id={tool_call_id}, graph_interrupt_id={new_graph_interrupt_id}, "
                                    f"langgraph_resume_id={new_langgraph_resume_id}"
                                )
                                yield f"data: {json.dumps({'type': 'approval', 'data': approval_data}, ensure_ascii=False)}\n\n"

                                if tool_call_id and session_id:
                                    await _request_approval(
                                        tool_call_id, session_id, message.id,
                                        approval_data, data, regen_thread_id,
                                    )
                    continue

                all_messages.append(mode_data if not isinstance(mode_data, tuple) else mode_data[0])

                try:
                    for event in process_stream_chunk(
                        mode_data, tool_calls_map, current_message_content,
                        tool_call_count=tool_call_count,
                        lcp_func=_lcp_len,
                        accumulated_reasoning=accumulated_reasoning,
                        tool_args_accumulator=tool_args_accumulator,
                        mode=mode,
                        session_id=session_id,
                        message_id=message.id,
                    ):
                        if event.get("type") == "chunk":
                            current_message_content += event.get("content", "")
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                        # tool/tool_result 事件由 stream_helpers 内部直接发布到 WebSocket
                except Exception as chunk_err:
                    logger.warning(f"[Regenerate] 处理流式 chunk 失败: {chunk_err}")
                    continue

                await asyncio.sleep(0.01)

        update_usage_and_tokens(cb, usage_tracker, token_detail_tracker)

        # finalize_tool_calls 内部直接通过 _publish_tool_lifecycle_event 发布 WebSocket 事件，
        # 返回事件列表为空，无需再 yield 到 SSE 流或同步到 session
        finalize_tool_calls(
            all_messages, tool_calls_map, tool_args_accumulator,
            session_id=session_id, message_id=message.id,
        )

        if interrupt_info is not None:
            logger.info(f"[Regenerate] 审批中断: tool={interrupt_info.get('tool_name')}")
            yield f"data: {json.dumps({'type': 'interrupted', 'data': {'interrupt_id': interrupt_info.get('interrupt_id'), 'tool_name': interrupt_info.get('tool_name'), 'reason': 'approval_required'}}, ensure_ascii=False)}\n\n"
        else:
            context_info = build_context_info(usage_tracker, token_detail_tracker, stream_start_time)
            yield f"data: {json.dumps({'type': 'context', 'data': context_info}, ensure_ascii=False)}\n\n"

        yield f"data: {json.dumps({'type': 'end', 'message': '重新生成完成'}, ensure_ascii=False)}\n\n"

    except asyncio.CancelledError:
        logger.info(f"[Regenerate] 客户端断开: session_id={session_id}, message_id={message.id}")
        try:
            await asyncio.shield(_save_regenerated_message(
                message.id, current_message_content, tool_calls_map,
                accumulated_reasoning, usage_tracker,
            ))
        except asyncio.CancelledError:
            logger.warning(f"[Regenerate] shield 保存被二次取消: message_id={message.id}")
        except Exception as cleanup_err:
            logger.error(f"[Regenerate] shield 保存失败: {cleanup_err}")
        try:
            await asyncio.shield(_cleanup_streaming_flag(message.id))
        except Exception:
            pass
        raise
    except Exception as e:
        logger.error(f"[Regenerate] 重新生成失败: {e}", exc_info=True)
        yield sse_error_event("regenerate_error", f"重新生成失败: {str(e)}")
    finally:
        has_content = bool(current_message_content and current_message_content.strip())
        has_tool_calls = bool(tool_calls_map)
        is_interrupted = interrupt_info is not None

        if is_interrupted:
            try:
                await _save_partial_regen_content(
                    message.id, current_message_content, tool_calls_map,
                    accumulated_reasoning,
                )
            except Exception as save_err:
                logger.warning(f"[Regenerate] 审批中断保存部分内容失败: {save_err}")
            try:
                await _cleanup_streaming_flag(message.id)
            except Exception:
                pass
        elif has_content or has_tool_calls:
            try:
                await _save_regenerated_message(
                    message.id, current_message_content, tool_calls_map,
                    accumulated_reasoning, usage_tracker,
                )
            except Exception as save_err:
                logger.error(f"[Regenerate] 保存重新生成内容失败: {save_err}")
        else:
            try:
                await _rollback_regenerated_message(message.id)
            except Exception as rb_err:
                logger.error(f"[Regenerate] 回滚版本失败: {rb_err}")

        if not is_interrupted:
            try:
                await _cleanup_streaming_flag(message.id)
            except Exception:
                pass

        try:
            from Django_xm.apps.ai_engine.services.checkpointer_factory import release_async_checkpointer
            await release_async_checkpointer()
        except Exception:
            pass

        if session_id and not is_interrupted:
            try:
                await publish_event(
                    EventType.STREAM_COMPLETED,
                    {
                        'source': EventSource.CHAT,
                        'source_id': session_id,
                        'session_id': session_id,
                        'message_id': str(message.id),
                        'is_streaming': False,
                        'finalized': False,
                        'regenerate_failed': not (has_content or has_tool_calls),
                    },
                    session_id=session_id,
                )
            except PayloadValidationError:
                logger.error(
                    f"[Regenerate] STREAM_COMPLETED payload 校验失败，跳过广播: "
                    f"session_id={session_id}, message_id={message.id}",
                    exc_info=True
                )
            except Exception as pub_err:
                logger.warning(f"[Regenerate] 广播 stream_completed 失败: {pub_err}")

        yield "data: [DONE]\n\n"


async def stream_regenerate_resume(request, approval, resume_value, session_id, request_data=None,
                                  graph_interrupt_id=None, langgraph_resume_id=None):
    """重新生成场景下的审批恢复流式生成器。

    与 stream_chat_resume_generator 的区别：
    1. 使用 regen_{session_id}_{message_id} 作为 thread_id（而非 session_id）
    2. 使用 approval.extra 中保存的 regenerate 配置重建 agent
    3. 流结束后调用 _save_regenerated_message 保存到 versions
    4. 不依赖 ChatService 的正常会话 checkpointer 恢复（regenerate 有独立状态）

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
    logger.info(f"[RegenerateResume] 开始: approval={approval.interrupt_id if approval else None}, session={session_id}")

    if request_data is None:
        try:
            request_data = dict(request.data) if hasattr(request, 'data') else {}
        except Exception as req_err:
            logger.warning(f"[RegenerateResume] 读取 request.data 失败: {req_err}")
            request_data = {}

    from Django_xm.apps.chat.services.stream_helpers import _publish_tool_lifecycle_event

    interrupt_id = approval.interrupt_id
    regen_extra = (approval.extra or {}).get('regenerate', {})
    if not isinstance(regen_extra, dict):
        regen_extra = {}

    regen_thread_id = regen_extra.get('thread_id') or (approval.extra or {}).get('regenerate_thread_id')
    regen_message_id = regen_extra.get('message_id')
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

    persisted_tool_config = (approval.extra or {}).get('tool_config', {})
    persisted_model_config = (approval.extra or {}).get('model_config', {})

    provider_id = request_data.get('provider_id') or persisted_model_config.get('provider_id')
    model_name = request_data.get('model_name') or persisted_model_config.get('model_name')
    use_deep_thinking = request_data.get('use_deep_thinking', persisted_model_config.get('use_deep_thinking', False))
    special_params = request_data.get('special_params') or persisted_model_config.get('special_params')
    temperature = request_data.get('temperature') or persisted_model_config.get('temperature')
    max_tokens = request_data.get('max_tokens') or persisted_model_config.get('max_tokens')

    use_tools = request_data.get('use_tools', persisted_tool_config.get('use_tools', True))
    use_web_search = request_data.get('use_web_search', persisted_tool_config.get('use_web_search', False))
    use_mcp = request_data.get('use_mcp', persisted_tool_config.get('use_mcp', False))
    selected_mcp_servers = request_data.get('selected_mcp_servers') or persisted_tool_config.get('selected_mcp_servers')
    selected_tools = request_data.get('selected_tools') or persisted_tool_config.get('selected_tools')
    use_knowledge_base = request_data.get('use_knowledge_base', persisted_tool_config.get('use_knowledge_base', False))
    selected_knowledge_bases = request_data.get('selected_knowledge_bases') or persisted_tool_config.get('selected_knowledge_bases', [])
    tool_tier = persisted_tool_config.get('tool_tier', 'standard')

    logger.info(f"[RegenerateResume] 配置: thread={regen_thread_id}, model={model_name}, tool_tier={tool_tier}, tools={selected_tools}")

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

        data: Dict[str, Any] = {
            'session_id': regen_thread_id,
            'mode': 'agent',
            'use_tools': use_tools,
            'use_web_search': use_web_search,
            'use_mcp': use_mcp,
            'selected_mcp_servers': selected_mcp_servers,
            'selected_tools': selected_tools,
            'use_knowledge_base': use_knowledge_base,
            'selected_knowledge_bases': selected_knowledge_bases,
            'tool_tier': tool_tier,
            'provider_id': provider_id,
            'model_name': model_name,
            'use_deep_thinking': use_deep_thinking,
            'special_params': special_params,
            'temperature': temperature,
            'max_tokens': max_tokens,
        }

        enable_deep_thinking = use_deep_thinking and provider_id and model_name
        if enable_deep_thinking:
            from Django_xm.apps.ai_engine.services.llm_factory import model_supports_capability
            enable_deep_thinking = model_supports_capability(provider_id, model_name, 'deep_thinking')
        if enable_deep_thinking:
            data['_enable_deep_thinking'] = True

        # Command(resume=...) 的 KEY 必须是 LangGraph 真正的 interrupt_id（intr.id），
        # 而非批次 UUID（graph_interrupt_id）或 tool_call_id（approval.interrupt_id）。
        # langgraph_resume_id 由调用方从 approval.extra 透传，缺失时回退到 approval.interrupt_id。
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
            data, prompt_mode='agent', model_instance=model_instance,
            tool_config=tool_config_for_agent, tools=tools,
        )

        if not use_checkpointer or not thread_config:
            logger.error("[RegenerateResume] checkpointer 不可用")
            yield sse_error_event("approval_error", "无法恢复重新生成状态：checkpointer 不可用")
            return

        logger.info(f"[RegenerateResume] Agent 创建成功，开始流式恢复...")

        tool_calls_map: Dict[str, Dict] = {}
        used_tool_call_ids = set()
        tool_call_count: Dict[str, int] = {}
        accumulated_reasoning: Dict[str, str] = {}
        tool_args_accumulator: Dict[str, str] = {}
        all_messages: List = []

        yield f"data: {json.dumps({'type': 'start', 'message': '审批恢复，继续生成...'}, ensure_ascii=False)}\n\n"

        stream_config = thread_config
        from Django_xm.apps.ai_engine.services.token_counter import TokenUsageCallbackHandler
        from Django_xm.apps.ai_engine.services.usage_tracker import create_usage_tracker
        from Django_xm.apps.ai_engine.services.cost_tracker import create_token_detail_tracker
        from Django_xm.apps.ai_engine.config import settings as ai_settings
        from langchain_core.messages import AIMessage as LCAIMessage, ToolMessage as LCToolMessage

        usage_tracker = create_usage_tracker(model_id=model_name or ai_settings.openai_model)
        token_detail_tracker = create_token_detail_tracker()
        cb = None

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
                            from langgraph.types import Interrupt
                            from Django_xm.apps.tools.base import is_approval_interrupt
                            from Django_xm.apps.chat.services.stream_helpers import (
                                parse_approval_interrupt,
                                extract_interrupt_ids,
                            )
                            interrupts = mode_data["__interrupt__"]
                            if interrupts:
                                # 使用 parse_approval_interrupt 解析批量 interrupt，并通过
                                # extract_interrupt_ids 统一提取批次 ID 与 LangGraph 恢复 ID。
                                batch_approval_data = []
                                for intr in interrupts:
                                    # 统一提取 interrupt_value + 批次 ID + LangGraph 恢复 ID
                                    interrupt_value, batch_id, resume_id = extract_interrupt_ids(intr)
                                    graph_intr_id = resume_id  # 兼容变量名（= intr.id）

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
                                    tool_name = approval_data.get('tool_name', 'unknown')
                                    tool_call_id = approval_data.get('tool_call_id', '') or graph_intr_id
                                    new_langgraph_resume_id = approval_data.get('langgraph_resume_id', graph_intr_id)
                                    new_graph_interrupt_id = approval_data.get('graph_interrupt_id', graph_intr_id)
                                    # 注入 message_id 与 regenerate 上下文
                                    approval_data['message_id'] = regen_message_id
                                    extra_dict = approval_data.get('extra') or {}
                                    if not isinstance(extra_dict, dict):
                                        extra_dict = {}
                                    extra_dict['graph_interrupt_id'] = new_graph_interrupt_id
                                    extra_dict['langgraph_resume_id'] = new_langgraph_resume_id
                                    extra_dict['tool_call_id'] = tool_call_id
                                    extra_dict['message_id'] = regen_message_id
                                    extra_dict['regenerate'] = {
                                        'thread_id': regen_thread_id,
                                        'message_id': regen_message_id,
                                    }
                                    approval_data['extra'] = extra_dict
                                    ended_by_interrupt = True
                                    if graph_intr_id:
                                        new_interrupt_ids.add(graph_intr_id)
                                    logger.info(
                                        f"[RegenerateResume] 审批恢复流中检测到新审批: "
                                        f"tool={tool_name}, interrupt_id={tool_call_id}, "
                                        f"graph_interrupt_id={new_graph_interrupt_id}, "
                                        f"langgraph_resume_id={new_langgraph_resume_id}"
                                    )
                                    yield f"data: {json.dumps({'type': 'approval', 'data': approval_data}, ensure_ascii=False)}\n\n"

                                    if tool_call_id and session_id:
                                        await _request_approval(
                                            tool_call_id, session_id, regen_message_id,
                                            approval_data, data, regen_thread_id,
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
                                            # 工具事件仅通过 WebSocket 发布，不再 yield 到 SSE 流
                                            _publish_tool_lifecycle_event(
                                                EventType.TOOL_CALL_COMPLETED,
                                                {
                                                    'id': tool_call_id,
                                                    'name': tool_name,
                                                    'result': tool_content,
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

                if isinstance(msg_obj, LCAIMessage) and (getattr(msg_obj, 'tool_calls', None) or getattr(msg_obj, 'tool_call_chunks', None)):
                    msg_tool_calls = getattr(msg_obj, 'tool_calls', None) or []
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
                                # 工具事件仅通过 WebSocket 发布，不再 yield 到 SSE 流
                                _publish_tool_lifecycle_event(
                                    EventType.TOOL_CALL_INPUT_READY,
                                    tool_calls_map[tc_id],
                                    session_id,
                                    str(regen_message_id) if regen_message_id else None,
                                )

                try:
                    for event in process_stream_chunk(
                        mode_data, tool_calls_map, current_message_content,
                        tool_call_count=tool_call_count,
                        lcp_func=_lcp_len,
                        accumulated_reasoning=accumulated_reasoning,
                        tool_args_accumulator=tool_args_accumulator,
                        mode='agent',
                        session_id=session_id,
                        message_id=regen_message_id,
                    ):
                        if event.get("type") == "chunk":
                            current_message_content += event.get("content", "")
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                        # tool/tool_result 事件由 stream_helpers 内部直接发布到 WebSocket
                except Exception as chunk_err:
                    logger.warning(f"[RegenerateResume] 处理流式 chunk 失败: {chunk_err}")
                    continue

        update_usage_and_tokens(cb, usage_tracker, token_detail_tracker)

        # finalize_tool_calls 内部直接通过 _publish_tool_lifecycle_event 发布 WebSocket 事件，
        # 返回事件列表为空，无需再 yield 到 SSE 流或同步到 session
        finalize_tool_calls(
            all_messages, tool_calls_map, tool_args_accumulator,
            session_id=session_id, message_id=regen_message_id,
        )

        if ended_by_interrupt:
            yield f"data: {json.dumps({'type': 'interrupted', 'data': {'reason': 'approval_required'}}, ensure_ascii=False)}\n\n"
        else:
            context_info = build_context_info(usage_tracker, token_detail_tracker, time.time())
            yield f"data: {json.dumps({'type': 'context', 'data': context_info}, ensure_ascii=False)}\n\n"

        yield f"data: {json.dumps({'type': 'end', 'message': '生成完成'}, ensure_ascii=False)}\n\n"
        yield f"data: [DONE]\n\n"

    except asyncio.CancelledError:
        logger.info(f"[RegenerateResume] 客户端断开: session={session_id}, interrupt={interrupt_id}")
        try:
            if current_message_content:
                await asyncio.shield(_save_regenerated_message(
                    regen_message_id, current_message_content, tool_calls_map,
                    accumulated_reasoning, None,
                ))
        except Exception as e:
            logger.error(f"[RegenerateResume] shield 保存失败: {e}")
        try:
            await asyncio.shield(_cleanup_streaming_flag(regen_message_id))
        except Exception:
            pass
        try:
            # BUG L 变体修复：批量场景 resume_value 是字典，需按 interrupt_id 查找实际审批值
            if isinstance(resume_value, dict):
                _shield_state = Approval.STATE_APPROVED if resume_value.get(interrupt_id) is True else Approval.STATE_REJECTED
            else:
                _shield_state = Approval.STATE_APPROVED if resume_value is not False else Approval.STATE_REJECTED
            await asyncio.shield(
                approval_service.complete_approval_async(interrupt_id, _shield_state),
            )
        except Exception as e:
            logger.error(f"[RegenerateResume] shield complete_approval 失败: {e}")
        raise
    except Exception as e:
        logger.error(f"[RegenerateResume] 审批恢复执行失败: {e}", exc_info=True)
        yield sse_error_event("approval_error", f"审批恢复执行失败: {str(e)}")
        yield "data: [DONE]\n\n"
    finally:
        try:
            from Django_xm.apps.ai_engine.services.checkpointer_factory import release_async_checkpointer
            await release_async_checkpointer()
        except Exception:
            pass

        # BUG L 变体修复：批量场景 resume_value 是 {tool_call_id: bool} 字典，
        # `resume_value is not False` 对字典恒为 True，导致拒绝被错误标记为 approved
        if isinstance(resume_value, dict):
            # 批量场景：从字典中查找当前 interrupt_id 对应的值
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
                        regen_message_id, pending_content, tool_calls_map, accumulated_reasoning,
                    )
                except Exception as save_err:
                    logger.error(f"[RegenerateResume] 保存中断内容失败: {save_err}")
            try:
                await _cleanup_streaming_flag(regen_message_id)
            except Exception:
                pass
            try:
                await approval_service.complete_approval_async(interrupt_id, final_state)
            except Exception as ce:
                logger.error(f"[RegenerateResume] complete_approval 失败: {ce}")
        else:
            has_content = bool(current_message_content and current_message_content.strip())
            has_tc = bool(tool_calls_map)
            if has_content or has_tc:
                try:
                    await _save_regenerated_message(
                        regen_message_id, current_message_content, tool_calls_map,
                        accumulated_reasoning, usage_tracker if 'usage_tracker' in dir() else None,
                    )
                except Exception as save_err:
                    logger.error(f"[RegenerateResume] 保存恢复内容失败: {save_err}")

            try:
                await _cleanup_streaming_flag(regen_message_id)
            except Exception:
                pass

            try:
                await approval_service.complete_approval_async(interrupt_id, final_state)
            except Exception as ce:
                logger.error(f"[RegenerateResume] complete_approval 失败: {ce}")

            if session_id:
                try:
                    import asyncio as _asyncio
                    await _asyncio.sleep(0.5)

                    @sync_to_async
                    def _get_last():
                        m = ChatMessage.objects.filter(id=regen_message_id).first()
                        return m.id if m else None
                    mid = await _get_last()
                    await publish_event(
                        EventType.STREAM_COMPLETED,
                        {
                            'source': EventSource.CHAT,
                            'source_id': session_id,
                            'session_id': session_id,
                            'message_id': str(mid) if mid else str(regen_message_id),
                            'is_streaming': False,
                            'finalized': False,
                        },
                        session_id=session_id,
                    )
                except PayloadValidationError:
                    logger.error(
                        f"[RegenerateResume] STREAM_COMPLETED payload 校验失败，跳过广播: "
                        f"session_id={session_id}, regen_message_id={regen_message_id}",
                        exc_info=True
                    )
                except Exception as e:
                    logger.error(f"[RegenerateResume] 广播 stream_completed 失败: {e}")


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------

# 注：_build_approval_data 已删除，批量 interrupt 解析统一使用
# stream_helpers.parse_approval_interrupt（支持新格式批量 + 旧格式单条）


async def _request_approval(
    interrupt_id: str,
    session_id: str,
    message_id: int,
    approval_data: Dict[str, Any],
    data: Dict[str, Any],
    regen_thread_id: str,
) -> None:
    """委托 approval_service 发起审批请求，保存 regenerate 完整配置到 extra。"""
    sync_payload = {
        'interrupt_id': interrupt_id,
        'source': Approval.SOURCE_CHAT,
        'source_id': session_id,
        'session_id': session_id,
        'state': 'pending',
        'tool_name': approval_data.get('tool_name'),
        'title': approval_data.get('title', ''),
        'description': approval_data.get('description', ''),
        'action': approval_data.get('action', 'confirm'),
        'operation': approval_data.get('operation', ''),
        'danger_level': approval_data.get('danger_level', 'medium'),
        'parameters': approval_data.get('parameters', {}),
        'tool_call_id': approval_data.get('tool_call_id', ''),
        'extra': {
            **(approval_data.get('extra') or {}),
            'tool_config': {
                'use_tools': data.get('use_tools', True),
                'use_web_search': data.get('use_web_search', False),
                'use_mcp': data.get('use_mcp', False),
                'selected_mcp_servers': data.get('selected_mcp_servers'),
                'selected_tools': data.get('selected_tools'),
                'use_knowledge_base': data.get('use_knowledge_base', False),
                'selected_knowledge_bases': data.get('selected_knowledge_bases', []),
                'tool_tier': data.get('tool_tier', 'standard'),
            },
            'model_config': {
                'provider_id': data.get('provider_id'),
                'model_name': data.get('model_name'),
                'use_deep_thinking': data.get('use_deep_thinking', False),
                'special_params': data.get('special_params'),
                'temperature': data.get('temperature'),
                'max_tokens': data.get('max_tokens'),
            },
            'regenerate': {
                'thread_id': regen_thread_id,
                'message_id': message_id,
            },
        },
        'message_id': message_id,
    }
    try:
        await approval_service.request_approval_async(
            source=Approval.SOURCE_CHAT,
            source_id=session_id,
            interrupt_id=interrupt_id,
            approval_data=sync_payload,
        )
    except Exception as approval_err:
        logger.warning(f"[Regenerate] request_approval_async 首次失败，尝试截断重试: {approval_err}")
        try:
            fallback_payload = dict(sync_payload)
            for fld in ('title', 'tool_name', 'tool_call_id'):
                val = fallback_payload.get(fld)
                if isinstance(val, str) and len(val) > 200:
                    fallback_payload[fld] = val[:200]
            await approval_service.request_approval_async(
                source=Approval.SOURCE_CHAT,
                source_id=session_id,
                interrupt_id=interrupt_id,
                approval_data=fallback_payload,
            )
        except Exception as fallback_err:
            logger.error(f"[Regenerate] 截断重试仍失败: interrupt_id={interrupt_id}: {fallback_err}")


async def _save_regenerated_message(
    message_id: int,
    content: str,
    tool_calls_map: Dict[str, Dict],
    accumulated_reasoning: Dict[str, str],
    usage_tracker,
) -> None:
    """将流式生成的 content/tool_calls 保存到 message 顶层和 versions[current_version]。"""
    clean_tool_calls = []
    for tc_info in tool_calls_map.values():
        clean = {k: v for k, v in tc_info.items() if not k.startswith('_')}
        clean_tool_calls.append(clean)

    reasoning_content = ""
    if accumulated_reasoning and accumulated_reasoning.get("content"):
        reasoning_content = accumulated_reasoning["content"]

    @sync_to_async
    def _do_save():
        msg = ChatMessage.objects.select_related('session').filter(id=message_id).first()
        if not msg:
            return None

        # 增量合并 tool_calls：resume 阶段的 tool_calls_map 只包含恢复后新产生的工具调用，
        # 需要与中断前已持久化的 tool_calls 合并，避免覆盖丢失
        existing_tcs = list(msg.tool_calls or [])
        existing_ids = {tc.get('id') for tc in existing_tcs if isinstance(tc, dict)}
        for tc in clean_tool_calls:
            tc_id = tc.get('id')
            if tc_id and tc_id not in existing_ids:
                existing_tcs.append(tc)
            elif tc_id:
                # 已存在的 tool_call：用新数据更新（如 status、output 等字段）
                for i, existing in enumerate(existing_tcs):
                    if isinstance(existing, dict) and existing.get('id') == tc_id:
                        existing_tcs[i] = {**existing, **tc}
                        break
        msg.tool_calls = existing_tcs

        # content 追加：resume 阶段的 content 是新产生的文本，需与中断前的内容拼接
        if content:
            msg.content = (msg.content or "") + content

        if usage_tracker is not None:
            msg.model = usage_tracker.model_id or msg.model or ''
        if msg.versions and 0 <= msg.current_version < len(msg.versions):
            msg.versions[msg.current_version] = {
                'content': msg.content,
                'tool_calls': list(msg.tool_calls or []),
                'sources': msg.sources or [],
                'reasoning': reasoning_content,
                'created_at': timezone.now().isoformat(),
                'model': msg.model or '',
            }
        msg.is_streaming = False
        msg.save()
        return msg

    saved_msg = await _do_save()
    if saved_msg:
        try:
            from Django_xm.apps.chat.serializers import ChatMessageSerializer
            real_session_id = saved_msg.session.session_id
            await publish_event(
                EventType.MESSAGE_UPDATED, {
                    'session_id': real_session_id,
                    'message_id': str(saved_msg.id),
                    'message': ChatMessageSerializer(saved_msg).data,
                }, session_id=real_session_id
            )
        except PayloadValidationError:
            logger.error(
                f"[Regenerate] MESSAGE_UPDATED payload 校验失败，跳过广播: "
                f"session_id={real_session_id}, message_id={saved_msg.id}",
                exc_info=True
            )
        except Exception as pub_err:
            logger.warning(f"[Regenerate] 广播 message_updated 失败: {pub_err}")


async def _save_partial_regen_content(
    message_id: int,
    content: str,
    tool_calls_map: Dict[str, Dict],
    accumulated_reasoning: Dict[str, str],
) -> None:
    """审批中断时保存部分已生成内容，保持 is_streaming=False 但不清空版本。"""
    clean_tool_calls = []
    for tc_info in tool_calls_map.values():
        clean = {k: v for k, v in tc_info.items() if not k.startswith('_')}
        clean_tool_calls.append(clean)

    reasoning_content = accumulated_reasoning.get("content", "") if accumulated_reasoning else ""

    @sync_to_async
    def _do_save():
        msg = ChatMessage.objects.select_related('session').filter(id=message_id).first()
        if not msg:
            return None
        existing = msg.content or ""
        if content and content not in existing:
            msg.content = existing + content
        if clean_tool_calls:
            existing_tcs = list(msg.tool_calls or [])
            existing_ids = {tc.get('id') for tc in existing_tcs if isinstance(tc, dict)}
            for tc in clean_tool_calls:
                tc_id = tc.get('id')
                if tc_id and tc_id not in existing_ids:
                    existing_tcs.append(tc)
            msg.tool_calls = existing_tcs
        if reasoning_content:
            cur_reasoning = msg.reasoning if isinstance(msg.reasoning, dict) else {}
            if not cur_reasoning.get('content'):
                cur_reasoning['content'] = reasoning_content
            msg.reasoning = cur_reasoning
        if msg.versions and 0 <= msg.current_version < len(msg.versions):
            ver = msg.versions[msg.current_version]
            ver['content'] = msg.content
            ver['tool_calls'] = list(msg.tool_calls or [])
            ver['reasoning'] = reasoning_content
        msg.save()
        return msg

    saved_msg = await _do_save()
    if saved_msg:
        try:
            from Django_xm.apps.chat.serializers import ChatMessageSerializer
            real_session_id = saved_msg.session.session_id
            await publish_event(
                EventType.MESSAGE_UPDATED, {
                    'session_id': real_session_id,
                    'message_id': str(saved_msg.id),
                    'message': ChatMessageSerializer(saved_msg).data,
                }, session_id=real_session_id
            )
        except PayloadValidationError:
            logger.error(
                f"[Regenerate] 审批中断 MESSAGE_UPDATED payload 校验失败，跳过广播: "
                f"session_id={real_session_id}, message_id={saved_msg.id}",
                exc_info=True
            )
        except Exception as pub_err:
            logger.warning(f"[Regenerate] 审批中断广播 message_updated 失败: {pub_err}")


async def _cleanup_streaming_flag(message_id: int) -> None:
    """清理 message 的 is_streaming 标记。"""
    @sync_to_async
    def _do_cleanup():
        ChatMessage.objects.filter(id=message_id).update(is_streaming=False)
    await _do_cleanup()


async def _rollback_regenerated_message(message_id: int) -> None:
    """重新生成失败时回滚版本：删除空版本，恢复上一个版本。"""
    @sync_to_async
    def _do_rollback():
        msg = ChatMessage.objects.select_related('session').filter(id=message_id).first()
        if not msg:
            return None
        versions = list(msg.versions or [])
        if len(versions) < 2:
            msg.content = ''
            msg.tool_calls = []
            msg.sources = []
            msg.reasoning = {}
            msg.is_streaming = False
            msg.save()
            return msg

        versions.pop()
        prev_idx = len(versions) - 1
        msg.versions = versions
        msg.current_version = prev_idx

        prev_version = versions[prev_idx]
        msg.content = prev_version.get('content', '') or ''
        msg.tool_calls = list(prev_version.get('tool_calls', []) or [])
        msg.sources = list(prev_version.get('sources', []) or [])
        reasoning = prev_version.get('reasoning', {})
        msg.reasoning = reasoning if reasoning else {}
        msg.is_streaming = False
        msg.save()
        return msg

    rolled_msg = await _do_rollback()
    if rolled_msg:
        try:
            from Django_xm.apps.chat.serializers import ChatMessageSerializer
            real_session_id = rolled_msg.session.session_id
            await publish_event(
                EventType.MESSAGE_REGENERATE_REVERTED, {
                    'session_id': real_session_id,
                    'message_id': str(rolled_msg.id),
                    'message': ChatMessageSerializer(rolled_msg).data,
                }, session_id=real_session_id
            )
        except PayloadValidationError:
            logger.error(
                f"[Regenerate] MESSAGE_REGENERATE_REVERTED payload 校验失败，跳过广播: "
                f"session_id={real_session_id}, message_id={rolled_msg.id}",
                exc_info=True
            )
        except Exception as pub_err:
            logger.warning(f"[Regenerate] 广播 message_regenerate_reverted 失败: {pub_err}")
