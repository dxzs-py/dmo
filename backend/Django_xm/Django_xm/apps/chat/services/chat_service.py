"""
聊天服务层

将复杂的业务逻辑从视图中分离，提高代码可维护性和可测试性。
深度研究模式委托给 deep_chat_service，RAG 模式委托给 rag_chat_service。

子服务拆分：
- AgentService：Agent 创建、配置和生命周期管理
- ChatMessageBuilder：消息构建、转换和内容处理
- ContextService：上下文压缩、注入检测和预算管理
- ToolService：工具获取、过滤和协调
"""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from asgiref.sync import sync_to_async
from langchain_core.messages import AIMessage, HumanMessage

from Django_xm.apps.agent_hub.services.agent_executor import AgentExecutor
from Django_xm.apps.agent_hub.services.agent_resilience import (
    DegradationLevel,
    get_degraded_tools,
)
from Django_xm.apps.ai_engine.services.cost_tracker import TokenDetailTracker
from Django_xm.apps.chat.services.slash_commands import execute_command, parse_command
from Django_xm.apps.chat.services.stream import (
    DeepThinkingStreamStrategy,
    FallbackStreamService,
    NormalStreamStrategy,
    StreamContext,
    finalize_stream,
    run_stream_loop,
)

from ..utils import _needs_completion, convert_chat_history, extract_suggestions
from .agent_service import AgentService
from .chat_message_builder import ChatMessageBuilder
from .context_service import ContextService
from .deep_chat_service import DeepChatService
from .rag_chat_service import RAGChatService
from .stream_helpers import (
    build_context_info,
)
from .tool_service import ToolService

logger = logging.getLogger(__name__)


