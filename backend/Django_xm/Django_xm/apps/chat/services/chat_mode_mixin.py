"""chat 模式编排域（阶段 5 自 chat_service 拆分，行为等价纯移动）。

职责（单一变化方向）：请求按模式分派（slash 命令 / deep-research / agent）、
slash 命令分支执行、agent 与 deep-research 分支的事件编排（检索器注入的
degradation 帧产出、深度思考开关、deep-research 任务创建与执行信令）。

拆分形式（patch 面约束定形，见 .trae/memory/2026-10-06.md 阶段 5 节）：
ChatService 经 mixin 继承承载本域方法——类级/类属性 patch
（``mock.patch.object(ChatService, "_deep_service"/"_tool_service", create=True)``
等）在 ChatService 类上设置属性即可被实例属性查找命中，既有测试锚定零破坏。

**留在 chat_service.py 的方法体**（模块命名空间 patch 约束）：
- ``_inject_agent_retriever_tools`` / ``_inject_deep_research_retriever_tools``：
  方法体引用 ``sync_to_async``（test_retriever_injection_degradation:56/:139
  patch 打在 chat_service 模块 globals，名字解析在定义模块）。
跨域调用一律经 ``self.``（运行期属性查找），本模块不 import chat_service（零循环）。
"""

import logging
from collections.abc import AsyncGenerator
from typing import Any

from Django_xm.apps.chat.services.slash_commands import aexecute_command, parse_command

logger = logging.getLogger(__name__)


