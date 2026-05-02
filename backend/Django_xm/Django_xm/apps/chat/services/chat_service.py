"""
聊天服务层
将复杂的业务逻辑从视图中分离，提高代码可维护性和可测试性
"""
import json
import asyncio
import time
import uuid
import logging
from typing import Optional, Dict, Any, List, AsyncGenerator

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

from Django_xm.apps.ai_engine.services.agent_factory import create_base_agent
from Django_xm.apps.research.services.deep_agent import create_deep_research_agent
from Django_xm.apps.research.services.deep_agent import should_use_deep_research
from Django_xm.apps.ai_engine.services.llm_factory import get_chat_model
from Django_xm.apps.ai_engine.prompts.system_prompts import SYSTEM_PROMPTS
from Django_xm.apps.tools import get_tools_for_request, WEATHER_TOOLS
from Django_xm.apps.ai_engine.services.usage_tracker import create_usage_tracker
from Django_xm.apps.ai_engine.services.cost_tracker import create_cost_tracker, CostTracker
from Django_xm.apps.core.permissions import PermissionService
from Django_xm.apps.chat.services.session_compactor import apply_compaction_to_chat_history
from Django_xm.apps.chat.services.slash_commands import parse_command, execute_command
from Django_xm.apps.knowledge.services.rag_agent import create_rag_agent, query_rag_agent
from Django_xm.apps.knowledge.services.index_service import IndexManager
from Django_xm.apps.knowledge.services.embedding_service import get_embeddings
from Django_xm.apps.knowledge.services.retrieval_service import create_retriever
from ..utils import _needs_completion, _lcp_len, convert_chat_history, extract_suggestions

logger = logging.getLogger(__name__)


class AttachmentService:
    """附件内容加载服务"""

    def load_attachment_contents(self, attachment_ids: List[int]) -> Optional[str]:
        if not attachment_ids:
            return None

        try:
            from Django_xm.apps.tools.file.reader import read_multiple_attachments
            content = read_multiple_attachments(attachment_ids)
            if content:
                logger.info(f"成功加载 {len(attachment_ids)} 个附件内容，共 {len(content)} 字符")
            return content if content.strip() else None
        except Exception as e:
            logger.error(f"加载附件内容失败: {e}")
            return None

    def build_message_with_attachments(self, user_message: str, attachment_content: Optional[str]) -> str:
        if not attachment_content:
            return user_message

        return (
            f"{user_message}\n\n"
            f"---\n"
            f"以下是用户上传的文件内容，请基于这些内容回答问题：\n\n"
            f"{attachment_content}\n"
            f"---\n"
        )

    def link_attachments_to_message(self, message, attachment_ids: Optional[List[int]]):
        if not attachment_ids:
            return

        from Django_xm.apps.chat.models import ChatAttachment

        ChatAttachment.objects.filter(
            id__in=attachment_ids,
            session=message.session,
            message__isnull=True
        ).update(message=message)


class MessagePersistenceService:
    """消息持久化服务"""

    def save_message_pair(
        self,
        session,
        user_content: str,
        ai_content: str,
        user_role: str = 'user',
        ai_role: str = 'assistant',
        attachment_ids: Optional[List[int]] = None
    ):
        from Django_xm.apps.chat.models import ChatMessage

        user_message = ChatMessage.objects.create(
            session=session,
            role=user_role,
            content=user_content
        )

        if attachment_ids:
            AttachmentService().link_attachments_to_message(user_message, attachment_ids)

        ai_message = ChatMessage.objects.create(
            session=session,
            role=ai_role,
            content=ai_content
        )

        return user_message, ai_message


