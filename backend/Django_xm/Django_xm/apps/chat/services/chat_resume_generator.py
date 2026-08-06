"""聊天审批恢复的异步 SSE 生成器（Task 11.2 架构解耦）。

将原 ``_stream_chat_resume_generator``（views 层模块级函数）下沉到 services 层，
解除 ``common/approval_gateway`` 与 ``tasks/approval_tasks`` 对 views 层的反向依赖
（views 层不应被下层模块引用，避免架构环）。

- ``_stream_chat_resume_generator``：审批恢复 SSE 生成器（原 views 层模块级函数）
"""

import json
import logging
import time

from Django_xm.common.sse_utils import sse_error_event

logger = logging.getLogger(__name__)


async def _stream_chat_resume_generator(
    request,
    approval,
    resume_value,
    session_id,
    request_data=None,
    graph_interrupt_id=None,
    langgraph_resume_id=None,
):
    """聊天审批恢复的异步 SSE 生成器。

    通过 ChatService 重建 agent，使用 ``Command(resume=...)`` 恢复 LangGraph 执行，
    并以 SSE 事件流推送后续输出。

    流程：
    1. 从 ``request_data`` 与 ``approval.extra`` 读取模型/工具配置
    2. 构造 ``Command(resume={langgraph_resume_id or approval.interrupt_id: resume_value})``
    3. 创建 ChatService 并构建 agent（含 checkpointer）
    4. 校验 interrupt 归属（防止深度研究 interrupt 被错误处理）
    5. 从 checkpoint 加载历史 tool_call 信息
    6. 流式恢复执行，处理 messages/updates 两种 stream_mode
    7. 检测新的审批中断（批量审批场景）并推送 approval 事件
    8. 流结束后发送 ``[DONE]``

    心跳保活由调用方通过 ``sse_async_heartbeat_generator`` 包装实现，
    本生成器只负责产出业务事件。

    Args:
        request: HTTP 请求对象（用到 ``request.user.id``）
        approval: Approval 模型实例
        resume_value: 恢复值（True/False/user_input，或批量场景的 {tool_call_id: bool}）
        session_id: 会话 ID（用作 thread_id）
        request_data: 预提取的 ``request.data`` 字典；为 None 时尝试从 request 读取。
        graph_interrupt_id: 批次 UUID（仅用于日志）；为 None 时回退到 ``approval.interrupt_id``
        langgraph_resume_id: LangGraph 恢复 ID（= ``intr.id``，作为 Command resume KEY）；
                             为 None 时回退到 ``approval.interrupt_id``

    Yields:
        SSE 格式字符串（``"data: ...\\n\\n"``）
    """
    from langgraph.types import Command

    from Django_xm.apps.ai_engine.services.checkpointer_factory import release_async_checkpointer
    from Django_xm.apps.approvals.services.approval_service import complete_approval_async
    from Django_xm.apps.chat.services.chat_service import ChatService
    from Django_xm.apps.chat.services.sse_generator import _publish_stream_event
    from Django_xm.common.event_schema import EventSource, EventType
    from Django_xm.common.realtime_events import publish_event

    if request_data is None:
        try:
            request_data = dict(request.data) if hasattr(request, "data") else {}
        except Exception as req_err:
            logger.warning(f"[ChatApprovalResume] 读取 request.data 失败: {req_err}")
            request_data = {}

    # 兼容历史 approval：extra 中持久化的 model_config / tool_config 优先级低于 request_data
    approval_extra = getattr(approval, "extra", None) or {}
    if not isinstance(approval_extra, dict):
        approval_extra = {}
    persisted_tool_config = (
        approval_extra.get("tool_config", {}) if isinstance(approval_extra.get("tool_config", {}), dict) else {}
    )
    persisted_model_config = (
        approval_extra.get("model_config", {}) if isinstance(approval_extra.get("model_config", {}), dict) else {}
    )

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

    interrupt_id = approval.interrupt_id
    effective_resume_key = langgraph_resume_id or interrupt_id

    logger.info(
        f"[ChatApprovalResume] 恢复: session={session_id}, "
        f"interrupt_id={interrupt_id}, graph_interrupt_id={graph_interrupt_id}, "
        f"langgraph_resume_id={langgraph_resume_id}, "
        f"effective_resume_key={effective_resume_key}, "
        f"resume_value_type={type(resume_value).__name__}"
    )

    current_message_content = ""
    tool_calls_map = {}
    tool_call_count = {}
    accumulated_reasoning = {}
    tool_args_accumulator = {}
    used_tool_call_ids = set()
    # P6-b 修复：恢复流的内容节流广播状态（与 sse_generator.py generate_chat_stream 对齐）
    content_state = {"content": "", "last_broadcast": 0.0}

    # 审批恢复成功标志：finally 块据此决定 complete_approval_async 的终态
    # （APPROVED / REJECTED）。原代码 finally 块仅释放 checkpointer，导致
    # approval_approved / approval_rejected 事件从未发布，非触发浏览器审批状态
    # 永久停留 processing（P3-12/P3-24 根因）。
    _resume_succeeded = False

    try:
        chat_service = ChatService(user_id=request.user.id, thread_id=session_id)
        data = {
            "session_id": session_id,
            "mode": "agent",
            "use_tools": use_tools,
            "use_web_search": use_web_search,
            "use_mcp": use_mcp,
            "selected_mcp_servers": selected_mcp_servers,
            "selected_tools": selected_tools,
            "use_knowledge_base": use_knowledge_base,
            "selected_knowledge_bases": selected_knowledge_bases,
            "provider_id": provider_id,
            "model_name": model_name,
            "use_deep_thinking": use_deep_thinking,
            "special_params": special_params,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # 深度思考模式标记
        enable_deep_thinking = bool(use_deep_thinking) and bool(provider_id) and bool(model_name)
        if enable_deep_thinking:
            from Django_xm.apps.ai_engine.services.llm_factory import model_supports_capability

            enable_deep_thinking = model_supports_capability(provider_id, model_name, "deep_thinking")
        if enable_deep_thinking:
            data["_enable_deep_thinking"] = True

        # 使用 dict 形式指定 interrupt_id，支持多个 pending interrupts 的场景
        command = Command(resume={effective_resume_key: resume_value})

        # 解析模型实例（与初始聊天请求一致，避免 model_resolver 创建 LazyFallbackChatModel）
        model_instance = ChatService._resolve_model_instance(data)
        tools = await chat_service._get_tools(data)
        agent, thread_config, use_checkpointer = await chat_service._create_agent_with_memory(
            data,
            prompt_mode="agent",
            model_instance=model_instance,
            tool_config=chat_service._build_tool_config(data),
            tools=tools,
        )

        if not use_checkpointer or not thread_config:
            yield sse_error_event("approval_error", "无法恢复会话状态：checkpointer 不可用")
            return

        # 校验 interrupt 归属：检查 Agent 的 checkpoint 中是否有该 interrupt_id 的 pending interrupt
        # 如果没有，说明该 interrupt 属于其他 Agent（如深度研究 Celery 任务），不应由聊天审批 API 处理
        #
        # P-BE-5 根因修复：子 agent（depth > 0）的 checkpoint 存储在与主 agent 不同的
        # checkpoint_ns 下，aget_state(thread_config) 只查主 namespace，找不到子 agent 的
        # interrupt。对子 agent interrupt 跳过主 agent namespace 归属校验，改为通过
        # approval_extra.depth 识别——子 agent 与主 agent 共享 thread_id，source="chat"，
        # 恢复时 LangGraph 通过 Command(resume=...) 的 interrupt_id 自动路由到正确的
        # checkpoint_ns。
        approval_depth = approval_extra.get("depth", 0)
        if not isinstance(approval_depth, int) or approval_depth < 0:
            approval_depth = 0
        if approval_depth > 0:
            logger.info(
                f"[ChatApprovalResume] 子 agent 中断（depth={approval_depth}, "
                f"agent_name={approval_extra.get('agent_name', '')}），"
                f"跳过主 agent checkpoint namespace 归属校验"
            )
        else:
            try:
                graph = agent.graph if hasattr(agent, "graph") else agent
                if hasattr(graph, "aget_state"):
                    check_state = await graph.aget_state(thread_config)
                    if check_state and hasattr(check_state, "tasks") and check_state.tasks:
                        found_interrupt = False
                        for task in check_state.tasks:
                            if hasattr(task, "interrupts") and task.interrupts:
                                for intr in task.interrupts:
                                    intr_id = intr.id if hasattr(intr, "id") else ""
                                    if intr_id == effective_resume_key:
                                        found_interrupt = True
                                        break
                            if found_interrupt:
                                break
                        if not found_interrupt:
                            logger.warning(
                                f"[ChatApprovalResume] 校验失败: interrupt_id={interrupt_id} "
                                f"(effective_key={effective_resume_key}) 不属于会话 {session_id} 的 Agent，"
                                f"可能属于深度研究或其他 Agent"
                            )
                            yield sse_error_event("approval_error", "该审批请求不属于当前会话，可能属于深度研究任务")
                            return
            except Exception as check_err:
                # 校验异常时也终止，不再放行 — 防止深度研究 interrupt 被错误处理
                logger.exception("[ChatApprovalResume] 归属校验异常")
                yield sse_error_event("approval_error", f"审批校验异常，可能属于深度研究任务: {check_err!s}")
                return

        # 从 checkpoint 加载历史消息，提取已有的 tool_call 信息，
        # 初始化 tool_calls_map，这样 ToolMessage 到达时能找到对应的 tool_info 并推送给前端
        try:
            graph = agent.graph if hasattr(agent, "graph") else agent
            if hasattr(graph, "aget_state"):
                history_state = await graph.aget_state(thread_config)
                if history_state and hasattr(history_state, "values"):
                    history_messages = history_state.values.get("messages", []) or []
                    for msg in history_messages:
                        msg_tool_calls = getattr(msg, "tool_calls", None) or []
                        for tc in msg_tool_calls:
                            tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                            tc_name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
                            tc_args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {})
                            if tc_id:
                                tool_calls_map[tc_id] = {
                                    "id": tc_id,
                                    "name": tc_name or "unknown",
                                    "parameters": tc_args or {},
                                    "state": "input-available",
                                    "status": "pending",
                                }
        except Exception as hist_err:
            logger.warning(f"[ChatApprovalResume] 加载历史 tool_call 失败（非致命）: {hist_err}")

        # 流式恢复 Agent 执行
        from langchain_core.messages import AIMessage as LCAIMessage

        from Django_xm.apps.chat.services.stream_helpers import process_stream_chunk

        async for chunk in agent.graph.astream(
            command,
            config=thread_config,
            stream_mode=["messages", "updates"],
        ):
            # 处理多 stream mode
            if isinstance(chunk, tuple) and len(chunk) == 2:
                mode_name, mode_data = chunk
            else:
                mode_name, mode_data = "messages", chunk

            # 处理 updates 模式：提取工具执行结果（ToolMessage）推送给前端
            # 同时处理 __interrupt__ 事件（审批恢复后 agent 又触发新审批）
            if mode_name == "updates":
                if isinstance(mode_data, dict):
                    # 处理 __interrupt__ 事件：审批恢复后 agent 又触发新的审批请求
                    if "__interrupt__" in mode_data:
                        # P6-b 修复：interrupt 前 flush 残留内容状态到 WebSocket
                        # 内容广播使用 0.5s 节流，interrupt 暂停流时可能仍有未广播内容
                        if content_state and content_state.get("content", "").strip():
                            try:
                                content_state["last_broadcast"] = time.monotonic()
                                await publish_event(
                                    EventType.STREAM_CONTENT_UPDATE,
                                    {
                                        "source": EventSource.CHAT,
                                        "source_id": str(session_id or ""),
                                        "data": {"content": content_state["content"]},
                                    },
                                    session_id=str(session_id or ""),
                                )
                            except Exception as flush_err:
                                logger.debug(f"[ChatApprovalResume] interrupt 前 flush 失败: {flush_err}")

                        from Django_xm.apps.chat.services.stream_helpers import (
                            extract_interrupt_ids,
                            parse_approval_interrupt,
                        )
                        from Django_xm.apps.tools.base import is_approval_interrupt

                        interrupts = mode_data["__interrupt__"]
                        if interrupts:
                            # 收集所有审批请求（兼容批量格式和旧格式单条）
                            batch_approval_data = []  # [(graph_intr_id, tool_call_id, approval_data)]
                            for intr in interrupts:
                                interrupt_value, batch_id, resume_id = extract_interrupt_ids(intr)

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
                                    tool_call_id = approval_data.get("tool_call_id", "")
                                    batch_approval_data.append(
                                        (resume_id, tool_call_id, approval_data)
                                    )

                            # 消息 ID（关联当前审批所属的消息）
                            _approval_msg_id = str(
                                (approval.extra or {}).get("message_id", "")
                                or getattr(approval, "message_id", "")
                                or ""
                            )

                            for graph_intr_id, tool_call_id, approval_data in batch_approval_data:
                                tool_name = approval_data.get("tool_name", "unknown")
                                logger.info(
                                    f"[ChatApprovalResume] 检测到新审批: tool={tool_name}, "
                                    f"danger={approval_data.get('danger_level', 'medium')}"
                                )

                                # 注入 session_id / message_id
                                approval_data["session_id"] = str(session_id or "")
                                approval_data["message_id"] = _approval_msg_id

                                # 持久化工具/模型配置到 approval.extra
                                # P20 修复：使用已从首轮 approval.extra 提取的完整配置
                                # 而非 request_data（HTTP POST 体仅含 {approved: true}）
                                from Django_xm.apps.approvals.services.approval_service import (
                                    build_approval_extra,
                                    request_approval_async,
                                )
                                _resume_config = {
                                    "use_tools": use_tools,
                                    "use_web_search": use_web_search,
                                    "use_mcp": use_mcp,
                                    "selected_mcp_servers": selected_mcp_servers,
                                    "selected_tools": selected_tools,
                                    "use_knowledge_base": use_knowledge_base,
                                    "selected_knowledge_bases": selected_knowledge_bases,
                                    "provider_id": provider_id,
                                    "model_name": model_name,
                                    "use_deep_thinking": use_deep_thinking,
                                    "special_params": special_params,
                                    "temperature": temperature,
                                    "max_tokens": max_tokens,
                                }
                                approval_data["extra"] = build_approval_extra(
                                    _resume_config,
                                    tool_call_id=tool_call_id,
                                    graph_interrupt_id=approval_data.get("graph_interrupt_id", ""),
                                    langgraph_resume_id=approval_data.get("langgraph_resume_id", ""),
                                    message_id=_approval_msg_id,
                                    base_extra=approval_data.get("extra"),
                                )

                                try:
                                    await request_approval_async(
                                        source="chat",
                                        source_id=str(session_id or ""),
                                        interrupt_id=tool_call_id,
                                        approval_data=approval_data,
                                    )
                                    logger.info(
                                        f"[Approval] DB记录已创建: interrupt_id={tool_call_id}, "
                                        f"session={session_id}"
                                    )
                                except Exception as e:
                                    logger.error(
                                        f"[Approval] DB记录创建失败: interrupt_id={tool_call_id}, error={e}"
                                    )

                                yield f"data: {json.dumps({'type': 'approval', 'data': approval_data}, ensure_ascii=False)}\n\n"
                                # P6-b 修复：广播 approval 事件到 WebSocket
                                await _publish_stream_event(
                                    {"type": "approval", "data": approval_data},
                                    str(session_id or ""),
                                    message_id=int(_approval_msg_id) if _approval_msg_id.isdigit() else None,
                                    content_state=content_state,
                                )

                    for node_name, node_output in mode_data.items():
                        if node_name == "__interrupt__":
                            continue
                        # tools 节点的输出包含 messages（ToolMessage）
                        if isinstance(node_output, dict):
                            node_messages = node_output.get("messages", [])
                            if isinstance(node_messages, list):
                                for msg in node_messages:
                                    # 提取 ToolMessage 的内容
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
                                        # 查找对应的工具调用名称
                                        tool_name = tool_name_from_msg
                                        tc_info = tool_calls_map.get(tool_call_id)
                                        if tc_info:
                                            tool_name = tc_info.get("name", tool_name)
                                            # 同步更新 tool_calls_map 中的状态和结果
                                            tc_info["status"] = "completed"
                                            tc_info["result"] = tool_content
                                        # 仅保留 tool_calls_map 状态跟踪（status/result 供后续
                                        # messages 模式处理与快照复用）。
                                        # 注意：不在此处发布 tool_result——同一 ToolMessage 会经
                                        # messages 模式 process_stream_chunk → _handle_tool_message_chunk
                                        # 单发完整 tool_result（含 lifecycle_event/parameters/message_id），
                                        # 由 _publish_stream_event 统一发布（P-BE-1），避免同一工具结果双发。
                continue

            # 处理 messages 模式
            # 审批恢复时，LangGraph 会重新执行 tools 节点（从头开始），
            # messages 模式可能重新发送 AIMessage（含 tool_calls）和 ToolMessage。
            # AIMessage 已在第一次请求中处理过，跳过以避免前端显示重复的工具调用。
            # 只处理 ToolMessage（工具结果）和最终的文本回复。
            # 解析 message 对象
            msg_obj = mode_data
            if isinstance(mode_data, tuple) and len(mode_data) == 2:
                msg_obj = mode_data[0]

            # 跳过含 tool_calls 的 AIMessage 中已有的 tool_call（已在第一次请求中处理），
            # 但不跳过新增的 tool_call（审批恢复后 Agent 可能生成新的工具调用，如 fs_write_file）。
            if isinstance(msg_obj, LCAIMessage) and (
                getattr(msg_obj, "tool_calls", None) or getattr(msg_obj, "tool_call_chunks", None)
            ):
                msg_tool_calls = getattr(msg_obj, "tool_calls", None) or []
                # 仅处理完整的 tool_calls；tool_call_chunks（流式部分数据）
                # 交由下方 process_stream_chunk 增量累积，确保工具名/参数完整
                if not msg_tool_calls:
                    # 只有 tool_call_chunks，无完整 tool_calls → 回退到增量处理
                    # （不 continue，让代码落入 process_stream_chunk）
                    pass
                else:
                    # 检查是否有新增的 tool_call（不在 tool_calls_map 中的）
                    new_tool_calls = []
                    for tc in msg_tool_calls:
                        tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                        if tc_id and tc_id not in tool_calls_map:
                            new_tool_calls.append(tc)

                    if not new_tool_calls:
                        # 全部是已有的 tool_calls，跳过整条 AIMessage
                        continue
                    # 有新增的 tool_calls，只处理新增部分
                    # 将新增的 tool_call 推送给前端
                    _approval_msg_id = str(
                        (approval.extra or {}).get("message_id", "") or getattr(approval, "message_id", "") or ""
                    )
                    for tc in new_tool_calls:
                        tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                        tc_name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
                        tc_args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {})
                        logger.info(
                            f"[ChatApprovalResume] NEW_TOOL: tc_id={tc_id}, "
                            f"tc_name={tc_name}, tc_type={type(tc).__name__}, "
                            f"tc_args_keys={list(tc_args.keys())[:5] if isinstance(tc_args, dict) else 'N/A'}"
                        )
                        if tc_id:
                            tool_calls_map[tc_id] = {
                                "id": tc_id,
                                "name": tc_name or "unknown",
                                "parameters": tc_args or {},
                                "state": "input-available",
                                "status": "pending",
                            }
                            # 广播到 WebSocket（非触发浏览器实时同步新增工具）
                            from Django_xm.apps.chat.services.stream_tool_lifecycle import _broadcast_tool_input_ready
                            _broadcast_tool_input_ready(
                                tool_calls_map[tc_id],
                                str(session_id or ""),
                                _approval_msg_id,
                                module=EventSource.CHAT,
                                module_id=str(session_id or ""),
                            )
                            yield f"data: {json.dumps({'type': 'tool', 'data': tool_calls_map[tc_id]}, ensure_ascii=False)}\n\n"
                    # 跳过这条 AIMessage 的文本内容处理（tool_calls 消息通常没有文本内容）
                    continue

            try:
                for event in process_stream_chunk(
                    mode_data,
                    tool_calls_map,
                    current_message_content,
                    tool_call_count=tool_call_count,
                    lcp_func=lambda a, b: 0,
                    accumulated_reasoning=accumulated_reasoning,
                    tool_args_accumulator=tool_args_accumulator,
                    mode="agent",
                    enable_deep_thinking=enable_deep_thinking,
                    session_id=str(session_id or ""),
                    message_id=str(
                        (approval.extra or {}).get("message_id", "") or getattr(approval, "message_id", "") or ""
                    ),
                    module_id=str(session_id or ""),
                ):
                    if event.get("type") == "chunk":
                        current_message_content += event.get("content", "")
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    # P6-b 修复：广播 chunk/tool 事件到 WebSocket（非触发浏览器实时同步）
                    _resume_msg_id = int(
                        (approval.extra or {}).get("message_id", "")
                        or getattr(approval, "message_id", "")
                        or "0"
                    )
                    if not isinstance(_resume_msg_id, int) or _resume_msg_id <= 0:
                        _resume_msg_id = None
                    await _publish_stream_event(
                        event,
                        str(session_id or ""),
                        message_id=_resume_msg_id,
                        content_state=content_state,
                    )
            except Exception as chunk_err:
                logger.warning(f"[ChatApprovalResume] 流式 chunk 处理失败: {chunk_err}")
                continue

        # 深度思考模式兜底：当模型将全部输出放入 thinking 字段而 content 为空时，
        # 将推理内容作为主内容发送
        if (
            not current_message_content.strip()
            and accumulated_reasoning
            and accumulated_reasoning.get("content", "").strip()
        ):
            reasoning_text = accumulated_reasoning["content"].strip()
            logger.info(
                f"[ChatApprovalResume] 深度思考兜底: content 为空，将推理内容 ({len(reasoning_text)} 字符) 作为主内容发送"
            )
            yield f"data: {json.dumps({'type': 'chunk', 'content': reasoning_text}, ensure_ascii=False)}\n\n"
            current_message_content = reasoning_text

        # 注意：审批恢复后的消息由前端 syncLastMessageToBackend 统一保存到数据库，
        # 后端不再重复保存，避免创建重复消息。
        # 后端只在流式完成后发送 [DONE]，前端收到后触发同步。

        # P6-b 修复：flush 恢复流残留的内容到 WebSocket
        if content_state and content_state.get("content", "").strip():
            try:
                await publish_event(
                    EventType.STREAM_CONTENT_UPDATE,
                    {
                        "source": EventSource.CHAT,
                        "source_id": str(session_id or ""),
                        "message_id": str(
                            (approval.extra or {}).get("message_id", "")
                            or getattr(approval, "message_id", "")
                            or ""
                        ) or None,
                        "data": {"content": content_state["content"]},
                    },
                    session_id=str(session_id or ""),
                )
            except Exception as flush_err:
                logger.debug(f"[ChatApprovalResume] content_state flush 失败: {flush_err}")

        # 标记恢复成功：finally 块据此调用 complete_approval_async(APPROVED)
        # 必须在 yield [DONE] 之前设置，避免 async generator 被消费方中断后
        # finally 块无法正确判断终态
        _resume_succeeded = True
        yield "data: [DONE]\n\n"

    except Exception as e:
        logger.exception("[ChatApprovalResume] 审批恢复执行失败")
        yield sse_error_event("approval_error", f"审批恢复执行失败: {e!s}")
    finally:
        # 审批恢复流结束：持久化工具调用终态到 ChatMessage（P3-R2 Task 4）。
        # 恢复流不经过 sse_generator._finalize_stream_content（该入口仅在 main 流
        # generate_chat_stream 的 finally 中调用），若不在此落库，工具完成后的
        # completed/result 无法写入 ChatMessage.tool_calls，刷新/断连后快照读取的
        # 仍是首轮流持久化的 pending（P3-R2 核心缺陷：审批恢复流无 persist 记录）。
        # 复用 persist_stream_result（唯一持久化入口，签名不变）：内部
        # _merge_tool_calls_incremental 做字段级演进（保留 approval 审批字段，
        # 演进 status/state/result/error，终态不回退），将首轮流落库的 pending
        # 条目演进为 completed + result。
        # 时序：必须位于 complete_approval_async（发布 approval_approved /
        # approval_rejected 终态事件）之前——终态事件触发前端刷新/快照拉取时，
        # DB 中应已是 completed + result 而非 pending；complete_approval_async 内部的
        # sync_approval_state_to_chat_message 仅更新 tool_calls[].approval 子字段，
        # 不覆盖 status/result，先后顺序安全。
        # 异常路径同样进入本 finally：尽力持久化已完成的工具部分，不破坏现状。
        try:
            from Django_xm.apps.chat.services.stream_persistence import persist_stream_result

            await persist_stream_result(
                session_id=session_id,
                user_id=None,
                content=current_message_content,
                tool_calls_map=tool_calls_map,
                message_id=str(
                    (approval.extra or {}).get("message_id", "")
                    or getattr(approval, "message_id", "")
                    or ""
                ) or None,
            )
        except Exception as persist_err:
            logger.warning(
                f"[ChatApprovalResume] 恢复流 tool_calls 持久化失败(非致命): "
                f"session_id={session_id}, err={persist_err}"
            )

        # 审批终态化（P3-12/P3-24 根因修复）：
        # 恢复流结束后必须调用 complete_approval_async 发布 approval_approved /
        # approval_rejected 事件，否则非触发浏览器审批状态永久停留 processing。
        # 成功 → APPROVED，异常 → REJECTED。
        try:
            from Django_xm.apps.approvals.models import Approval

            final_state = Approval.STATE_APPROVED if _resume_succeeded else Approval.STATE_REJECTED
            await complete_approval_async(
                interrupt_id=interrupt_id,
                state=final_state,
            )
            logger.info(
                f"[ChatApprovalResume] 审批终态化: interrupt_id={interrupt_id}, "
                f"state={final_state}, succeeded={_resume_succeeded}"
            )
        except Exception as complete_err:
            logger.warning(
                f"[ChatApprovalResume] complete_approval_async 失败(非致命): "
                f"interrupt_id={interrupt_id}, err={complete_err}"
            )

        # 释放异步 Checkpointer 连接池，防止 PostgreSQL 连接泄漏
        try:
            await release_async_checkpointer()
        except Exception:  # noqa: S110  # cleanup, checkpointer 资源释放失败可忽略
            pass