class ChatService:
    """聊天服务类 - 处理聊天相关的业务逻辑

    职责：业务流程编排和协调，具体实现委托给子服务：
    - AgentService: Agent 创建和管理
    - ChatMessageBuilder: 消息构建和转换
    - ContextService: 上下文工程
    - ToolService: 工具管理
    """

    CHECKPOINTER_ENABLED = True

    def __init__(self, user_id: int | None = None, thread_id: str | None = None):
        self.user_id = user_id

        self._agent_service = AgentService(user_id)
        self._message_builder = ChatMessageBuilder(user_id)
        self._context_service = ContextService(user_id, thread_id=thread_id)
        self._tool_service = ToolService(user_id)

        self._rag_service = RAGChatService(user_id=user_id, chat_service=self)
        self._deep_service = DeepChatService(self)

    async def _update_last_message_tokens(
        self,
        session_id: str,
        token_count: int,
        token_detail: dict,
        model: str,
        response_time: float,
    ):
        await ChatMessageBuilder.update_last_message_tokens(
            self.user_id,
            session_id,
            token_count,
            token_detail,
            model,
            response_time,
        )

    def _build_thread_config(self, session_id: str | None = None, **kwargs) -> dict[str, Any]:
        return self._agent_service.build_thread_config(session_id, **kwargs)

    async def _create_agent_with_memory(
        self,
        data: dict[str, Any],
        prompt_mode: str = "agent",
        model_instance=None,
        tool_config: dict[str, Any] | None = None,
        tools: list | None = None,
    ) -> tuple:
        return await self._agent_service.create_agent_with_memory(
            data,
            prompt_mode,
            model_instance,
            tool_config=tool_config,
            tools=tools,
        )

    @staticmethod
    def _resolve_model_instance(data: dict[str, Any], streaming: bool = True):
        return AgentService.resolve_model_instance(data, streaming)

    @staticmethod
    def _resolve_kb_ids(data: dict[str, Any]) -> list[str] | None:
        if not data.get("use_knowledge_base"):
            return None
        kb_ids = list(data.get("selected_knowledge_bases") or [])
        single_kb = data.get("selected_knowledge_base")
        if single_kb and single_kb not in kb_ids:
            kb_ids.append(single_kb)
        return kb_ids if kb_ids else None

    async def _get_tools(self, data: dict[str, Any]) -> list:
        return await self._tool_service.get_tools(data)

    def _build_tool_config(self, data: dict[str, Any]) -> dict[str, Any]:
        from Django_xm.apps.tools import TOOL_TIER_STANDARD

        use_web_search = data.get("use_web_search", False)
        use_mcp = data.get("use_mcp", False)
        selected_tools = data.get("selected_tools")
        selected_mcp_servers = data.get("selected_mcp_servers")
        use_knowledge_base = data.get("use_knowledge_base", False)
        # 尊重前端显式传递的 use_tools 参数
        explicit_use_tools = data.get("use_tools")
        if explicit_use_tools is not None:
            has_any_tool_enabled = bool(explicit_use_tools)
        else:
            has_any_tool_enabled = bool(use_web_search or use_mcp or use_knowledge_base or selected_tools)
        return {
            "use_tools": has_any_tool_enabled,
            "use_web_search": use_web_search,
            "use_mcp": use_mcp,
            "selected_tools": selected_tools,
            "selected_mcp_servers": selected_mcp_servers,
            "user_id": self.user_id,
            "tool_tier": data.get("tool_tier", TOOL_TIER_STANDARD),
        }

    async def _abuild_user_content(self, data: dict[str, Any]) -> dict[str, Any]:
        return await self._message_builder.abuild_user_content(data)

    def _build_user_content(self, data: dict[str, Any]) -> dict[str, Any]:
        return self._message_builder.build_user_content(data)

    async def _acreate_human_message(self, data: dict[str, Any]) -> HumanMessage:
        return await self._message_builder.acreate_human_message(data)

    def _create_human_message(self, data: dict[str, Any]) -> HumanMessage:
        return self._message_builder.create_human_message(data)

    def _load_research_context(
        self, research_task_id: str, user_id: int | None = None, session_id: str | None = None
    ) -> str | None:
        return self._context_service.load_research_context(research_task_id, user_id, session_id)

    def _apply_compaction(self, chat_history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._context_service.apply_compaction(chat_history)

    def _apply_context_engineering(
        self,
        chat_history: list[dict[str, Any]],
        query: str,
        mode: str = "agent",
        model_name: str | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return self._context_service.apply_context_engineering(
            chat_history,
            query,
            mode,
            model_name,
        )

    def _apply_context_engineering_for_checkpointer(
        self,
        user_message: str,
        model_name: str | None = None,
        mode: str = "agent",
    ) -> dict[str, Any]:
        return self._context_service.apply_context_engineering_for_checkpointer(
            user_message,
            model_name,
            mode,
        )

    async def process_chat_request(self, data: dict[str, Any]) -> dict[str, Any]:
        from Django_xm.apps.ai_engine.services.llm_factory import get_chat_model, get_model_string
        from Django_xm.apps.ai_engine.services.token_counter import TokenUsageCallbackHandler
        from Django_xm.apps.cache_manager.services.cache_service import ModelResponseCacheService

        parsed = parse_command(data.get("message", ""))
        if parsed:
            command_name, args = parsed
            context = {
                "args": args,
                "user_id": self.user_id,
                "session_id": data.get("session_id"),
                "messages": data.get("chat_history", []),
            }
            cmd_result = execute_command(command_name, context)
            return {
                "message": cmd_result.get("content", ""),
                "mode": data.get("mode", "agent"),
                "tools_used": [],
                "success": True,
                "is_command": True,
                "command_type": cmd_result.get("type", "info"),
            }

        mode = data.get("mode", "agent")

        cached_response = ModelResponseCacheService.get_cached_response(data["message"], get_model_string(), mode)
        if cached_response is not None:
            logger.info("模型响应缓存命中")
            return cached_response["response"]

        rag_result = self._rag_service.process_rag_request(data)
        if rag_result is not None:
            return rag_result

        tools = await self._get_tools(data)
        tool_config = self._build_tool_config(data)

        # 在创建 agent 之前加载研究上下文，以便注入到 system_prompt
        research_context = await sync_to_async(self._load_research_context)(
            data.get("research_task_id", ""), user_id=self.user_id, session_id=data.get("session_id")
        )
        if research_context:
            data["_research_system_prompt"] = (
                "## 深度研究参考内容\n\n"
                "以下是用户之前完成的深度研究报告，请在回答时参考这些信息：\n\n"
                f"{research_context}"
            )
            data["_has_research_context"] = True

        agent, thread_config, use_checkpointer = await self._create_agent_with_memory(
            data,
            prompt_mode=data["mode"],
            tool_config=tool_config,
            tools=tools,
        )

        user_content = self._build_user_content(data)
        human_msg = HumanMessage(content=user_content["content"])

        if use_checkpointer:
            _ce_metadata = self._apply_context_engineering_for_checkpointer(
                user_message=data.get("message", ""),
                model_name=data.get("model_name"),
                mode=data.get("mode", "agent"),
            )
            # Checkpointer 模式：将研究上下文作为 SystemMessage 注入到 human_msg 之前
            if research_context:
                from langchain_core.messages import SystemMessage

                research_system_msg = SystemMessage(content=data["_research_system_prompt"])
                graph_input = {"messages": [research_system_msg, human_msg]}
            else:
                graph_input = {"messages": [human_msg]}
            invoke_config = thread_config
        else:
            chat_history = data.get("chat_history", [])
            chat_history, _ce_metadata = self._apply_context_engineering(
                chat_history,
                data.get("message", ""),
                mode=data.get("mode", "agent"),
                model_name=data.get("model_name"),
            )
            langchain_chat_history = convert_chat_history(chat_history)
            messages = list(langchain_chat_history) if langchain_chat_history else []
            # 非 Checkpointer 模式：将研究上下文作为 SystemMessage 注入到对话历史最前面
            if research_context:
                from langchain_core.messages import SystemMessage

                research_system_msg = SystemMessage(content=data["_research_system_prompt"])
                messages.insert(0, research_system_msg)
            messages.append(human_msg)
            graph_input = {"messages": messages}
            invoke_config = {"recursion_limit": 500}

        with TokenUsageCallbackHandler() as cb:
            invoke_config["callbacks"] = [cb]
            result = await agent.graph.ainvoke(graph_input, config=invoke_config)

        output_messages = result.get("messages", [])
        response = ""
        for msg in reversed(output_messages):
            if isinstance(msg, AIMessage):
                response = msg.content
                break

        if _needs_completion(response):
            model = get_chat_model()
            prompt = (
                f"用户问题：{data['message']}\n\n"
                f"当前回复（不完整）：{response}\n\n"
                "请继续并完整回答上述问题，补充必要的解释或例子，最后给出一句简明结论。"
            )
            with TokenUsageCallbackHandler() as cb_comp:
                completion = await model.ainvoke(
                    [{"role": "user", "content": prompt}],
                    config={"callbacks": [cb_comp]},
                )
            if getattr(completion, "content", None):
                response = completion.content

        tool_names = [tool.name for tool in tools]
        logger.info(f"聊天请求处理完成，响应长度: {len(response)} 字符")

        result = {"message": response, "mode": data["mode"], "tools_used": tool_names, "success": True}

        ModelResponseCacheService.cache_model_response(data["message"], result, get_model_string(), mode)

        return result

    async def process_stream_chat_request(self, data: dict[str, Any]) -> AsyncGenerator[dict[str, Any], None]:
        """流式聊天请求入口：初始化追踪器，分发模式，处理响应"""
        from Django_xm.apps.ai_engine.services.cost_tracker import create_token_detail_tracker
        from Django_xm.apps.ai_engine.services.usage_tracker import create_usage_tracker

        model_name = data.get("model_name")
        from Django_xm.apps.ai_engine.config import settings as ai_settings

        tracker_model_id = model_name or ai_settings.openai_model
        usage_tracker = create_usage_tracker(model_id=tracker_model_id)
        token_detail_tracker = create_token_detail_tracker()
        stream_start_time = time.time()

        mode = data.get("mode", "agent")
        session_id = data.get("session_id", "N/A")
        msg_preview = data.get("message", "")[:80]
        research_task_id = data.get("research_task_id", "")
        logger.info(
            f"[StreamChat] 开始处理: mode={mode}, session={session_id}, msg={msg_preview}..., research_task_id={research_task_id or '(无)'}"
        )

        yield {"type": "start", "message": "开始生成..."}

        async for event in self._dispatch_by_mode(
            data,
            usage_tracker,
            token_detail_tracker,
        ):
            yield event

        context_info = build_context_info(usage_tracker, token_detail_tracker, stream_start_time)
        yield {"type": "context", "data": context_info}
        yield {"type": "end", "message": "生成完成"}
        usage_tracker.log_summary()
        token_detail_tracker.log_summary()

        session_id_for_update = data.get("session_id")
        if session_id_for_update is None:
            raise ValueError("session_id is required for streaming chat")
        await self._update_last_message_tokens(
            session_id=session_id_for_update,
            token_count=usage_tracker.get_total_tokens(),
            token_detail=token_detail_tracker.get_token_detail(),
            model=usage_tracker.model_id,
            response_time=round(time.time() - stream_start_time, 2),
        )
        logger.info("流式聊天请求处理完成")

    async def _dispatch_by_mode(
        self,
        data: dict[str, Any],
        usage_tracker,
        token_detail_tracker,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """按模式分发请求：slash 命令 / deep-research / agent"""
        mode = data.get("mode", "agent")

        # 1. 解析 slash 命令
        parsed = parse_command(data.get("message", ""))
        if parsed:
            command_name, args = parsed
            context = {
                "args": args,
                "user_id": self.user_id,
                "session_id": data.get("session_id"),
                "messages": data.get("chat_history", []),
                "token_info": token_detail_tracker.get_summary(),
            }
            cmd_result = execute_command(command_name, context)
            yield {
                "type": "command",
                "data": cmd_result,
                "content": cmd_result.get("content", ""),
            }
            yield {"type": "end", "message": "命令执行完成"}
            return

        # 2. deep-research 模式 → 多步骤工作流
        if mode == "deep-research":
            if data.get("use_knowledge_base"):
                kb_ids = data.get("selected_knowledge_bases") or []
                single_kb = data.get("selected_knowledge_base")
                if single_kb and single_kb not in kb_ids:
                    kb_ids.append(single_kb)

                if kb_ids:
                    from Django_xm.apps.knowledge.services.kb_service import list_knowledge_bases
                    from Django_xm.apps.knowledge.services.retrieval_service import create_retriever_tool

                    kb_info_map = {}
                    try:
                        kbs = await sync_to_async(list_knowledge_bases)(self._rag_service.user_id)
                        kb_info_map = {kb.get("id"): kb for kb in kbs}
                    except Exception:
                        # 知识库列表读取失败时回退到使用 kb_id 作为名称
                        logger.debug("读取知识库列表失败，使用 kb_id 作为名称")

                    for kb_id in kb_ids:
                        retriever = await sync_to_async(self._rag_service.get_rag_retriever)(
                            kb_id, retrieval_mode="comprehensive"
                        )
                        if retriever:
                            kb_info = kb_info_map.get(kb_id)
                            kb_name = kb_id
                            kb_desc = ""
                            if kb_info:
                                kb_name = kb_info.get("name", kb_id)
                                kb_desc = kb_info.get("description", "")

                            tool_name = f"knowledge_base_{kb_id}".replace("-", "_").replace(" ", "_")
                            tool_desc = f"搜索知识库「{kb_name}」中的相关信息。"
                            if kb_desc:
                                tool_desc += f" 知识库描述: {kb_desc}"

                            retriever_tool = create_retriever_tool(
                                retriever,
                                name=tool_name,
                                description=tool_desc,
                                retrieval_mode="comprehensive",
                                kb_name=kb_name,
                                kb_description=kb_desc,
                            )
                            data.setdefault("_retriever_tool_list", []).append(retriever_tool)

                    if data.get("_retriever_tool_list"):
                        data["_retriever_tool"] = data["_retriever_tool_list"][0]
                elif data.get("selected_knowledge_base"):
                    single_kb_id = data["selected_knowledge_base"]
                    retriever = await sync_to_async(self._rag_service.get_rag_retriever)(
                        single_kb_id, retrieval_mode="comprehensive"
                    )
                    if retriever:
                        from Django_xm.apps.knowledge.services.kb_service import list_knowledge_bases
                        from Django_xm.apps.knowledge.services.retrieval_service import create_retriever_tool

                        single_kb_name = single_kb_id
                        single_kb_desc = ""
                        try:
                            kbs = await sync_to_async(list_knowledge_bases)(self._rag_service.user_id)
                            kb_info = next((kb for kb in kbs if kb.get("id") == single_kb_id), None)
                            if kb_info:
                                single_kb_name = kb_info.get("name", single_kb_id)
                                single_kb_desc = kb_info.get("description", "")
                        except Exception:
                            # 知识库信息读取失败时回退到使用 kb_id 作为名称
                            logger.debug("读取知识库 %s 信息失败", single_kb_id)

                        retriever_tool = create_retriever_tool(
                            retriever,
                            retrieval_mode="comprehensive",
                            kb_name=single_kb_name,
                            kb_description=single_kb_desc,
                        )
                        data["_retriever_tool"] = retriever_tool
            # MCP 工具和用户选择工具
            data["_deep_extra_tools"] = await self._tool_service.get_deep_research_tools(data)
            if data.get("_retriever_tool_list") and len(data["_retriever_tool_list"]) > 1:
                data.setdefault("_deep_extra_tools", []).extend(data["_retriever_tool_list"][1:])
            async for event in self._create_agent_for_mode("deep-research", data, usage_tracker, token_detail_tracker):
                yield event
            return

        # 3. agent 模式 → Agent（动态判断是否使用工具）
        if mode == "agent":
            if data.get("use_knowledge_base"):
                kb_ids = data.get("selected_knowledge_bases") or []
                single_kb = data.get("selected_knowledge_base")
                if single_kb and single_kb not in kb_ids:
                    kb_ids.append(single_kb)

                for kb_id in kb_ids:
                    retriever = await sync_to_async(self._rag_service.get_rag_retriever)(
                        kb_id, retrieval_mode="precise"
                    )
                    if retriever:
                        try:
                            comprehensive_retriever = await sync_to_async(self._rag_service.get_rag_retriever)(
                                kb_id, retrieval_mode="comprehensive"
                            )
                        except Exception as e:
                            logger.warning(f"创建 comprehensive 检索器失败: {e}")
                            comprehensive_retriever = None

                        from Django_xm.apps.ai_engine.services.llm_factory import get_helper_model
                        from Django_xm.apps.knowledge.services.kb_service import list_knowledge_bases
                        from Django_xm.apps.knowledge.services.retrieval_service import create_retriever_tool

                        kb_info = None
                        try:
                            kbs = await sync_to_async(list_knowledge_bases)(self._rag_service.user_id)
                            kb_info = next((kb for kb in kbs if kb.get("id") == kb_id), None)
                        except Exception:
                            # 知识库信息读取失败时回退到使用 kb_id 作为名称
                            logger.debug("读取知识库 %s 信息失败", kb_id)

                        kb_name = kb_id
                        kb_desc = ""
                        if kb_info:
                            kb_name = kb_info.get("name", kb_id)
                            kb_desc = kb_info.get("description", "")

                        tool_name = f"knowledge_base_{kb_id}".replace("-", "_").replace(" ", "_")
                        tool_desc = f"搜索知识库「{kb_name}」中的相关信息。"
                        if kb_desc:
                            tool_desc += f" 知识库描述: {kb_desc}"

                        retriever_tool = create_retriever_tool(
                            retriever,
                            name=tool_name,
                            description=tool_desc,
                            retrieval_mode="auto",
                            comprehensive_retriever=comprehensive_retriever,
                            llm=get_helper_model(),
                            kb_name=kb_name,
                            kb_description=kb_desc,
                        )
                        data.setdefault("_extra_tools", []).append(retriever_tool)

            # 深度思考叠加
            if data.get("use_deep_thinking"):
                from Django_xm.apps.ai_engine.services.llm_factory import model_supports_capability

                provider_id = data.get("provider_id", "")
                model_name = data.get("model_name", "")
                if model_supports_capability(provider_id, model_name, "deep_thinking"):
                    data["_enable_deep_thinking"] = True

            # 动态判断 use_tools：如果用户没有开启任何工具/能力，则不使用工具
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

            async for event in self._process_normal_stream_chat(data, usage_tracker, token_detail_tracker):
                yield event
            return

        # 默认 fallback
        async for event in self._process_normal_stream_chat(data, usage_tracker, token_detail_tracker):
            yield event

    async def _create_agent_for_mode(
        self,
        mode: str,
        data: dict[str, Any],
        usage_tracker,
        token_detail_tracker,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """为特定模式创建并执行 Agent 流，返回事件流"""
        if mode == "deep-research":
            # 深度研究模式架构（chat SSE 立即返回）：
            # 1. 创建深度研究任务记录（create_deep_research_task，不启动 Celery）
            # 2. 发送 deep_research 事件，前端据此：
            #    - 设置 researchTaskId
            #    - 将消息 streamState 转为 INTERRUPTED
            # 3. 更新 ChatMessage 双向关联（research_task_id + message_id）
            # 4. 启动 Celery 任务（start_celery，不等待结果）
            # 5. 发送 interrupted 事件（触发 sse_generator 广播 stream_interrupted WebSocket 事件）
            # 6. chat SSE 立即结束（return），不持续等待研究完成
            #
            # 研究结果回写链路（完全独立于 chat SSE）：
            #   - Celery worker 完成/失败时调用 writeback_to_chat_message 回写 final_report 到 ChatMessage
            #   - Celery worker 调用 broadcast_stream_completed 广播 WebSocket 事件
            #   - 前端通过 WebSocket stream_completed 事件回写结果并转 COMPLETED
            #
            # 实时进度同步（跨浏览器）：
            #   - 审批 / 工具事件通过 WebSocket 统一推送
            #   - 非触发浏览器通过 stream_interrupted WebSocket 事件感知深度研究模式切换
            task_id = await self._deep_service.create_deep_research_task(
                data["message"],
                session_id=data.get("session_id"),
                use_web_search=data.get("use_web_search", True),
                retriever_tool=data.get("_retriever_tool"),
                task_title=data.get("_original_message"),
                selected_tools=data.get("selected_tools"),
                use_mcp=data.get("use_mcp", False),
                selected_mcp_servers=data.get("selected_mcp_servers"),
            )
            # 立即发送 deep_research 事件，让用户可以跳转到深度研究模块查看实时进度
            yield {
                "type": "deep_research",
                "data": {
                    "task_id": task_id,
                    "session_id": data.get("session_id", ""),
                },
            }
            # 将 research_task_id 写入 ChatMessage（双向关联），
            # 确保 sync_approval_state_to_chat_message 和 writeback_to_chat_message 能通过
            # research_task_id 查找到关联的 ChatMessage。
            assistant_msg_id = data.get("_assistant_message_id")
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
            from Django_xm.apps.ai_engine.services.llm_factory import model_supports_capability

            # 深度思考参数透传：前端开启时后端不再静默禁用，仅在不支持时发送 warning 提示
            use_deep_thinking = data.get("use_deep_thinking", False)
            enable_deep_thinking = use_deep_thinking
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
            # 启动 Celery 任务（不等待结果，Chat SSE 立即返回）
            await self._deep_service.start_celery(
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
            )
            # 通知前端 stream_interrupted（触发 sse_generator 广播 WebSocket 事件）
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
            # 立即结束 chat SSE 流，不发送 chunk（最终报告由 Celery worker 通过 WebSocket 回写）
            return

    async def _get_deep_research_tools(self, data: dict) -> list:
        """获取深度研究模式的额外工具（MCP + 用户选择）"""
        return await self._tool_service.get_deep_research_tools(data)

    async def _process_normal_stream_chat(
        self,
        data: dict[str, Any],
        usage_tracker,
        token_detail_tracker: TokenDetailTracker | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        from Django_xm.apps.ai_engine.services.token_counter import TokenUsageCallbackHandler

        tools = await self._get_tools(data)
        model_instance = self._resolve_model_instance(data)
        provider_id = data.get("provider_id")
        model_name = data.get("model_name")

        # 检测 LLM 降级：用户选择的模型创建失败，回退到默认模型
        if model_instance is None and provider_id:
            from Django_xm.apps.ai_engine.services.llm_factory import get_chat_model

            try:
                model_instance = get_chat_model(streaming=True)
                # LazyFallbackChatModel / RunnableWithFallbacks 包装了底层模型，需要从 .bound 获取
                bound_model = getattr(model_instance, "bound", model_instance)
                actual_provider = getattr(bound_model, "_provider_id", None)
                actual_model = getattr(bound_model, "model_name", None) or getattr(bound_model, "model", None)
                if actual_provider and actual_provider != provider_id:
                    yield {
                        "type": "model_fallback",
                        "data": {
                            "original_provider": provider_id,
                            "original_model": model_name,
                            "actual_provider": actual_provider,
                            "actual_model": actual_model,
                            "message": f"模型 {provider_id}/{model_name} 不可用，已自动切换到 {actual_provider}/{actual_model}",
                        },
                    }
                    # 自动更新 SystemConfig
                    try:
                        from Django_xm.apps.ai_engine.models import SystemConfig

                        SystemConfig.set_value(
                            "default_chat_model",
                            {
                                "provider_id": actual_provider,
                                "model_name": actual_model,
                            },
                        )
                    except Exception:
                        # 持久化 fallback 配置失败不影响当前会话，运行时已切换
                        logger.debug("持久化模型 fallback 配置到 SystemConfig 失败")
            except Exception:
                # fallback 检测失败不影响主流程，继续使用原模型
                logger.debug("检测 LLM 降级失败", exc_info=True)

        from Django_xm.apps.tools.langchain.agent_context import clear_parent_tool_context, set_parent_tool_context

        set_parent_tool_context(
            tools,
            {
                "use_web_search": data.get("use_web_search", False),
                "use_mcp": data.get("use_mcp", False),
                "user_id": self.user_id,
                "session_id": data.get("session_id"),
                "model_name": data.get("model"),
                "store": data.get("store"),
            },
        )

        tool_config = self._build_tool_config(data)

        # 在创建 agent 之前加载研究上下文，以便注入到 system_prompt
        research_context = await sync_to_async(self._load_research_context)(
            data.get("research_task_id", ""), user_id=self.user_id, session_id=data.get("session_id")
        )
        if research_context:
            # 将研究上下文注入到 system_prompt，确保每次对话都能看到
            data["_research_system_prompt"] = (
                "## 深度研究参考内容\n\n"
                "以下是用户之前完成的深度研究报告，请在回答时参考这些信息：\n\n"
                f"{research_context}"
            )
            data["_has_research_context"] = True

        agent, thread_config, use_checkpointer = await self._create_agent_with_memory(
            data,
            prompt_mode=data["mode"],
            model_instance=model_instance,
            tool_config=tool_config,
            tools=tools,
        )

        human_msg = await self._acreate_human_message(data)

        if use_checkpointer:
            # Checkpointer 模式下仍执行注入检测和预算分配
            self._apply_context_engineering_for_checkpointer(
                user_message=data.get("message", ""),
                model_name=data.get("model_name"),
                mode=data.get("mode", "agent"),
            )

            # 检查 checkpoint 中是否有 pending interrupt，如果有则自动拒绝
            # 避免用户在有 pending interrupt 时发新消息导致状态损坏
            try:
                state = await agent.graph.aget_state(thread_config)
                if state and state.tasks:
                    from langgraph.types import Command as LgCommand

                    for task in state.tasks:
                        if hasattr(task, "interrupts") and task.interrupts:
                            for intr in task.interrupts:
                                intr_id = intr.id if hasattr(intr, "id") else ""
                                if intr_id:
                                    await agent.graph.ainvoke(
                                        LgCommand(resume={intr_id: False}),
                                        config=thread_config,
                                    )
                                    logger.info(f"自动拒绝 pending interrupt: {intr_id}（用户发送了新消息）")
            except Exception as e:
                logger.warning(f"检查/清理 pending interrupt 失败（非致命）: {e}")

            # Checkpointer 模式：将研究上下文作为 SystemMessage 注入到 human_msg 之前
            if research_context:
                from langchain_core.messages import SystemMessage

                research_system_msg = SystemMessage(content=data["_research_system_prompt"])
                graph_input = {"messages": [research_system_msg, human_msg]}
            else:
                graph_input = {"messages": [human_msg]}
            config = thread_config
        else:
            chat_history = data.get("chat_history", [])
            user_message = data.get("message", "")
            model_name_for_budget = data.get("model_name")

            if self._context_service.check_injection(user_message):
                logger.warning("检测到指令注入尝试，用户输入将被隔离")

            chat_history, _ce_metadata = self._apply_context_engineering(
                chat_history,
                user_message,
                mode=data.get("mode", "agent"),
                model_name=model_name_for_budget,
            )
            langchain_chat_history = convert_chat_history(chat_history)
            messages = list(langchain_chat_history) if langchain_chat_history else []
            # 非 Checkpointer 模式：将研究上下文作为 SystemMessage 注入到对话历史最前面
            if research_context:
                from langchain_core.messages import SystemMessage

                research_system_msg = SystemMessage(content=data["_research_system_prompt"])
                messages.insert(0, research_system_msg)
            messages.append(human_msg)
            graph_input = {"messages": messages}
            config = {"recursion_limit": 500}

        # 创建 StreamContext（流式可变状态封装，替代散布的局部变量）
        ctx = StreamContext()
        ctx.init_stream_state(data.get("_stream_state"))
        # P25修复：注入 session_id / message_id，供 _handle_updates_chunk
        # 创建 Approval DB 记录时使用
        ctx.session_id = data.get("session_id", "")
        ctx.message_id = str(data.get("_assistant_message_id") or data.get("message_id", ""))

        # 根据 _enable_deep_thinking 选择策略
        strategy = DeepThinkingStreamStrategy() if data.get("_enable_deep_thinking") else NormalStreamStrategy()

        # 预创建降级 agent（用于 AgentExecutor 的 DEGRADE 分支）
        # AgentExecutor.rebuild_agent_fn 是同步回调，无法 await 异步 create_agent_with_memory，
        # 因此在主事件循环中预创建降级 agent（使用相同的降级工具集），
        # rebuild_agent_fn 仅返回预创建的实例。若预创建失败，DEGRADE 将落入 FALLBACK。
        degraded_tools_preview = get_degraded_tools(tools, DegradationLevel.REDUCED_TOOLS)
        degraded_agent_holder: dict[str, Any] = {}
        if tools and degraded_tools_preview and len(degraded_tools_preview) < len(tools):
            try:
                deg_agent, deg_config, _ = await asyncio.wait_for(
                    self._create_agent_with_memory(
                        data,
                        prompt_mode=data["mode"],
                        model_instance=model_instance,
                        tool_config=tool_config,
                        tools=degraded_tools_preview,
                    ),
                    timeout=10.0,
                )
                if deg_config is None:
                    deg_config = {"recursion_limit": 500}
                degraded_agent_holder["agent"] = deg_agent
                degraded_agent_holder["config"] = deg_config
            except asyncio.TimeoutError:
                logger.warning("预创建降级 agent 超时（DEGRADE 将落入 FALLBACK）")
            except Exception as e:
                logger.warning(f"预创建降级 agent 失败（DEGRADE 将落入 FALLBACK）: {e}")

        def rebuild_agent_fn(_degraded_tools: list) -> tuple[Any, dict]:
            """AgentExecutor 降级回调：返回预创建的降级 agent

            AgentExecutor 内部已通过 get_degraded_tools 计算降级工具集，
            并将其传入此回调。我们使用预创建的降级 agent（基于相同的降级工具集）。
            若预创建失败，抛异常使 AgentExecutor 落入 FALLBACK。
            """
            if "agent" not in degraded_agent_holder:
                raise RuntimeError("降级 agent 预创建失败或未创建")
            return degraded_agent_holder["agent"], degraded_agent_holder["config"]

        # 创建无工具回退服务（AgentExecutor 的 FALLBACK 分支使用）
        fallback_service = FallbackStreamService(self)

        # 创建 AgentExecutor（公共执行器，提供重试/降级/超时/回退）
        # 替代原内联的 retry/timeout/degrade 循环
        executor = AgentExecutor(
            fallback_service=fallback_service,
            rebuild_agent_fn=rebuild_agent_fn,
            tools=tools,
            model_instance=model_instance,
            data=data,
            usage_tracker=usage_tracker,
            token_detail_tracker=token_detail_tracker,
        )

        with TokenUsageCallbackHandler() as cb:
            from Django_xm.apps.ai_engine.services.llm_fallback import FallbackDetectionCallback

            fb_callback = FallbackDetectionCallback(
                expected_provider=provider_id or "",
                expected_model=model_name or "",
            )
            config["callbacks"] = [cb, fb_callback]

            # 执行 AgentExecutor（带韧性的流式执行）
            # 替代原内联的 retry/timeout/degrade 循环：
            # - GraphRecursionError → 优雅降级（用已收集内容），不重试
            # - 可恢复异常 → RETRY + 退避
            # - 不可恢复但可降级 → DEGRADE（用减少的工具重试，由 rebuild_agent_fn 重建）
            # - 不可恢复 → FALLBACK（无工具纯对话，由 fallback_service 处理）
            # - soft timeout → 警告一次
            # - hard timeout → FALLBACK
            async for event in executor.run(
                run_stream_loop,
                agent,
                graph_input,
                config,
                ctx,
                strategy,
                data,
            ):
                yield event

        # finalize 阶段：循环后统一处理（由 stream 子包统一实现）
        # 包括：update_usage / LLM fallback 检测 / tool_usage 统计 /
        #       审批中断收尾 / _pending_content 刷新 / 深度思考兜底 /
        #       reasoning 完成事件 / finalize_tool_calls / 补发 + 补全检查 + 建议生成
        async for event in finalize_stream(
            ctx,
            data,
            tools,
            model_instance,
            strategy,
            cb,
            fb_callback,
            usage_tracker,
            token_detail_tracker,
        ):
            yield event

        clear_parent_tool_context()

    async def _finalize_stream_response(
        self,
        all_messages: list,
        current_message_content: str,
        tool_calls_map: dict[str, dict],
        prefer_tool_result: bool,
        data: dict[str, Any],
        weather_tool_names: set,
        model_instance=None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        from Django_xm.apps.ai_engine.services.llm_factory import get_chat_model

        final_ai_message = None
        for msg in reversed(all_messages):
            if isinstance(msg, AIMessage) and isinstance(msg.content, str) and msg.content.strip():
                final_ai_message = msg
                break

        # Agent 模式下，_pending_content 已刷新完整内容，跳过 final_ai_message 补发
        # 避免 current_message_content 与 final_ai_message.content 不完全一致时重复发送
        mode = data.get("mode", "agent")
        if final_ai_message and isinstance(final_ai_message.content, str) and mode != "agent":
            final_content = final_ai_message.content
            if len(final_content) > len(current_message_content):
                remaining_content = final_content[len(current_message_content) :]
                if remaining_content:
                    yield {"type": "chunk", "content": remaining_content}
                    current_message_content = final_content

        # 计算 final_ai_message 内容的 strip 长度（用于判断是否需要兜底）
        # msg.content 类型为 str | list[str | dict]，仅 str 可调用 .strip()
        final_ai_content = final_ai_message.content if final_ai_message else None
        final_ai_strip_len = len(final_ai_content.strip()) if isinstance(final_ai_content, str) else 0
        if (not final_ai_message or not final_ai_content or final_ai_strip_len < 10) and tool_calls_map:
            weather_tools = ["weather_query", "get_weather_forecast", "get_weather"]
            for tool_name in weather_tools:
                for tool_info in tool_calls_map.values():
                    if (
                        tool_info.get("name") == tool_name
                        and tool_info.get("state") == "output-available"
                        and tool_info.get("result")
                    ):
                        result_content = tool_info.get("result", "")
                        if isinstance(result_content, list):
                            result_content = str(result_content)
                        if result_content and result_content not in current_message_content:
                            yield {"type": "chunk", "content": result_content}
                        break
                else:
                    continue
                break
            else:
                # 排除返回大量原始内容的工具，其结果不应直接作为聊天文本输出
                raw_content_tools = {"web_fetch", "web_search", "skill_web_research"}
                for tool_info in tool_calls_map.values():
                    result = tool_info.get("result")
                    tool_name = tool_info.get("name", "")
                    # 知识库检索工具和原始内容工具的结果不应作为聊天文本
                    is_raw_content = (
                        tool_name in raw_content_tools
                        or tool_name.startswith("knowledge_base_")
                        or tool_info.get("_summarized")
                    )
                    if (
                        tool_info.get("state") == "output-available"
                        and result
                        and not is_raw_content
                        and (isinstance(result, str) and result not in current_message_content)
                    ):
                        result_content = result if isinstance(result, str) else str(result)
                        if result_content:
                            yield {"type": "chunk", "content": result_content}
                        break

        # Agent 模式下跳过补全检查：Agent 已生成完整回答，
        # _needs_completion 的"补全"会触发模型重新生成完整回答，导致内容重复
        mode = data.get("mode", "agent")
        if mode != "agent" and not prefer_tool_result and _needs_completion(current_message_content):
            model = model_instance or get_chat_model()
            prompt = (
                f"用户问题：{data['message']}\n\n"
                f"当前回复（不完整）：{current_message_content}\n\n"
                "请继续并完整回答上述问题，补充必要的解释或例子，最后给出一句简明结论。"
            )
            try:
                completion = await model.ainvoke([{"role": "user", "content": prompt}])
                extra = getattr(completion, "content", "")
                if extra:
                    yield {"type": "chunk", "content": extra}
                    current_message_content += extra
            except Exception:
                # 补充回复失败不影响主流程，已有不完整回复
                logger.debug("生成补充回复失败")

        try:
            model = model_instance or get_chat_model()
            suggestions_prompt = (
                "你是一个辅助对话的助手。请根据以下用户问题和最终回复，生成4条简洁、相关、可点击的后续问题建议。\n"
                "用JSON数组返回，每个元素是不超过30字的中文字符串，不要包含编号或多余文本。\n\n"
                f"用户问题：{data['message']}\n\n"
                f"最终回复：{current_message_content}"
            )
            completion = await model.ainvoke([{"role": "user", "content": suggestions_prompt}])
            raw = getattr(completion, "content", "")
            suggestions = extract_suggestions(raw)
            if suggestions:
                yield {"type": "suggestions", "data": suggestions}
        except Exception:
            # 建议生成失败不影响主流程
            logger.debug("生成后续问题建议失败")


class ChatModeService:
    """聊天模式服务类"""

    FRONTEND_SUPPORTED_MODES = ("agent", "deep-research")
    DEFAULT_MODE = "agent"

    @classmethod
    def get_supported_modes(cls) -> dict[str, str]:
        from Django_xm.apps.ai_engine.prompts.system_prompts import SYSTEM_PROMPTS

        modes = {}
        for mode_name in cls.FRONTEND_SUPPORTED_MODES:
            prompt = SYSTEM_PROMPTS.get(mode_name)
            if prompt is None:
                prompt = SYSTEM_PROMPTS.get("default")
            if prompt:
                modes[mode_name] = prompt.split("\n")[0]
        return modes
