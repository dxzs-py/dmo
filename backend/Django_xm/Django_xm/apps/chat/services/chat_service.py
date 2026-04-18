"""
聊天服务层
将复杂的业务逻辑从视图中分离，提高代码可维护性和可测试性
"""
import json
import asyncio
import time
import logging
from typing import Optional, Dict, Any, List, AsyncGenerator

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

from Django_xm.apps.agents import create_base_agent
from Django_xm.apps.deep_research import create_deep_research_agent, should_use_deep_research
from Django_xm.apps.core.models import get_chat_model
from Django_xm.apps.core.prompts import SYSTEM_PROMPTS
from Django_xm.apps.core.tools import get_tools_for_request, WEATHER_TOOLS
from Django_xm.apps.core.usage_tracker import create_usage_tracker
from ..utils import _needs_completion, _lcp_len, convert_chat_history, extract_suggestions

logger = logging.getLogger(__name__)


class ChatService:
    """聊天服务类 - 处理聊天相关的业务逻辑"""

    @staticmethod
    def process_chat_request(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        处理非流式聊天请求
        
        Args:
            data: 验证后的请求数据
            
        Returns:
            包含响应消息的字典
        """
        tools = get_tools_for_request(data['use_tools'], data['use_advanced_tools'])
        agent = create_base_agent(tools=tools, prompt_mode=data['mode'])

        chat_history = data.get('chat_history', [])
        langchain_chat_history = convert_chat_history(chat_history)

        response = agent.invoke(
            input_text=data['message'],
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

    @staticmethod
    async def process_stream_chat_request(data: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        """
        处理流式聊天请求
        
        Args:
            data: 验证后的请求数据
            
        Yields:
            包含事件类型和数据的字典
        """
        usage_tracker = create_usage_tracker()

        yield {'type': 'start', 'message': '开始生成...'}

        if data['mode'] == 'deep-thinking':
            async for event in ChatService._process_deep_thinking_stream(data, usage_tracker):
                yield event
            context_info = usage_tracker.get_usage_info()
            yield {'type': 'context', 'data': context_info}
            yield {'type': 'end', 'message': '生成完成'}
            usage_tracker.log_summary()
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

            deep_result = await ChatService._run_deep_research_task(data['message'])
            final_report = deep_result.get("final_report") or deep_result.get("error")
            if not final_report:
                final_report = (
                    "深度研究已完成，但未生成可用报告。"
                    " 请稍后重试或调整问题表述。"
                )

            yield {"type": "chunk", "content": final_report}

            context_info = usage_tracker.get_usage_info()
            yield {'type': 'context', 'data': context_info}
            yield {'type': 'end', 'message': '生成完成'}

            usage_tracker.log_summary()
            logger.info("深度研究流程完成")
            return

        async for event in ChatService._process_normal_stream_chat(data, usage_tracker):
            yield event

        context_info = usage_tracker.get_usage_info()
        yield {'type': 'context', 'data': context_info}
        yield {'type': 'end', 'message': '生成完成'}

        usage_tracker.log_summary()
        logger.info("流式聊天请求处理完成")

    @staticmethod
    async def _run_deep_research_task(query: str) -> Dict[str, Any]:
        """运行深度研究任务"""
        thread_id = f"deep_{int(time.time() * 1000)}"

        def _task():
            agent = create_deep_research_agent(
                thread_id=thread_id,
                enable_web_search=True,
                enable_doc_analysis=False,
            )
            return agent.research(query)

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _task)

    @staticmethod
    async def _process_deep_thinking_stream(
        data: Dict[str, Any],
        usage_tracker
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """处理深度思考模式的流式响应"""
        tools = get_tools_for_request(data['use_tools'], data['use_advanced_tools'])
        agent = create_base_agent(tools=tools, prompt_mode='deep-thinking')

        chat_history = data.get('chat_history', [])
        langchain_chat_history = convert_chat_history(chat_history)
        messages = []
        if langchain_chat_history:
            messages.extend(langchain_chat_history)
        messages.append(HumanMessage(content=data['message']))
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

    @staticmethod
    async def _process_normal_stream_chat(
        data: Dict[str, Any],
        usage_tracker
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """处理普通流式聊天"""
        tools = get_tools_for_request(data['use_tools'], data['use_advanced_tools'])
        tool_names = [tool.name for tool in tools]
        weather_tool_names = {tool.name for tool in WEATHER_TOOLS}

        agent = create_base_agent(tools=tools, prompt_mode=data['mode'])

        chat_history = data.get('chat_history', [])
        langchain_chat_history = convert_chat_history(chat_history)
        messages = []
        if langchain_chat_history:
            messages.extend(langchain_chat_history)

        messages.append(HumanMessage(content=data['message']))
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

        async for final_event in ChatService._finalize_stream_response(
            all_messages, current_message_content, tool_calls_map,
            prefer_tool_result, data, weather_tool_names
        ):
            yield final_event

    @staticmethod
    async def _finalize_stream_response(
        all_messages: List,
        current_message_content: str,
        tool_calls_map: Dict[str, Dict],
        prefer_tool_result: bool,
        data: Dict[str, Any],
        weather_tool_names: set
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """完成流式响应的最终处理"""
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
        """获取支持的模式列表"""
        modes = {}
        for mode_name, prompt in SYSTEM_PROMPTS.items():
            if mode_name in cls.FRONTEND_SUPPORTED_MODES:
                modes[mode_name] = prompt.split('\n')[0]
        return modes
