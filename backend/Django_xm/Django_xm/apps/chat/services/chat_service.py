"""
聊天服务层

将复杂的业务逻辑从视图中分离，提高代码可维护性和可测试性。
深度思考/研究模式委托给 deep_chat_service，RAG 模式委托给 rag_chat_service。
"""
import asyncio
import time
import uuid
import logging
from typing import Optional, Dict, Any, List, AsyncGenerator

from langchain_core.messages import HumanMessage, AIMessage

from Django_xm.apps.ai_engine.services.token_counter import TokenUsageCallbackHandler
from Django_xm.apps.ai_engine.services.agent_factory import create_base_agent
from Django_xm.apps.ai_engine.services.llm_factory import get_chat_model, get_model_string, get_chat_model_by_provider
from Django_xm.apps.ai_engine.prompts.system_prompts import SYSTEM_PROMPTS
from Django_xm.apps.tools import get_tools_for_request, get_tools_for_request_async, WEATHER_TOOLS
from Django_xm.apps.ai_engine.services.usage_tracker import create_usage_tracker
from Django_xm.apps.ai_engine.services.cost_tracker import create_cost_tracker, CostTracker
from Django_xm.apps.cache_manager.services.cache_service import ModelResponseCacheService
from Django_xm.apps.context_manager.services.session_compactor import apply_compaction_to_chat_history
from Django_xm.apps.chat.services.slash_commands import parse_command, execute_command
from Django_xm.apps.attachments.services.attachment_content_service import AttachmentService
from Django_xm.apps.ai_engine.services.checkpointer_factory import get_checkpointer, get_store, get_async_checkpointer
from Django_xm.apps.context_manager.services.manager import ContextManager, create_context_manager
from Django_xm.apps.research.services.deep_agent import should_use_deep_research
from .rag_chat_service import RAGChatService
from .deep_chat_service import DeepChatService
from .stream_helpers import (
    process_stream_chunk,
    build_context_info,
    update_usage_and_cost,
)
from ..utils import _needs_completion, _lcp_len, convert_chat_history, extract_suggestions

logger = logging.getLogger(__name__)

from dataclasses import dataclass


@dataclass
class ChatContext:
    user_id: str