class ChatService:
    """聊天服务类 - 处理聊天相关的业务逻辑"""

    def __init__(self, user_id: Optional[int] = None):
        self.user_id = user_id
        self._attachment_service = AttachmentService()

    def _get_user_index_name(self, index_name):
        """生成带用户标识的索引名称"""
        return f"user_{self.user_id}_{index_name}" if self.user_id else index_name

    def _get_rag_retriever(self, index_name):
        """获取 RAG 检索器"""
        if not self.user_id:
            return None
        
        try:
            user_index_name = self._get_user_index_name(index_name)
            manager = IndexManager()
            
            if not manager.index_exists(user_index_name):
                logger.warning(f"索引不存在: {user_index_name}")
                return None
            
            metadata = manager._load_metadata(user_index_name)
            num_documents = metadata.get('num_documents', 0) if metadata else 0
            if num_documents == 0:
                logger.warning(f"索引中无文档: {user_index_name}")
                return None
            
            embeddings = get_embeddings()
            vector_store = manager.load_index(user_index_name, embeddings)
            retriever = create_retriever(vector_store, k=4)
            
            return retriever
        except Exception as e:
            logger.error(f"获取 RAG 检索器失败: {e}", exc_info=True)
            return None

    def _get_tools(self, data: Dict[str, Any]) -> List:
        use_tools = data.get('use_tools', True)
        use_advanced_tools = data.get('use_advanced_tools', False)
        use_web_search = data.get('use_web_search', False)
        attachment_ids = data.get('attachment_ids')

        tools = get_tools_for_request(
            use_tools=use_tools,
            use_advanced_tools=use_advanced_tools,
            use_web_search=use_web_search,
            attachment_ids=attachment_ids,
        )

        if self.user_id:
            session_id = data.get('session_id')
            tools = PermissionService.filter_tools_by_permission(self.user_id, tools, session_id)

        return tools

    def _build_user_message(self, data: Dict[str, Any]) -> str:
        user_message = data['message']
        attachment_ids = data.get('attachment_ids')

        if attachment_ids:
            attachment_content = self._attachment_service.load_attachment_contents(attachment_ids)
            if attachment_content:
                user_message = self._attachment_service.build_message_with_attachments(
                    user_message, attachment_content
                )

        return user_message

    def _apply_compaction(self, chat_history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        compacted, result = apply_compaction_to_chat_history(chat_history)
        if result.compressed:
            logger.info(
                f"会话已自动压缩: {result.original_message_count} -> "
                f"{result.kept_message_count} 条消息"
            )
        return compacted

    def process_chat_request(self, data: Dict[str, Any]) -> Dict[str, Any]:
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

        selected_kb = data.get('selected_knowledge_base')
        
        if selected_kb:
            retriever = self._get_rag_retriever(selected_kb)
            if retriever:
                logger.info(f"使用知识库 {selected_kb} 进行 RAG 查询")
                agent = create_rag_agent(retriever)
                result = query_rag_agent(agent, data['message'], return_sources=True)
                
                return {
                    'message': result.get('answer', ''),
                    'mode': 'rag',
                    'tools_used': ['rag_retrieve'],
                    'success': True,
                    'sources': result.get('sources', [])
                }

        tools = self._get_tools(data)
        agent = create_base_agent(tools=tools, prompt_mode=data['mode'])

        chat_history = data.get('chat_history', [])
        chat_history = self._apply_compaction(chat_history)
        langchain_chat_history = convert_chat_history(chat_history)

        user_message = self._build_user_message(data)

        response = agent.invoke(
            input_text=user_message,
            chat_history=langchain_chat_history,
            config={"recursion_limit": 50}
        )

        if _needs_completion(response):
            model = get_chat_model()
            prompt = (
                f"用户问题：{data['message']}\n\n"
                f"当前回复（不完整）：{response}\n\n"
                "请继续并完整回答上述问题，补充必要的解释或例子，最后给出一句简明结论。"
            )
            completion = model.invoke([{"role": "user", "content": prompt}])
            if getattr(completion, "content", None):
                response = completion.content

        tool_names = [tool.name for tool in tools]

        logger.info(f"聊天请求处理完成，响应长度: {len(response)} 字符")

        return {
            'message': response,
            'mode': data['mode'],
            'tools_used': tool_names,
            'success': True
        }

    async def process_stream_chat_request(self, data: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        usage_tracker = create_usage_tracker()
        cost_tracker = create_cost_tracker()

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
            retriever = self._get_rag_retriever(selected_kb)
            if retriever:
                logger.info(f"使用知识库 {selected_kb} 进行流式 RAG 查询")
                async for event in self._process_rag_stream(data, retriever, usage_tracker, cost_tracker):
                    yield event
                context_info = usage_tracker.get_usage_info()
                cost_info = cost_tracker.get_summary()
                context_info['cost'] = cost_info
                yield {'type': 'context', 'data': context_info}
                yield {'type': 'end', 'message': '生成完成'}
                return

        if data['mode'] == 'deep-thinking':
            async for event in self._process_deep_thinking_stream(data, usage_tracker, cost_tracker):
                yield event
            context_info = usage_tracker.get_usage_info()
            cost_info = cost_tracker.get_summary()
            context_info['cost'] = cost_info
            yield {'type': 'context', 'data': context_info}
            yield {'type': 'end', 'message': '生成完成'}
            usage_tracker.log_summary()
            cost_tracker.log_summary()
            logger.info("深度思考流程完成")
            return

        if should_use_deep_research(data['message']):
            logger.info("触发深度研究流程")

            yield {
                "type": "reasoning",
                "data": {
                    "content": "问题较为复杂，正在调度深度研究工作流并执行网络搜索...",
                    "duration": 0,
                },
            }

            deep_result = await self._run_deep_research_task(data['message'])
            final_report = deep_result.get("final_report") or deep_result.get("error")
            if not final_report:
                final_report = (
                    "深度研究已完成，但未生成可用报告。"
                    " 请稍后重试或调整问题表述。"
                )

            yield {"type": "chunk", "content": final_report}

            context_info = usage_tracker.get_usage_info()
            cost_info = cost_tracker.get_summary()
            context_info['cost'] = cost_info
            yield {'type': 'context', 'data': context_info}
            yield {'type': 'end', 'message': '生成完成'}

            usage_tracker.log_summary()
            cost_tracker.log_summary()
            logger.info("深度研究流程完成")
            return

        async for event in self._process_normal_stream_chat(data, usage_tracker, cost_tracker):
            yield event

        context_info = usage_tracker.get_usage_info()
        cost_info = cost_tracker.get_summary()
        context_info['cost'] = cost_info
        yield {'type': 'context', 'data': context_info}
        yield {'type': 'end', 'message': '生成完成'}

        usage_tracker.log_summary()
        cost_tracker.log_summary()
        logger.info("流式聊天请求处理完成")

    async def _process_rag_stream(self, data: Dict[str, Any], retriever, usage_tracker, cost_tracker):
        """处理流式 RAG 查询"""
        agent = create_rag_agent(retriever, streaming=True)
        
        messages = []
        chat_history = data.get('chat_history', [])
        chat_history = self._apply_compaction(chat_history)
        langchain_chat_history = convert_chat_history(chat_history)
        if langchain_chat_history:
            messages.extend(langchain_chat_history)
        
        user_message = self._build_user_message(data)
        messages.append(HumanMessage(content=user_message))
        
        try:
            full_response = ""
            sources = []
            
            for chunk in agent.stream({"messages": messages}):
                if isinstance(chunk, dict) and "messages" in chunk:
                    chunk_messages = chunk["messages"]
                    if chunk_messages:
                        content = chunk_messages[-1].content if hasattr(chunk_messages[-1], 'content') else str(chunk_messages[-1])
                        if content:
                            full_response += content
                            yield {"type": "chunk", "content": content}
            
            if retriever:
                try:
                    retrieved_docs = retriever.get_relevant_documents(data['message'])
                    for doc in retrieved_docs:
                        sources.append({
                            'content': doc.page_content,
                            'source': doc.metadata.get('source', 'Unknown')
                        })
                except Exception as e:
                    logger.warning(f"获取来源文档失败: {e}")
            
            if sources:
                yield {'type': 'sources', 'data': sources}
            
        except Exception as e:
            logger.error(f"RAG 流式查询失败: {e}", exc_info=True)
            yield {"type": "error", "message": str(e)}

    async def _run_deep_research_task(self, query: str) -> Dict[str, Any]:
        thread_id = f"deep_{uuid.uuid4().hex[:12]}"

        def _task():
            agent = create_deep_research_agent(
                thread_id=thread_id,
                enable_web_search=True,
                enable_doc_analysis=False,
            )
            return agent.research(query)

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _task)

    async def _process_deep_thinking_stream(
        self,
        data: Dict[str, Any],
        usage_tracker,
        cost_tracker: Optional[CostTracker] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        tools = self._get_tools(data)
        agent = create_base_agent(tools=tools, prompt_mode='deep-thinking')

        chat_history = data.get('chat_history', [])
        langchain_chat_history = convert_chat_history(chat_history)
        messages = []
        if langchain_chat_history:
            messages.extend(langchain_chat_history)

        user_message = self._build_user_message(data)
        messages.append(HumanMessage(content=user_message))
        graph_input = {"messages": messages}

        current_message_content = ""
        all_messages = []
        tool_calls_map: Dict[str, Dict] = {}
        thinking_start_time = time.time()
        has_sent_reasoning = False

        config = {"recursion_limit": 50}

        yield {
            "type": "reasoning",
            "data": {
                "content": "正在深度思考中，分析问题的多个维度...",
                "duration": 0,
            },
        }
        has_sent_reasoning = True

        async for chunk in agent.graph.astream(graph_input, config=config, stream_mode="messages"):
            if isinstance(chunk, tuple) and len(chunk) == 2:
                message, metadata = chunk
            else:
                message = chunk
                metadata = {}

            if metadata:
                usage_tracker.update_from_metadata(metadata)

            all_messages.append(message)

            if isinstance(message, AIMessage):
                tool_calls = getattr(message, "tool_calls", [])
                if tool_calls:
                    for tool_call in tool_calls:
                        tool_id = tool_call.get("id", "")
                        tool_name = tool_call.get("name", "")
                        tool_info = {
                            "id": tool_id,
                            "name": tool_name,
                            "type": f"tool-call-{tool_name}",
                            "state": "input-available",
                            "parameters": tool_call.get("args", {}),
                            "result": None,
                            "error": None,
                        }
                        tool_calls_map[tool_id] = tool_info
                        yield {'type': 'tool', 'data': tool_info}

                if message.content and not tool_calls:
                    lcp = _lcp_len(current_message_content, message.content)
                    if lcp < len(message.content):
                        new_content = message.content[lcp:]
                        current_message_content = message.content
                        yield {"type": "chunk", "content": new_content}

            elif isinstance(message, ToolMessage):
                tool_call_id = getattr(message, "tool_call_id", "")
                is_error = getattr(message, "status", None) == "error"
                if tool_call_id in tool_calls_map:
                    tool_info = tool_calls_map[tool_call_id]
                    tool_info["state"] = "output-error" if is_error else "output-available"
                    tool_info["result"] = None if is_error else message.content
                    tool_info["error"] = message.content if is_error else None
                    yield {'type': 'tool_result', 'data': tool_info}

            await asyncio.sleep(0.01)

        thinking_duration = round(time.time() - thinking_start_time, 1)

        if has_sent_reasoning:
            yield {
                "type": "reasoning",
                "data": {
                    "content": f"深度思考完成，共思考了 {thinking_duration} 秒",
                    "duration": thinking_duration,
                },
            }

    async def _process_normal_stream_chat(
        self,
        data: Dict[str, Any],
        usage_tracker,
        cost_tracker: Optional[CostTracker] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        tools = self._get_tools(data)
        tool_names = [tool.name for tool in tools]
        weather_tool_names = {tool.name for tool in WEATHER_TOOLS}

        agent = create_base_agent(tools=tools, prompt_mode=data['mode'])

        chat_history = data.get('chat_history', [])
        chat_history = self._apply_compaction(chat_history)
        langchain_chat_history = convert_chat_history(chat_history)
        messages = []
        if langchain_chat_history:
            messages.extend(langchain_chat_history)

        user_message = self._build_user_message(data)
        messages.append(HumanMessage(content=user_message))
        graph_input = {"messages": messages}

        tool_calls_map: Dict[str, Dict] = {}
        current_message_content = ""
        last_ai_message = None
        all_messages = []
        tool_call_count: Dict[str, int] = {}
        prefer_tool_result = False

        config = {"recursion_limit": 50}

        async for chunk in agent.graph.astream(graph_input, config=config, stream_mode="messages"):
            if isinstance(chunk, tuple) and len(chunk) == 2:
                message, metadata = chunk
            else:
                message = chunk
                metadata = {}

            if metadata:
                usage_tracker.update_from_metadata(metadata)

            all_messages.append(message)

            if isinstance(message, AIMessage):
                last_ai_message = message
                tool_calls = getattr(message, "tool_calls", [])
                if tool_calls:
                    for tool_call in tool_calls:
                        tool_id = tool_call.get("id", "")
                        tool_name = tool_call.get("name", "")

                        if tool_name not in tool_call_count:
                            tool_call_count[tool_name] = 0
                        tool_call_count[tool_name] += 1

                        if tool_call_count[tool_name] > 3:
                            logger.warning(f"工具 {tool_name} 被调用了 {tool_call_count[tool_name]} 次，可能存在循环调用")

                        tool_info = {
                            "id": tool_id,
                            "name": tool_name,
                            "type": f"tool-call-{tool_name}",
                            "state": "input-available",
                            "parameters": tool_call.get("args", {}),
                            "result": None,
                            "error": None,
                        }
                        tool_calls_map[tool_id] = tool_info

                        yield {'type': 'tool', 'data': tool_info}

                if message.content and not tool_calls and not prefer_tool_result:
                    lcp = _lcp_len(current_message_content, message.content)
                    if lcp < len(message.content):
                        new_content = message.content[lcp:]
                        current_message_content = message.content
                        yield {"type": "chunk", "content": new_content}

            elif isinstance(message, ToolMessage):
                tool_call_id = getattr(message, "tool_call_id", "")
                is_error = getattr(message, "status", None) == "error"

                if tool_call_id in tool_calls_map:
                    tool_info = tool_calls_map[tool_call_id]
                    tool_info["state"] = "output-error" if is_error else "output-available"
                    tool_info["result"] = None if is_error else message.content
                    tool_info["error"] = message.content if is_error else None

                    yield {'type': 'tool_result', 'data': tool_info}

                    if (not is_error
                            and tool_info.get("name") in weather_tool_names
                            and tool_info.get("result")
                            and not tool_info.get("delivered")):
                        weather_result = tool_info["result"]
                        yield {"type": "chunk", "content": weather_result}
                        current_message_content += weather_result

                        ai_message = AIMessage(content=weather_result)
                        all_messages.append(ai_message)
                        last_ai_message = ai_message

                        tool_info["delivered"] = True
                        prefer_tool_result = True

            await asyncio.sleep(0.01)

        async for final_event in self._finalize_stream_response(
            all_messages, current_message_content, tool_calls_map,
            prefer_tool_result, data, weather_tool_names
        ):
            yield final_event

    async def _finalize_stream_response(
        self,
        all_messages: List,
        current_message_content: str,
        tool_calls_map: Dict[str, Dict],
        prefer_tool_result: bool,
        data: Dict[str, Any],
        weather_tool_names: set
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
            model = get_chat_model()
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
            model = get_chat_model()
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
