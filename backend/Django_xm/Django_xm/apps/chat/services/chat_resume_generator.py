"""聊天审批恢复 SSE 生成器（从 views_chat.py 抽离，P1 #5 拆分）。

职责：
    通过 ChatService 重建 agent，使用 ``Command(resume=...)`` 恢复 LangGraph 执行，
    并以 SSE 事件流推送后续输出。心跳保活由调用方通过
    ``sse_async_heartbeat_generator`` 包装实现，本生成器只负责产出业务事件。

设计说明：
    - 原位于 ``views_chat.py`` 末尾（L1169-1773），与视图层职责不同，故抽出独立服务模块。
    - 函数体原样迁移，所有 LangChain/LangGraph/服务层依赖均为函数内 lazy import，
      模块级仅依赖 ``logger``/``json``/``asyncio``/``sse_error_event``，无循环依赖。
    - ``views_chat.py`` 通过 re-export 保持 facade 兼容（向后兼容别名
      ``_stream_chat_resume_generator``）。
"""

import asyncio
import json
import logging
from typing import Any

from Django_xm.common.sse_utils import sse_error_event

logger = logging.getLogger(__name__)


async def stream_chat_resume_generator(
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
    from Django_xm.apps.chat.services.chat_service import ChatService

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
    # 已审批的 tool_call_id 集合（用于 parse_approval_interrupt 去重，避免重复审批）
    used_tool_call_ids: set[str] = set()
    # 历史 ToolMessage 的 tool_call_id 集合（用于跳过恢复流前已存在的工具结果，避免串话）
    existing_tool_result_ids: set[str] = set()
    # 当前恢复的审批所属 message_id（从 approval.extra 提取，供新审批持久化时关联消息）
    _existing_msg_id = (approval_extra or {}).get("message_id", "")
    # 流是否因新审批中断结束（True 时跳过 STREAM_COMPLETED 广播，与基线一致）
    ended_by_interrupt = False

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
        # 如果没有，说明该 interrupt 属于其他 Agent（如深度研究），不应由聊天审批 API 处理
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
        # 同时收集 existing_tool_result_ids（历史 ToolMessage 的 tool_call_id），
        # 用于在恢复流中跳过历史工具结果，避免把之前对话的工具结果重复推送（串话 BUG S 修复）
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
                                    "status": "running",
                                }
                        # 收集历史 ToolMessage 的 tool_call_id（用于跳过恢复流中重复的工具结果）
                        msg_tool_call_id = getattr(msg, "tool_call_id", None)
                        if msg_tool_call_id:
                            existing_tool_result_ids.add(msg_tool_call_id)
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
                    # 使用 extract_interrupt_ids + parse_approval_interrupt + request_approval_async
                    # 完整持久化审批记录并广播到 WebSocket，确保非请求浏览器能看到审批卡片，
                    # 刷新页面后审批不丢失，支持批量审批格式（{"_approval": True, "requests": [...]}）
                    if "__interrupt__" in mode_data:
                        from Django_xm.apps.tools.base import is_approval_interrupt

                        interrupts = mode_data["__interrupt__"]
                        if interrupts:
                            # 收集所有审批请求（兼容批量格式和旧格式单条）
                            from Django_xm.apps.chat.services.stream_helpers import (
                                extract_interrupt_ids,
                                parse_approval_interrupt,
                            )

                            batch_approval_data: list[tuple[str, dict]] = []
                            for intr in interrupts:
                                # 统一提取 interrupt_value + 批次 ID + LangGraph 恢复 ID
                                interrupt_value, new_batch_id, new_resume_id = extract_interrupt_ids(intr)
                                new_interrupt_id = new_resume_id  # 兼容变量名（= intr.id）

                                if not is_approval_interrupt(interrupt_value):
                                    continue

                                # 使用共享解析函数（支持批量格式 + 旧格式单条），
                                # 提取 parameters / llm_tool_call_id / operation 等完整字段
                                parsed_list = parse_approval_interrupt(
                                    interrupt_value,
                                    graph_interrupt_id=new_batch_id,
                                    langgraph_resume_id=new_resume_id,
                                    tool_calls_map=tool_calls_map,
                                    tool_args_accumulator=tool_args_accumulator,
                                    used_tool_call_ids=used_tool_call_ids,
                                )
                                for approval_data in parsed_list:
                                    batch_approval_data.append((new_interrupt_id, approval_data))

                            # 对每个审批请求独立持久化和推送前端
                            # 批量审批场景下所有请求共用同一个 LangGraph intr.id（new_interrupt_id），
                            # 前端用 tool_call_id 作为 Approval 主键独立审批每个工具，
                            # graph_interrupt_id（批次 ID）和 langgraph_resume_id（恢复 KEY）存入 extra
                            from Django_xm.apps.approvals.services import approval_service

                            for new_interrupt_id, approval_data in batch_approval_data:
                                tool_name = approval_data.get("tool_name", "unknown")
                                tool_call_id = approval_data.get("tool_call_id", "") or new_interrupt_id
                                # 局部变量：从 approval_data 读取批次 ID 和恢复 ID，不覆盖函数参数
                                new_graph_interrupt_id = approval_data.get("graph_interrupt_id", new_interrupt_id)
                                new_langgraph_resume_id = approval_data.get("langgraph_resume_id", new_interrupt_id)
                                # 确保 graph_interrupt_id / langgraph_resume_id / tool_call_id 存入 extra
                                extra_dict = approval_data.get("extra", {}) or {}
                                if not isinstance(extra_dict, dict):
                                    extra_dict = {}
                                extra_dict["graph_interrupt_id"] = new_graph_interrupt_id
                                extra_dict["langgraph_resume_id"] = new_langgraph_resume_id
                                extra_dict["tool_call_id"] = tool_call_id
                                # 注入 tool_config 和 model_config，与初始流保持一致，
                                # 避免多级审批恢复时 selected_tools 错误缩减导致工具不可用
                                extra_dict["tool_config"] = {
                                    "use_tools": data.get("use_tools", True),
                                    "use_web_search": data.get("use_web_search", False),
                                    "use_mcp": data.get("use_mcp", False),
                                    "selected_mcp_servers": data.get("selected_mcp_servers"),
                                    "selected_tools": data.get("selected_tools"),
                                    "use_knowledge_base": data.get("use_knowledge_base", False),
                                    "selected_knowledge_bases": data.get("selected_knowledge_bases", []),
                                    "tool_tier": data.get("tool_tier", "standard"),
                                }
                                extra_dict["model_config"] = {
                                    "provider_id": data.get("provider_id"),
                                    "model_name": data.get("model_name"),
                                    "use_deep_thinking": data.get("use_deep_thinking", False),
                                    "special_params": data.get("special_params"),
                                    "temperature": data.get("temperature"),
                                    "max_tokens": data.get("max_tokens"),
                                }
                                approval_data["extra"] = extra_dict

                                # 标记该 tool_call_id 已审批，避免 parse_approval_interrupt 重复解析
                                used_tool_call_ids.add(tool_call_id)
                                # 标记流因新审批中断，跳过末尾 STREAM_COMPLETED 广播
                                ended_by_interrupt = True

                                logger.info(
                                    f"[ChatApprovalResume] 审批恢复流中检测到新审批: "
                                    f"tool={tool_name}, danger={approval_data.get('danger_level', 'medium')}, "
                                    f"interrupt_id={tool_call_id}, graph_interrupt_id={new_graph_interrupt_id}, "
                                    f"langgraph_resume_id={new_langgraph_resume_id}"
                                )
                                # 持久化审批记录到 approval_service，确保：
                                # 1. 前端点击确认时能找到记录（避免 404）
                                # 2. approval_pending 事件广播到 WebSocket（非请求浏览器看到审批卡片）
                                # 3. 刷新页面后审批不丢失（DB 记录恢复）
                                # chat 模块必须显式传入 session_id（M2 fail-fast 校验）
                                approval_data["session_id"] = session_id
                                if _existing_msg_id:
                                    approval_data["message_id"] = str(_existing_msg_id)
                                try:
                                    await approval_service.request_approval_async(
                                        source="chat",
                                        source_id=session_id,
                                        interrupt_id=tool_call_id,
                                        approval_data=approval_data,
                                    )
                                    logger.info(
                                        f"[ChatApprovalResume] 新审批已持久化: "
                                        f"interrupt_id={tool_call_id}, "
                                        f"graph_interrupt_id={new_graph_interrupt_id}, "
                                        f"langgraph_resume_id={new_langgraph_resume_id}, "
                                        f"tool={tool_name}"
                                    )
                                except Exception as persist_err:
                                    logger.warning(
                                        f"[ChatApprovalResume] 新审批持久化失败（仍推送前端）: {persist_err}"
                                    )
                                approval_event = json.dumps(
                                    {"type": "approval", "data": approval_data}, ensure_ascii=False
                                )
                                yield f"data: {approval_event}\n\n"

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
                                        # BUG S 修复：跳过历史 ToolMessage（恢复流前已存在的结果），
                                        # 避免把之前对话的工具结果重复推送到当前消息，导致"工具调用串话"
                                        if tool_call_id in existing_tool_result_ids:
                                            logger.debug(
                                                f"[ChatApprovalResume] 跳过历史 ToolMessage: "
                                                f"tool_call_id={tool_call_id}"
                                            )
                                            continue
                                        # 查找对应的工具调用名称
                                        tool_name = tool_name_from_msg
                                        tc_info = tool_calls_map.get(tool_call_id)
                                        if tc_info:
                                            tool_name = tc_info.get("name", tool_name)
                                            # 同步更新 tool_calls_map 中的状态和结果
                                            tc_info["status"] = "completed"
                                            tc_info["result"] = tool_content
                                        # 写入 tool result，供前端展示
                                        # 必须包含 id 字段（值等于 tool_call_id），否则前端
                                        # _findMatchingToolCall 无法匹配，会新增而非更新
                                        tool_result_event = json.dumps(
                                            {
                                                "type": "tool_result",
                                                "data": {
                                                    "id": tool_call_id,
                                                    "tool_call_id": tool_call_id,
                                                    "name": tool_name,
                                                    "content": tool_content,
                                                    "state": "output-available",
                                                    "status": "completed",
                                                },
                                            },
                                            ensure_ascii=False,
                                        )
                                        yield f"data: {tool_result_event}\n\n"
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
                # 检查是否有新增的 tool_call（不在 tool_calls_map 中的）
                new_tool_calls = []
                for tc in msg_tool_calls:
                    tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                    if tc_id and tc_id not in tool_calls_map:
                        new_tool_calls.append(tc)

                if not new_tool_calls:
                    # 全部是已有的 tool_calls，跳过整条 AIMessage
                    continue
                else:
                    # 有新增的 tool_calls，只处理新增部分
                    # 将新增的 tool_call 推送给前端
                    from Django_xm.apps.chat.services.stream_helpers import _broadcast_tool_input_ready

                    _approval_msg_id = str(
                        (approval.extra or {}).get("message_id", "") or getattr(approval, "message_id", "") or ""
                    )
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
                            # 审批恢复场景新增工具：广播到 WebSocket（非触发浏览器可见）
                            _broadcast_tool_input_ready(
                                tool_calls_map[tc_id],
                                str(session_id or ""),
                                _approval_msg_id,
                                module_id=str(session_id or ""),
                            )
                            tool_event = json.dumps({"type": "tool", "data": tool_calls_map[tc_id]}, ensure_ascii=False)
                            yield f"data: {tool_event}\n\n"
                    # 跳过这条 AIMessage 的文本内容处理（tool_calls 消息通常没有文本内容）
                    continue

            try:
                from Django_xm.apps.chat.services.stream_helpers import _broadcast_tool_input_ready

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
                "[ChatApprovalResume] 深度思考兜底: content 为空，"
                f"将推理内容 ({len(reasoning_text)} 字符) 作为主内容发送"
            )
            chunk_event = json.dumps({"type": "chunk", "content": reasoning_text}, ensure_ascii=False)
            yield f"data: {chunk_event}\n\n"
            current_message_content = reasoning_text

        # 持久化 AI 文本内容到 DB
        # 审批恢复流结束后，后端 persist_stream_result 主动持久化 AI 文本内容 + tool_calls + reasoning。
        # 前端 PATCH 路径已移除，后端是唯一持久化源（content + tool_calls）。
        # persist_stream_result 内部通过 message_id 查找已有 assistant 消息并更新 content，
        # 不会创建重复消息（content 仅在新内容严格长于已有内容时覆盖）。
        #
        # 持久化条件（与 finalize_stream / finalize_interrupt 行为对齐）：
        # - 正常完成：持久化完整 content + tool_calls + reasoning
        # - 审批中断（ended_by_interrupt=True）：持久化部分 content + tool_calls + reasoning
        #   （与 finalize_interrupt 一致，确保非触发浏览器拉取中断前的完整数据）
        # - content 为空但 tool_calls 非空：仍持久化 tool_calls（persist_stream_result 内部处理空 content）
        if session_id:
            # 构建 reasoning 数据
            _resume_reasoning_data: dict[str, Any] | None = None
            if accumulated_reasoning and accumulated_reasoning.get("content"):
                _resume_reasoning_data = {"content": accumulated_reasoning["content"]}

            try:
                from Django_xm.apps.chat.services.stream_helpers import persist_stream_result

                saved_id = await persist_stream_result(
                    session_id=session_id,
                    user_id=request.user.id if hasattr(request, "user") else None,
                    content=current_message_content,
                    tool_calls_map=tool_calls_map,
                    message_id=str(_existing_msg_id) if _existing_msg_id else None,
                    reasoning=_resume_reasoning_data,
                )
                if saved_id:
                    logger.info(
                        f"[ChatApprovalResume] AI 文本持久化成功: session={session_id}, "
                        f"message_id={saved_id}, content_len={len(current_message_content)}, "
                        f"tool_calls_count={len(tool_calls_map)}, "
                        f"ended_by_interrupt={ended_by_interrupt}"
                    )
            except Exception as persist_err:
                logger.warning(
                    f"[ChatApprovalResume] AI 文本持久化失败(非致命): session={session_id}, err={persist_err}"
                )

        # 广播 STREAM_COMPLETED 事件，通知非请求浏览器流已结束
        # 审批中断场景跳过（ended_by_interrupt=True），与基线一致：
        # 中断时其他浏览器通过 approval 事件感知，无需 stream_completed
        if session_id and not ended_by_interrupt:
            import asyncio as _asyncio

            from Django_xm.common.event_schema import EventSource, EventType, PayloadValidationError
            from Django_xm.common.realtime_events import publish_event

            async def _broadcast_stream_completed():
                await _asyncio.sleep(0.5)
                await publish_event(
                    EventType.STREAM_COMPLETED,
                    {
                        "source": EventSource.CHAT,
                        "source_id": session_id,
                        "session_id": session_id,
                        "is_streaming": False,
                        "finalized": False,
                    },
                    session_id=session_id,
                )

            try:
                await _asyncio.shield(_broadcast_stream_completed())
            except PayloadValidationError:
                logger.exception(
                    f"[ChatApprovalResume] STREAM_COMPLETED payload 校验失败，跳过广播: session_id={session_id}",
                )
            except Exception:
                logger.exception("[ChatApprovalResume] 广播 stream_completed 失败")

        yield "data: [DONE]\n\n"

    except asyncio.CancelledError:
        logger.info(f"[ChatApprovalResume] 客户端断开: session={session_id}, interrupt={interrupt_id}")
        # 客户端断开时兜底持久化已收集的内容，避免非触发浏览器刷新后数据缺失（P0 根因）
        if session_id and (current_message_content or tool_calls_map):
            _cancel_reasoning_data: dict[str, Any] | None = None
            if accumulated_reasoning and accumulated_reasoning.get("content"):
                _cancel_reasoning_data = {"content": accumulated_reasoning["content"]}
            try:
                from Django_xm.apps.chat.services.stream_helpers import persist_stream_result

                await persist_stream_result(
                    session_id=session_id,
                    user_id=request.user.id if hasattr(request, "user") else None,
                    content=current_message_content,
                    tool_calls_map=tool_calls_map,
                    message_id=str(_existing_msg_id) if _existing_msg_id else None,
                    reasoning=_cancel_reasoning_data,
                )
                logger.info(
                    f"[ChatApprovalResume] 客户端断开兜底持久化完成: session={session_id}, "
                    f"content_len={len(current_message_content)}, "
                    f"tool_calls_count={len(tool_calls_map)}"
                )
            except Exception:
                logger.exception(f"[ChatApprovalResume] 客户端断开兜底持久化失败: session={session_id}")
        # 不在 CancelledError 块中调用 complete_batch_async，统一由 finally 块处理，
        # 避免重复调用导致状态混乱（与基线实现一致）
        raise
    except Exception as e:
        logger.exception("[ChatApprovalResume] 审批恢复执行失败")
        # 异常路径兜底持久化：已收集的 content + tool_calls + reasoning 不应丢失（P0 根因）
        if session_id and (current_message_content or tool_calls_map):
            _err_reasoning_data: dict[str, Any] | None = None
            if accumulated_reasoning and accumulated_reasoning.get("content"):
                _err_reasoning_data = {"content": accumulated_reasoning["content"]}
            try:
                from Django_xm.apps.chat.services.stream_helpers import persist_stream_result

                await persist_stream_result(
                    session_id=session_id,
                    user_id=request.user.id if hasattr(request, "user") else None,
                    content=current_message_content,
                    tool_calls_map=tool_calls_map,
                    message_id=str(_existing_msg_id) if _existing_msg_id else None,
                    reasoning=_err_reasoning_data,
                )
                logger.info(
                    f"[ChatApprovalResume] 异常路径兜底持久化完成: session={session_id}, "
                    f"content_len={len(current_message_content)}, "
                    f"tool_calls_count={len(tool_calls_map)}"
                )
            except Exception:
                logger.exception(f"[ChatApprovalResume] 异常路径兜底持久化失败: session={session_id}")
        yield sse_error_event("approval_error", f"审批恢复执行失败: {e!s}")
    finally:
        # 释放异步 Checkpointer 连接池，防止 PostgreSQL 连接泄漏
        try:
            await release_async_checkpointer()
        except Exception:  # cleanup, checkpointer 资源释放失败可忽略  # noqa: S110
            pass

        # finally 块调用 ApprovalLifecycleService.complete_batch_async 统一终态化
        # 同批次 approval，避免流结束后审批状态卡在 processing。
        # 通过 asyncio.shield 保护避免 CancelledError 取消。
        try:
            from Django_xm.apps.approvals.models import Approval
            from Django_xm.common.approval_lifecycle import service as approval_lifecycle_service
            from Django_xm.common.constants import TIMEOUT_DECISION

            if resume_value == TIMEOUT_DECISION:
                final_state = Approval.STATE_TIMEOUT
            elif isinstance(resume_value, dict) and resume_value:
                values = list(resume_value.values())
                if all(v == TIMEOUT_DECISION for v in values):
                    final_state = Approval.STATE_TIMEOUT
                elif all(v is True for v in values):
                    final_state = Approval.STATE_APPROVED
                elif all(v is False for v in values):
                    final_state = Approval.STATE_REJECTED
                else:
                    final_state = Approval.STATE_APPROVED
            elif resume_value is False:
                final_state = Approval.STATE_REJECTED
            else:
                final_state = Approval.STATE_APPROVED

            # graph_interrupt_id 为 None 时，complete_batch 内部回退到 _complete_single
            # （仅终态化当前 approval，不查询同批次）
            await asyncio.shield(
                approval_lifecycle_service.complete_batch_async(
                    graph_interrupt_id,
                    approval.interrupt_id,
                    final_state,
                )
            )
            logger.info(
                f"[ChatApprovalResume] 审批终态化完成: interrupt_id={interrupt_id}, "
                f"final_state={final_state}, graph_interrupt_id={graph_interrupt_id}"
            )
        except Exception:
            logger.exception(f"[ChatApprovalResume] complete_batch_async 失败(非致命): interrupt_id={interrupt_id}")