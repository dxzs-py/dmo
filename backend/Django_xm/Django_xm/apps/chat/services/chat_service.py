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
import time
import logging
from asgiref.sync import sync_to_async
from typing import Optional, Dict, Any, List, AsyncGenerator, Tuple

from langchain_core.messages import HumanMessage, AIMessage

from Django_xm.apps.ai_engine.services.cost_tracker import TokenDetailTracker
from Django_xm.apps.chat.services.slash_commands import parse_command, execute_command
from .agent_service import AgentService
from .chat_message_builder import ChatMessageBuilder
from .context_service import ContextService
from .tool_service import ToolService
from .rag_chat_service import RAGChatService
from .deep_chat_service import DeepChatService
from .stream_helpers import (
    process_stream_chunk,
    build_context_info,
    update_usage_and_tokens,
    finalize_tool_calls,
)
from ..utils import _needs_completion, _lcp_len, convert_chat_history, extract_suggestions

logger = logging.getLogger(__name__)

from .models_context import ChatContext


class ChatService:
    """聊天服务类 - 处理聊天相关的业务逻辑

    职责：业务流程编排和协调，具体实现委托给子服务：
    - AgentService: Agent 创建和管理
    - ChatMessageBuilder: 消息构建和转换
    - ContextService: 上下文工程
    - ToolService: 工具管理
    """

    CHECKPOINTER_ENABLED = True

    def __init__(self, user_id: Optional[int] = None, thread_id: Optional[str] = None):
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
            self.user_id, session_id, token_count, token_detail, model, response_time,
        )

    def _build_thread_config(self, session_id: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        return self._agent_service.build_thread_config(session_id, **kwargs)

    async def _create_agent_with_memory(
        self,
        data: Dict[str, Any],
        prompt_mode: str = "default",
        model_instance=None,
        tool_config: Optional[Dict[str, Any]] = None,
        tools: Optional[list] = None,
    ) -> tuple:
        return await self._agent_service.create_agent_with_memory(
            data, prompt_mode, model_instance, tool_config=tool_config, tools=tools,
        )

    @staticmethod
    def _resolve_model_instance(data: Dict[str, Any], streaming: bool = True):
        return AgentService.resolve_model_instance(data, streaming)

    @staticmethod
    def _resolve_kb_ids(data: Dict[str, Any]) -> Optional[List[str]]:
        if not data.get('use_knowledge_base'):
            return None
        kb_ids = list(data.get('selected_knowledge_bases') or [])
        single_kb = data.get('selected_knowledge_base')
        if single_kb and single_kb not in kb_ids:
            kb_ids.append(single_kb)
        return kb_ids if kb_ids else None

    async def _get_tools(self, data: Dict[str, Any]) -> List:
        return await self._tool_service.get_tools(data)

    def _build_tool_config(self, data: Dict[str, Any]) -> Dict[str, Any]:
        from Django_xm.apps.tools import TOOL_TIER_STANDARD
        use_web_search = data.get('use_web_search', False)
        use_mcp = data.get('use_mcp', False)
        selected_tools = data.get('selected_tools')
        selected_mcp_servers = data.get('selected_mcp_servers')
        use_knowledge_base = data.get('use_knowledge_base', False)
        # 尊重前端显式传递的 use_tools 参数
        explicit_use_tools = data.get('use_tools')
        if explicit_use_tools is not None:
            has_any_tool_enabled = bool(explicit_use_tools)
        else:
            has_any_tool_enabled = bool(
                use_web_search or use_mcp or use_knowledge_base or selected_tools
            )
        return {
            "use_tools": has_any_tool_enabled,
            "use_web_search": use_web_search,
            "use_mcp": use_mcp,
            "selected_tools": selected_tools,
            "selected_mcp_servers": selected_mcp_servers,
            "user_id": self.user_id,
            "tool_tier": data.get('tool_tier', TOOL_TIER_STANDARD),
        }

    async def _abuild_user_content(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return await self._message_builder.abuild_user_content(data)

    def _build_user_content(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return self._message_builder.build_user_content(data)

    async def _acreate_human_message(self, data: Dict[str, Any]) -> HumanMessage:
        return await self._message_builder.acreate_human_message(data)

    def _create_human_message(self, data: Dict[str, Any]) -> HumanMessage:
        return self._message_builder.create_human_message(data)

    def _load_research_context(self, research_task_id: str, user_id: Optional[int] = None) -> Optional[str]:
        return self._context_service.load_research_context(research_task_id, user_id)

    def _apply_compaction(self, chat_history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return self._context_service.apply_compaction(chat_history)

    def _apply_context_engineering(
        self,
        chat_history: List[Dict[str, Any]],
        query: str,
        mode: str = "agent",
        model_name: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        return self._context_service.apply_context_engineering(
            chat_history, query, mode, model_name,
        )

    def _apply_context_engineering_for_checkpointer(
        self,
        user_message: str,
        model_name: Optional[str] = None,
        mode: str = "agent",
    ) -> Dict[str, Any]:
        return self._context_service.apply_context_engineering_for_checkpointer(
            user_message, model_name, mode,
        )

    async def process_chat_request(self, data: Dict[str, Any]) -> Dict[str, Any]:
        from Django_xm.apps.ai_engine.services.token_counter import TokenUsageCallbackHandler
        from Django_xm.apps.ai_engine.services.llm_factory import get_chat_model, get_model_string
        from Django_xm.apps.cache_manager.services.cache_service import ModelResponseCacheService

        parsed = parse_command(data.get('message', ''))
        if parsed:
            command_name, args = parsed
            context = {
                "args": args,
                "user_id": self.user_id,
                "session_id": data.get('session_id'),
                "messages": data.get('chat_history', []),
            }
            cmd_result = execute_command(command_name, context)
            return {
                'message': cmd_result.get('content', ''),
                'mode': data.get('mode', 'agent'),
                'tools_used': [],
                'success': True,
                'is_command': True,
                'command_type': cmd_result.get('type', 'info'),
            }

        mode = data.get('mode', 'agent')

        cached_response = ModelResponseCacheService.get_cached_response(
            data['message'], get_model_string(), mode
        )
        if cached_response is not None:
            logger.info("模型响应缓存命中")
            return cached_response['response']

        rag_result = self._rag_service.process_rag_request(data)
        if rag_result is not None:
            return rag_result

        tools = await self._get_tools(data)
        tool_config = self._build_tool_config(data)

        # 在创建 agent 之前加载研究上下文，以便注入到 system_prompt
        research_context = await sync_to_async(self._load_research_context)(
            data.get('research_task_id', ''), user_id=self.user_id
        )
        if research_context:
            data['_research_system_prompt'] = (
                "## 深度研究参考内容\n\n"
                "以下是用户之前完成的深度研究报告，请在回答时参考这些信息：\n\n"
                f"{research_context}"
            )
            data['_has_research_context'] = True

        agent, thread_config, use_checkpointer = await self._create_agent_with_memory(
            data, prompt_mode=data['mode'], tool_config=tool_config, tools=tools,
        )

        user_content = self._build_user_content(data)
        human_msg = HumanMessage(content=user_content["content"])

        if use_checkpointer:
            ce_metadata = self._apply_context_engineering_for_checkpointer(
                user_message=data.get('message', ''),
                model_name=data.get('model_name'),
                mode=data.get('mode', 'agent'),
            )
            # Checkpointer 模式：将研究上下文作为 SystemMessage 注入到 human_msg 之前
            if research_context:
                from langchain_core.messages import SystemMessage
                research_system_msg = SystemMessage(content=data['_research_system_prompt'])
                graph_input = {"messages": [research_system_msg, human_msg]}
            else:
                graph_input = {"messages": [human_msg]}
            invoke_config = thread_config
        else:
            chat_history = data.get('chat_history', [])
            chat_history, _ce_metadata = self._apply_context_engineering(
                chat_history, data.get('message', ''), mode=data.get('mode', 'agent'),
                model_name=data.get('model_name'),
            )
            langchain_chat_history = convert_chat_history(chat_history)
            messages = list(langchain_chat_history) if langchain_chat_history else []
            # 非 Checkpointer 模式：将研究上下文作为 SystemMessage 注入到对话历史最前面
            if research_context:
                from langchain_core.messages import SystemMessage
                research_system_msg = SystemMessage(content=data['_research_system_prompt'])
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

        result = {
            'message': response,
            'mode': data['mode'],
            'tools_used': tool_names,
            'success': True
        }

        ModelResponseCacheService.cache_model_response(
            data['message'], result, get_model_string(), mode
        )

        return result

    async def process_stream_chat_request(self, data: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        """流式聊天请求入口：初始化追踪器，分发模式，处理响应"""
        from Django_xm.apps.ai_engine.services.usage_tracker import create_usage_tracker
        from Django_xm.apps.ai_engine.services.cost_tracker import create_token_detail_tracker

        provider_id = data.get('provider_id')
        model_name = data.get('model_name')
        from Django_xm.apps.ai_engine.config import settings as ai_settings
        tracker_model_id = model_name or ai_settings.openai_model
        usage_tracker = create_usage_tracker(model_id=tracker_model_id)
        token_detail_tracker = create_token_detail_tracker()
        stream_start_time = time.time()

        mode = data.get('mode', 'agent')
        session_id = data.get('session_id', 'N/A')
        msg_preview = data.get('message', '')[:80]
        research_task_id = data.get('research_task_id', '')
        logger.info(f"[StreamChat] 开始处理: mode={mode}, session={session_id}, msg={msg_preview}..., research_task_id={research_task_id or '(无)'}")

        yield {'type': 'start', 'message': '开始生成...'}

        async for event in self._dispatch_by_mode(
            data, usage_tracker, token_detail_tracker,
        ):
            yield event

        context_info = build_context_info(usage_tracker, token_detail_tracker, stream_start_time)
        yield {'type': 'context', 'data': context_info}
        yield {'type': 'end', 'message': '生成完成'}
        usage_tracker.log_summary()
        token_detail_tracker.log_summary()

        await self._update_last_message_tokens(
            session_id=data.get('session_id'),
            token_count=usage_tracker.get_total_tokens(),
            token_detail=token_detail_tracker.get_token_detail(),
            model=usage_tracker.model_id,
            response_time=round(time.time() - stream_start_time, 2),
        )
        logger.info("流式聊天请求处理完成")

    async def _dispatch_by_mode(
        self,
        data: Dict[str, Any],
        usage_tracker,
        token_detail_tracker,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """按模式分发请求：slash 命令 / deep-research / agent"""
        mode = data.get('mode', 'agent')

        # 1. 解析 slash 命令
        parsed = parse_command(data.get('message', ''))
        if parsed:
            command_name, args = parsed
            context = {
                "args": args,
                "user_id": self.user_id,
                "session_id": data.get('session_id'),
                "messages": data.get('chat_history', []),
                "token_info": token_detail_tracker.get_summary(),
            }
            cmd_result = execute_command(command_name, context)
            yield {
                'type': 'command',
                'data': cmd_result,
                'content': cmd_result.get('content', ''),
            }
            yield {'type': 'end', 'message': '命令执行完成'}
            return

        # 2. deep-research 模式 → 多步骤工作流
        if mode == 'deep-research':
            if data.get('use_knowledge_base'):
                kb_ids = data.get('selected_knowledge_bases') or []
                single_kb = data.get('selected_knowledge_base')
                if single_kb and single_kb not in kb_ids:
                    kb_ids.append(single_kb)

                if kb_ids:
                    from Django_xm.apps.knowledge.services.retrieval_service import create_retriever_tool
                    from Django_xm.apps.knowledge.services.kb_service import list_knowledge_bases

                    kb_info_map = {}
                    try:
                        kbs = await sync_to_async(list_knowledge_bases)(self._rag_service.user_id)
                        kb_info_map = {kb.get('id'): kb for kb in kbs}
                    except Exception:
                        pass

                    for kb_id in kb_ids:
                        retriever = await sync_to_async(self._rag_service.get_rag_retriever)(kb_id, retrieval_mode="comprehensive")
                        if retriever:
                            kb_info = kb_info_map.get(kb_id)
                            kb_name = kb_id
                            kb_desc = ""
                            if kb_info:
                                kb_name = kb_info.get('name', kb_id)
                                kb_desc = kb_info.get('description', '')

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
                            data.setdefault('_retriever_tool_list', []).append(retriever_tool)

                    if data.get('_retriever_tool_list'):
                        data['_retriever_tool'] = data['_retriever_tool_list'][0]
                elif data.get('selected_knowledge_base'):
                    single_kb_id = data['selected_knowledge_base']
                    retriever = await sync_to_async(self._rag_service.get_rag_retriever)(single_kb_id, retrieval_mode="comprehensive")
                    if retriever:
                        from Django_xm.apps.knowledge.services.retrieval_service import create_retriever_tool
                        from Django_xm.apps.knowledge.services.kb_service import list_knowledge_bases

                        single_kb_name = single_kb_id
                        single_kb_desc = ""
                        try:
                            kbs = await sync_to_async(list_knowledge_bases)(self._rag_service.user_id)
                            kb_info = next((kb for kb in kbs if kb.get('id') == single_kb_id), None)
                            if kb_info:
                                single_kb_name = kb_info.get('name', single_kb_id)
                                single_kb_desc = kb_info.get('description', '')
                        except Exception:
                            pass

                        retriever_tool = create_retriever_tool(
                            retriever,
                            retrieval_mode="comprehensive",
                            kb_name=single_kb_name,
                            kb_description=single_kb_desc,
                        )
                        data['_retriever_tool'] = retriever_tool
            # MCP 工具和用户选择工具
            data['_deep_extra_tools'] = await self._tool_service.get_deep_research_tools(data)
            if data.get('_retriever_tool_list') and len(data['_retriever_tool_list']) > 1:
                data.setdefault('_deep_extra_tools', []).extend(data['_retriever_tool_list'][1:])
            async for event in self._create_agent_for_mode('deep-research', data, usage_tracker, token_detail_tracker):
                yield event
            return

        # 3. agent 模式 → Agent（动态判断是否使用工具）
        if mode == 'agent':
            if data.get('use_knowledge_base'):
                kb_ids = data.get('selected_knowledge_bases') or []
                single_kb = data.get('selected_knowledge_base')
                if single_kb and single_kb not in kb_ids:
                    kb_ids.append(single_kb)

                for kb_id in kb_ids:
                    retriever = await sync_to_async(self._rag_service.get_rag_retriever)(kb_id, retrieval_mode="precise")
                    if retriever:
                        try:
                            comprehensive_retriever = await sync_to_async(self._rag_service.get_rag_retriever)(kb_id, retrieval_mode="comprehensive")
                        except Exception as e:
                            logger.warning(f"创建 comprehensive 检索器失败: {e}")
                            comprehensive_retriever = None

                        from Django_xm.apps.knowledge.services.retrieval_service import create_retriever_tool
                        from Django_xm.apps.knowledge.services.kb_service import list_knowledge_bases
                        from Django_xm.apps.ai_engine.services.llm_factory import get_helper_model

                        kb_info = None
                        try:
                            kbs = await sync_to_async(list_knowledge_bases)(self._rag_service.user_id)
                            kb_info = next((kb for kb in kbs if kb.get('id') == kb_id), None)
                        except Exception:
                            pass

                        kb_name = kb_id
                        kb_desc = ""
                        if kb_info:
                            kb_name = kb_info.get('name', kb_id)
                            kb_desc = kb_info.get('description', '')

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
                        data.setdefault('_extra_tools', []).append(retriever_tool)

            # 深度思考叠加
            if data.get('use_deep_thinking'):
                from Django_xm.apps.ai_engine.services.llm_factory import model_supports_capability
                provider_id = data.get('provider_id', '')
                model_name = data.get('model_name', '')
                if model_supports_capability(provider_id, model_name, 'deep_thinking'):
                    data['_enable_deep_thinking'] = True

            # 动态判断 use_tools：如果用户没有开启任何工具/能力，则不使用工具
            # 尊重前端显式传递的 use_tools 参数
            explicit_use_tools = data.get('use_tools')
            if explicit_use_tools is not None:
                data['use_tools'] = bool(explicit_use_tools)
            else:
                use_web_search = data.get('use_web_search', False)
                use_mcp = data.get('use_mcp', False)
                use_knowledge_base = data.get('use_knowledge_base', False)
                selected_tools = data.get('selected_tools')
                has_any_tool_enabled = bool(
                    use_web_search or use_mcp or use_knowledge_base or selected_tools
                    or data.get('_extra_tools')
                )
                data['use_tools'] = has_any_tool_enabled

            async for event in self._process_normal_stream_chat(data, usage_tracker, token_detail_tracker):
                yield event
            return

        # 默认 fallback
        async for event in self._process_normal_stream_chat(data, usage_tracker, token_detail_tracker):
            yield event

    async def _create_agent_for_mode(
        self,
        mode: str,
        data: Dict[str, Any],
        usage_tracker,
        token_detail_tracker,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """为特定模式创建并执行 Agent 流，返回事件流"""
        if mode == 'deep-research':
            yield {
                "type": "reasoning",
                "data": {"content": "正在调度深度研究工作流并执行网络搜索...", "duration": 0},
            }
            # 先创建任务获取 task_id，以便前端尽早展示跳转链接
            task_id = await self._deep_service.create_deep_research_task(
                data['message'], session_id=data.get('session_id'),
                use_web_search=data.get('use_web_search', True),
                retriever_tool=data.get('_retriever_tool'),
            )
            # 立即发送 deep_research 事件，让用户可以跳转到深度研究模块查看实时进度
            yield {
                "type": "deep_research",
                "data": {
                    "task_id": task_id,
                    "session_id": data.get('session_id', ''),
                },
            }
            from Django_xm.apps.ai_engine.services.llm_factory import model_supports_capability
            deep_result = await self._deep_service.run_deep_research_task(
                data['message'], session_id=data.get('session_id'),
                usage_tracker=usage_tracker, token_detail_tracker=token_detail_tracker,
                use_web_search=data.get('use_web_search', True),
                retriever_tool=data.get('_retriever_tool'),
                extra_tools=data.get('_deep_extra_tools', []),
                enable_deep_thinking=data.get('use_deep_thinking', False) and model_supports_capability(
                    data.get('provider_id', ''), data.get('model_name', ''), 'deep_thinking'
                ),
                provider_id=data.get('provider_id'),
                model_name=data.get('model_name'),
                task_id=task_id,
                knowledge_base_ids=self._resolve_kb_ids(data),
                temperature=data.get('temperature'),
                max_tokens=data.get('max_tokens'),
                special_params=data.get('special_params'),
                continue_task_id=data.get('continue_task_id'),
            )
            final_report = deep_result.get("final_report") or deep_result.get("error")
            if not final_report:
                final_report = "深度研究已完成，但未生成可用报告。请稍后重试或调整问题表述。"
            yield {"type": "chunk", "content": final_report}
            yield {"type": "research_task_id", "data": {"research_task_id": task_id}}
            return

    async def _get_deep_research_tools(self, data: dict) -> list:
        """获取深度研究模式的额外工具（MCP + 用户选择）"""
        return await self._tool_service.get_deep_research_tools(data)

    async def _process_normal_stream_chat(
        self,
        data: Dict[str, Any],
        usage_tracker,
        token_detail_tracker: Optional[TokenDetailTracker] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        from Django_xm.apps.ai_engine.services.token_counter import TokenUsageCallbackHandler
        from Django_xm.apps.tools import WEATHER_TOOLS

        tools = await self._get_tools(data)
        weather_tool_names = {tool.name for tool in WEATHER_TOOLS}
        model_instance = self._resolve_model_instance(data)
        provider_id = data.get('provider_id')
        model_name = data.get('model_name')

        # 检测 LLM 降级：用户选择的模型创建失败，回退到默认模型
        if model_instance is None and provider_id:
            from Django_xm.apps.ai_engine.services.llm_factory import get_chat_model
            try:
                model_instance = get_chat_model(streaming=True)
                # LazyFallbackChatModel / RunnableWithFallbacks 包装了底层模型，需要从 .bound 获取
                bound_model = getattr(model_instance, 'bound', model_instance)
                actual_provider = getattr(bound_model, '_provider_id', None)
                actual_model = getattr(bound_model, 'model_name', None) or getattr(bound_model, 'model', None)
                if actual_provider and actual_provider != provider_id:
                    yield {
                        'type': 'model_fallback',
                        'data': {
                            'original_provider': provider_id,
                            'original_model': model_name,
                            'actual_provider': actual_provider,
                            'actual_model': actual_model,
                            'message': f'模型 {provider_id}/{model_name} 不可用，已自动切换到 {actual_provider}/{actual_model}',
                        }
                    }
                    # 自动更新 SystemConfig
                    try:
                        from Django_xm.apps.ai_engine.models import SystemConfig
                        SystemConfig.set_value("default_chat_model", {
                            "provider_id": actual_provider,
                            "model_name": actual_model,
                        })
                    except Exception:
                        pass
            except Exception:
                pass

        from Django_xm.apps.tools.langchain.agent_context import set_parent_tool_context, clear_parent_tool_context
        set_parent_tool_context(tools, {
            "use_web_search": data.get('use_web_search', False),
            "use_mcp": data.get('use_mcp', False),
            "user_id": self.user_id,
            "session_id": data.get('session_id'),
            "model_name": data.get('model'),
            "store": data.get('store'),
        })

        # 深度思考叠加：调用 DeepChatService 处理流式输出
        if data.get('_enable_deep_thinking'):
            async for event in self._deep_service.process_deep_thinking_stream(
                data, usage_tracker, token_detail_tracker, tools=tools, model_instance=model_instance,
            ):
                yield event
            clear_parent_tool_context()
            return

        tool_config = self._build_tool_config(data)

        # 在创建 agent 之前加载研究上下文，以便注入到 system_prompt
        research_context = await sync_to_async(self._load_research_context)(
            data.get('research_task_id', ''), user_id=self.user_id
        )
        if research_context:
            # 将研究上下文注入到 system_prompt，确保每次对话都能看到
            data['_research_system_prompt'] = (
                "## 深度研究参考内容\n\n"
                "以下是用户之前完成的深度研究报告，请在回答时参考这些信息：\n\n"
                f"{research_context}"
            )
            data['_has_research_context'] = True

        agent, thread_config, use_checkpointer = await self._create_agent_with_memory(
            data, prompt_mode=data['mode'], model_instance=model_instance,
            tool_config=tool_config, tools=tools,
        )

        human_msg = await self._acreate_human_message(data)

        if use_checkpointer:
            # Checkpointer 模式下仍执行注入检测和预算分配
            ce_metadata = self._apply_context_engineering_for_checkpointer(
                user_message=data.get('message', ''),
                model_name=data.get('model_name'),
                mode=data.get('mode', 'agent'),
            )
            # Checkpointer 模式：将研究上下文作为 SystemMessage 注入到 human_msg 之前
            if research_context:
                from langchain_core.messages import SystemMessage
                research_system_msg = SystemMessage(content=data['_research_system_prompt'])
                graph_input = {"messages": [research_system_msg, human_msg]}
            else:
                graph_input = {"messages": [human_msg]}
            config = thread_config
        else:
            chat_history = data.get('chat_history', [])
            user_message = data.get('message', '')
            model_name_for_budget = data.get('model_name')

            if self._context_service.check_injection(user_message):
                logger.warning(f"检测到指令注入尝试，用户输入将被隔离")

            chat_history, ce_metadata = self._apply_context_engineering(
                chat_history, user_message, mode=data.get('mode', 'agent'),
                model_name=model_name_for_budget,
            )
            langchain_chat_history = convert_chat_history(chat_history)
            messages = list(langchain_chat_history) if langchain_chat_history else []
            # 非 Checkpointer 模式：将研究上下文作为 SystemMessage 注入到对话历史最前面
            if research_context:
                from langchain_core.messages import SystemMessage
                research_system_msg = SystemMessage(content=data['_research_system_prompt'])
                messages.insert(0, research_system_msg)
            messages.append(human_msg)
            graph_input = {"messages": messages}
            config = {"recursion_limit": 500}

        tool_calls_map: Dict[str, Dict] = {}
        current_message_content = ""
        all_messages = []
        tool_call_count: Dict[str, int] = {}
        prefer_tool_result = False
        accumulated_reasoning: Dict[str, str] = {
            "content": "",
            "_stream_state": data.get('_stream_state'),  # 共享状态，供 generate() finally 兜底刷新
        }
        tool_args_accumulator: Dict[str, str] = {}

        with TokenUsageCallbackHandler() as cb:
            from Django_xm.apps.ai_engine.services.llm_factory import FallbackDetectionCallback
            fb_callback = FallbackDetectionCallback(
                expected_provider=provider_id or "",
                expected_model=model_name or "",
            )
            config["callbacks"] = [cb, fb_callback]
            # 使用多 stream mode：messages 获取消息流，updates 捕获 interrupt 事件
            interrupt_info = None
            try:
                async for chunk in agent.graph.astream(graph_input, config=config, stream_mode=["messages", "updates"]):
                    # 多 stream mode 下 chunk 是 (mode_name, data) 元组
                    if isinstance(chunk, tuple) and len(chunk) == 2:
                        mode_name, mode_data = chunk
                    else:
                        mode_name, mode_data = "messages", chunk

                    # 处理 updates stream mode（包含 interrupt 事件）
                    if mode_name == "updates":
                        if isinstance(mode_data, dict) and "__interrupt__" in mode_data:
                            interrupts = mode_data["__interrupt__"]
                            if interrupts:
                                from langgraph.types import Interrupt
                                from Django_xm.apps.tools.base import is_approval_interrupt
                                for intr in interrupts:
                                    if isinstance(intr, Interrupt):
                                        interrupt_value = intr.value
                                    elif isinstance(intr, dict):
                                        interrupt_value = intr.get("value", intr)
                                    else:
                                        interrupt_value = intr

                                    # 通用审批中断检测：任何工具都可以通过 interrupt_for_approval 触发
                                    if is_approval_interrupt(interrupt_value):
                                        tool_name = interrupt_value.get("tool_name", "unknown")
                                        action = interrupt_value.get("action", "confirm")
                                        logger.info(
                                            f"approval interrupt: tool={tool_name}, "
                                            f"action={action}, danger={interrupt_value.get('danger_level', 'medium')}"
                                        )
                                        interrupt_id = intr.id if isinstance(intr, Interrupt) else ""
                                        # 标记发生了审批中断，finalize 阶段需要跳过部分逻辑
                                        interrupt_info = {
                                            "tool_name": tool_name,
                                            "interrupt_id": interrupt_id,
                                        }
                                        # 构建通用审批数据，透传所有字段给前端
                                        approval_data = {
                                            'tool_name': tool_name,
                                            'tool_call_id': interrupt_id,
                                            'interrupt_id': interrupt_id,
                                            'title': interrupt_value.get("title", "确认操作"),
                                            'description': interrupt_value.get("description", ""),
                                            'action': action,
                                            'danger_level': interrupt_value.get("danger_level", "medium"),
                                            'state': 'pending',
                                        }
                                        # 透传 command（如 shell_exec 的命令）
                                        if interrupt_value.get("command"):
                                            approval_data['command'] = interrupt_value["command"]
                                        # 透传 extra（工具自定义数据）
                                        if interrupt_value.get("extra"):
                                            approval_data['extra'] = interrupt_value["extra"]
                                        # 透传 input_placeholder（CONFIRM_WITH_INPUT 模式）
                                        if interrupt_value.get("input_placeholder"):
                                            approval_data['input_placeholder'] = interrupt_value["input_placeholder"]
                                        yield {
                                            'type': 'approval',
                                            'data': approval_data,
                                        }
                        continue  # updates 模式的其他事件跳过

                    # 处理 messages stream mode（原有逻辑）
                    all_messages.append(mode_data if not isinstance(mode_data, tuple) else mode_data[0])

                    try:
                        for event in process_stream_chunk(
                            mode_data, tool_calls_map, current_message_content,
                            weather_tool_names=weather_tool_names,
                            tool_call_count=tool_call_count,
                            lcp_func=_lcp_len,
                            accumulated_reasoning=accumulated_reasoning,
                            tool_args_accumulator=tool_args_accumulator,
                            mode=data.get('mode', 'agent'),
                            enable_deep_thinking=data.get('_enable_deep_thinking', False),
                        ):
                            if event.get("type") == "chunk":
                                current_message_content += event.get("content", "")
                            yield event

                            # ToolUsageGuard 事件处理（已不再使用 force_terminate 硬中断）
                            if event.get("type") == "tool_usage_dedup":
                                # 短时相同内容：将阻断原因注入下一条 ToolMessage
                                # 让模型看到"本次被系统跳过"且不重复写入
                                tool_info = event.get("data", {})
                                short_msg = tool_info.get("short_circuit_response", "")
                                # 仅记录到 current_message_content，前端不直接展示
                                logger.info(
                                    f"[Chat Service] 工具 {tool_info.get('tool_name')} "
                                    f"被去重: {short_msg}"
                                )
                                continue

                            if event.get("type") == "tool_usage_blocked":
                                # 渐进式阻断：记录警告，不中断流式
                                # 由模型基于 short_circuit_response 自我调整行为
                                tool_info = event.get("data", {})
                                short_msg = tool_info.get("short_circuit_response", "")
                                logger.warning(
                                    f"[Chat Service] 工具 {tool_info.get('tool_name')} "
                                    f"被阻断: {short_msg}"
                                )
                                # 把阻断提示以 chunk 形式通知前端（可见的）
                                yield {
                                    "type": "chunk",
                                    "content": f"\n\n[系统提示] {short_msg}\n",
                                }
                                current_message_content += f"\n\n[系统提示] {short_msg}\n"
                                continue

                            if event.get("type") == "tool_result":
                                tool_info = event.get("data", {})
                                if (tool_info.get("state") == "output-available"
                                        and tool_info.get("name") in weather_tool_names
                                        and tool_info.get("result")
                                        and not tool_info.get("delivered")):
                                    weather_result = tool_info["result"]
                                    yield {"type": "chunk", "content": weather_result}
                                    current_message_content += weather_result
                                    ai_message = AIMessage(content=weather_result)
                                    all_messages.append(ai_message)
                                    tool_info["delivered"] = True
                                    prefer_tool_result = True
                    except Exception as chunk_err:
                        logger.warning(f"处理流式 chunk 失败: {chunk_err}")
                        continue

                    await asyncio.sleep(0.01)
            except Exception as stream_err:
                # 异常路径：先刷新可能残留的缓冲内容，避免前端什么都没看到
                pending = accumulated_reasoning.get("_pending_content", "") if accumulated_reasoning else ""
                if pending:
                    logger.debug(f"Agent 模式异常路径: 刷新缓冲内容 ({len(pending)} 字符)")
                    yield {"type": "chunk", "content": pending}
                    current_message_content += pending
                    accumulated_reasoning["_pending_content"] = ""
                    # 同步清除 stream_state，避免 generate() finally 重复刷新
                    from .stream_helpers import _sync_pending_to_stream_state
                    _sync_pending_to_stream_state(accumulated_reasoning)

                # GraphRecursionError: Agent 步数超限，但已收集了部分结果
                # 优雅降级：用已收集的内容生成回复，不丢弃上下文
                from langgraph.errors import GraphRecursionError
                if isinstance(stream_err, GraphRecursionError):
                    logger.warning(
                        f"Agent达到递归上限，优雅降级: 已收集 {len(all_messages)} 条消息, "
                        f"内容长度={len(current_message_content)}"
                    )
                    if current_message_content:
                        yield {"type": "chunk", "content": ""}
                    else:
                        for msg in reversed(all_messages):
                            if isinstance(msg, AIMessage) and msg.content:
                                current_message_content = msg.content
                                yield {"type": "chunk", "content": msg.content}
                                break
                        if not current_message_content:
                            yield {"type": "chunk", "content": "任务执行步骤较多，已达到单次执行上限。以上是已收集的部分结果。"}
                    # 跳过 fallback，继续后续处理
                elif provider_id and model_instance and tools:
                    # 其他异常（如 API 连接错误）：回退到无工具纯对话模式
                    logger.warning(
                        f"模型 {provider_id} agent模式执行失败，回退到无工具纯对话模式: {type(stream_err).__name__}: {stream_err}"
                    )
                    yield {"type": "chunk", "content": ""}
                    try:
                        async for fallback_event in self._deep_service._stream_without_tools(
                            model_instance, data, usage_tracker, token_detail_tracker
                        ):
                            yield fallback_event
                    except Exception as fallback_err:
                        logger.error(
                            f"无工具回退模式也失败: {type(fallback_err).__name__}: {fallback_err}"
                        )
                        yield {"type": "error", "content": f"模型服务暂时不可用，请稍后重试（{type(fallback_err).__name__}）"}
                    clear_parent_tool_context()
                    return
                else:
                    logger.error(f"agent.graph.astream 执行异常: {type(stream_err).__name__}: {stream_err}", exc_info=True)
                    clear_parent_tool_context()
                    raise

        update_usage_and_tokens(cb, usage_tracker, token_detail_tracker)

        # 检测运行时 LLM fallback
        if fb_callback.fallback_detected:
            fallback_info = fb_callback.get_fallback_info()
            if fallback_info:
                yield {
                    'type': 'model_fallback',
                    'data': fallback_info,
                }
                # 自动更新 SystemConfig
                try:
                    from Django_xm.apps.ai_engine.models import SystemConfig
                    SystemConfig.set_value("default_chat_model", {
                        "provider_id": fallback_info["actual_provider"],
                        "model_name": fallback_info["actual_model"],
                    })
                except Exception:
                    pass

        for tc_info in tool_calls_map.values():
            tool_name = tc_info.get("name", "unknown")
            token_detail_tracker.track_tool_usage(tool_name)

        # 审批中断场景：Agent 被 interrupt 暂停，等待用户确认
        # 跳过 finalize 逻辑（不需要补发 final_ai_message 等），直接结束流
        if interrupt_info is not None:
            logger.info(f"审批中断，跳过 finalize: tool={interrupt_info.get('tool_name')}")
            from .stream_helpers import finalize_tool_calls
            for tool_update_event in finalize_tool_calls(all_messages, tool_calls_map, tool_args_accumulator):
                yield tool_update_event
            return

        # Agent 模式：刷新缓冲的 content（最终回答）
        # 在流式传输中，AIMessage 的 content 先于 tool_calls 到达，
        # 所以 content 被缓冲而非立即发送。当流结束时，缓冲区中的
        # content 就是最终回答（无 tool_calls 的最后一条 AIMessage）
        pending = accumulated_reasoning.get("_pending_content", "") if accumulated_reasoning else ""
        if pending and data.get('mode') == 'agent':
            logger.debug(f"Agent 模式: 刷新缓冲的最终回答内容 ({len(pending)} 字符)")
            yield {"type": "chunk", "content": pending}
            current_message_content += pending
            accumulated_reasoning["_pending_content"] = ""
            # 同步清除 stream_state，避免 generate() finally 重复刷新
            from .stream_helpers import _sync_pending_to_stream_state
            _sync_pending_to_stream_state(accumulated_reasoning)

        # 深度思考模式兜底：当模型（如 qwen3:8b + reasoning=True）将全部输出
        # 放入 thinking 字段而 content 为空时，将推理内容作为主内容发送，
        # 避免前端只显示"已深度思考"而无任何文本
        # 但如果有 interrupt（工具审批等待），不应触发兜底
        if (not current_message_content.strip()
                and not interrupt_info
                and accumulated_reasoning
                and accumulated_reasoning.get("content", "").strip()
                and data.get('_enable_deep_thinking')):
            reasoning_text = accumulated_reasoning["content"].strip()
            logger.info(
                f"深度思考兜底: content 为空，将推理内容 ({len(reasoning_text)} 字符) 作为主内容发送"
            )
            yield {"type": "chunk", "content": reasoning_text}
            current_message_content = reasoning_text

        # 普通模式下，若模型自带推理内容（如 DeepSeek reasoning_content），
        # 发送 reasoning 完成事件，让前端能正确切换显示状态
        # 仅当深度思考启用时才发送
        if (accumulated_reasoning and accumulated_reasoning.get("content", "").strip()
                and data.get('_enable_deep_thinking')):
            yield {
                "type": "reasoning",
                "data": {
                    "content": accumulated_reasoning["content"].strip(),
                    "duration": 0,
                    "source": "model_intrinsic",
                    "finished": True,
                },
            }

        from .stream_helpers import finalize_tool_calls
        for tool_update_event in finalize_tool_calls(all_messages, tool_calls_map, tool_args_accumulator):
            yield tool_update_event

        async for final_event in self._finalize_stream_response(
            all_messages, current_message_content, tool_calls_map,
            prefer_tool_result, data, weather_tool_names,
            model_instance=model_instance,
        ):
            yield final_event

        clear_parent_tool_context()

    async def _finalize_stream_response(
        self,
        all_messages: List,
        current_message_content: str,
        tool_calls_map: Dict[str, Dict],
        prefer_tool_result: bool,
        data: Dict[str, Any],
        weather_tool_names: set,
        model_instance=None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        from Django_xm.apps.ai_engine.services.llm_factory import get_chat_model

        final_ai_message = None
        for msg in reversed(all_messages):
            if isinstance(msg, AIMessage) and msg.content and msg.content.strip():
                final_ai_message = msg
                break

        # Agent 模式下，_pending_content 已刷新完整内容，跳过 final_ai_message 补发
        # 避免 current_message_content 与 final_ai_message.content 不完全一致时重复发送
        mode = data.get('mode', 'agent')
        if final_ai_message and final_ai_message.content and mode != 'agent':
            final_content = final_ai_message.content
            if len(final_content) > len(current_message_content):
                remaining_content = final_content[len(current_message_content):]
                if remaining_content:
                    yield {"type": "chunk", "content": remaining_content}
                    current_message_content = final_content

        if (not final_ai_message or not final_ai_message.content or len(final_ai_message.content.strip()) < 10) and tool_calls_map:
            weather_tools = ["get_daily_weather", "get_weather_forecast", "get_weather"]
            for tool_name in weather_tools:
                for tool_info in tool_calls_map.values():
                    if (tool_info.get("name") == tool_name and
                        tool_info.get("state") == "output-available" and
                        tool_info.get("result")):
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
                        tool_name in raw_content_tools or
                        tool_name.startswith("knowledge_base_") or
                        tool_info.get("_summarized")
                    )
                    if (tool_info.get("state") == "output-available" and
                            result and
                            not is_raw_content and
                            (isinstance(result, str) and result not in current_message_content)):
                        result_content = result if isinstance(result, str) else str(result)
                        if result_content:
                            yield {"type": "chunk", "content": result_content}
                        break

        # Agent 模式下跳过补全检查：Agent 已生成完整回答，
        # _needs_completion 的"补全"会触发模型重新生成完整回答，导致内容重复
        mode = data.get('mode', 'agent')
        if mode != 'agent' and not prefer_tool_result and _needs_completion(current_message_content):
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
                pass

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
                yield {'type': 'suggestions', 'data': suggestions}
        except Exception:
            pass


class ChatModeService:
    """聊天模式服务类"""

    FRONTEND_SUPPORTED_MODES = ['agent', 'deep-research']
    DEFAULT_MODE = 'agent'

    @classmethod
    def get_supported_modes(cls) -> Dict[str, str]:
        from Django_xm.apps.ai_engine.prompts.system_prompts import SYSTEM_PROMPTS

        modes = {}
        for mode_name in cls.FRONTEND_SUPPORTED_MODES:
            prompt = SYSTEM_PROMPTS.get(mode_name)
            if prompt is None:
                prompt = SYSTEM_PROMPTS.get("default")
            if prompt:
                modes[mode_name] = prompt.split('\n')[0]
        return modes