class ChatService:
    """聊天服务类 - 处理聊天相关的业务逻辑"""

    CHECKPOINTER_ENABLED = True

    def __init__(self, user_id: Optional[int] = None):
        self.user_id = user_id
        self._attachment_service = AttachmentService()
        self._checkpointer = None
        self._store = None
        self._context_manager: Optional[ContextManager] = None
        self._rag_service = RAGChatService(user_id=user_id)
        self._deep_service = DeepChatService(self)

        if self.CHECKPOINTER_ENABLED:
            try:
                self._checkpointer = get_checkpointer()
                self._store = get_store()
                logger.info("Checkpointer + Store 已初始化（多轮对话状态持久化已启用）")
            except Exception as e:
                logger.warning(f"Checkpointer 初始化失败，回退到前端传递历史模式: {e}")
                self._checkpointer = None

        try:
            self._context_manager = create_context_manager(
                user_id=self.user_id,
                store=self._store,
            )
            logger.info(f"ContextManager 已初始化 (user={self.user_id})")
        except Exception as e:
            logger.warning(f"ContextManager 初始化失败: {e}")
            self._context_manager = None

    def _build_thread_config(self, session_id: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        thread_id = session_id or str(uuid.uuid4())
        config: Dict[str, Any] = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": kwargs.pop("recursion_limit", 50),
        }
        config.update(kwargs)
        return config

    async def _create_agent_with_memory(
        self,
        data: Dict[str, Any],
        tools: Optional[List] = None,
        prompt_mode: str = "default",
        model_instance=None,
    ) -> tuple:
        session_id = data.get('session_id')
        use_checkpointer = self._checkpointer is not None and session_id

        resolved_tools = tools or await self._get_tools(data)
        agent_kwargs: Dict[str, Any] = {
            "tools": resolved_tools,
            "prompt_mode": prompt_mode,
            "user_id": self.user_id,
            "session_id": session_id,
        }

        if model_instance:
            agent_kwargs["model"] = model_instance

        if use_checkpointer:
            try:
                async_cp = await get_async_checkpointer()
                if async_cp is not None:
                    if hasattr(async_cp, 'setup'):
                        try:
                            await async_cp.setup()
                        except Exception:
                            pass
                    checkpointer = async_cp
                    logger.info("流式聊天使用异步 Checkpointer")
                else:
                    checkpointer = self._checkpointer
                    use_checkpointer = False
                    logger.warning("异步 Checkpointer 不可用，禁用 Checkpointer 以避免 astream 异常")
            except Exception as e:
                logger.warning(f"获取异步 Checkpointer 失败，禁用 Checkpointer: {e}")
                checkpointer = self._checkpointer
                use_checkpointer = False

            agent_kwargs["checkpointer"] = checkpointer
            if self._store is not None:
                agent_kwargs["store"] = self._store
                agent_kwargs["context_schema"] = ChatContext
                logger.info(f"Agent 已注入 Store + context_schema (user_id={self.user_id})")

        agent = create_base_agent(**agent_kwargs)
        config = self._build_thread_config(session_id) if use_checkpointer else None
        return agent, config, use_checkpointer

    @staticmethod
    def _resolve_model_instance(data: Dict[str, Any], streaming: bool = True):
        provider_id = data.get('provider_id')
        if not provider_id:
            return None
        model_name = data.get('model_name')
        special_params = data.get('special_params')
        model_temperature = data.get('temperature')
        model_max_tokens = data.get('max_tokens')
        try:
            return get_chat_model_by_provider(
                provider_id=provider_id,
                model_name=model_name or None,
                temperature=float(model_temperature) if model_temperature is not None else None,
                max_tokens=int(model_max_tokens) if model_max_tokens is not None else None,
                special_params=special_params or None,
                streaming=streaming,
            )
        except Exception as e:
            logger.warning(f"模型选择失败(provider={provider_id})，回退默认: {e}")
            return None

    async def _get_tools(self, data: Dict[str, Any]) -> List:
        use_tools = data.get('use_tools', True)
        use_advanced_tools = data.get('use_advanced_tools', False)
        use_web_search = data.get('use_web_search', False)
        use_mcp = data.get('use_mcp', False)
        selected_mcp_servers = data.get('selected_mcp_servers')
        selected_tools = data.get('selected_tools')
        logger.info(f"[GetTools] use_tools={use_tools}, use_advanced={use_advanced_tools}, use_web={use_web_search}, use_mcp={use_mcp}, selected_mcp_servers={selected_mcp_servers}, selected_tools={selected_tools}")
        tools = await get_tools_for_request_async(
            use_tools=use_tools,
            use_advanced_tools=use_advanced_tools,
            use_web_search=use_web_search,
            use_mcp=use_mcp,
            attachment_ids=None,
            selected_mcp_servers=selected_mcp_servers,
            selected_tools=selected_tools,
        )
        logger.info(f"[GetTools] 返回 {len(tools)} 个工具: {[t.name for t in tools]}")
        return tools

    def _build_user_content(self, data: Dict[str, Any]) -> Dict[str, Any]:
        user_message = data['message']
        attachment_ids = data.get('attachment_ids')
        if not attachment_ids:
            return {"type": "text", "content": user_message}
        return self._attachment_service.build_user_content(user_message, attachment_ids)

    def _create_human_message(self, data: Dict[str, Any]) -> HumanMessage:
        preloaded_type = data.get('_preloaded_attachment_type')
        if preloaded_type == 'multimodal':
            content = data.get('_preloaded_attachment_content', data['message'])
            return HumanMessage(content=content)
        if preloaded_type == 'text':
            return HumanMessage(content=data['message'])

        user_content = self._build_user_content(data)
        if user_content["type"] == "multimodal":
            return HumanMessage(content=user_content["content"])
        return HumanMessage(content=user_content["content"])

    def _apply_compaction(self, chat_history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if self._context_manager is not None:
            try:
                processed, metadata = self._context_manager.process_messages(chat_history)
                if metadata.get("compression", {}).get("compressed"):
                    logger.info(
                        f"上下文压缩: {metadata['compression']['original_tokens']} -> "
                        f"{metadata['compression']['compressed_tokens']} tokens, "
                        f"压缩率 {metadata['compression']['ratio']:.1%}"
                    )
                return processed
            except Exception as e:
                logger.warning(f"ContextManager 压缩失败，回退到 SessionCompactor: {e}")

        compacted, result = apply_compaction_to_chat_history(chat_history)
        if result.compressed:
            logger.info(
                f"会话已自动压缩: {result.original_message_count} -> "
                f"{result.kept_message_count} 条消息"
            )
        return compacted

    async def process_chat_request(self, data: Dict[str, Any]) -> Dict[str, Any]:
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
                'mode': data.get('mode', 'default'),
                'tools_used': [],
                'success': True,
                'is_command': True,
                'command_type': cmd_result.get('type', 'info'),
            }

        mode = data.get('mode', 'default')

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
        agent, thread_config, use_checkpointer = await self._create_agent_with_memory(
            data, tools=tools, prompt_mode=data['mode'],
        )

        user_content = self._build_user_content(data)
        human_msg = HumanMessage(content=user_content["content"])

        if use_checkpointer:
            graph_input = {"messages": [human_msg]}
            invoke_config = thread_config
        else:
            chat_history = data.get('chat_history', [])
            chat_history = self._apply_compaction(chat_history)
            langchain_chat_history = convert_chat_history(chat_history)
            messages = list(langchain_chat_history) if langchain_chat_history else []
            messages.append(human_msg)
            graph_input = {"messages": messages}
            invoke_config = {"recursion_limit": 50}

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
                completion = model.invoke(
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
        provider_id = data.get('provider_id')
        model_name = data.get('model_name')
        tracker_model_id = model_name or getattr(settings, 'openai_model', 'gpt-4o')
        usage_tracker = create_usage_tracker(model_id=tracker_model_id)
        cost_tracker = create_cost_tracker()
        stream_start_time = time.time()

        mode = data.get('mode', 'default')
        session_id = data.get('session_id', 'N/A')
        msg_preview = data.get('message', '')[:80]
        logger.info(f"[StreamChat] 开始处理: mode={mode}, session={session_id}, msg={msg_preview}...")

        yield {'type': 'start', 'message': '开始生成...'}

        parsed = parse_command(data.get('message', ''))
        if parsed:
            command_name, args = parsed
            context = {
                "args": args,
                "user_id": self.user_id,
                "session_id": data.get('session_id'),
                "messages": data.get('chat_history', []),
                "cost_info": cost_tracker.get_summary(),
            }
            cmd_result = execute_command(command_name, context)
            yield {
                'type': 'command',
                'data': cmd_result,
                'content': cmd_result.get('content', ''),
            }
            yield {'type': 'end', 'message': '命令执行完成'}
            return

        selected_kb = data.get('selected_knowledge_base')
        if selected_kb:
            retriever = self._rag_service.get_rag_retriever(selected_kb)
            if retriever:
                logger.info(f"使用知识库 {selected_kb} 进行流式 RAG 查询")
                async for event in self._rag_service.process_rag_stream(data, retriever, usage_tracker, cost_tracker):
                    yield event
                context_info = build_context_info(usage_tracker, cost_tracker, stream_start_time)
                yield {'type': 'context', 'data': context_info}
                yield {'type': 'end', 'message': '生成完成'}
                return

        if data['mode'] == 'deep-thinking':
            async for event in self._deep_service.process_deep_thinking_stream(data, usage_tracker, cost_tracker):
                yield event
            context_info = build_context_info(usage_tracker, cost_tracker, stream_start_time)
            yield {'type': 'context', 'data': context_info}
            yield {'type': 'end', 'message': '生成完成'}
            usage_tracker.log_summary()
            cost_tracker.log_summary()
            logger.info("深度思考流程完成")
            return

        attachment_ids = data.get('attachment_ids') or []
        has_attachments = data.get('_has_attachments', False) or bool(attachment_ids)

        if attachment_ids and self.user_id and self._store is not None:
            try:
                self._attachment_service.persist_attachments_to_store(
                    user_id=self.user_id,
                    attachment_ids=attachment_ids,
                    store=self._store,
                )
            except Exception as e:
                logger.warning(f"附件持久化到 Store 失败（不影响主流程）: {e}")

        if should_use_deep_research(data['message']) and not has_attachments:
            logger.info("触发深度研究流程")
            yield {
                "type": "reasoning",
                "data": {
                    "content": "问题较为复杂，正在调度深度研究工作流并执行网络搜索...",
                    "duration": 0,
                },
            }
            deep_result = await self._deep_service.run_deep_research_task(
                data['message'], session_id=data.get('session_id'),
                usage_tracker=usage_tracker, cost_tracker=cost_tracker,
            )
            final_report = deep_result.get("final_report") or deep_result.get("error")
            if not final_report:
                final_report = "深度研究已完成，但未生成可用报告。请稍后重试或调整问题表述。"
            yield {"type": "chunk", "content": final_report}
            context_info = build_context_info(usage_tracker, cost_tracker, stream_start_time)
            yield {'type': 'context', 'data': context_info}
            yield {'type': 'end', 'message': '生成完成'}
            usage_tracker.log_summary()
            cost_tracker.log_summary()
            logger.info("深度研究流程完成")
            return

        async for event in self._process_normal_stream_chat(data, usage_tracker, cost_tracker):
            yield event

        context_info = build_context_info(usage_tracker, cost_tracker, stream_start_time)
        yield {'type': 'context', 'data': context_info}
        yield {'type': 'end', 'message': '生成完成'}
        usage_tracker.log_summary()
        cost_tracker.log_summary()
        logger.info("流式聊天请求处理完成")

    async def _process_normal_stream_chat(
        self,
        data: Dict[str, Any],
        usage_tracker,
        cost_tracker: Optional[CostTracker] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        tools = await self._get_tools(data)
        weather_tool_names = {tool.name for tool in WEATHER_TOOLS}
        model_instance = self._resolve_model_instance(data)
        provider_id = data.get('provider_id')

        agent, thread_config, use_checkpointer = await self._create_agent_with_memory(
            data, tools=tools, prompt_mode=data['mode'], model_instance=model_instance,
        )

        human_msg = self._create_human_message(data)

        if use_checkpointer:
            graph_input = {"messages": [human_msg]}
            config = thread_config
        else:
            chat_history = data.get('chat_history', [])
            chat_history = self._apply_compaction(chat_history)
            langchain_chat_history = convert_chat_history(chat_history)
            messages = list(langchain_chat_history) if langchain_chat_history else []
            messages.append(human_msg)
            graph_input = {"messages": messages}
            config = {"recursion_limit": 50}

        tool_calls_map: Dict[str, Dict] = {}
        current_message_content = ""
        all_messages = []
        tool_call_count: Dict[str, int] = {}
        prefer_tool_result = False
        accumulated_reasoning: Dict[str, str] = {"content": ""}
        tool_args_accumulator: Dict[str, str] = {}

        with TokenUsageCallbackHandler() as cb:
            config["callbacks"] = [cb]
            try:
                async for chunk in agent.graph.astream(graph_input, config=config, stream_mode="messages"):
                    all_messages.append(chunk if not isinstance(chunk, tuple) else chunk[0])

                    try:
                        events = process_stream_chunk(
                            chunk, tool_calls_map, current_message_content,
                            weather_tool_names=weather_tool_names,
                            tool_call_count=tool_call_count,
                            lcp_func=_lcp_len,
                            accumulated_reasoning=accumulated_reasoning,
                            tool_args_accumulator=tool_args_accumulator,
                        )
                    except Exception as chunk_err:
                        logger.warning(f"处理流式 chunk 失败: {chunk_err}")
                        continue
                    for event in events:
                        if event.get("type") == "chunk":
                            current_message_content += event.get("content", "")
                        yield event

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

                    await asyncio.sleep(0.01)
            except Exception as stream_err:
                if provider_id and model_instance and tools:
                    logger.warning(
                        f"模型 {provider_id} agent模式执行失败，回退到无工具纯对话模式: {type(stream_err).__name__}: {stream_err}"
                    )
                    yield {"type": "chunk", "content": ""}
                    async for fallback_event in self._deep_service._stream_without_tools(
                        model_instance, data, usage_tracker, cost_tracker
                    ):
                        yield fallback_event
                    return
                logger.error(f"agent.graph.astream 执行异常: {type(stream_err).__name__}: {stream_err}", exc_info=True)
                raise

        update_usage_and_cost(cb, usage_tracker, cost_tracker)

        from .stream_helpers import finalize_tool_calls
        for tool_update_event in finalize_tool_calls(all_messages, tool_calls_map, tool_args_accumulator):
            yield tool_update_event

        async for final_event in self._finalize_stream_response(
            all_messages, current_message_content, tool_calls_map,
            prefer_tool_result, data, weather_tool_names,
            model_instance=model_instance,
        ):
            yield final_event

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
        final_ai_message = None
        for msg in reversed(all_messages):
            if isinstance(msg, AIMessage) and msg.content and msg.content.strip():
                final_ai_message = msg
                break

        if final_ai_message and final_ai_message.content:
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
                        if result_content and result_content not in current_message_content:
                            yield {"type": "chunk", "content": result_content}
                        break
                else:
                    continue
                break
            else:
                for tool_info in tool_calls_map.values():
                    if (tool_info.get("state") == "output-available" and
                        tool_info.get("result") and
                        tool_info.get("result") not in current_message_content):
                        result_content = tool_info.get("result", "")
                        if result_content:
                            yield {"type": "chunk", "content": result_content}
                        break

        if not prefer_tool_result and _needs_completion(current_message_content):
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

    FRONTEND_SUPPORTED_MODES = ['basic-agent', 'rag', 'workflow', 'deep-research', 'guarded', 'deep-thinking']

    @classmethod
    def get_supported_modes(cls) -> Dict[str, str]:
        modes = {}
        for mode_name, prompt in SYSTEM_PROMPTS.items():
            if mode_name in cls.FRONTEND_SUPPORTED_MODES:
                modes[mode_name] = prompt.split('\n')[0]
        return modes