class ChatModeOrchestrationMixin:
    """ChatService 模式编排域 mixin（方法体自 chat_service.py 原样迁入）。"""

    async def _dispatch_by_mode(
        self,
        data: dict[str, Any],
        usage_tracker,
        token_detail_tracker,
        interrupt_handler=None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """按模式分发请求：slash 命令 / deep-research / agent"""
        mode = data.get("mode", "agent")

        # 1. 解析 slash 命令
        parsed = parse_command(data.get("message", ""))
        if parsed:
            async for event in self._handle_slash_command(data, parsed, token_detail_tracker):
                yield event
            return

        # 2. deep-research 模式 → 多步骤工作流
        if mode == "deep-research":
            async for event in self._handle_deep_research_mode(data, usage_tracker, token_detail_tracker):
                yield event
            return

        # 3. agent 模式 → Agent（动态判断是否使用工具）
        if mode == "agent":
            async for event in self._handle_agent_mode(
                data, usage_tracker, token_detail_tracker, interrupt_handler=interrupt_handler
            ):
                yield event
            return

        # 默认 fallback
        async for event in self._process_normal_stream_chat(data, usage_tracker, token_detail_tracker):
            yield event

    async def _handle_slash_command(
        self,
        data: dict[str, Any],
        parsed: tuple,
        token_detail_tracker,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """slash 命令分支：构建命令上下文并执行命令，产出命令结果与结束事件

        命令执行经 aexecute_command（E11/P2-7）移出事件循环（/compact 含
        同步 store/embedding 重活，在循环上执行会阻塞整个执行服务）。
        """
        command_name, args = parsed
        context = {
            "args": args,
            "user_id": self.user_id,
            "session_id": data.get("session_id"),
            "messages": data.get("chat_history", []),
            "token_info": token_detail_tracker.get_summary(),
        }
        cmd_result = await aexecute_command(command_name, context)
        yield {
            "type": "command",
            "data": cmd_result,
            "content": cmd_result.get("content", ""),
        }
        yield {"type": "end", "message": "命令执行完成"}

    async def _handle_deep_research_mode(
        self,
        data: dict[str, Any],
        usage_tracker,
        token_detail_tracker,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """deep-research 分支：注入知识库检索工具与深度研究工具后委托模式分发执行"""
        if data.get("use_knowledge_base"):
            await self._inject_deep_research_retriever_tools(data)

        # E5-02：注入失败降级事件广播（用户可见提示，属"流继续"类，不中断流）
        for degradation in data.pop("_stream_degradation_events", []):
            yield {"type": "degradation", "data": degradation}

        # MCP 工具和用户选择工具
        data["_deep_extra_tools"] = await self._tool_service.get_deep_research_tools(data)
        if data.get("_retriever_tool_list") and len(data["_retriever_tool_list"]) > 1:
            data.setdefault("_deep_extra_tools", []).extend(data["_retriever_tool_list"][1:])
        async for event in self._create_agent_for_mode(
            "deep-research", data, usage_tracker, token_detail_tracker
        ):
            yield event

    async def _handle_agent_mode(
        self,
        data: dict[str, Any],
        usage_tracker,
        token_detail_tracker,
        interrupt_handler=None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """agent 分支：知识库工具注入、深度思考开关、use_tools 动态判断后委托常规流式聊天"""
        if data.get("use_knowledge_base"):
            await self._inject_agent_retriever_tools(data)

        # E5-02：注入失败降级事件广播（用户可见提示，属"流继续"类，不中断流）
        for degradation in data.pop("_stream_degradation_events", []):
            yield {"type": "degradation", "data": degradation}

        # 深度思考叠加
        if data.get("use_deep_thinking"):
            self._apply_deep_thinking_flag(data)

        # 动态判断 use_tools：如果用户没有开启任何工具/能力，则不使用工具
        self._resolve_use_tools_flag(data)

        async for event in self._process_normal_stream_chat(
            data, usage_tracker, token_detail_tracker, interrupt_handler=interrupt_handler
        ):
            yield event

    def _apply_deep_thinking_flag(self, data: dict[str, Any]) -> None:
        """深度思考开关：模型支持 deep_thinking 能力时设置 _enable_deep_thinking 标志"""
        from Django_xm.apps.ai_engine.services.llm_factory import model_supports_capability

        provider_id = data.get("provider_id", "")
        model_name = data.get("model_name", "")
        if model_supports_capability(provider_id, model_name, "deep_thinking"):
            data["_enable_deep_thinking"] = True

    def _resolve_use_tools_flag(self, data: dict[str, Any]) -> None:
        """动态判断 use_tools：优先尊重前端显式传参，否则按各工具/能力开关推断"""
        # 尊重前端显式传递的 use_tools 参数
        explicit_use_tools = data.get("use_tools")
        if explicit_use_tools is not None:
            data["use_tools"] = bool(explicit_use_tools)
        else:
            use_web_search = data.get("use_web_search", False)
            use_mcp = data.get("use_mcp", False)
            use_knowledge_base = data.get("use_knowledge_base", False)
            selected_tools = data.get("selected_tools")
            has_any_tool_enabled = bool(
                use_web_search or use_mcp or use_knowledge_base or selected_tools or data.get("_extra_tools")
            )
            data["use_tools"] = has_any_tool_enabled

    async def _create_agent_for_mode(
        self,
        mode: str,
        data: dict[str, Any],
        usage_tracker,
        token_detail_tracker,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """为特定模式创建并执行 Agent 流，按模式路由分发到对应处理分支"""
        if mode == "deep-research":
            async for event in self._create_agent_for_deep_research(data):
                yield event

    async def _create_agent_for_deep_research(self, data: dict[str, Any]) -> AsyncGenerator[dict[str, Any], None]:
        """深度研究模式处理（聊天流式连接立即返回架构）：

        1. 创建深度研究任务记录（create_deep_research_task，仅建任务，不启动执行）
        2. 发送 deep_research 事件，前端据此：
           - 设置 researchTaskId
           - 将消息 streamState 转为 INTERRUPTED
        3. 更新 ChatMessage 双向关联（research_task_id + message_id）
        4. 发布执行启动信令（start_execution → Redis SIGNAL_START，不等待结果）
        5. 发送 interrupted 事件（触发 stream_broadcast 广播 stream_interrupted WebSocket 事件）
        6. 聊天流式连接立即结束（return），不持续等待研究完成

        研究结果回写链路（完全独立于聊天流式连接）：
        - 执行服务完成/失败时调用 writeback_to_chat_message 回写 final_report 到 ChatMessage
        - 执行服务调用 broadcast_stream_completed 广播 WebSocket 事件
        - 前端通过 WebSocket stream_completed 事件回写结果并转 COMPLETED

        实时进度同步（跨浏览器）：
        - 审批 / 工具事件通过 WebSocket 统一推送
        - 非触发浏览器通过 stream_interrupted WebSocket 事件感知深度研究模式切换
        """
        task_id = await self._deep_service.create_deep_research_task(
            data["message"],
            session_id=data.get("session_id"),
            use_web_search=data.get("use_web_search", True),
            retriever_tool=data.get("_retriever_tool"),
            task_title=data.get("_original_message"),
            selected_tools=data.get("selected_tools"),
            use_mcp=data.get("use_mcp", False),
            selected_mcp_servers=data.get("selected_mcp_servers"),
            enable_sandbox=data.get("enable_sandbox", False),
        )
        # 立即发送 deep_research 事件，让用户可以跳转到深度研究模块查看实时进度
        yield {
            "type": "deep_research",
            "data": {
                "task_id": task_id,
                "session_id": data.get("session_id", ""),
            },
        }
        assistant_msg_id = data.get("_assistant_message_id")
        await self._link_research_task_to_message(assistant_msg_id, task_id)

        # 深度思考参数透传：前端开启时后端不再静默禁用，仅在不支持时发送 warning 提示
        enable_deep_thinking = data.get("use_deep_thinking", False)
        async for warning_event in self._warn_if_deep_thinking_unsupported(data):
            yield warning_event

        await self._start_deep_research_execution(data, task_id, enable_deep_thinking, assistant_msg_id)

        # 通知前端 stream_interrupted（触发 stream_broadcast 广播 WebSocket 事件）
        # 非触发浏览器通过此事件感知深度研究模式切换
        yield {
            "type": "interrupted",
            "data": {
                "task_id": task_id,
                "message_id": str(assistant_msg_id) if assistant_msg_id else None,
                "session_id": data.get("session_id", ""),
            },
        }
        # 设置 researchTaskId（触发浏览器通过 SSE 设置 lastMessage 的 researchTaskId）
        yield {"type": "research_task_id", "data": {"research_task_id": task_id}}
        # 立即结束聊天流式连接，不发送 chunk（最终报告由执行服务通过 WebSocket 回写）
        return

    async def _link_research_task_to_message(self, assistant_msg_id, task_id: str) -> None:
        """将 research_task_id 写入 ChatMessage（ResearchTask↔ChatSession 双向关联）。

        确保 sync_approval_state_to_chat_message 和 writeback_to_chat_message 能通过
        research_task_id 查找到关联的 ChatMessage。
        """
        if assistant_msg_id:
            try:
                assistant_msg_id_int = int(assistant_msg_id)
                from Django_xm.apps.chat.services.deep_chat_service import (
                    update_chat_message_research_task_id_async,
                )
                await update_chat_message_research_task_id_async(
                    assistant_msg_id_int, task_id,
                )
            except (ValueError, TypeError):
                pass

    async def _warn_if_deep_thinking_unsupported(self, data: dict[str, Any]) -> AsyncGenerator[dict[str, Any], None]:
        """深度思考能力检查：模型不支持时 yield warning 事件（参数仍照常透传）。"""
        from Django_xm.apps.ai_engine.services.llm_factory import model_supports_capability

        use_deep_thinking = data.get("use_deep_thinking", False)
        if use_deep_thinking and not model_supports_capability(
            data.get("provider_id", ""), data.get("model_name", ""), "deep_thinking"
        ):
            yield {
                "type": "warning",
                "data": {
                    "message": (
                        f'模型 {data.get("provider_id", "")}/{data.get("model_name", "")} '
                        "可能不支持深度思考，将尝试透传参数"
                    ),
                },
            }

    async def _start_deep_research_execution(
        self,
        data: dict[str, Any],
        task_id: str,
        enable_deep_thinking,
        assistant_msg_id,
    ) -> None:
        """发布深度研究执行启动信令（start_execution → Redis SIGNAL_START，不等待结果）。

        message_id：透传 assistant 消息 ID，深度研究工具事件/审批事件
        依赖它定位到聊天消息（toolCallsMap → message.toolCalls 归属），
        缺失时聊天深度研究模式的工具调用卡片会从消息中消失（根因修复）。
        """
        await self._deep_service.start_execution(
            query=data["message"],
            session_id=data.get("session_id"),
            use_web_search=data.get("use_web_search", True),
            retriever_tool=data.get("_retriever_tool"),
            extra_tools=data.get("_deep_extra_tools", []),
            enable_deep_thinking=enable_deep_thinking,
            provider_id=data.get("provider_id"),
            model_name=data.get("model_name"),
            task_id=task_id,
            knowledge_base_ids=self._resolve_kb_ids(data),
            temperature=data.get("temperature"),
            max_tokens=data.get("max_tokens"),
            special_params=data.get("special_params"),
            continue_task_id=data.get("continue_task_id"),
            message_id=str(assistant_msg_id) if assistant_msg_id else None,
            enable_sandbox=data.get("enable_sandbox", False),
        )
